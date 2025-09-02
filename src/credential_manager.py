"""
High-performance credential manager with call-based rotation and caching.
Rotates credentials based on API call count rather than time for better quota distribution.
"""
import asyncio
import glob
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Dict, Any
from asyncio import Queue, Event
from contextlib import asynccontextmanager

import aiofiles
import httpx
import toml
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

from config import (
    CREDENTIALS_DIR, CODE_ASSIST_ENDPOINT,
    get_proxy_config, get_calls_per_rotation, get_http_timeout, get_max_connections,
    get_auto_ban_enabled,
    get_auto_ban_error_codes
)
from log import log
from .memory_manager import register_cache_for_cleanup
from .utils import get_user_agent, get_client_metadata

def _normalize_to_relative_path(filepath: str, base_dir: str = None) -> str:
    """将文件路径标准化为相对于CREDENTIALS_DIR的相对路径"""
    if base_dir is None:
        base_dir = CREDENTIALS_DIR
    
    # 如果已经是相对路径且在当前目录内，直接返回
    if not os.path.isabs(filepath):
        # 检查相对路径是否安全（不包含..等）
        if ".." not in filepath and filepath.endswith('.json'):
            return os.path.basename(filepath)  # 只保留文件名
    
    # 绝对路径转相对路径
    abs_filepath = os.path.abspath(filepath)
    abs_base_dir = os.path.abspath(base_dir)
    
    try:
        # 如果文件在base_dir内，返回相对路径（只要文件名）
        if abs_filepath.startswith(abs_base_dir):
            return os.path.basename(abs_filepath)
    except Exception:
        pass
    
    # 环境变量路径保持原样
    if filepath.startswith('<ENV_'):
        return filepath
    
    # 其他情况也只返回文件名
    return os.path.basename(filepath)

class CredentialManager:
    """High-performance credential manager with call-based rotation and caching."""

    def __init__(self, calls_per_rotation: int = None):  # Use dynamic config by default
        # 细粒度锁机制
        self._read_lock = asyncio.Lock()  # 保护读操作
        self._state_lock = asyncio.Lock()  # 保护状态修改
        
        # 写操作队列化机制
        self._write_queue: Queue = Queue()
        self._write_worker_running = False
        self._write_worker_task: Optional[asyncio.Task] = None
        self._shutdown_event = Event()
        
        self._current_credential_index = 0
        self._credential_files: List[str] = []
        
        # Call-based rotation instead of time-based
        self._call_count = 0
        self._calls_per_rotation = calls_per_rotation or get_calls_per_rotation()
        
        # Onboarding state
        self._onboarding_complete = False
        self._onboarding_checked = False
        
        # HTTP client reuse
        self._http_client: Optional[httpx.AsyncClient] = None
        
        # TOML状态文件路径
        self._state_file = os.path.join(CREDENTIALS_DIR, "creds_state.toml")
        self._creds_state: Dict[str, Any] = {}
        self._state_dirty = False  # 状态脏标记，减少不必要的写入
        
        # 当前使用的凭证文件路径
        self._current_file_path: Optional[str] = None
        
        # 最后一次文件扫描时间
        self._last_file_scan_time = 0
        
        self._initialized = False
        
        # 原子操作计数器
        self._pending_writes = 0
        self._readers_count = 0
        self._readers_lock = asyncio.Lock()
        
        # 原子操作锁和计数器
        self._atomic_counter = 0
        self._atomic_lock = asyncio.Lock()
    
    @asynccontextmanager
    async def _read_context(self):
        """读操作上下文管理器"""
        async with self._readers_lock:
            self._readers_count += 1
            
        try:
            yield
        finally:
            async with self._readers_lock:
                self._readers_count -= 1
    
    @asynccontextmanager
    async def _write_context(self):
        """写操作上下文管理器 - 等待所有读操作完成"""
        # 等待所有读者完成
        while True:
            async with self._readers_lock:
                if self._readers_count == 0:
                    break
            await asyncio.sleep(0.01)  # 短暂等待
        
        # 获取写锁
        async with self._state_lock:
            try:
                yield
            finally:
                pass
    
    async def _atomic_operation(self, operation_name: str, func, *args, **kwargs):
        """原子操作包装器"""
        async with self._atomic_lock:
            self._atomic_counter += 1
            operation_id = self._atomic_counter
            log.debug(f"开始原子操作[{operation_id}]: {operation_name}")
            
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                log.debug(f"完成原子操作[{operation_id}]: {operation_name}")
                return result
            except Exception as e:
                log.error(f"原子操作[{operation_id}]失败: {operation_name} - {e}")
                raise

    async def _start_write_worker(self):
        """启动写操作工作线程"""
        if not self._write_worker_running:
            self._write_worker_running = True
            self._write_worker_task = asyncio.create_task(self._write_worker())
    
    async def _write_worker(self):
        """写操作工作线程 - 串行化处理所有写操作"""
        while not self._shutdown_event.is_set():
            try:
                # 等待写操作任务
                timeout = 1.0  # 1秒超时，允许定期检查关闭信号
                try:
                    write_task = await asyncio.wait_for(
                        self._write_queue.get(), 
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    continue  # 继续循环，检查关闭信号
                
                # 执行写操作
                if write_task:
                    await write_task["func"](*write_task["args"], **write_task["kwargs"])
                    
                    # 通知等待的协程
                    if "event" in write_task:
                        write_task["event"].set()
                
                # 标记任务完成
                self._write_queue.task_done()
                
            except Exception as e:
                log.error(f"写操作工作线程错误: {e}")
        
        log.info("写操作工作线程已停止")
    
    async def _queue_write_operation(self, func, *args, **kwargs) -> None:
        """将写操作加入队列"""
        if self._shutdown_event.is_set():
            return
            
        # 创建事件用于等待完成
        completion_event = Event()
        
        write_task = {
            "func": func,
            "args": args,
            "kwargs": kwargs,
            "event": completion_event
        }
        
        # 加入队列
        await self._write_queue.put(write_task)
        
        # 等待完成
        await completion_event.wait()
    
    async def initialize(self):
        """Initialize the credential manager."""
        async with self._state_lock:
            if self._initialized:
                return
            
            # 启动写操作工作线程
            await self._start_write_worker()
            
            # 加载状态文件
            await self._load_state()
            
            await self._discover_credential_files()
            
            # Initialize HTTP client with connection pooling and proxy support
            proxy = get_proxy_config()
            client_kwargs = {
                "timeout": get_http_timeout(),
                "limits": httpx.Limits(max_keepalive_connections=20, max_connections=get_max_connections())
            }
            if proxy:
                client_kwargs["proxy"] = proxy
            self._http_client = httpx.AsyncClient(**client_kwargs)
            
            self._initialized = True
            
            # 注册到内存管理器
            register_cache_for_cleanup("credential_manager", self)

    async def close(self):
        """Clean up resources."""
        # 关闭写操作工作线程
        self._shutdown_event.set()
        if self._write_worker_task:
            try:
                await asyncio.wait_for(self._write_worker_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._write_worker_task.cancel()
                try:
                    await self._write_worker_task
                except asyncio.CancelledError:
                    pass
        
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
    
    def emergency_cleanup(self) -> Dict[str, int]:
        """紧急内存清理"""
        log.warning("执行凭证管理器紧急内存清理")
        cleaned = {
            'cache_cleared': 0,
            'states_cleaned': 0
        }
        
        # 清理过期状态
        if self._creds_state:
            original_count = len(self._creds_state)
            # 只保留最近成功的凭证状态
            keep_states = {}
            for filename, state in list(self._creds_state.items()):
                if state.get("last_success") and not state.get("disabled", False):
                    keep_states[filename] = state
                    if len(keep_states) >= 5:  # 最多保留5个状态
                        break
            
            self._creds_state = keep_states
            self._state_dirty = True
            cleaned['states_cleaned'] = original_count - len(keep_states)
        
        log.info(f"凭证管理器紧急清理完成: {cleaned}")
        return cleaned

    async def _load_state(self):
        """从TOML文件加载状态"""
        try:
            if os.path.exists(self._state_file):
                async with aiofiles.open(self._state_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                self._creds_state = toml.loads(content)
            else:
                self._creds_state = {}
            
        except Exception as e:
            log.warning(f"Failed to load state file: {e}")
            self._creds_state = {}

    async def _save_state_unsafe(self):
        """保存状态到TOML文件（不安全版本，必须在锁内调用）"""
        # 使用脏标记，只在必要时写入
        if not self._state_dirty:
            return
            
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            async with aiofiles.open(self._state_file, "w", encoding="utf-8") as f:
                await f.write(toml.dumps(self._creds_state))
            self._state_dirty = False  # 清除脏标记
            log.debug("状态文件已保存")
        except Exception as e:
            log.error(f"Failed to save state file: {e}")
    
    async def _save_state(self):
        """安全保存状态到TOML文件"""
        await self._queue_write_operation(self._save_state_unsafe)
    
    def _get_cred_state(self, filename: str) -> Dict[str, Any]:
        """获取指定凭证文件的状态，使用相对路径作为键"""
        # 标准化为相对路径
        relative_filename = _normalize_to_relative_path(filename)
        
        # 先检查是否已存在（支持向后兼容）
        existing_state = None
        existing_key = None
        
        # 优先查找相对路径键
        if relative_filename in self._creds_state:
            return self._creds_state[relative_filename]
        
        # 向后兼容：查找可能的绝对路径键并迁移
        for key, state in list(self._creds_state.items()):
            if os.path.isabs(key):
                # 检查绝对路径是否对应同一个文件
                if _normalize_to_relative_path(key) == relative_filename:
                    existing_state = state
                    existing_key = key
                    break
            elif key == relative_filename:
                # 理论上不会到这里，但为安全起见
                return state
        
        if existing_state is not None:
            # 迁移绝对路径键到相对路径键
            self._creds_state[relative_filename] = existing_state
            del self._creds_state[existing_key]
            log.info(f"Migrated creds state key from absolute to relative: {existing_key} -> {relative_filename}")
            return existing_state
        
        # 创建新状态（使用相对路径作为键）
        log.debug(f"Creating new state for {relative_filename}")
        self._creds_state[relative_filename] = {
            "error_codes": [],
            "disabled": False,
            "last_success": None,
            "user_email": None
        }
        self._state_dirty = True  # 标记状态已修改
        return self._creds_state[relative_filename]

    async def record_error(self, filename: str, status_code: int, response_content: str = ""):
        """记录API错误码"""
        async with self._write_context():
            # 使用相对路径进行状态管理
            relative_filename = _normalize_to_relative_path(filename)
            cred_state = self._get_cred_state(filename)
            
            # 记录错误码
            if "error_codes" not in cred_state:
                cred_state["error_codes"] = []
            if status_code not in cred_state["error_codes"]:
                cred_state["error_codes"].append(status_code)
            
            # 简单记录429错误，不做特殊处理
            if status_code == 429:
                log.info(f"Got 429 error for {os.path.basename(filename)}")
            
            # 自动封禁功能
            auto_ban_enabled = get_auto_ban_enabled()
            auto_ban_error_codes = get_auto_ban_error_codes()
            log.debug(f"AUTO_BAN check: enabled={auto_ban_enabled}, status_code={status_code}, error_codes={auto_ban_error_codes}")
            if auto_ban_enabled and status_code in auto_ban_error_codes:
                if not cred_state.get("disabled", False):
                    cred_state["disabled"] = True
                    log.warning(f"AUTO_BAN: Disabled credential {os.path.basename(filename)} due to error {status_code}")
                else:
                    log.debug(f"Credential {os.path.basename(filename)} already disabled, error {status_code} recorded")
            elif auto_ban_enabled:
                log.debug(f"AUTO_BAN enabled but status_code {status_code} not in ban list {auto_ban_error_codes}")
            else:
                log.debug(f"AUTO_BAN disabled, status_code {status_code} recorded but no auto ban")
            
            self._state_dirty = True  # 标记状态已修改
            await self._save_state()

    async def record_success(self, filename: str, api_type: str = "other"):
        """记录成功的API调用"""
        async with self._write_context():
            # 使用相对路径进行状态管理
            cred_state = self._get_cred_state(filename)
            
            # 只有聊天内容生成API成功才清除错误码，其他API不清除
            if api_type == "chat_content":
                cred_state["error_codes"] = []
                log.debug(f"Cleared error codes for {filename} due to successful chat content generation")
            
            cred_state["last_success"] = datetime.now(timezone.utc).isoformat()
            
            self._state_dirty = True  # 标记状态已修改
            await self._save_state()

    async def fetch_user_email(self, filename: str) -> Optional[str]:
        """获取凭证对应的用户邮箱地址"""
        try:
            # 加载凭证
            creds, _ = await self._load_credentials_from_file(filename)
            if not creds or not creds.token:
                log.warning(f"无法加载凭证或获取访问令牌: {filename}")
                return None
            
            # 刷新令牌如果需要
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(GoogleAuthRequest())
                except Exception as e:
                    log.warning(f"刷新凭证失败 {filename}: {e}")
                    return None
            
            # 调用Google userinfo API获取邮箱
            if not self._http_client:
                log.warning("HTTP客户端未初始化")
                return None
                
            headers = {
                "Authorization": f"Bearer {creds.token}",
                "Accept": "application/json"
            }
            
            response = await self._http_client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers=headers
            )
            
            if response.status_code == 200:
                user_info = response.json()
                email = user_info.get("email")
                if email:
                    log.info(f"成功获取邮箱地址: {email} for {os.path.basename(filename)}")
                    return email
                else:
                    log.warning(f"userinfo响应中没有邮箱信息: {user_info}")
                    return None
            else:
                log.warning(f"获取用户信息失败 {filename}: HTTP {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            log.error(f"获取用户邮箱失败 {filename}: {e}")
            return None

    async def get_or_fetch_user_email(self, filename: str) -> Optional[str]:
        """获取用户邮箱（优先使用缓存，如果没有则从API获取）"""
        async with self._read_context():
            cred_state = self._get_cred_state(filename)
            
            # 检查是否已经有邮箱信息
            if "user_email" in cred_state and cred_state["user_email"]:
                return cred_state["user_email"]
        
        # 从API获取邮箱
        email = await self.fetch_user_email(filename)
        if email:
            async with self._write_context():
                cred_state = self._get_cred_state(filename)
                cred_state["user_email"] = email
                self._state_dirty = True  # 标记状态已修改
                await self._save_state()
        
        return email


    def is_cred_disabled(self, filename: str) -> bool:
        """检查凭证是否被禁用"""
        cred_state = self._get_cred_state(filename)
        return cred_state.get("disabled", False)

    async def set_cred_disabled(self, filename: str, disabled: bool):
        """设置凭证的禁用状态"""
        async with self._write_context():
            relative_filename = _normalize_to_relative_path(filename)
            log.info(f"Setting disabled={disabled} for file: {relative_filename}")
            cred_state = self._get_cred_state(filename)
            old_disabled = cred_state.get("disabled", False)
            cred_state["disabled"] = disabled
            self._state_dirty = True  # 标记状态已修改
            log.info(f"Updated state for {relative_filename}: {cred_state}")
            await self._save_state()
            log.info(f"State saved successfully")
            
            # 如果状态发生了变化，强制重新发现文件
            if old_disabled != disabled:
                log.info(f"Credential disabled status changed from {old_disabled} to {disabled}, refreshing file list")
                
                # 立即重新发现文件以更新可用文件列表
                await self._discover_credential_files()
                log.info(f"File list refreshed. Available files: {len(self._credential_files)}")

    def get_creds_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证的状态信息"""
        status = {}
        # 获取所有文件
        credentials_dir = CREDENTIALS_DIR
        patterns = [os.path.join(credentials_dir, "*.json")]
        all_files = []
        for pattern in patterns:
            all_files.extend(glob.glob(pattern))
        all_files = sorted(list(set(all_files)))
        
        for filename in all_files:
            # 使用相对路径作为键，但返回时仍使用绝对路径（兼容前端）
            absolute_filename = os.path.abspath(filename)
            cred_state = self._get_cred_state(filename)
            file_status = {
                "error_codes": cred_state.get("error_codes", []),
                "disabled": cred_state.get("disabled", False),
                "last_success": cred_state.get("last_success"),
                "user_email": cred_state.get("user_email")
            }
            status[absolute_filename] = file_status
            
            # 调试：记录特定文件的状态
            basename = os.path.basename(filename)
            if "atomic-affinity" in basename:
                relative_filename = _normalize_to_relative_path(filename)
                log.info(f"Status for {basename}: disabled={file_status['disabled']} (normalized: {relative_filename})")
                
        return status

    def get_current_file_path(self) -> Optional[str]:
        """获取当前使用的凭证文件路径"""
        return self._current_file_path

    async def _discover_credential_files(self):
        """Discover all credential files with hot reload support."""
        # 获取状态文件中记录的文件列表（作为基准进行对比）
        state_files = set()
        if self._creds_state:
            for key in self._creds_state.keys():
                # 跳过usage_stats等非文件名的key
                if not key.endswith('.usage_stats'):
                    # 将相对路径转换为绝对路径以便对比
                    if not os.path.isabs(key):
                        abs_path = os.path.join(CREDENTIALS_DIR, key)
                        state_files.add(abs_path)
                    else:
                        state_files.add(key)
        
        all_files = []
        
        # Discover from directory
        credentials_dir = CREDENTIALS_DIR
        patterns = [os.path.join(credentials_dir, "*.json")]
        
        for pattern in patterns:
            discovered_files = glob.glob(pattern)
            for file in discovered_files:
                all_files.append(file)
        
        all_files = sorted(list(set(all_files)))
        
        # 过滤掉被禁用的文件（移除CD机制）
        self._credential_files = []
        for filename in all_files:
            is_disabled = self.is_cred_disabled(filename)
            
            if not is_disabled:
                self._credential_files.append(filename)
            else:
                # 记录被过滤的文件信息供调试
                log.debug(f"Filtered out {os.path.basename(filename)}: disabled")
        
        new_files = set(self._credential_files)
        
        # 检测文件变化 - 与状态文件中记录的文件对比
        if state_files != new_files:
            added_files = new_files - state_files
            removed_files = state_files - new_files
            
            if added_files:
                log.info(f"发现新的可用凭证文件: {[os.path.basename(f) for f in added_files]}")
            
            if removed_files:
                log.info(f"凭证文件已移除或不可用: {[os.path.basename(f) for f in removed_files]}")
                # 如果当前使用的文件被移除，切换到下一个文件
                if self._credential_files and self._current_credential_index >= len(self._credential_files):
                    self._current_credential_index = 0
        
        # 同步状态文件，清理不存在的文件状态
        await self._sync_state_with_files(all_files)
        
        if not self._credential_files:
            log.warning("No available credential files found")

    async def _sync_state_with_files(self, current_files: List[str]):
        """同步状态文件与实际文件"""
        # 标准化当前文件列表为相对路径
        normalized_current_files = [_normalize_to_relative_path(f) for f in current_files]
        
        # 移除不存在文件的状态
        files_to_remove = []
        for filename in list(self._creds_state.keys()):
            # 将状态文件中的键也标准化为相对路径进行比较
            normalized_state_key = _normalize_to_relative_path(filename) if not filename.startswith('<ENV_') else filename
            if normalized_state_key not in normalized_current_files:
                files_to_remove.append(filename)
        
        if files_to_remove:
            for filename in files_to_remove:
                del self._creds_state[filename]
            await self._save_state()
            log.info(f"Removed state for deleted files: {files_to_remove}")

    def _should_rotate(self) -> bool:
        """检查是否需要轮换凭证"""
        current_calls_per_rotation = get_calls_per_rotation()
        return self._call_count >= current_calls_per_rotation

    async def _rotate_credential_if_needed(self):
        """Rotate to next credential if call limit reached."""
        current_calls_per_rotation = get_calls_per_rotation()
        if self._call_count >= current_calls_per_rotation:
            self._current_credential_index = (self._current_credential_index + 1) % len(self._credential_files)
            self._call_count = 0  # Reset call counter
            log.info(f"Rotated to credential index {self._current_credential_index}")
    
    async def _force_rotate_credential(self):
        """Force rotate to next credential immediately (used for 429 errors)."""
        if len(self._credential_files) <= 1:
            log.warning("Only one credential available, cannot rotate")
            return

        old_index = self._current_credential_index
        self._current_credential_index = (self._current_credential_index + 1) % len(self._credential_files)
        self._call_count = 0  # Reset call counter

        # 重置当前文件路径
        self._current_file_path = None

        log.info(f"Force rotated from credential index {old_index} to {self._current_credential_index} due to 429 error")

    async def _discover_credential_files_unlocked(self):
        """在锁外进行文件发现操作（用于避免阻塞其他操作）"""
        # 这个方法不使用锁，因为主要是文件系统读操作
        # 文件列表的更新会在后续的 _discover_credential_files 中同步
        
        # 临时存储发现的文件
        temp_files = []
        
        # 复制当前状态以避免并发修改问题
        try:
            # 检查文件系统中的凭证
            if os.path.exists(CREDENTIALS_DIR):
                json_pattern = os.path.join(CREDENTIALS_DIR, "*.json")
                for filepath in glob.glob(json_pattern):
                    # 检查禁用状态，使用相对路径保持一致性
                    if not self.is_cred_disabled(filepath):
                        # 转换为相对路径格式 ./creds/filename.json
                        relative_path = os.path.join("./creds", os.path.basename(filepath))
                        temp_files.append(relative_path)
            
            # 在锁内快速更新文件列表
            async with self._state_lock:
                if temp_files != self._credential_files:
                    # 获取状态文件中记录的文件列表（作为基准进行对比）
                    state_files = set()
                    if self._creds_state:
                        for key in self._creds_state.keys():
                            # 跳过usage_stats等非文件名的key
                            if not key.endswith('.usage_stats'):
                                state_files.add(key)
                    
                    self._credential_files = temp_files
                    new_files = set(self._credential_files)
                    
                    # 日志记录变化 - 与状态文件中记录的文件对比
                    if state_files != new_files:
                        added_files = new_files - state_files
                        removed_files = state_files - new_files
                        
                        if added_files:
                            log.info(f"发现新的可用凭证文件: {[os.path.basename(f) for f in added_files]}")
                        
                        if removed_files:
                            log.info(f"移除不可用凭证文件: {[os.path.basename(f) for f in removed_files]}")
        
        except Exception as e:
            log.error(f"Error in _discover_credential_files_unlocked: {e}")
            # 发生错误时，回退到带锁的方法
            async with self._write_context():
                await self._discover_credential_files()

    async def _load_credential_with_fallback(self, current_file: str) -> Tuple[Optional[Credentials], Optional[str]]:
        """Load credentials with fallback to next file on failure."""
        creds, project_id = await self._load_credentials_from_file(current_file)
        if not creds:
            # Try next file on failure
            self._current_credential_index = (self._current_credential_index + 1) % len(self._credential_files)
            if self._current_credential_index < len(self._credential_files):
                current_file = self._credential_files[self._current_credential_index]
                creds, project_id = await self._load_credentials_from_file(current_file)
        return creds, project_id

    async def get_credentials(self) -> Tuple[Optional[Credentials], Optional[str]]:
        """获取凭证，使用原子操作保护"""
        return await self._atomic_operation(
            "get_credentials", 
            self._get_credentials_internal
        )
    
    async def _get_credentials_internal(self) -> Tuple[Optional[Credentials], Optional[str]]:
        """内部获取凭证的实现"""
        # 第一阶段：检查是否需要重新发现文件
        async with self._read_context():
            current_calls_per_rotation = get_calls_per_rotation()
            
            # 检查是否需要重新发现文件
            current_time = time.time()
            should_check_files = (
                not self._credential_files or  # 无文件时每次都检查
                self._call_count >= current_calls_per_rotation or  # 轮换时检查
                current_time - self._last_file_scan_time > 30  # 每30秒至少扫描一次文件
            )
        
        # 第二阶段：如果需要，在锁外进行文件发现（避免阻塞其他操作）
        if should_check_files:
            # 文件发现操作不需要锁，因为它主要是读操作
            await self._discover_credential_files_unlocked()
            # 更新扫描时间
            async with self._state_lock:
                self._last_file_scan_time = current_time
        
        # 第三阶段：获取凭证（持有锁但时间较短）
        async with self._state_lock:
            current_calls_per_rotation = get_calls_per_rotation()  # 重新获取配置
            
            # 需要加载新凭证
            if self._call_count >= current_calls_per_rotation:
                log.info(f"Rotating credentials after {self._call_count} calls")
            else:
                log.info("Cache miss - loading fresh credentials")
            
            # 轮换凭证
            await self._rotate_credential_if_needed()
            
            if not self._credential_files:
                log.error("No available credential files")
                return None, None
            
            current_file = self._credential_files[self._current_credential_index]
            
            # 记录当前使用的文件路径
            self._current_file_path = current_file
            
        # 第四阶段：在锁外加载凭证文件（避免I/O阻塞其他操作）
        creds, project_id = await self._load_credential_with_fallback(current_file)
        
        # 返回凭证
        if creds:
            log.debug(f"成功加载凭证: {os.path.basename(current_file)}")
        else:
            log.error(f"加载凭证失败: {os.path.basename(current_file)}")
        
        return creds, project_id

    async def _update_token_in_file(self, file_path: str, creds: Credentials):
        """更新凭证文件中的token和过期时间"""
        try:
            # 读取原文件
            async with aiofiles.open(file_path, "r") as f:
                content = await f.read()
            creds_data = json.loads(content)
            
            # 更新access_token和过期时间（使用标准OAuth2字段名）
            if creds.token:
                creds_data["access_token"] = creds.token
                # 移除旧的token字段（如果存在）避免重复
                if "token" in creds_data:
                    del creds_data["token"]
            
            if creds.expiry:
                # 保存ISO格式的过期时间
                creds_data["expiry"] = creds.expiry.isoformat()
            
            # 写入队列化处理
            await self._queue_write_operation(
                self._write_json_file_unsafe,
                file_path, creds_data
            )
            
            log.info(f"Token已更新到文件: {os.path.basename(file_path)}")
            
        except Exception as e:
            log.error(f"更新token到文件失败 {file_path}: {e}")
    
    async def _write_json_file_unsafe(self, file_path: str, data: dict):
        """不安全的JSON文件写入（必须在队列中调用）"""
        async with aiofiles.open(file_path, "w") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
    
    async def _load_credentials_from_file(self, file_path: str) -> Tuple[Optional[Credentials], Optional[str]]:
        """Load credentials from file (optimized)."""
        try:
            async with aiofiles.open(file_path, "r") as f:
                content = await f.read()
            creds_data = json.loads(content)
            
            if "refresh_token" not in creds_data or not creds_data["refresh_token"]:
                log.warning(f"No refresh token in {file_path}")
                return None, None
            
            # Auto-add 'type' field if missing but has required OAuth fields
            if 'type' not in creds_data and all(key in creds_data for key in ['client_id', 'refresh_token']):
                creds_data['type'] = 'authorized_user'
                log.debug(f"Auto-added 'type' field to credential from file {file_path}")
            
            # Handle different credential formats
            if "access_token" in creds_data and "token" not in creds_data:
                creds_data["token"] = creds_data["access_token"]
            if "scope" in creds_data and "scopes" not in creds_data:
                creds_data["scopes"] = creds_data["scope"].split()
            
            # Handle expiry time format for OAuth2 library
            if "expiry" in creds_data and isinstance(creds_data["expiry"], str):
                try:
                    exp = creds_data["expiry"]
                    # Parse the datetime string with proper timezone handling
                    if "+00:00" in exp or exp.endswith("Z"):
                        # Already has timezone info
                        if exp.endswith("Z"):
                            parsed = datetime.fromisoformat(exp.replace('Z', '+00:00'))
                        else:
                            parsed = datetime.fromisoformat(exp)
                    else:
                        # Assume UTC if no timezone info
                        parsed = datetime.fromisoformat(exp).replace(tzinfo=timezone.utc)
                    
                    # OAuth2 library expects ISO string format without timezone info (naive datetime)
                    # Convert to UTC and format as string for OAuth2 library
                    utc_datetime = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                    creds_data["expiry"] = utc_datetime.isoformat()
                    log.debug(f"Parsed expiry time: {parsed} -> OAuth2 format: {creds_data['expiry']}")
                except Exception as e:
                    log.warning(f"Could not parse expiry in {file_path}: {e}")
                    del creds_data["expiry"]
            
            creds = Credentials.from_authorized_user_info(creds_data, creds_data.get("scopes"))
            project_id = creds_data.get("project_id")
            setattr(creds, "project_id", project_id)
            
            # 智能token管理：检查是否需要刷新，如果需要则刷新并写入文件
            should_refresh = creds.expired and creds.refresh_token
            
            # 调试OAuth2库的过期检查
            log.debug(f"OAuth2库过期检查 - 文件: {os.path.basename(file_path)}")
            log.debug(f"creds.expired: {creds.expired}")
            log.debug(f"creds.expiry: {creds.expiry}")
            log.debug(f"should_refresh (初始): {should_refresh}")

            # 如果文件中已有有效access_token，先尝试使用它
            if not should_refresh and ("access_token" in creds_data or "token" in creds_data) and "expiry" in creds_data:
                try:
                    # 解析文件中的过期时间
                    file_expiry_str = creds_data["expiry"]
                    if isinstance(file_expiry_str, str):
                        # 解析ISO格式时间
                        if "+" in file_expiry_str:
                            file_expiry = datetime.fromisoformat(file_expiry_str)
                        elif file_expiry_str.endswith("Z"):
                            file_expiry = datetime.fromisoformat(file_expiry_str.replace('Z', '+00:00'))
                        else:
                            file_expiry = datetime.fromisoformat(file_expiry_str)
                        
                        # 检查是否还有至少5分钟有效期
                        now = datetime.now(timezone.utc)
                        if file_expiry.tzinfo is None:
                            file_expiry = file_expiry.replace(tzinfo=timezone.utc)
                        
                        time_left = (file_expiry - now).total_seconds()
                        
                        # 调试时间比较
                        log.debug(f"时间比较调试 - 文件: {os.path.basename(file_path)}")
                        log.debug(f"当前时间: {now}")  
                        log.debug(f"Token过期时间: {file_expiry}")
                        log.debug(f"剩余时间(秒): {time_left}")
                        log.debug(f"剩余时间(分钟): {int(time_left/60)}")
                        
                        if time_left > 300:  # 5分钟缓冲
                            # 使用文件中的access_token（优先）或token
                            file_token = creds_data.get("access_token") or creds_data.get("token")
                            creds._token = file_token
                            creds._expiry = file_expiry
                            log.info(f"使用文件中的有效access_token，剩余时间: {int(time_left/60)}分钟")
                        else:
                            should_refresh = True
                            log.debug(f"文件中token即将过期（剩余{int(time_left/60)}分钟），需要刷新")

                except Exception as e:
                    log.warning(f"解析文件中token过期时间失败: {e}，将刷新token")
                    should_refresh = True
            
            # 执行token刷新
            if should_refresh:
                try:
                    log.debug(f"刷新token - 文件: {os.path.basename(file_path)}")
                    creds.refresh(GoogleAuthRequest())
                    
                    # 刷新成功后写入文件
                    await self._update_token_in_file(file_path, creds)
                    log.info(f"Token刷新并保存成功: {os.path.basename(file_path)}")
                        
                except Exception as e:
                    log.warning(f"Token刷新失败 {os.path.basename(file_path)}: {e}")
            
            return creds, project_id
        except Exception as e:
            log.error(f"Failed to load credentials from {os.path.basename(file_path)}: {e}")
            return None, None

    async def increment_call_count(self):
        """Increment the call count for tracking rotation."""
        return await self._atomic_operation(
            "increment_call_count", 
            self._increment_call_count_internal
        )
    
    async def _increment_call_count_internal(self):
        """增加调用计数的内部实现"""
        async with self._state_lock:
            self._call_count += 1
            current_calls_per_rotation = get_calls_per_rotation()
            log.debug(f"Call count incremented to {self._call_count}/{current_calls_per_rotation}")

    async def rotate_to_next_credential(self):
        """Manually rotate to next credential (for error recovery)."""
        return await self._atomic_operation(
            "rotate_to_next_credential", 
            self._rotate_to_next_credential_internal
        )
    
    async def _rotate_to_next_credential_internal(self):
        """轮换凭证的内部实现"""
        async with self._write_context():
            # Reset call count
            self._call_count = 0
            
            # 重新发现可用凭证文件（过滤掉禁用的文件）
            await self._discover_credential_files()
            
            # 如果没有可用凭证，早期返回
            if not self._credential_files:
                log.error("No available credentials to rotate to")
                return
            
            # Move to next credential
            self._current_credential_index = (self._current_credential_index + 1) % len(self._credential_files)
            log.info(f"Rotated to credential index {self._current_credential_index}, total available: {len(self._credential_files)}")
            
            # 记录当前使用的文件名称供调试
            if self._credential_files:
                current_file = self._credential_files[self._current_credential_index]
                log.info(f"Now using credential: {os.path.basename(current_file)}")

    def get_user_project_id(self, creds: Credentials) -> str:
        """Get user project ID from credentials."""
        project_id = getattr(creds, "project_id", None)
        if project_id:
            return project_id

        raise Exception(
            "Unable to determine Google Cloud project ID. "
            "Ensure credential file contains project_id."
        )

    async def onboard_user(self, creds: Credentials, project_id: str):
        """Optimized user onboarding with caching."""
        # Skip if already onboarded for this session
        if self._onboarding_complete:
            return
        
        async with self._write_context():
            # Double-check after acquiring lock
            if self._onboarding_complete:
                return
            
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(GoogleAuthRequest())
                except Exception as e:
                    raise Exception(f"Failed to refresh credentials during onboarding: {str(e)}")
            
            headers = {
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
                "User-Agent": get_user_agent(),
            }
            
            load_assist_payload = {
                "cloudaicompanionProject": project_id,
                "metadata": get_client_metadata(project_id),
            }
            
            try:
                # Use reusable HTTP client
                resp = await self._http_client.post(
                    f"{CODE_ASSIST_ENDPOINT}/v1internal:loadCodeAssist",
                    json=load_assist_payload,
                    headers=headers,
                )
                resp.raise_for_status()
                load_data = resp.json()
                
                # Determine tier
                tier = None
                if load_data.get("currentTier"):
                    tier = load_data["currentTier"]
                else:
                    for allowed_tier in load_data.get("allowedTiers", []):
                        if allowed_tier.get("isDefault"):
                            tier = allowed_tier
                            break
                    
                    if not tier:
                        tier = {
                            "name": "",
                            "description": "",
                            "id": "legacy-tier",
                            "userDefinedCloudaicompanionProject": True,
                        }

                if tier.get("userDefinedCloudaicompanionProject") and not project_id:
                    raise ValueError("This account requires setting the GOOGLE_CLOUD_PROJECT env var.")

                if load_data.get("currentTier"):
                    self._onboarding_complete = True
                    return

                # Onboard user
                onboard_req_payload = {
                    "tierId": tier.get("id"),
                    "cloudaicompanionProject": project_id,
                    "metadata": get_client_metadata(project_id),
                }

                while True:
                    onboard_resp = await self._http_client.post(
                        f"{CODE_ASSIST_ENDPOINT}/v1internal:onboardUser",
                        json=onboard_req_payload,
                        headers=headers,
                    )
                    onboard_resp.raise_for_status()
                    lro_data = onboard_resp.json()

                    if lro_data.get("done"):
                        self._onboarding_complete = True
                        break
                    
                    await asyncio.sleep(5)

            except httpx.HTTPStatusError as e:
                error_text = e.response.text if hasattr(e, 'response') else str(e)
                raise Exception(f"User onboarding failed. Please check your Google Cloud project permissions and try again. Error: {error_text}")
            except Exception as e:
                raise Exception(f"User onboarding failed due to an unexpected error: {str(e)}")

    async def get_credentials_and_project(self) -> Tuple[Optional[Credentials], Optional[str]]:
        """Get both credentials and project ID in one optimized call."""
        if not self._initialized:
            await self.initialize()
        
        # 如果当前没有文件，强制检查一次
        if not self._credential_files:
            log.info("No credentials found, forcing file discovery")
            async with self._write_context():
                await self._discover_credential_files()
        
        return await self.get_credentials()
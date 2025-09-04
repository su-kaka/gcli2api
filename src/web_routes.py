"""
Web路由模块 - 处理认证相关的HTTP请求和控制面板功能
用于与上级web.py集成
"""
import asyncio
import datetime
import glob
import io
import json
import os
import time
import zipfile
from collections import deque
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, File, UploadFile, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from starlette.websockets import WebSocketState
import toml
import zipfile

import config
from log import log
from .auth import (
    create_auth_url, get_auth_status,
    verify_password, generate_auth_token, verify_auth_token,
    batch_upload_credentials, asyncio_complete_auth_flow, 
    load_credentials_from_env, clear_env_credentials
)
from .credential_manager import CredentialManager
from .usage_stats import get_usage_stats, get_aggregated_stats, get_usage_stats_instance

# 创建路由器
router = APIRouter()
security = HTTPBearer()

# 创建credential manager实例
credential_manager = CredentialManager()

# WebSocket连接管理

class ConnectionManager:
    def __init__(self, max_connections: int = 3):  # 进一步降低最大连接数
        # 使用双端队列严格限制内存使用
        self.active_connections: deque = deque(maxlen=max_connections)
        self.max_connections = max_connections
        self._last_cleanup = 0
        self._cleanup_interval = 120  # 120秒清理一次死连接

    async def connect(self, websocket: WebSocket):
        # 自动清理死连接
        self._auto_cleanup()
        
        # 限制最大连接数，防止内存无限增长
        if len(self.active_connections) >= self.max_connections:
            await websocket.close(code=1008, reason="Too many connections")
            return False
        
        await websocket.accept()
        self.active_connections.append(websocket)
        log.debug(f"WebSocket连接建立，当前连接数: {len(self.active_connections)}")
        return True

    def disconnect(self, websocket: WebSocket):
        # 使用更高效的方式移除连接
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass  # 连接已不存在
        log.debug(f"WebSocket连接断开，当前连接数: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, message: str):
        # 使用更高效的方式处理广播，避免索引操作
        dead_connections = []
        for conn in self.active_connections:
            try:
                await conn.send_text(message)
            except Exception:
                dead_connections.append(conn)
        
        # 批量移除死连接
        for dead_conn in dead_connections:
            self.disconnect(dead_conn)
                
    def _auto_cleanup(self):
        """自动清理死连接"""
        current_time = time.time()
        if current_time - self._last_cleanup > self._cleanup_interval:
            self.cleanup_dead_connections()
            self._last_cleanup = current_time
    
    def cleanup_dead_connections(self):
        """清理已断开的连接"""
        original_count = len(self.active_connections)
        # 使用列表推导式过滤活跃连接，更高效
        alive_connections = deque([
            conn for conn in self.active_connections 
            if hasattr(conn, 'client_state') and conn.client_state != WebSocketState.DISCONNECTED
        ], maxlen=self.max_connections)
        
        self.active_connections = alive_connections
        cleaned = original_count - len(self.active_connections)
        if cleaned > 0:
            log.debug(f"清理了 {cleaned} 个死连接，剩余连接数: {len(self.active_connections)}")

manager = ConnectionManager()


async def ensure_credential_manager_initialized():
    """确保credential manager已初始化"""
    if not credential_manager._initialized:
        await credential_manager.initialize()

async def get_credential_manager():
    """获取全局凭证管理器实例"""
    global credential_manager
    if not credential_manager:
        credential_manager = CredentialManager()
        await credential_manager.initialize()
    return credential_manager

def authenticate(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """验证用户密码（控制面板使用）"""
    from config import get_panel_password
    password = get_panel_password()
    token = credentials.credentials
    if token != password:
        raise HTTPException(status_code=403, detail="密码错误")
    return token

class LoginRequest(BaseModel):
    password: str

class AuthStartRequest(BaseModel):
    project_id: Optional[str] = None  # 现在是可选的

class AuthCallbackRequest(BaseModel):
    project_id: Optional[str] = None  # 现在是可选的

class CredFileActionRequest(BaseModel):
    filename: str
    action: str  # enable, disable, delete

class CredFileBatchActionRequest(BaseModel):
    action: str  # "enable", "disable", "delete"
    filenames: List[str]  # 批量操作的文件名列表

class ConfigSaveRequest(BaseModel):
    config: dict



def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """验证认证令牌"""
    if not verify_auth_token(credentials.credentials):
        raise HTTPException(status_code=401, detail="无效的认证令牌")
    return credentials.credentials

def is_mobile_user_agent(user_agent: str) -> bool:
    """检测是否为移动设备用户代理"""
    if not user_agent:
        return False
    
    user_agent_lower = user_agent.lower()
    mobile_keywords = [
        'mobile', 'android', 'iphone', 'ipad', 'ipod', 
        'blackberry', 'windows phone', 'samsung', 'htc',
        'motorola', 'nokia', 'palm', 'webos', 'opera mini',
        'opera mobi', 'fennec', 'minimo', 'symbian', 'psp',
        'nintendo', 'tablet'
    ]
    
    return any(keyword in user_agent_lower for keyword in mobile_keywords)

@router.get("/", response_class=HTMLResponse)
@router.get("/v1", response_class=HTMLResponse)
@router.get("/auth", response_class=HTMLResponse)
async def serve_control_panel(request: Request):
    """提供统一控制面板（包含认证、文件管理、配置等功能）"""
    try:
        # 获取用户代理并判断是否为移动设备
        user_agent = request.headers.get("user-agent", "")
        is_mobile = is_mobile_user_agent(user_agent)
        
        # 根据设备类型选择相应的HTML文件
        if is_mobile:
            html_file_path = "front/control_panel_mobile.html"
            log.info(f"Serving mobile control panel to user-agent: {user_agent}")
        else:
            html_file_path = "front/control_panel.html"
            log.info(f"Serving desktop control panel to user-agent: {user_agent}")
        
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        log.error(f"控制面板页面文件不存在: {html_file_path}")
        # 如果移动端文件不存在，回退到桌面版
        if is_mobile:
            try:
                with open("front/control_panel.html", "r", encoding="utf-8") as f:
                    html_content = f.read()
                return HTMLResponse(content=html_content)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="控制面板页面不存在")
        else:
            raise HTTPException(status_code=404, detail="控制面板页面不存在")
    except Exception as e:
        log.error(f"加载控制面板页面失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.post("/auth/login")
async def login(request: LoginRequest):
    """用户登录"""
    try:
        if verify_password(request.password):
            token = generate_auth_token()
            return JSONResponse(content={"token": token, "message": "登录成功"})
        else:
            raise HTTPException(status_code=401, detail="密码错误")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"登录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/start")
async def start_auth(request: AuthStartRequest, token: str = Depends(verify_token)):
    """开始认证流程，支持自动检测项目ID"""
    try:
        # 如果没有提供项目ID，尝试自动检测
        project_id = request.project_id
        if not project_id:
            log.info("用户未提供项目ID，后续将使用自动检测...")
        
        # 使用认证令牌作为用户会话标识
        user_session = token if token else None
        result = create_auth_url(project_id, user_session)
        
        if result['success']:
            return JSONResponse(content={
                "auth_url": result['auth_url'],
                "state": result['state'],
                "auto_project_detection": result.get('auto_project_detection', False),
                "detected_project_id": result.get('detected_project_id')
            })
        else:
            raise HTTPException(status_code=500, detail=result['error'])
            
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"开始认证流程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/callback")
async def auth_callback(request: AuthCallbackRequest, token: str = Depends(verify_token)):
    """处理认证回调，支持自动检测项目ID"""
    try:
        # 项目ID现在是可选的，在回调处理中进行自动检测
        project_id = request.project_id
        
        # 使用认证令牌作为用户会话标识
        user_session = token if token else None
        # 异步等待OAuth回调完成
        result = await asyncio_complete_auth_flow(project_id, user_session)
        
        if result['success']:
            return JSONResponse(content={
                "credentials": result['credentials'],
                "file_path": result['file_path'],
                "message": "认证成功，凭证已保存",
                "auto_detected_project": result.get('auto_detected_project', False)
            })
        else:
            # 如果需要手动项目ID或项目选择，在响应中标明
            if result.get('requires_manual_project_id'):
                # 使用JSON响应
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": result['error'],
                        "requires_manual_project_id": True
                    }
                )
            elif result.get('requires_project_selection'):
                # 返回项目列表供用户选择
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": result['error'],
                        "requires_project_selection": True,
                        "available_projects": result['available_projects']
                    }
                )
            else:
                raise HTTPException(status_code=400, detail=result['error'])
            
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"处理认证回调失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/status/{project_id}")
async def check_auth_status(project_id: str, token: str = Depends(verify_token)):
    """检查认证状态"""
    try:
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID 不能为空")
        
        status = get_auth_status(project_id)
        return JSONResponse(content=status)
        
    except Exception as e:
        log.error(f"检查认证状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def extract_json_files_from_zip(zip_file: UploadFile) -> List[dict]:
    """从ZIP文件中提取JSON文件"""
    try:
        # 读取ZIP文件内容
        zip_content = await zip_file.read()
        
        # 不限制ZIP文件大小，只在处理时控制文件数量
        
        files_data = []
        
        with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zip_ref:
            # 获取ZIP中的所有文件
            file_list = zip_ref.namelist()
            json_files = [f for f in file_list if f.endswith('.json') and not f.startswith('__MACOSX/')]
            
            if not json_files:
                raise HTTPException(status_code=400, detail="ZIP文件中没有找到JSON文件")

            log.info(f"从ZIP文件 {zip_file.filename} 中找到 {len(json_files)} 个JSON文件")
            
            for json_filename in json_files:
                try:
                    # 读取JSON文件内容
                    with zip_ref.open(json_filename) as json_file:
                        content = json_file.read()
                        
                        try:
                            content_str = content.decode('utf-8')
                        except UnicodeDecodeError:
                            log.warning(f"跳过编码错误的文件: {json_filename}")
                            continue
                        
                        # 使用原始文件名（去掉路径）
                        filename = os.path.basename(json_filename)
                        files_data.append({
                            'filename': filename,
                            'content': content_str
                        })
                        
                except Exception as e:
                    log.warning(f"处理ZIP中的文件 {json_filename} 时出错: {e}")
                    continue
        
        log.info(f"成功从ZIP文件中提取 {len(files_data)} 个有效的JSON文件")
        return files_data
        
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="无效的ZIP文件格式")
    except Exception as e:
        log.error(f"处理ZIP文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"处理ZIP文件失败: {str(e)}")


@router.post("/auth/upload")
async def upload_credentials(files: List[UploadFile] = File(...), token: str = Depends(verify_token)):
    """批量上传认证文件"""
    try:
        if not files:
            raise HTTPException(status_code=400, detail="请选择要上传的文件")
        
        # 检查文件数量限制
        if len(files) > 100:
            raise HTTPException(status_code=400, detail=f"文件数量过多，最多支持100个文件，当前：{len(files)}个")
        
        files_data = []
        for file in files:
            # 检查文件类型：支持JSON和ZIP
            if file.filename.endswith('.zip'):
                # 处理ZIP文件
                zip_files_data = await extract_json_files_from_zip(file)
                files_data.extend(zip_files_data)
                log.info(f"从ZIP文件 {file.filename} 中提取了 {len(zip_files_data)} 个JSON文件")
                
            elif file.filename.endswith('.json'):
                # 处理单个JSON文件
                # 流式读取文件内容
                content_chunks = []
                while True:
                    chunk = await file.read(8192)  # 8KB chunks
                    if not chunk:
                        break
                    content_chunks.append(chunk)
                
                content = b''.join(content_chunks)
                try:
                    content_str = content.decode('utf-8')
                except UnicodeDecodeError:
                    raise HTTPException(status_code=400, detail=f"文件 {file.filename} 编码格式不支持")
                
                files_data.append({
                    'filename': file.filename,
                    'content': content_str
                })
            else:
                raise HTTPException(status_code=400, detail=f"文件 {file.filename} 格式不支持，只支持JSON和ZIP文件")
        
        # 分批处理大量文件以提高稳定性
        batch_size = 50  # 每批处理50个文件
        all_results = []
        total_success = 0
        
        for i in range(0, len(files_data), batch_size):
            batch_files = files_data[i:i + batch_size]
            batch_result = batch_upload_credentials(batch_files)
            
            all_results.extend(batch_result['results'])
            total_success += batch_result['uploaded_count']
            
            # 记录批次进度
            batch_num = (i // batch_size) + 1
            total_batches = (len(files_data) + batch_size - 1) // batch_size
            log.info(f"批次 {batch_num}/{total_batches} 完成: 成功 {batch_result['uploaded_count']}/{len(batch_files)} 个文件")
        
        if total_success > 0:
            return JSONResponse(content={
                "uploaded_count": total_success,
                "total_count": len(files_data),
                "results": all_results,
                "message": f"批量上传完成: 成功 {total_success}/{len(files_data)} 个文件"
            })
        else:
            raise HTTPException(status_code=400, detail="没有文件上传成功")
            
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"批量上传失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/status")
async def get_creds_status(token: str = Depends(verify_token)):
    """获取所有凭证文件的状态"""
    try:
        await ensure_credential_manager_initialized()
        
        # 强制从文件重新加载最新状态
        await credential_manager._load_state()
        
        # 获取状态时不要调用_discover_credential_files，因为它会过滤被禁用的文件
        # 直接获取所有文件的状态
        status = await credential_manager.get_creds_status()
        
        # 读取文件内容
        creds_info = {}
        for filename, file_status in status.items():
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    content = json.loads(f.read())
                
                creds_info[filename] = {
                    "status": file_status,
                    "content": content,
                    "filename": os.path.basename(filename),
                    "size": os.path.getsize(filename),
                    "modified_time": os.path.getmtime(filename),
                    "user_email": file_status.get("user_email")
                }
            except Exception as e:
                log.error(f"读取凭证文件失败 {filename}: {e}")
                creds_info[filename] = {
                    "status": file_status,
                    "content": None,
                    "filename": os.path.basename(filename),
                    "error": str(e)
                }
        
        return JSONResponse(content={"creds": creds_info})
        
    except Exception as e:
        log.error(f"获取凭证状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/action")
async def creds_action(request: CredFileActionRequest, token: str = Depends(verify_token)):
    """对凭证文件执行操作（启用/禁用/删除）"""
    try:
        await ensure_credential_manager_initialized()
        
        log.info(f"Received request: {request}")
        
        filename = request.filename
        action = request.action
        
        log.info(f"Performing action '{action}' on file: {filename}")
        
        # 统一使用标准路径格式：CREDENTIALS_DIR + filename
        from .credential_manager import _normalize_filename_only, _make_standard_path
        
        filename_only = _normalize_filename_only(filename)
        if not filename_only.endswith('.json'):
            log.error(f"Invalid filename: {filename_only} (not a .json file)")
            raise HTTPException(status_code=400, detail=f"无效的文件名: {filename_only}")
        
        # 使用标准路径格式
        filename = _make_standard_path(filename)
        log.info(f"Using standard path: {filename}")
        
        if not os.path.exists(filename):
            log.error(f"File not found: {filename}")
            raise HTTPException(status_code=404, detail="文件不存在")
        
        if action == "enable":
            log.info(f"Web request: ENABLING file {filename}")
            await credential_manager.set_cred_disabled(filename, False)
            log.info(f"Web request: ENABLED file {filename} successfully")
            return JSONResponse(content={"message": f"已启用凭证文件 {os.path.basename(filename)}"})
        
        elif action == "disable":
            log.info(f"Web request: DISABLING file {filename}")
            await credential_manager.set_cred_disabled(filename, True)
            log.info(f"Web request: DISABLED file {filename} successfully")
            return JSONResponse(content={"message": f"已禁用凭证文件 {os.path.basename(filename)}"})
        
        elif action == "delete":
            try:
                os.remove(filename)
                # 状态会在下次文件发现时自动清理，无需手动删除
                
                return JSONResponse(content={"message": f"已删除凭证文件 {os.path.basename(filename)}"})
            except OSError as e:
                raise HTTPException(status_code=500, detail=f"删除文件失败: {str(e)}")
        
        else:
            raise HTTPException(status_code=400, detail="无效的操作类型")
            
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"凭证文件操作失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/batch-action")
async def creds_batch_action(request: CredFileBatchActionRequest, token: str = Depends(verify_token)):
    """批量对凭证文件执行操作（启用/禁用/删除）"""
    try:
        await ensure_credential_manager_initialized()
        
        action = request.action
        filenames = request.filenames
        
        if not filenames:
            raise HTTPException(status_code=400, detail="文件名列表不能为空")
        
        log.info(f"Performing batch action '{action}' on {len(filenames)} files")
        
        success_count = 0
        errors = []
        
        for filename in filenames:
            try:
                # 验证文件路径安全性
                if not filename.endswith('.json'):
                    errors.append(f"{filename}: 无效的文件类型")
                    continue
                
                # 使用标准路径格式
                from .credential_manager import _make_standard_path
                filepath = _make_standard_path(filename)
                
                if not os.path.exists(filepath):
                    errors.append(f"{filename}: 文件不存在")
                    continue
                
                # 执行相应操作
                if action == "enable":
                    await credential_manager.set_cred_disabled(filepath, False)
                    success_count += 1
                    
                elif action == "disable":
                    await credential_manager.set_cred_disabled(filepath, True)
                    success_count += 1
                    
                elif action == "delete":
                    try:
                        os.remove(filepath)
                        # 状态会在下次文件发现时自动清理，无需手动删除
                        
                        success_count += 1
                    except OSError as e:
                        errors.append(f"{filename}: 删除文件失败 - {str(e)}")
                        continue
                else:
                    errors.append(f"{filename}: 无效的操作类型")
                    continue
                    
            except Exception as e:
                log.error(f"Processing {filename} failed: {e}")
                errors.append(f"{filename}: 处理失败 - {str(e)}")
                continue
        
        # 构建返回消息
        result_message = f"批量操作完成：成功处理 {success_count}/{len(filenames)} 个文件"
        if errors:
            result_message += f"\n错误详情：\n" + "\n".join(errors)
            
        response_data = {
            "success_count": success_count,
            "total_count": len(filenames),
            "errors": errors,
            "message": result_message
        }
        
        return JSONResponse(content=response_data)
            
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"批量凭证文件操作失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/download/{filename}")
async def download_cred_file(filename: str, token: str = Depends(verify_token)):
    """下载单个凭证文件"""
    try:
        # 构建完整路径
        from config import CREDENTIALS_DIR
        filepath = os.path.join(CREDENTIALS_DIR, filename)
        
        # 验证文件路径安全性
        if not filepath.endswith('.json') or not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")
        
        # 读取文件内容
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        from fastapi.responses import Response
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"下载凭证文件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/fetch-email/{filename}")
async def fetch_user_email(filename: str, token: str = Depends(verify_token)):
    """获取指定凭证文件的用户邮箱地址"""
    try:
        await ensure_credential_manager_initialized()
        
        # 使用标准路径格式
        from .credential_manager import _normalize_filename_only, _make_standard_path
        
        filename_only = _normalize_filename_only(filename)
        if not filename_only.endswith('.json'):
            raise HTTPException(status_code=404, detail="无效的文件名")
        
        filepath = _make_standard_path(filename)
        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")
        
        # 获取用户邮箱
        email = await credential_manager.get_or_fetch_user_email(filepath)
        
        if email:
            return JSONResponse(content={
                "filename": os.path.basename(filepath),
                "user_email": email,
                "message": "成功获取用户邮箱"
            })
        else:
            return JSONResponse(content={
                "filename": os.path.basename(filepath),
                "user_email": None,
                "message": "无法获取用户邮箱，可能凭证已过期或权限不足"
            }, status_code=400)
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取用户邮箱失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/creds/refresh-all-emails")
async def refresh_all_user_emails(token: str = Depends(verify_token)):
    """刷新所有凭证文件的用户邮箱地址"""
    try:
        await ensure_credential_manager_initialized()
        
        # 获取所有凭证文件
        from config import CREDENTIALS_DIR
        
        json_files = glob.glob(os.path.join(CREDENTIALS_DIR, "*.json"))
        
        results = []
        success_count = 0
        
        for filepath in json_files:
            try:
                email = await credential_manager.get_or_fetch_user_email(filepath)
                if email:
                    success_count += 1
                    results.append({
                        "filename": os.path.basename(filepath),
                        "user_email": email,
                        "success": True
                    })
                else:
                    results.append({
                        "filename": os.path.basename(filepath),
                        "user_email": None,
                        "success": False,
                        "error": "无法获取邮箱"
                    })
            except Exception as e:
                results.append({
                    "filename": os.path.basename(filepath),
                    "user_email": None,
                    "success": False,
                    "error": str(e)
                })
        
        return JSONResponse(content={
            "success_count": success_count,
            "total_count": len(json_files),
            "results": results,
            "message": f"成功获取 {success_count}/{len(json_files)} 个邮箱地址"
        })
        
    except Exception as e:
        log.error(f"批量获取用户邮箱失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/creds/download-all")
async def download_all_creds(token: str = Depends(verify_token)):
    """打包下载所有凭证文件"""
    try:
        from config import CREDENTIALS_DIR
        
        # 创建内存中的ZIP文件
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 遍历所有JSON文件
            for filename in os.listdir(CREDENTIALS_DIR):
                if filename.endswith('.json'):
                    filepath = os.path.join(CREDENTIALS_DIR, filename)
                    if os.path.isfile(filepath):
                        zip_file.write(filepath, filename)
        
        zip_buffer.seek(0)
        return Response(
            content=zip_buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=credentials.zip"}
        )
        
    except Exception as e:
        log.error(f"打包下载失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/get")
async def get_config(token: str = Depends(verify_token)):
    """获取当前配置"""
    try:
        await ensure_credential_manager_initialized()
        
        # 导入配置相关模块
        
        # 读取当前配置（包括环境变量和TOML文件中的配置）
        current_config = {}
        env_locked = []
        
        # 基础配置
        current_config["code_assist_endpoint"] = config.get_code_assist_endpoint()
        current_config["credentials_dir"] = config.get_credentials_dir()
        current_config["proxy"] = config.get_proxy_config() or ""
        
        # 代理端点配置
        current_config["oauth_proxy_url"] = config.get_oauth_proxy_url()
        current_config["googleapis_proxy_url"] = config.get_googleapis_proxy_url()
        current_config["resource_manager_api_url"] = config.get_resource_manager_api_url()
        current_config["service_usage_api_url"] = config.get_service_usage_api_url()
        
        # 检查环境变量锁定状态
        if os.getenv("CODE_ASSIST_ENDPOINT"):
            env_locked.append("code_assist_endpoint")
        if os.getenv("CREDENTIALS_DIR"):
            env_locked.append("credentials_dir")
        if os.getenv("PROXY"):
            env_locked.append("proxy")
        if os.getenv("OAUTH_PROXY_URL"):
            env_locked.append("oauth_proxy_url")
        if os.getenv("GOOGLEAPIS_PROXY_URL"):
            env_locked.append("googleapis_proxy_url")
        if os.getenv("RESOURCE_MANAGER_API_URL"):
            env_locked.append("resource_manager_api_url")
        if os.getenv("SERVICE_USAGE_API_URL"):
            env_locked.append("service_usage_api_url")
        
        # 自动封禁配置
        current_config["auto_ban_enabled"] = config.get_auto_ban_enabled()
        current_config["auto_ban_error_codes"] = config.get_auto_ban_error_codes()
        
        # 检查环境变量锁定状态
        if os.getenv("AUTO_BAN"):
            env_locked.append("auto_ban_enabled")
        
        # 尝试从config.toml文件读取额外配置
        try:
            config_file = os.path.join(config.CREDENTIALS_DIR, "config.toml")
            if os.path.exists(config_file):
                with open(config_file, "r", encoding="utf-8") as f:
                    toml_data = toml.load(f)
                
                # 合并TOML配置（不覆盖环境变量）
                for key, value in toml_data.items():
                    if key not in env_locked:
                        current_config[key] = value
        except Exception as e:
            log.warning(f"读取TOML配置失败: {e}")
        
        # 性能配置
        current_config["calls_per_rotation"] = config.get_calls_per_rotation()
        
        # 429重试配置
        current_config["retry_429_max_retries"] = config.get_retry_429_max_retries()
        current_config["retry_429_enabled"] = config.get_retry_429_enabled()
        current_config["retry_429_interval"] = config.get_retry_429_interval()
        
        # 日志配置
        current_config["log_level"] = config.get_log_level()
        current_config["log_file"] = config.get_log_file()
        
        # 抗截断配置
        current_config["anti_truncation_max_attempts"] = config.get_anti_truncation_max_attempts()
        
        # 兼容性配置
        current_config["compatibility_mode_enabled"] = config.get_compatibility_mode_enabled()
        
        # 服务器配置
        current_config["host"] = config.get_server_host()
        current_config["port"] = config.get_server_port()
        current_config["api_password"] = config.get_api_password()
        current_config["panel_password"] = config.get_panel_password()
        current_config["password"] = config.get_server_password()
        
        # 检查其他环境变量锁定状态
        if os.getenv("RETRY_429_MAX_RETRIES"):
            env_locked.append("retry_429_max_retries")
        if os.getenv("RETRY_429_ENABLED"):
            env_locked.append("retry_429_enabled")
        if os.getenv("RETRY_429_INTERVAL"):
            env_locked.append("retry_429_interval")
        if os.getenv("LOG_LEVEL"):
            env_locked.append("log_level")
        if os.getenv("LOG_FILE"):
            env_locked.append("log_file")
        if os.getenv("ANTI_TRUNCATION_MAX_ATTEMPTS"):
            env_locked.append("anti_truncation_max_attempts")
        if os.getenv("COMPATIBILITY_MODE"):
            env_locked.append("compatibility_mode_enabled")
        if os.getenv("HOST"):
            env_locked.append("host")
        if os.getenv("PORT"):
            env_locked.append("port")
        if os.getenv("API_PASSWORD"):
            env_locked.append("api_password")
        if os.getenv("PANEL_PASSWORD"):
            env_locked.append("panel_password")
        if os.getenv("PASSWORD"):
            env_locked.append("password")
        
        return JSONResponse(content={
            "config": current_config,
            "env_locked": env_locked
        })
        
    except Exception as e:
        log.error(f"获取配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/save")
async def save_config(request: ConfigSaveRequest, token: str = Depends(verify_token)):
    """保存配置到TOML文件"""
    try:
        await ensure_credential_manager_initialized()
        new_config = request.config
        
        log.info(f"收到的配置数据: {list(new_config.keys())}")
        log.info(f"收到的password值: {new_config.get('password', 'NOT_FOUND')}")
        
        # 验证配置项
        if "calls_per_rotation" in new_config:
            if not isinstance(new_config["calls_per_rotation"], int) or new_config["calls_per_rotation"] < 1:
                raise HTTPException(status_code=400, detail="凭证轮换调用次数必须是大于0的整数")
        
        
        if "retry_429_max_retries" in new_config:
            if not isinstance(new_config["retry_429_max_retries"], int) or new_config["retry_429_max_retries"] < 0:
                raise HTTPException(status_code=400, detail="最大429重试次数必须是大于等于0的整数")
        
        if "retry_429_enabled" in new_config:
            if not isinstance(new_config["retry_429_enabled"], bool):
                raise HTTPException(status_code=400, detail="429重试开关必须是布尔值")
        
        # 验证新的配置项
        if "retry_429_interval" in new_config:
            try:
                interval = float(new_config["retry_429_interval"])
                if interval < 0.01 or interval > 10:
                    raise HTTPException(status_code=400, detail="429重试间隔必须在0.01-10秒之间")
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="429重试间隔必须是有效的数字")
        
        if "log_level" in new_config:
            valid_levels = ["debug", "info", "warning", "error", "critical"]
            if new_config["log_level"].lower() not in valid_levels:
                raise HTTPException(status_code=400, detail=f"日志级别必须是以下之一: {', '.join(valid_levels)}")
        
        if "anti_truncation_max_attempts" in new_config:
            if not isinstance(new_config["anti_truncation_max_attempts"], int) or new_config["anti_truncation_max_attempts"] < 1 or new_config["anti_truncation_max_attempts"] > 10:
                raise HTTPException(status_code=400, detail="抗截断最大重试次数必须是1-10之间的整数")
        
        if "compatibility_mode_enabled" in new_config:
            if not isinstance(new_config["compatibility_mode_enabled"], bool):
                raise HTTPException(status_code=400, detail="兼容性模式开关必须是布尔值")
        
        # 验证服务器配置
        if "host" in new_config:
            if not isinstance(new_config["host"], str) or not new_config["host"].strip():
                raise HTTPException(status_code=400, detail="服务器主机地址不能为空")
        
        if "port" in new_config:
            if not isinstance(new_config["port"], int) or new_config["port"] < 1 or new_config["port"] > 65535:
                raise HTTPException(status_code=400, detail="端口号必须是1-65535之间的整数")
        
        if "api_password" in new_config:
            if not isinstance(new_config["api_password"], str):
                raise HTTPException(status_code=400, detail="API访问密码必须是字符串")
        
        if "panel_password" in new_config:
            if not isinstance(new_config["panel_password"], str):
                raise HTTPException(status_code=400, detail="控制面板密码必须是字符串")
        
        if "password" in new_config:
            if not isinstance(new_config["password"], str):
                raise HTTPException(status_code=400, detail="访问密码必须是字符串")
        
        # 读取现有的配置文件
        config_file = os.path.join(config.CREDENTIALS_DIR, "config.toml")
        existing_config = {}
        
        try:
            if os.path.exists(config_file):
                with open(config_file, "r", encoding="utf-8") as f:
                    existing_config = toml.load(f)
        except Exception as e:
            log.warning(f"读取现有配置文件失败: {e}")
        
        # 只更新不被环境变量锁定的配置项
        env_locked_keys = set()
        if os.getenv("CODE_ASSIST_ENDPOINT"):
            env_locked_keys.add("code_assist_endpoint")
        if os.getenv("CREDENTIALS_DIR"):
            env_locked_keys.add("credentials_dir")
        if os.getenv("PROXY"):
            env_locked_keys.add("proxy")
        if os.getenv("OAUTH_PROXY_URL"):
            env_locked_keys.add("oauth_proxy_url")
        if os.getenv("GOOGLEAPIS_PROXY_URL"):
            env_locked_keys.add("googleapis_proxy_url")
        if os.getenv("AUTO_BAN"):
            env_locked_keys.add("auto_ban_enabled")
        if os.getenv("RETRY_429_MAX_RETRIES"):
            env_locked_keys.add("retry_429_max_retries")
        if os.getenv("RETRY_429_ENABLED"):
            env_locked_keys.add("retry_429_enabled")
        if os.getenv("RETRY_429_INTERVAL"):
            env_locked_keys.add("retry_429_interval")
        if os.getenv("LOG_LEVEL"):
            env_locked_keys.add("log_level")
        if os.getenv("LOG_FILE"):
            env_locked_keys.add("log_file")
        if os.getenv("ANTI_TRUNCATION_MAX_ATTEMPTS"):
            env_locked_keys.add("anti_truncation_max_attempts")
        if os.getenv("COMPATIBILITY_MODE"):
            env_locked_keys.add("compatibility_mode_enabled")
        if os.getenv("HOST"):
            env_locked_keys.add("host")
        if os.getenv("PORT"):
            env_locked_keys.add("port")
        if os.getenv("API_PASSWORD"):
            env_locked_keys.add("api_password")
        if os.getenv("PANEL_PASSWORD"):
            env_locked_keys.add("panel_password")
        if os.getenv("PASSWORD"):
            env_locked_keys.add("password")
        
        for key, value in new_config.items():
            if key not in env_locked_keys:
                existing_config[key] = value
                if key == 'password':
                    log.info(f"设置password字段为: {value}")
                elif key == 'api_password':
                    log.info(f"设置api_password字段为: {value}")
                elif key == 'panel_password':
                    log.info(f"设置panel_password字段为: {value}")
        
        log.info(f"最终保存的existing_config中password = {existing_config.get('password', 'NOT_FOUND')}")
        
        # 使用config模块的保存函数
        config.save_config_to_toml(existing_config)
        
        # 验证保存后的结果
        test_api_password = config.get_api_password()
        test_panel_password = config.get_panel_password()
        test_password = config.get_server_password()
        log.info(f"保存后立即读取的API密码: {test_api_password}")
        log.info(f"保存后立即读取的面板密码: {test_panel_password}")
        log.info(f"保存后立即读取的通用密码: {test_password}")
        
        # 热更新配置到内存中的模块（如果可能）
        hot_updated = []  # 记录成功热更新的配置项
        restart_required = []  # 记录需要重启的配置项
        
        # 支持热更新的配置项：
        # - calls_per_rotation: 凭证轮换调用次数
        # - proxy: 网络配置
        # - log_level: 日志级别
        # - auto_ban_enabled, auto_ban_error_codes: 自动封禁配置
        # - retry_429_enabled, retry_429_max_retries, retry_429_interval: 429重试配置
        # - anti_truncation_max_attempts: 抗截断配置
        # - compatibility_mode_enabled: 兼容性模式
        # - api_password, panel_password, password: 访问密码
        #
        # 需要重启的配置项：
        # - host, port: 服务器地址和端口
        # - log_file: 日志文件路径
        
        try:
            # save_config_to_toml已经更新了缓存，不需要reload
            
            # 1. 更新credential_manager的配置
            if "calls_per_rotation" in new_config and "calls_per_rotation" not in env_locked_keys:
                credential_manager._calls_per_rotation = new_config["calls_per_rotation"]
                hot_updated.append("calls_per_rotation")

            # 2. 代理配置（部分热更新）
            if "proxy" in new_config and "proxy" not in env_locked_keys:
                hot_updated.append("proxy")
            
            # 代理端点配置（可热更新）
            proxy_endpoint_configs = ["oauth_proxy_url", "googleapis_proxy_url"]
            for config_key in proxy_endpoint_configs:
                if config_key in new_config and config_key not in env_locked_keys:
                    hot_updated.append(config_key)

            # 3. 日志配置（部分热更新）
            # 注意：日志级别可以热更新，但日志文件路径需要重启
            if "log_level" in new_config and "log_level" not in env_locked_keys:
                hot_updated.append("log_level")
            
            if "log_file" in new_config and "log_file" not in env_locked_keys:
                restart_required.append("log_file")

            # 4. 其他可热更新的配置项
            hot_updatable_configs = [
                "auto_ban_enabled", "auto_ban_error_codes",
                "retry_429_enabled", "retry_429_max_retries", "retry_429_interval",
                "anti_truncation_max_attempts", "compatibility_mode_enabled"
            ]
            
            for config_key in hot_updatable_configs:
                if config_key in new_config and config_key not in env_locked_keys:
                    hot_updated.append(config_key)
            
            # 5. 需要重启的配置项
            restart_required_configs = ["host", "port"]
            for config_key in restart_required_configs:
                if config_key in new_config and config_key not in env_locked_keys:
                    restart_required.append(config_key)
            
            # 6. 密码配置（立即生效）
            password_configs = ["api_password", "panel_password", "password"]
            for config_key in password_configs:
                if config_key in new_config and config_key not in env_locked_keys:
                    hot_updated.append(config_key)
            
        except Exception as e:
            log.warning(f"热更新配置失败: {e}")
        
        # 构建响应消息
        response_data = {
            "message": "配置保存成功",
            "saved_config": {k: v for k, v in new_config.items() if k not in env_locked_keys}
        }
        
        # 添加热更新状态信息
        if hot_updated:
            response_data["hot_updated"] = hot_updated
        
        if restart_required:
            response_data["restart_required"] = restart_required
            response_data["restart_notice"] = f"以下配置项需要重启服务器才能生效: {', '.join(restart_required)}"
        
        return JSONResponse(content=response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"保存配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/load-env-creds")
async def load_env_credentials(token: str = Depends(verify_token)):
    """从环境变量加载凭证文件"""
    try:
        result = load_credentials_from_env()
        
        if result['loaded_count'] > 0:
            return JSONResponse(content={
                "loaded_count": result['loaded_count'],
                "total_count": result['total_count'],
                "results": result['results'],
                "message": result['message']
            })
        else:
            return JSONResponse(content={
                "loaded_count": 0,
                "total_count": result['total_count'],
                "message": result['message'],
                "results": result['results']
            })
            
    except Exception as e:
        log.error(f"从环境变量加载凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/auth/env-creds")
async def clear_env_creds(token: str = Depends(verify_token)):
    """清除所有从环境变量导入的凭证文件"""
    try:
        result = clear_env_credentials()
        
        if 'error' in result:
            raise HTTPException(status_code=500, detail=result['error'])
        
        return JSONResponse(content={
            "deleted_count": result['deleted_count'],
            "deleted_files": result.get('deleted_files', []),
            "message": result['message']
        })
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"清除环境变量凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/env-creds-status")
async def get_env_creds_status(token: str = Depends(verify_token)):
    """获取环境变量凭证状态"""
    try:
        # 检查有哪些环境变量可用
        available_env_vars = {key: "***已设置***" for key, value in os.environ.items() 
                              if key.startswith('GCLI_CREDS_') and value.strip()}
        
        # 检查自动加载设置
        auto_load_enabled = config.get_auto_load_env_creds()
        
        # 统计已存在的环境变量凭证文件
        from config import CREDENTIALS_DIR
        existing_env_files = []
        if os.path.exists(CREDENTIALS_DIR):
            for filename in os.listdir(CREDENTIALS_DIR):
                if filename.startswith('env-') and filename.endswith('.json'):
                    existing_env_files.append(filename)
        
        return JSONResponse(content={
            "available_env_vars": available_env_vars,
            "auto_load_enabled": auto_load_enabled,
            "existing_env_files_count": len(existing_env_files),
            "existing_env_files": existing_env_files
        })
        
    except Exception as e:
        log.error(f"获取环境变量凭证状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# 实时日志WebSocket (Real-time Logs WebSocket)
# =============================================================================

@router.post("/auth/logs/clear")
async def clear_logs(token: str = Depends(verify_token)):
    """清空日志文件"""
    try:
        log_file_path = config.get_log_file()
        
        # 检查日志文件是否存在
        if os.path.exists(log_file_path):
            try:
                # 清空文件内容（保留文件），确保以UTF-8编码写入
                with open(log_file_path, 'w', encoding='utf-8', newline='') as f:
                    f.write('')
                    f.flush()  # 强制刷新到磁盘
                log.info(f"日志文件已清空: {log_file_path}")
                
                # 通知所有WebSocket连接日志已清空
                await manager.broadcast("--- 日志文件已清空 ---")
                
                return JSONResponse(content={"message": f"日志文件已清空: {os.path.basename(log_file_path)}"})
            except Exception as e:
                log.error(f"清空日志文件失败: {e}")
                raise HTTPException(status_code=500, detail=f"清空日志文件失败: {str(e)}")
        else:
            return JSONResponse(content={"message": "日志文件不存在"})
            
    except Exception as e:
        log.error(f"清空日志文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空日志文件失败: {str(e)}")

@router.get("/auth/logs/download")
async def download_logs(token: str = Depends(verify_token)):
    """下载日志文件"""
    try:
        log_file_path = config.get_log_file()
        
        # 检查日志文件是否存在
        if not os.path.exists(log_file_path):
            raise HTTPException(status_code=404, detail="日志文件不存在")
        
        # 检查文件是否为空
        file_size = os.path.getsize(log_file_path)
        if file_size == 0:
            raise HTTPException(status_code=404, detail="日志文件为空")
        
        # 生成文件名（包含时间戳）
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gcli2api_logs_{timestamp}.txt"
        
        log.info(f"下载日志文件: {log_file_path}")
        
        return FileResponse(
            path=log_file_path,
            filename=filename,
            media_type='text/plain',
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
            
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"下载日志文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"下载日志文件失败: {str(e)}")

@router.websocket("/auth/logs/stream")
async def websocket_logs(websocket: WebSocket):
    """WebSocket端点，用于实时日志流"""
    # 检查连接数限制
    if not await manager.connect(websocket):
        return
    
    try:
        # 从配置获取日志文件路径
        log_file_path = config.get_log_file()
        
        # 发送初始日志（限制为最后50行，减少内存占用）
        if os.path.exists(log_file_path):
            try:
                with open(log_file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    # 只发送最后50行，减少初始内存消耗
                    for line in lines[-50:]:
                        if line.strip():
                            await websocket.send_text(line.strip())
            except Exception as e:
                await websocket.send_text(f"Error reading log file: {e}")
        
        # 监控日志文件变化
        last_size = os.path.getsize(log_file_path) if os.path.exists(log_file_path) else 0
        max_read_size = 8192  # 限制单次读取大小为8KB，防止大量日志造成内存激增
        check_interval = 2    # 增加检查间隔，减少CPU和I/O开销
        
        while websocket.client_state == WebSocketState.CONNECTED:
            await asyncio.sleep(check_interval)
            
            if os.path.exists(log_file_path):
                current_size = os.path.getsize(log_file_path)
                if current_size > last_size:
                    # 限制读取大小，防止单次读取过多内容
                    read_size = min(current_size - last_size, max_read_size)
                    
                    try:
                        with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(last_size)
                            new_content = f.read(read_size)
                            
                            # 处理编码错误的情况
                            if not new_content:
                                last_size = current_size
                                continue
                            
                            # 分行发送，避免发送不完整的行
                            lines = new_content.splitlines(keepends=True)
                            if lines:
                                # 如果最后一行没有换行符，保留到下次处理
                                if not lines[-1].endswith('\n') and len(lines) > 1:
                                    # 除了最后一行，其他都发送
                                    for line in lines[:-1]:
                                        if line.strip():
                                            await websocket.send_text(line.rstrip())
                                    # 更新位置，但要退回最后一行的字节数
                                    last_size += len(new_content.encode('utf-8')) - len(lines[-1].encode('utf-8'))
                                else:
                                    # 所有行都发送
                                    for line in lines:
                                        if line.strip():
                                            await websocket.send_text(line.rstrip())
                                    last_size += len(new_content.encode('utf-8'))
                    except UnicodeDecodeError as e:
                        # 遇到编码错误时，跳过这部分内容
                        log.warning(f"WebSocket日志读取编码错误: {e}, 跳过部分内容")
                        last_size = current_size
                    except Exception as e:
                        await websocket.send_text(f"Error reading new content: {e}")
                        # 发生其他错误时，重置文件位置
                        last_size = current_size
                        
                # 如果文件被截断（如清空日志），重置位置
                elif current_size < last_size:
                    last_size = 0
                    await websocket.send_text("--- 日志已清空 ---")
                    
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"WebSocket logs error: {e}")
    finally:
        manager.disconnect(websocket)


# =============================================================================
# Usage Statistics API (使用统计API)
# =============================================================================

@router.get("/usage/stats")
async def get_usage_statistics(filename: Optional[str] = None, token: str = Depends(verify_token)):
    """
    获取使用统计信息
    
    Args:
        filename: 可选，指定凭证文件名。如果不提供则返回所有文件的统计
    
    Returns:
        usage statistics for the specified file or all files
    """
    try:
        stats = await get_usage_stats(filename)
        return JSONResponse(content={
            "success": True,
            "data": stats
        })
    except Exception as e:
        log.error(f"获取使用统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/usage/aggregated")
async def get_aggregated_usage_statistics(token: str = Depends(verify_token)):
    """
    获取聚合使用统计信息
    
    Returns:
        Aggregated statistics across all credential files
    """
    try:
        stats = await get_aggregated_stats()
        return JSONResponse(content={
            "success": True,
            "data": stats
        })
    except Exception as e:
        log.error(f"获取聚合统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))



class UsageLimitsUpdateRequest(BaseModel):
    filename: str
    gemini_2_5_pro_limit: Optional[int] = None
    total_limit: Optional[int] = None


@router.post("/usage/update-limits")
async def update_usage_limits(request: UsageLimitsUpdateRequest, token: str = Depends(verify_token)):
    """
    更新指定凭证文件的每日使用限制
    
    Args:
        request: 包含文件名和新限制值的请求
    
    Returns:
        Success message
    """
    try:
        stats_instance = await get_usage_stats_instance()
        
        await stats_instance.update_daily_limits(
            filename=request.filename,
            gemini_2_5_pro_limit=request.gemini_2_5_pro_limit,
            total_limit=request.total_limit
        )
        
        return JSONResponse(content={
            "success": True,
            "message": f"已更新 {request.filename} 的使用限制"
        })
        
    except Exception as e:
        log.error(f"更新使用限制失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class UsageResetRequest(BaseModel):
    filename: Optional[str] = None


@router.post("/usage/reset")
async def reset_usage_statistics(request: UsageResetRequest, token: str = Depends(verify_token)):
    """
    重置使用统计
    
    Args:
        request: 包含可选文件名的请求。如果不提供文件名则重置所有统计
    
    Returns:
        Success message
    """
    try:
        stats_instance = await get_usage_stats_instance()
        
        await stats_instance.reset_stats(filename=request.filename)
        
        if request.filename:
            message = f"已重置 {request.filename} 的使用统计"
        else:
            message = "已重置所有文件的使用统计"
        
        return JSONResponse(content={
            "success": True,
            "message": message
        })
        
    except Exception as e:
        log.error(f"重置使用统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


"""
Web路由模块 - 处理认证相关的HTTP请求和控制面板功能
用于与上级web.py集成
"""

import asyncio
import datetime
import io
import json
import os
import time
import zipfile
from collections import deque
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.websockets import WebSocketState

import config
from log import log

from .auth import (
    asyncio_complete_auth_flow,
    complete_auth_flow_from_callback_url,
    create_auth_url,
    get_auth_status,
    verify_password,
)
from .credential_manager import CredentialManager
from .models import (
    LoginRequest,
    AuthStartRequest,
    AuthCallbackRequest,
    AuthCallbackUrlRequest,
    CredFileActionRequest,
    CredFileBatchActionRequest,
    ConfigSaveRequest,
)
from .storage_adapter import get_storage_adapter
from .utils import verify_panel_token, STANDARD_USER_AGENT, ANTIGRAVITY_USER_AGENT
from .antigravity_api import fetch_quota_info
from .google_oauth_api import Credentials, fetch_project_id
from config import get_code_assist_endpoint, get_antigravity_api_url

# 创建路由器
router = APIRouter()

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
        alive_connections = deque(
            [
                conn
                for conn in self.active_connections
                if hasattr(conn, "client_state")
                and conn.client_state != WebSocketState.DISCONNECTED
            ],
            maxlen=self.max_connections,
        )

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


def is_mobile_user_agent(user_agent: str) -> bool:
    """检测是否为移动设备用户代理"""
    if not user_agent:
        return False

    user_agent_lower = user_agent.lower()
    mobile_keywords = [
        "mobile",
        "android",
        "iphone",
        "ipad",
        "ipod",
        "blackberry",
        "windows phone",
        "samsung",
        "htc",
        "motorola",
        "nokia",
        "palm",
        "webos",
        "opera mini",
        "opera mobi",
        "fennec",
        "minimo",
        "symbian",
        "psp",
        "nintendo",
        "tablet",
    ]

    return any(keyword in user_agent_lower for keyword in mobile_keywords)


@router.get("/", response_class=HTMLResponse)
async def serve_control_panel(request: Request):
    """提供统一控制面板"""
    try:
        user_agent = request.headers.get("user-agent", "")
        is_mobile = is_mobile_user_agent(user_agent)

        if is_mobile:
            html_file_path = "front/control_panel_mobile.html"
        else:
            html_file_path = "front/control_panel.html"

        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)

    except Exception as e:
        log.error(f"加载控制面板页面失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


@router.post("/auth/login")
async def login(request: LoginRequest):
    """用户登录（简化版：直接返回密码作为token）"""
    try:
        if await verify_password(request.password):
            # 直接使用密码作为token，简化认证流程
            return JSONResponse(content={"token": request.password, "message": "登录成功"})
        else:
            raise HTTPException(status_code=401, detail="密码错误")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"登录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/start")
async def start_auth(request: AuthStartRequest, token: str = Depends(verify_panel_token)):
    """开始认证流程，支持自动检测项目ID"""
    try:
        # 如果没有提供项目ID，尝试自动检测
        project_id = request.project_id
        if not project_id:
            log.info("用户未提供项目ID，后续将使用自动检测...")

        # 使用认证令牌作为用户会话标识
        user_session = token if token else None
        result = await create_auth_url(
            project_id, user_session, use_antigravity=request.use_antigravity
        )

        if result["success"]:
            return JSONResponse(
                content={
                    "auth_url": result["auth_url"],
                    "state": result["state"],
                    "auto_project_detection": result.get("auto_project_detection", False),
                    "detected_project_id": result.get("detected_project_id"),
                }
            )
        else:
            raise HTTPException(status_code=500, detail=result["error"])

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"开始认证流程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/callback")
async def auth_callback(request: AuthCallbackRequest, token: str = Depends(verify_panel_token)):
    """处理认证回调，支持自动检测项目ID"""
    try:
        # 项目ID现在是可选的，在回调处理中进行自动检测
        project_id = request.project_id

        # 使用认证令牌作为用户会话标识
        user_session = token if token else None
        # 异步等待OAuth回调完成
        result = await asyncio_complete_auth_flow(
            project_id, user_session, use_antigravity=request.use_antigravity
        )

        if result["success"]:
            # 单项目认证成功
            return JSONResponse(
                content={
                    "credentials": result["credentials"],
                    "file_path": result["file_path"],
                    "message": "认证成功，凭证已保存",
                    "auto_detected_project": result.get("auto_detected_project", False),
                }
            )
        else:
            # 如果需要手动项目ID或项目选择，在响应中标明
            if result.get("requires_manual_project_id"):
                # 使用JSON响应
                return JSONResponse(
                    status_code=400,
                    content={"error": result["error"], "requires_manual_project_id": True},
                )
            elif result.get("requires_project_selection"):
                # 返回项目列表供用户选择
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": result["error"],
                        "requires_project_selection": True,
                        "available_projects": result["available_projects"],
                    },
                )
            else:
                raise HTTPException(status_code=400, detail=result["error"])

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"处理认证回调失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/callback-url")
async def auth_callback_url(request: AuthCallbackUrlRequest, token: str = Depends(verify_panel_token)):
    """从回调URL直接完成认证"""
    try:
        # 验证URL格式
        if not request.callback_url or not request.callback_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="请提供有效的回调URL")

        # 从回调URL完成认证
        result = await complete_auth_flow_from_callback_url(
            request.callback_url, request.project_id, use_antigravity=request.use_antigravity
        )

        if result["success"]:
            # 单项目认证成功
            return JSONResponse(
                content={
                    "credentials": result["credentials"],
                    "file_path": result["file_path"],
                    "message": "从回调URL认证成功，凭证已保存",
                    "auto_detected_project": result.get("auto_detected_project", False),
                }
            )
        else:
            # 处理各种错误情况
            if result.get("requires_manual_project_id"):
                return JSONResponse(
                    status_code=400,
                    content={"error": result["error"], "requires_manual_project_id": True},
                )
            elif result.get("requires_project_selection"):
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": result["error"],
                        "requires_project_selection": True,
                        "available_projects": result["available_projects"],
                    },
                )
            else:
                raise HTTPException(status_code=400, detail=result["error"])

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"从回调URL处理认证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/status/{project_id}")
async def check_auth_status(project_id: str, token: str = Depends(verify_panel_token)):
    """检查认证状态"""
    try:
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID 不能为空")

        status = get_auth_status(project_id)
        return JSONResponse(content=status)

    except Exception as e:
        log.error(f"检查认证状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# 工具函数 (Helper Functions)
# =============================================================================


def get_env_locked_keys() -> set:
    """获取被环境变量锁定的配置键集合"""
    env_locked_keys = set()
    env_mappings = {
        "CODE_ASSIST_ENDPOINT": "code_assist_endpoint",
        "CREDENTIALS_DIR": "credentials_dir",
        "PROXY": "proxy",
        "OAUTH_PROXY_URL": "oauth_proxy_url",
        "GOOGLEAPIS_PROXY_URL": "googleapis_proxy_url",
        "RESOURCE_MANAGER_API_URL": "resource_manager_api_url",
        "SERVICE_USAGE_API_URL": "service_usage_api_url",
        "AUTO_BAN": "auto_ban_enabled",
        "RETRY_429_MAX_RETRIES": "retry_429_max_retries",
        "RETRY_429_ENABLED": "retry_429_enabled",
        "RETRY_429_INTERVAL": "retry_429_interval",
        "ANTI_TRUNCATION_MAX_ATTEMPTS": "anti_truncation_max_attempts",
        "COMPATIBILITY_MODE": "compatibility_mode_enabled",
        "RETURN_THOUGHTS_TO_FRONTEND": "return_thoughts_to_frontend",
        "HOST": "host",
        "PORT": "port",
        "API_PASSWORD": "api_password",
        "PANEL_PASSWORD": "panel_password",
        "PASSWORD": "password",
    }

    for env_key, config_key in env_mappings.items():
        if os.getenv(env_key):
            env_locked_keys.add(config_key)

    return env_locked_keys


async def extract_json_files_from_zip(zip_file: UploadFile) -> List[dict]:
    """从ZIP文件中提取JSON文件"""
    try:
        # 读取ZIP文件内容
        zip_content = await zip_file.read()

        # 不限制ZIP文件大小，只在处理时控制文件数量

        files_data = []

        with zipfile.ZipFile(io.BytesIO(zip_content), "r") as zip_ref:
            # 获取ZIP中的所有文件
            file_list = zip_ref.namelist()
            json_files = [
                f for f in file_list if f.endswith(".json") and not f.startswith("__MACOSX/")
            ]

            if not json_files:
                raise HTTPException(status_code=400, detail="ZIP文件中没有找到JSON文件")

            log.info(f"从ZIP文件 {zip_file.filename} 中找到 {len(json_files)} 个JSON文件")

            for json_filename in json_files:
                try:
                    # 读取JSON文件内容
                    with zip_ref.open(json_filename) as json_file:
                        content = json_file.read()

                        try:
                            content_str = content.decode("utf-8")
                        except UnicodeDecodeError:
                            log.warning(f"跳过编码错误的文件: {json_filename}")
                            continue

                        # 使用原始文件名（去掉路径）
                        filename = os.path.basename(json_filename)
                        files_data.append({"filename": filename, "content": content_str})

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


async def upload_credentials_common(
    files: List[UploadFile], is_antigravity: bool = False
) -> JSONResponse:
    """批量上传凭证文件的通用函数"""
    cred_type = "Antigravity" if is_antigravity else "普通"

    if not files:
        raise HTTPException(status_code=400, detail="请选择要上传的文件")

    # 检查文件数量限制
    if len(files) > 100:
        raise HTTPException(
            status_code=400, detail=f"文件数量过多，最多支持100个文件，当前：{len(files)}个"
        )

    files_data = []
    for file in files:
        # 检查文件类型：支持JSON和ZIP
        if file.filename.endswith(".zip"):
            zip_files_data = await extract_json_files_from_zip(file)
            files_data.extend(zip_files_data)
            log.info(f"从ZIP文件 {file.filename} 中提取了 {len(zip_files_data)} 个JSON文件")

        elif file.filename.endswith(".json"):
            # 处理单个JSON文件 - 流式读取
            content_chunks = []
            while True:
                chunk = await file.read(8192)
                if not chunk:
                    break
                content_chunks.append(chunk)

            content = b"".join(content_chunks)
            try:
                content_str = content.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=400, detail=f"文件 {file.filename} 编码格式不支持"
                )

            files_data.append({"filename": file.filename, "content": content_str})
        else:
            raise HTTPException(
                status_code=400, detail=f"文件 {file.filename} 格式不支持，只支持JSON和ZIP文件"
            )

    await ensure_credential_manager_initialized()

    batch_size = 1000
    all_results = []
    total_success = 0

    for i in range(0, len(files_data), batch_size):
        batch_files = files_data[i : i + batch_size]

        async def process_single_file(file_data):
            try:
                filename = file_data["filename"]
                # 确保文件名只保存basename，避免路径问题
                filename = os.path.basename(filename)
                content_str = file_data["content"]
                credential_data = json.loads(content_str)

                # 根据凭证类型调用不同的添加方法
                if is_antigravity:
                    await credential_manager.add_antigravity_credential(filename, credential_data)
                else:
                    await credential_manager.add_credential(filename, credential_data)

                log.debug(f"成功上传{cred_type}凭证文件: {filename}")
                return {"filename": filename, "status": "success", "message": "上传成功"}

            except json.JSONDecodeError as e:
                return {
                    "filename": file_data["filename"],
                    "status": "error",
                    "message": f"JSON格式错误: {str(e)}",
                }
            except Exception as e:
                return {
                    "filename": file_data["filename"],
                    "status": "error",
                    "message": f"处理失败: {str(e)}",
                }

        log.info(f"开始并发处理 {len(batch_files)} 个{cred_type}文件...")
        concurrent_tasks = [process_single_file(file_data) for file_data in batch_files]
        batch_results = await asyncio.gather(*concurrent_tasks, return_exceptions=True)

        processed_results = []
        batch_uploaded_count = 0
        for result in batch_results:
            if isinstance(result, Exception):
                processed_results.append(
                    {
                        "filename": "unknown",
                        "status": "error",
                        "message": f"处理异常: {str(result)}",
                    }
                )
            else:
                processed_results.append(result)
                if result["status"] == "success":
                    batch_uploaded_count += 1

        all_results.extend(processed_results)
        total_success += batch_uploaded_count

        batch_num = (i // batch_size) + 1
        total_batches = (len(files_data) + batch_size - 1) // batch_size
        log.info(
            f"批次 {batch_num}/{total_batches} 完成: 成功 "
            f"{batch_uploaded_count}/{len(batch_files)} 个{cred_type}文件"
        )

    if total_success > 0:
        return JSONResponse(
            content={
                "uploaded_count": total_success,
                "total_count": len(files_data),
                "results": all_results,
                "message": f"批量上传完成: 成功 {total_success}/{len(files_data)} 个{cred_type}文件",
            }
        )
    else:
        raise HTTPException(status_code=400, detail=f"没有{cred_type}文件上传成功")


async def get_creds_status_common(
    offset: int, limit: int, status_filter: str, is_antigravity: bool = False,
    error_code_filter: str = None, cooldown_filter: str = None
) -> JSONResponse:
    """获取凭证文件状态的通用函数"""
    # 验证分页参数
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset 必须大于等于 0")
    if limit not in [20, 50, 100, 200, 500, 1000]:
        raise HTTPException(status_code=400, detail="limit 只能是 20、50、100、200、500 或 1000")
    if status_filter not in ["all", "enabled", "disabled"]:
        raise HTTPException(status_code=400, detail="status_filter 只能是 all、enabled 或 disabled")
    if cooldown_filter and cooldown_filter not in ["all", "in_cooldown", "no_cooldown"]:
        raise HTTPException(status_code=400, detail="cooldown_filter 只能是 all、in_cooldown 或 no_cooldown")

    await ensure_credential_manager_initialized()

    storage_adapter = await get_storage_adapter()
    backend_info = await storage_adapter.get_backend_info()
    backend_type = backend_info.get("backend_type", "unknown")

    # 优先使用高性能的分页摘要查询（SQLite专用）
    if hasattr(storage_adapter._backend, 'get_credentials_summary'):
        result = await storage_adapter._backend.get_credentials_summary(
            offset=offset,
            limit=limit,
            status_filter=status_filter,
            is_antigravity=is_antigravity,
            error_code_filter=error_code_filter if error_code_filter and error_code_filter != "all" else None,
            cooldown_filter=cooldown_filter if cooldown_filter and cooldown_filter != "all" else None
        )

        creds_list = []
        for summary in result["items"]:
            cred_info = {
                "filename": os.path.basename(summary["filename"]),
                "user_email": summary["user_email"],
                "disabled": summary["disabled"],
                "error_codes": summary["error_codes"],
                "last_success": summary["last_success"],
                "backend_type": backend_type,
                "model_cooldowns": summary.get("model_cooldowns", {}),
            }

            creds_list.append(cred_info)

        return JSONResponse(content={
            "items": creds_list,
            "total": result["total"],
            "offset": offset,
            "limit": limit,
            "has_more": (offset + limit) < result["total"],
            "stats": result.get("stats", {"total": 0, "normal": 0, "disabled": 0}),
        })

    # 回退到传统方式（MongoDB/其他后端）
    all_credentials = await storage_adapter.list_credentials(is_antigravity=is_antigravity)
    all_states = await storage_adapter.get_all_credential_states(is_antigravity=is_antigravity)

    # 应用状态筛选
    filtered_credentials = []
    for filename in all_credentials:
        file_status = all_states.get(filename, {"disabled": False})
        is_disabled = file_status.get("disabled", False)

        if status_filter == "all":
            filtered_credentials.append(filename)
        elif status_filter == "enabled" and not is_disabled:
            filtered_credentials.append(filename)
        elif status_filter == "disabled" and is_disabled:
            filtered_credentials.append(filename)

    total_count = len(filtered_credentials)
    paginated_credentials = filtered_credentials[offset:offset + limit]

    creds_list = []
    for filename in paginated_credentials:
        file_status = all_states.get(filename, {
            "error_codes": [],
            "disabled": False,
            "last_success": time.time(),
            "user_email": None,
        })

        cred_info = {
            "filename": os.path.basename(filename),
            "user_email": file_status.get("user_email"),
            "disabled": file_status.get("disabled", False),
            "error_codes": file_status.get("error_codes", []),
            "last_success": file_status.get("last_success", time.time()),
            "backend_type": backend_type,
            "model_cooldowns": file_status.get("model_cooldowns", {}),
        }

        creds_list.append(cred_info)

    return JSONResponse(content={
        "items": creds_list,
        "total": total_count,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total_count,
    })


async def download_all_creds_common(is_antigravity: bool = False) -> Response:
    """打包下载所有凭证文件的通用函数"""
    cred_type = "Antigravity" if is_antigravity else "普通"
    zip_filename = "antigravity_credentials.zip" if is_antigravity else "credentials.zip"

    storage_adapter = await get_storage_adapter()
    credential_filenames = await storage_adapter.list_credentials(is_antigravity=is_antigravity)

    if not credential_filenames:
        raise HTTPException(status_code=404, detail=f"没有找到{cred_type}凭证文件")

    log.info(f"开始打包 {len(credential_filenames)} 个{cred_type}凭证文件...")

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        success_count = 0
        for idx, filename in enumerate(credential_filenames, 1):
            try:
                credential_data = await storage_adapter.get_credential(filename, is_antigravity=is_antigravity)
                if credential_data:
                    content = json.dumps(credential_data, ensure_ascii=False, indent=2)
                    zip_file.writestr(os.path.basename(filename), content)
                    success_count += 1

                    if idx % 10 == 0:
                        log.debug(f"打包进度: {idx}/{len(credential_filenames)}")

            except Exception as e:
                log.warning(f"处理{cred_type}凭证文件 {filename} 时出错: {e}")
                continue

    log.info(f"打包完成: 成功 {success_count}/{len(credential_filenames)} 个文件")

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_filename}"},
    )


async def fetch_user_email_common(filename: str, is_antigravity: bool = False) -> JSONResponse:
    """获取指定凭证文件用户邮箱的通用函数"""
    await ensure_credential_manager_initialized()

    filename_only = os.path.basename(filename)
    if not filename_only.endswith(".json"):
        raise HTTPException(status_code=404, detail="无效的文件名")

    storage_adapter = await get_storage_adapter()
    credential_data = await storage_adapter.get_credential(filename_only, is_antigravity=is_antigravity)
    if not credential_data:
        raise HTTPException(status_code=404, detail="凭证文件不存在")

    email = await credential_manager.get_or_fetch_user_email(filename_only, is_antigravity=is_antigravity)

    if email:
        return JSONResponse(
            content={
                "filename": filename_only,
                "user_email": email,
                "message": "成功获取用户邮箱",
            }
        )
    else:
        return JSONResponse(
            content={
                "filename": filename_only,
                "user_email": None,
                "message": "无法获取用户邮箱，可能凭证已过期或权限不足",
            },
            status_code=400,
        )


async def refresh_all_user_emails_common(is_antigravity: bool = False) -> JSONResponse:
    """刷新所有凭证文件用户邮箱的通用函数"""
    await ensure_credential_manager_initialized()

    storage_adapter = await get_storage_adapter()
    credential_filenames = await storage_adapter.list_credentials(is_antigravity=is_antigravity)

    results = []
    success_count = 0

    for filename in credential_filenames:
        try:
            email = await credential_manager.get_or_fetch_user_email(filename, is_antigravity=is_antigravity)
            if email:
                success_count += 1
                results.append({
                    "filename": os.path.basename(filename),
                    "user_email": email,
                    "success": True,
                })
            else:
                results.append({
                    "filename": os.path.basename(filename),
                    "user_email": None,
                    "success": False,
                    "error": "无法获取邮箱",
                })
        except Exception as e:
            results.append({
                "filename": os.path.basename(filename),
                "user_email": None,
                "success": False,
                "error": str(e),
            })

    return JSONResponse(
        content={
            "success_count": success_count,
            "total_count": len(credential_filenames),
            "results": results,
            "message": f"成功获取 {success_count}/{len(credential_filenames)} 个邮箱地址",
        }
    )


# =============================================================================
# 路由处理函数 (Route Handlers)
# =============================================================================


@router.post("/auth/upload")
async def upload_credentials(
    files: List[UploadFile] = File(...), token: str = Depends(verify_panel_token)
):
    """批量上传认证文件"""
    try:
        return await upload_credentials_common(files, is_antigravity=False)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"批量上传失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/status")
async def get_creds_status(
    token: str = Depends(verify_panel_token),
    offset: int = 0,
    limit: int = 50,
    status_filter: str = "all",
    error_code_filter: str = "all",
    cooldown_filter: str = "all"
):
    """
    获取凭证文件的状态（轻量级摘要，不包含完整凭证数据，支持分页和状态筛选）

    Args:
        offset: 跳过的记录数（默认0）
        limit: 每页返回的记录数（默认50，可选：20, 50, 100, 200, 500, 1000）
        status_filter: 状态筛选（all=全部, enabled=仅启用, disabled=仅禁用）
        error_code_filter: 错误码筛选（all=全部, 或具体错误码如"400", "403"）
        cooldown_filter: 冷却状态筛选（all=全部, in_cooldown=冷却中, no_cooldown=未冷却）

    Returns:
        包含凭证列表、总数、分页信息的响应
    """
    try:
        return await get_creds_status_common(
            offset, limit, status_filter, is_antigravity=False,
            error_code_filter=error_code_filter,
            cooldown_filter=cooldown_filter
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取凭证状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/detail/{filename}")
async def get_cred_detail(filename: str, token: str = Depends(verify_panel_token)):
    """
    按需获取单个凭证的详细数据（包含完整凭证内容）
    用于用户查看/编辑凭证详情
    """
    try:
        # 验证文件名
        if not filename.endswith(".json"):
            raise HTTPException(status_code=400, detail="无效的文件名")

        await ensure_credential_manager_initialized()

        storage_adapter = await get_storage_adapter()
        backend_info = await storage_adapter.get_backend_info()
        backend_type = backend_info.get("backend_type", "unknown")

        # 获取凭证数据
        credential_data = await storage_adapter.get_credential(filename)
        if not credential_data:
            raise HTTPException(status_code=404, detail="凭证不存在")

        # 获取状态信息
        file_status = await storage_adapter.get_credential_state(filename)
        if not file_status:
            file_status = {
                "error_codes": [],
                "disabled": False,
                "last_success": time.time(),
                "user_email": None,
            }

        result = {
            "status": file_status,
            "content": credential_data,
            "filename": os.path.basename(filename),
            "backend_type": backend_type,
            "user_email": file_status.get("user_email"),
            "model_cooldowns": file_status.get("model_cooldowns", {}),
        }

        if backend_type == "file" and os.path.exists(filename):
            result.update({
                "size": os.path.getsize(filename),
                "modified_time": os.path.getmtime(filename),
            })

        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取凭证详情失败 {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/action")
async def creds_action(request: CredFileActionRequest, token: str = Depends(verify_panel_token)):
    """对凭证文件执行操作（启用/禁用/删除）"""
    try:
        await ensure_credential_manager_initialized()

        log.info(f"Received request: {request}")

        filename = request.filename
        action = request.action

        log.info(f"Performing action '{action}' on file: {filename}")

        # 验证文件名
        if not filename.endswith(".json"):
            log.error(f"无效的文件名: {filename}（不是.json文件）")
            raise HTTPException(status_code=400, detail=f"无效的文件名: {filename}")

        # 获取存储适配器
        storage_adapter = await get_storage_adapter()

        # 对于删除操作，不需要检查凭证数据是否完整，只需检查条目是否存在
        # 对于其他操作，需要确保凭证数据存在且完整
        if action != "delete":
            # 检查凭证数据是否存在
            credential_data = await storage_adapter.get_credential(filename)
            if not credential_data:
                log.error(f"凭证未找到: {filename}")
                raise HTTPException(status_code=404, detail="凭证文件不存在")

        if action == "enable":
            log.info(f"Web请求: 启用文件 {filename}")
            await credential_manager.set_cred_disabled(filename, False)
            log.info(f"Web请求: 文件 {filename} 已启用")
            return JSONResponse(content={"message": f"已启用凭证文件 {os.path.basename(filename)}"})

        elif action == "disable":
            log.info(f"Web请求: 禁用文件 {filename}")
            await credential_manager.set_cred_disabled(filename, True)
            log.info(f"Web请求: 文件 {filename} 已禁用")
            return JSONResponse(content={"message": f"已禁用凭证文件 {os.path.basename(filename)}"})

        elif action == "delete":
            try:
                # 使用 CredentialManager 删除凭证（包含队列/状态同步）
                success = await credential_manager.remove_credential(filename)
                if success:
                    log.info(f"通过管理器成功删除凭证: {filename}")
                    return JSONResponse(
                        content={"message": f"已删除凭证文件 {os.path.basename(filename)}"}
                    )
                else:
                    raise HTTPException(status_code=500, detail="删除凭证失败")
            except Exception as e:
                log.error(f"删除凭证 {filename} 时出错: {e}")
                raise HTTPException(status_code=500, detail=f"删除文件失败: {str(e)}")

        else:
            raise HTTPException(status_code=400, detail="无效的操作类型")

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"凭证文件操作失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/batch-action")
async def creds_batch_action(
    request: CredFileBatchActionRequest, token: str = Depends(verify_panel_token)
):
    """批量对凭证文件执行操作（启用/禁用/删除）"""
    try:
        await ensure_credential_manager_initialized()

        action = request.action
        filenames = request.filenames

        if not filenames:
            raise HTTPException(status_code=400, detail="文件名列表不能为空")

        log.info(f"对 {len(filenames)} 个文件执行批量操作 '{action}'")

        success_count = 0
        errors = []

        storage_adapter = await get_storage_adapter()

        for filename in filenames:
            try:
                # 验证文件名安全性
                if not filename.endswith(".json"):
                    errors.append(f"{filename}: 无效的文件类型")
                    continue

                # 对于删除操作，不需要检查凭证数据完整性
                # 对于其他操作，需要确保凭证数据存在
                if action != "delete":
                    credential_data = await storage_adapter.get_credential(filename)
                    if not credential_data:
                        errors.append(f"{filename}: 凭证不存在")
                        continue

                # 执行相应操作
                if action == "enable":
                    await credential_manager.set_cred_disabled(filename, False)
                    success_count += 1

                elif action == "disable":
                    await credential_manager.set_cred_disabled(filename, True)
                    success_count += 1

                elif action == "delete":
                    try:
                        delete_success = await credential_manager.remove_credential(filename)
                        if delete_success:
                            success_count += 1
                            log.info(f"成功删除批量中的凭证: {filename}")
                        else:
                            errors.append(f"{filename}: 删除失败")
                            continue
                    except Exception as e:
                        errors.append(f"{filename}: 删除文件失败 - {str(e)}")
                        continue
                else:
                    errors.append(f"{filename}: 无效的操作类型")
                    continue

            except Exception as e:
                log.error(f"处理 {filename} 时出错: {e}")
                errors.append(f"{filename}: 处理失败 - {str(e)}")
                continue

        # 构建返回消息
        result_message = f"批量操作完成：成功处理 {success_count}/{len(filenames)} 个文件"
        if errors:
            result_message += "\n错误详情:\n" + "\n".join(errors)

        response_data = {
            "success_count": success_count,
            "total_count": len(filenames),
            "errors": errors,
            "message": result_message,
        }

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"批量凭证文件操作失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/download/{filename}")
async def download_cred_file(filename: str, token: str = Depends(verify_panel_token)):
    """下载单个凭证文件"""
    try:
        # 验证文件名安全性
        if not filename.endswith(".json"):
            raise HTTPException(status_code=404, detail="无效的文件名")

        # 获取存储适配器
        storage_adapter = await get_storage_adapter()

        # 从存储系统获取凭证数据
        credential_data = await storage_adapter.get_credential(filename)
        if not credential_data:
            raise HTTPException(status_code=404, detail="文件不存在")

        # 转换为JSON字符串
        content = json.dumps(credential_data, ensure_ascii=False, indent=2)

        from fastapi.responses import Response

        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"下载凭证文件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/fetch-email/{filename}")
async def fetch_user_email(filename: str, token: str = Depends(verify_panel_token)):
    """获取指定凭证文件的用户邮箱地址"""
    try:
        return await fetch_user_email_common(filename, is_antigravity=False)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取用户邮箱失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/refresh-all-emails")
async def refresh_all_user_emails(token: str = Depends(verify_panel_token)):
    """刷新所有凭证文件的用户邮箱地址"""
    try:
        return await refresh_all_user_emails_common(is_antigravity=False)
    except Exception as e:
        log.error(f"批量获取用户邮箱失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/download-all")
async def download_all_creds(token: str = Depends(verify_panel_token)):
    """
    打包下载所有凭证文件（流式处理，按需加载每个凭证数据）
    只在实际下载时才加载完整凭证内容，最大化性能
    """
    try:
        return await download_all_creds_common(is_antigravity=False)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"打包下载失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/get")
async def get_config(token: str = Depends(verify_panel_token)):
    """获取当前配置"""
    try:
        await ensure_credential_manager_initialized()

        # 读取当前配置（包括环境变量和TOML文件中的配置）
        current_config = {}

        # 基础配置
        current_config["code_assist_endpoint"] = await config.get_code_assist_endpoint()
        current_config["credentials_dir"] = await config.get_credentials_dir()
        current_config["proxy"] = await config.get_proxy_config() or ""

        # 代理端点配置
        current_config["oauth_proxy_url"] = await config.get_oauth_proxy_url()
        current_config["googleapis_proxy_url"] = await config.get_googleapis_proxy_url()
        current_config["resource_manager_api_url"] = await config.get_resource_manager_api_url()
        current_config["service_usage_api_url"] = await config.get_service_usage_api_url()
        current_config["antigravity_api_url"] = await config.get_antigravity_api_url()

        # 自动封禁配置
        current_config["auto_ban_enabled"] = await config.get_auto_ban_enabled()
        current_config["auto_ban_error_codes"] = await config.get_auto_ban_error_codes()

        # 429重试配置
        current_config["retry_429_max_retries"] = await config.get_retry_429_max_retries()
        current_config["retry_429_enabled"] = await config.get_retry_429_enabled()
        current_config["retry_429_interval"] = await config.get_retry_429_interval()

        # 抗截断配置
        current_config["anti_truncation_max_attempts"] = await config.get_anti_truncation_max_attempts()

        # 兼容性配置
        current_config["compatibility_mode_enabled"] = await config.get_compatibility_mode_enabled()

        # 思维链返回配置
        current_config["return_thoughts_to_frontend"] = await config.get_return_thoughts_to_frontend()

        # 服务器配置
        current_config["host"] = await config.get_server_host()
        current_config["port"] = await config.get_server_port()
        current_config["api_password"] = await config.get_api_password()
        current_config["panel_password"] = await config.get_panel_password()
        current_config["password"] = await config.get_server_password()

        # 从存储系统读取配置
        storage_adapter = await get_storage_adapter()
        storage_config = await storage_adapter.get_all_config()

        # 获取环境变量锁定的配置键
        env_locked_keys = get_env_locked_keys()

        # 合并存储系统配置（不覆盖环境变量）
        for key, value in storage_config.items():
            if key not in env_locked_keys:
                current_config[key] = value

        return JSONResponse(content={"config": current_config, "env_locked": list(env_locked_keys)})

    except Exception as e:
        log.error(f"获取配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/save")
async def save_config(request: ConfigSaveRequest, token: str = Depends(verify_panel_token)):
    """保存配置到TOML文件"""
    try:
        await ensure_credential_manager_initialized()
        new_config = request.config

        log.debug(f"收到的配置数据: {list(new_config.keys())}")
        log.debug(f"收到的password值: {new_config.get('password', 'NOT_FOUND')}")

        # 验证配置项
        if "retry_429_max_retries" in new_config:
            if (
                not isinstance(new_config["retry_429_max_retries"], int)
                or new_config["retry_429_max_retries"] < 0
            ):
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

        if "anti_truncation_max_attempts" in new_config:
            if (
                not isinstance(new_config["anti_truncation_max_attempts"], int)
                or new_config["anti_truncation_max_attempts"] < 1
                or new_config["anti_truncation_max_attempts"] > 10
            ):
                raise HTTPException(
                    status_code=400, detail="抗截断最大重试次数必须是1-10之间的整数"
                )

        if "compatibility_mode_enabled" in new_config:
            if not isinstance(new_config["compatibility_mode_enabled"], bool):
                raise HTTPException(status_code=400, detail="兼容性模式开关必须是布尔值")

        if "return_thoughts_to_frontend" in new_config:
            if not isinstance(new_config["return_thoughts_to_frontend"], bool):
                raise HTTPException(status_code=400, detail="思维链返回开关必须是布尔值")

        # 验证服务器配置
        if "host" in new_config:
            if not isinstance(new_config["host"], str) or not new_config["host"].strip():
                raise HTTPException(status_code=400, detail="服务器主机地址不能为空")

        if "port" in new_config:
            if (
                not isinstance(new_config["port"], int)
                or new_config["port"] < 1
                or new_config["port"] > 65535
            ):
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

        # 获取环境变量锁定的配置键
        env_locked_keys = get_env_locked_keys()

        # 直接使用存储适配器保存配置
        storage_adapter = await get_storage_adapter()
        for key, value in new_config.items():
            if key not in env_locked_keys:
                await storage_adapter.set_config(key, value)
                if key in ("password", "api_password", "panel_password"):
                    log.debug(f"设置{key}字段为: {value}")

        # 重新加载配置缓存（关键！）
        await config.reload_config()

        # 验证保存后的结果
        test_api_password = await config.get_api_password()
        test_panel_password = await config.get_panel_password()
        test_password = await config.get_server_password()
        log.debug(f"保存后立即读取的API密码: {test_api_password}")
        log.debug(f"保存后立即读取的面板密码: {test_panel_password}")
        log.debug(f"保存后立即读取的通用密码: {test_password}")

        # 构建响应消息
        response_data = {
            "message": "配置保存成功",
            "saved_config": {k: v for k, v in new_config.items() if k not in env_locked_keys},
        }

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"保存配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# 实时日志WebSocket (Real-time Logs WebSocket)
# =============================================================================


@router.post("/auth/logs/clear")
async def clear_logs(token: str = Depends(verify_panel_token)):
    """清空日志文件"""
    try:
        # 直接使用环境变量获取日志文件路径
        log_file_path = os.getenv("LOG_FILE", "log.txt")

        # 检查日志文件是否存在
        if os.path.exists(log_file_path):
            try:
                # 清空文件内容（保留文件），确保以UTF-8编码写入
                with open(log_file_path, "w", encoding="utf-8", newline="") as f:
                    f.write("")
                    f.flush()  # 强制刷新到磁盘
                log.info(f"日志文件已清空: {log_file_path}")

                # 通知所有WebSocket连接日志已清空
                await manager.broadcast("--- 日志文件已清空 ---")

                return JSONResponse(
                    content={"message": f"日志文件已清空: {os.path.basename(log_file_path)}"}
                )
            except Exception as e:
                log.error(f"清空日志文件失败: {e}")
                raise HTTPException(status_code=500, detail=f"清空日志文件失败: {str(e)}")
        else:
            return JSONResponse(content={"message": "日志文件不存在"})

    except Exception as e:
        log.error(f"清空日志文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空日志文件失败: {str(e)}")


@router.get("/auth/logs/download")
async def download_logs(token: str = Depends(verify_panel_token)):
    """下载日志文件"""
    try:
        # 直接使用环境变量获取日志文件路径
        log_file_path = os.getenv("LOG_FILE", "log.txt")

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
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
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
        # 直接使用环境变量获取日志文件路径
        log_file_path = os.getenv("LOG_FILE", "log.txt")

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
        check_interval = 2  # 增加检查间隔，减少CPU和I/O开销

        # 创建后台任务监听客户端断开
        # 即使没有日志更新，receive_text() 也能即时感知断开
        async def listen_for_disconnect():
            try:
                while True:
                    await websocket.receive_text()
            except Exception:
                pass

        listener_task = asyncio.create_task(listen_for_disconnect())

        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                # 使用 asyncio.wait 同时等待定时器和断开信号
                # timeout=check_interval 替代了 asyncio.sleep
                done, pending = await asyncio.wait(
                    [listener_task],
                    timeout=check_interval,
                    return_when=asyncio.FIRST_COMPLETED
                )

                # 如果监听任务结束（通常是因为连接断开），则退出循环
                if listener_task in done:
                    break

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
                                    if not lines[-1].endswith("\n") and len(lines) > 1:
                                        # 除了最后一行，其他都发送
                                        for line in lines[:-1]:
                                            if line.strip():
                                                await websocket.send_text(line.rstrip())
                                        # 更新位置，但要退回最后一行的字节数
                                        last_size += len(new_content.encode("utf-8")) - len(
                                            lines[-1].encode("utf-8")
                                        )
                                    else:
                                        # 所有行都发送
                                        for line in lines:
                                            if line.strip():
                                                await websocket.send_text(line.rstrip())
                                        last_size += len(new_content.encode("utf-8"))
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

        finally:
            # 确保清理监听任务
            if not listener_task.done():
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"WebSocket logs error: {e}")
    finally:
        manager.disconnect(websocket)


# ============ Antigravity 凭证管理路由 ============

@router.post("/antigravity/upload")
async def upload_antigravity_credentials(
    files: List[UploadFile] = File(...), token: str = Depends(verify_panel_token)
):
    """批量上传Antigravity认证文件"""
    try:
        return await upload_credentials_common(files, is_antigravity=True)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"批量上传Antigravity文件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/antigravity/creds/status")
async def get_antigravity_creds_status(
    token: str = Depends(verify_panel_token),
    offset: int = 0,
    limit: int = 50,
    status_filter: str = "all",
    error_code_filter: str = "all",
    cooldown_filter: str = "all"
):
    """
    获取Antigravity凭证文件的状态（轻量级摘要，不包含完整凭证数据，支持分页和状态筛选）

    Args:
        offset: 跳过的记录数（默认0）
        limit: 每页返回的记录数（默认50，可选：20, 50, 100, 200, 500, 1000）
        status_filter: 状态筛选（all=全部, enabled=仅启用, disabled=仅禁用）
        error_code_filter: 错误码筛选（all=全部, 或具体错误码如"400", "403"）
        cooldown_filter: 冷却状态筛选（all=全部, in_cooldown=冷却中, no_cooldown=未冷却）

    Returns:
        包含凭证列表、总数、分页信息的响应
    """
    try:
        return await get_creds_status_common(
            offset, limit, status_filter, is_antigravity=True,
            error_code_filter=error_code_filter,
            cooldown_filter=cooldown_filter
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取Antigravity凭证状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/antigravity/creds/action")
async def antigravity_cred_action(request: CredFileActionRequest, token: str = Depends(verify_panel_token)):
    """对 antigravity 凭证执行操作（启用/禁用/删除）"""
    try:
        storage_adapter = await get_storage_adapter()
        filename = request.filename
        action = request.action

        if action == "enable":
            await storage_adapter.update_credential_state(filename, {"disabled": False}, is_antigravity=True)
            return JSONResponse(content={"success": True, "message": f"已启用凭证: {filename}"})
        elif action == "disable":
            await storage_adapter.update_credential_state(filename, {"disabled": True}, is_antigravity=True)
            return JSONResponse(content={"success": True, "message": f"已禁用凭证: {filename}"})
        elif action == "delete":
            success = await storage_adapter.delete_credential(filename, is_antigravity=True)
            if success:
                return JSONResponse(content={"success": True, "message": f"已删除凭证: {filename}"})
            else:
                raise HTTPException(status_code=500, detail="删除失败")
        else:
            raise HTTPException(status_code=400, detail=f"未知操作: {action}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Antigravity 凭证操作失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/antigravity/creds/download/{filename}")
async def download_antigravity_cred(filename: str, token: str = Depends(verify_panel_token)):
    """下载 antigravity 凭证文件"""
    try:
        storage_adapter = await get_storage_adapter()
        credential_data = await storage_adapter.get_credential(filename, is_antigravity=True)

        if not credential_data:
            raise HTTPException(status_code=404, detail="凭证不存在")

        json_content = json.dumps(credential_data, indent=2, ensure_ascii=False)

        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"下载 antigravity 凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/antigravity/creds/batch-action")
async def antigravity_batch_action(request: CredFileBatchActionRequest, token: str = Depends(verify_panel_token)):
    """批量操作 antigravity 凭证"""
    try:
        storage_adapter = await get_storage_adapter()
        filenames = request.filenames
        action = request.action

        results = []
        for filename in filenames:
            try:
                if action == "enable":
                    await storage_adapter.update_credential_state(filename, {"disabled": False}, is_antigravity=True)
                    results.append({"filename": filename, "success": True})
                elif action == "disable":
                    await storage_adapter.update_credential_state(filename, {"disabled": True}, is_antigravity=True)
                    results.append({"filename": filename, "success": True})
                elif action == "delete":
                    success = await storage_adapter.delete_credential(filename, is_antigravity=True)
                    results.append({"filename": filename, "success": success})
                else:
                    results.append({"filename": filename, "success": False, "error": "未知操作"})
            except Exception as e:
                results.append({"filename": filename, "success": False, "error": str(e)})

        success_count = sum(1 for r in results if r.get("success"))
        return JSONResponse(content={
            "success": True,
            "success_count": success_count,
            "total": len(filenames),
            "succeeded": success_count,
            "failed": len(filenames) - success_count,
            "results": results
        })
    except Exception as e:
        log.error(f"批量操作 antigravity 凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/antigravity/creds/fetch-email/{filename}")
async def fetch_antigravity_user_email(filename: str, token: str = Depends(verify_panel_token)):
    """获取指定Antigravity凭证文件的用户邮箱地址"""
    try:
        return await fetch_user_email_common(filename, is_antigravity=True)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取Antigravity用户邮箱失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/antigravity/creds/refresh-all-emails")
async def refresh_all_antigravity_user_emails(token: str = Depends(verify_panel_token)):
    """刷新所有Antigravity凭证文件的用户邮箱地址"""
    try:
        return await refresh_all_user_emails_common(is_antigravity=True)
    except Exception as e:
        log.error(f"批量获取Antigravity用户邮箱失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/antigravity/creds/download-all")
async def download_all_antigravity_creds(token: str = Depends(verify_panel_token)):
    """
    打包下载所有Antigravity凭证文件（流式处理，按需加载每个凭证数据）
    只在实际下载时才加载完整凭证内容，最大化性能
    """
    try:
        return await download_all_creds_common(is_antigravity=True)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"打包下载Antigravity凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def verify_credential_project_common(filename: str, is_antigravity: bool = False) -> JSONResponse:
    """验证并重新获取凭证的project id的通用函数"""

    cred_type = "Antigravity" if is_antigravity else "GCLI"

    # 验证文件名
    if not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="无效的文件名")

    await ensure_credential_manager_initialized()
    storage_adapter = await get_storage_adapter()

    # 获取凭证数据
    credential_data = await storage_adapter.get_credential(filename, is_antigravity=is_antigravity)
    if not credential_data:
        raise HTTPException(status_code=404, detail="凭证不存在")

    # 创建凭证对象
    credentials = Credentials.from_dict(credential_data)

    # 确保token有效（自动刷新）
    token_refreshed = await credentials.refresh_if_needed()

    # 如果token被刷新了，更新存储
    if token_refreshed:
        log.info(f"Token已自动刷新: {filename} (is_antigravity={is_antigravity})")
        credential_data = credentials.to_dict()
        await storage_adapter.store_credential(filename, credential_data, is_antigravity=is_antigravity)

    # 获取API端点和对应的User-Agent
    if is_antigravity:
        api_base_url = await get_antigravity_api_url()
        user_agent = ANTIGRAVITY_USER_AGENT
    else:
        api_base_url = await get_code_assist_endpoint()
        user_agent = STANDARD_USER_AGENT

    # 重新获取project id
    project_id = await fetch_project_id(
        access_token=credentials.access_token,
        user_agent=user_agent,
        api_base_url=api_base_url
    )

    if project_id:
        # 更新凭证数据中的project_id
        credential_data["project_id"] = project_id
        await storage_adapter.store_credential(filename, credential_data, is_antigravity=is_antigravity)

        # 检验成功后自动解除禁用状态并清除错误码
        await storage_adapter.update_credential_state(filename, {
            "disabled": False,
            "error_codes": []
        }, is_antigravity=is_antigravity)

        log.info(f"检验{cred_type}凭证成功: {filename} - Project ID: {project_id} - 已解除禁用并清除错误码")

        return JSONResponse(content={
            "success": True,
            "filename": filename,
            "project_id": project_id,
            "message": "检验成功！Project ID已更新，已解除禁用状态并清除错误码，403错误应该已恢复"
        })
    else:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "filename": filename,
                "message": "检验失败：无法获取Project ID，请检查凭证是否有效"
            }
        )


@router.post("/creds/verify-project/{filename}")
async def verify_credential_project(filename: str, token: str = Depends(verify_panel_token)):
    """
    检验GCLI凭证的project id，重新获取project id
    检验成功可以使403错误恢复
    """
    try:
        return await verify_credential_project_common(filename, is_antigravity=False)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"检验凭证Project ID失败 {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"检验失败: {str(e)}")


@router.post("/antigravity/creds/verify-project/{filename}")
async def verify_antigravity_credential_project(filename: str, token: str = Depends(verify_panel_token)):
    """
    检验Antigravity凭证的project id，重新获取project id
    检验成功可以使403错误恢复
    """
    try:
        return await verify_credential_project_common(filename, is_antigravity=True)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"检验Antigravity凭证Project ID失败 {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"检验失败: {str(e)}")


@router.get("/antigravity/creds/quota/{filename}")
async def get_antigravity_credential_quota(filename: str, token: str = Depends(verify_panel_token)):
    """
    获取指定Antigravity凭证的额度信息
    """
    try:
        # 验证文件名
        if not filename.endswith(".json"):
            raise HTTPException(status_code=400, detail="无效的文件名")

        await ensure_credential_manager_initialized()
        storage_adapter = await get_storage_adapter()

        # 获取凭证数据
        credential_data = await storage_adapter.get_credential(filename, is_antigravity=True)
        if not credential_data:
            raise HTTPException(status_code=404, detail="凭证不存在")

        # 使用 Credentials 对象自动处理 token 刷新
        from .google_oauth_api import Credentials

        creds = Credentials.from_dict(credential_data)

        # 自动刷新 token（如果需要）
        await creds.refresh_if_needed()

        # 如果 token 被刷新了，更新存储
        updated_data = creds.to_dict()
        if updated_data != credential_data:
            log.info(f"Token已自动刷新: {filename}")
            await storage_adapter.store_credential(filename, updated_data, is_antigravity=True)
            credential_data = updated_data

        # 获取访问令牌
        access_token = credential_data.get("access_token") or credential_data.get("token")
        if not access_token:
            raise HTTPException(status_code=400, detail="凭证中没有访问令牌")

        # 获取额度信息
        quota_info = await fetch_quota_info(access_token)

        if quota_info.get("success"):
            return JSONResponse(content={
                "success": True,
                "filename": filename,
                "models": quota_info.get("models", {})
            })
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "filename": filename,
                    "error": quota_info.get("error", "未知错误")
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取Antigravity凭证额度失败 {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"获取额度失败: {str(e)}")




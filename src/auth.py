"""
认证API模块 - 使用统一存储中间层，完全摆脱文件操作
"""

import asyncio
import json
import secrets
import socket
import threading
import time
import uuid
from datetime import timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from config import get_config_value, get_antigravity_api_url, get_code_assist_endpoint
from log import log

from .google_oauth_api import (
    Credentials,
    Flow,
    enable_required_apis,
    fetch_project_id,
    get_user_projects,
    select_default_project,
)
from .storage_adapter import get_storage_adapter
from .utils import (
    ANTIGRAVITY_CLIENT_ID,
    ANTIGRAVITY_CLIENT_SECRET,
    ANTIGRAVITY_SCOPES,
    ANTIGRAVITY_USER_AGENT,
    CALLBACK_HOST,
    CLIENT_ID,
    CLIENT_SECRET,
    SCOPES,
    STANDARD_USER_AGENT,
    TOKEN_URL,
)


async def get_callback_port():
    """获取OAuth回调端口"""
    return int(await get_config_value("oauth_callback_port", "11451", "OAUTH_CALLBACK_PORT"))


def _prepare_credentials_data(credentials: Credentials, project_id: str, is_antigravity: bool = False) -> Dict[str, Any]:
    """准备凭证数据字典（统一函数）"""
    if is_antigravity:
        creds_data = {
            "client_id": ANTIGRAVITY_CLIENT_ID,
            "client_secret": ANTIGRAVITY_CLIENT_SECRET,
            "token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
            "scopes": ANTIGRAVITY_SCOPES,
            "token_uri": TOKEN_URL,
            "project_id": project_id,
        }
    else:
        creds_data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
            "scopes": SCOPES,
            "token_uri": TOKEN_URL,
            "project_id": project_id,
        }

    if credentials.expires_at:
        if credentials.expires_at.tzinfo is None:
            expiry_utc = credentials.expires_at.replace(tzinfo=timezone.utc)
        else:
            expiry_utc = credentials.expires_at
        creds_data["expiry"] = expiry_utc.isoformat()

    return creds_data


def _generate_random_project_id() -> str:
    """生成随机project_id（antigravity模式使用）"""
    random_id = uuid.uuid4().hex[:8]
    return f"projects/random-{random_id}/locations/global"


def _cleanup_auth_flow_server(state: str):
    """清理认证流程的服务器资源"""
    if state in auth_flows:
        flow_data_to_clean = auth_flows[state]
        try:
            if flow_data_to_clean.get("server"):
                server = flow_data_to_clean["server"]
                port = flow_data_to_clean.get("callback_port")
                async_shutdown_server(server, port)
        except Exception as e:
            log.debug(f"关闭服务器时出错: {e}")
        del auth_flows[state]


class _OAuthLibPatcher:
    """oauthlib参数验证补丁的上下文管理器"""
    def __init__(self):
        import oauthlib.oauth2.rfc6749.parameters
        self.module = oauthlib.oauth2.rfc6749.parameters
        self.original_validate = None

    def __enter__(self):
        self.original_validate = self.module.validate_token_parameters

        def patched_validate(params):
            try:
                return self.original_validate(params)
            except Warning:
                pass

        self.module.validate_token_parameters = patched_validate
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.original_validate:
            self.module.validate_token_parameters = self.original_validate


# 全局状态管理 - 严格限制大小
auth_flows = {}  # 存储进行中的认证流程
MAX_AUTH_FLOWS = 20  # 严格限制最大认证流程数


def cleanup_auth_flows_for_memory():
    """清理认证流程以释放内存"""
    global auth_flows
    cleanup_expired_flows()
    # 如果还是太多，强制清理一些旧的流程
    if len(auth_flows) > 10:
        # 按创建时间排序，保留最新的10个
        sorted_flows = sorted(
            auth_flows.items(), key=lambda x: x[1].get("created_at", 0), reverse=True
        )
        new_auth_flows = dict(sorted_flows[:10])

        # 清理被移除的流程
        for state, flow_data in auth_flows.items():
            if state not in new_auth_flows:
                try:
                    if flow_data.get("server"):
                        server = flow_data["server"]
                        port = flow_data.get("callback_port")
                        async_shutdown_server(server, port)
                except Exception:
                    pass
                flow_data.clear()

        auth_flows = new_auth_flows
        log.info(f"强制清理认证流程，保留 {len(auth_flows)} 个最新流程")

    return len(auth_flows)


async def find_available_port(start_port: int = None) -> int:
    """动态查找可用端口"""
    if start_port is None:
        start_port = await get_callback_port()

    # 首先尝试默认端口
    for port in range(start_port, start_port + 100):  # 尝试100个端口
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                log.info(f"找到可用端口: {port}")
                return port
        except OSError:
            continue

    # 如果都不可用，让系统自动分配端口
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", 0))
            port = s.getsockname()[1]
            log.info(f"系统分配可用端口: {port}")
            return port
    except OSError as e:
        log.error(f"无法找到可用端口: {e}")
        raise RuntimeError("无法找到可用端口")


def create_callback_server(port: int) -> HTTPServer:
    """创建指定端口的回调服务器，优化快速关闭"""
    try:
        # 服务器监听0.0.0.0
        server = HTTPServer(("0.0.0.0", port), AuthCallbackHandler)

        # 设置socket选项以支持快速关闭
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 设置较短的超时时间
        server.timeout = 1.0

        log.info(f"创建OAuth回调服务器，监听端口: {port}")
        return server
    except OSError as e:
        log.error(f"创建端口{port}的服务器失败: {e}")
        raise


class AuthCallbackHandler(BaseHTTPRequestHandler):
    """OAuth回调处理器"""

    def do_GET(self):
        query_components = parse_qs(urlparse(self.path).query)
        code = query_components.get("code", [None])[0]
        state = query_components.get("state", [None])[0]

        log.info(f"收到OAuth回调: code={'已获取' if code else '未获取'}, state={state}")

        if code and state and state in auth_flows:
            # 更新流程状态
            auth_flows[state]["code"] = code
            auth_flows[state]["completed"] = True

            log.info(f"OAuth回调成功处理: state={state}")

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            # 成功页面
            self.wfile.write(
                b"<h1>OAuth authentication successful!</h1><p>You can close this window. Please return to the original page and click 'Get Credentials' button.</p>"
            )
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authentication failed.</h1><p>Please try again.</p>")

    def log_message(self, format, *args):
        # 减少日志噪音
        pass


async def create_auth_url(
    project_id: Optional[str] = None, user_session: str = None, use_antigravity: bool = False
) -> Dict[str, Any]:
    """创建认证URL，支持动态端口分配"""
    try:
        # 动态分配端口
        callback_port = await find_available_port()
        callback_url = f"http://{CALLBACK_HOST}:{callback_port}"

        # 立即启动回调服务器
        try:
            callback_server = create_callback_server(callback_port)
            # 在后台线程中运行服务器
            server_thread = threading.Thread(
                target=callback_server.serve_forever,
                daemon=True,
                name=f"OAuth-Server-{callback_port}",
            )
            server_thread.start()
            log.info(f"OAuth回调服务器已启动，端口: {callback_port}")
        except Exception as e:
            log.error(f"启动回调服务器失败: {e}")
            return {
                "success": False,
                "error": f"无法启动OAuth回调服务器，端口{callback_port}: {str(e)}",
            }

        # 创建OAuth流程
        # 根据模式选择配置
        if use_antigravity:
            client_id = ANTIGRAVITY_CLIENT_ID
            client_secret = ANTIGRAVITY_CLIENT_SECRET
            scopes = ANTIGRAVITY_SCOPES
        else:
            client_id = CLIENT_ID
            client_secret = CLIENT_SECRET
            scopes = SCOPES

        flow = Flow(
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            redirect_uri=callback_url,
        )

        # 生成状态标识符，包含用户会话信息
        if user_session:
            state = f"{user_session}_{str(uuid.uuid4())}"
        else:
            state = str(uuid.uuid4())

        # 生成认证URL
        auth_url = flow.get_auth_url(state=state)

        # 严格控制认证流程数量 - 超过限制时立即清理最旧的
        if len(auth_flows) >= MAX_AUTH_FLOWS:
            # 清理最旧的认证流程
            oldest_state = min(auth_flows.keys(), key=lambda k: auth_flows[k].get("created_at", 0))
            try:
                # 清理服务器资源
                old_flow = auth_flows[oldest_state]
                if old_flow.get("server"):
                    server = old_flow["server"]
                    port = old_flow.get("callback_port")
                    async_shutdown_server(server, port)
            except Exception as e:
                log.warning(f"Failed to cleanup old auth flow {oldest_state}: {e}")

            del auth_flows[oldest_state]
            log.debug(f"Removed oldest auth flow: {oldest_state}")

        # 保存流程状态
        auth_flows[state] = {
            "flow": flow,
            "project_id": project_id,  # 可能为None，稍后在回调时确定
            "user_session": user_session,
            "callback_port": callback_port,  # 存储分配的端口
            "callback_url": callback_url,  # 存储完整回调URL
            "server": callback_server,  # 存储服务器实例
            "server_thread": server_thread,  # 存储服务器线程
            "code": None,
            "completed": False,
            "created_at": time.time(),
            "auto_project_detection": project_id is None,  # 标记是否需要自动检测项目ID
            "use_antigravity": use_antigravity,  # 是否使用antigravity模式
        }

        # 清理过期的流程（30分钟）
        cleanup_expired_flows()

        log.info(f"OAuth流程已创建: state={state}, project_id={project_id}")
        log.info(f"用户需要访问认证URL，然后OAuth会回调到 {callback_url}")
        log.info(f"为此认证流程分配的端口: {callback_port}")

        return {
            "auth_url": auth_url,
            "state": state,
            "callback_port": callback_port,
            "success": True,
            "auto_project_detection": project_id is None,
            "detected_project_id": project_id,
        }

    except Exception as e:
        log.error(f"创建认证URL失败: {e}")
        return {"success": False, "error": str(e)}


def wait_for_callback_sync(state: str, timeout: int = 300) -> Optional[str]:
    """同步等待OAuth回调完成，使用对应流程的专用服务器"""
    if state not in auth_flows:
        log.error(f"未找到状态为 {state} 的认证流程")
        return None

    flow_data = auth_flows[state]
    callback_port = flow_data["callback_port"]

    # 服务器已经在create_auth_url时启动了，这里只需要等待
    log.info(f"等待OAuth回调完成，端口: {callback_port}")

    # 等待回调完成
    start_time = time.time()
    while time.time() - start_time < timeout:
        if flow_data.get("code"):
            log.info("OAuth回调成功完成")
            return flow_data["code"]
        time.sleep(0.5)  # 每0.5秒检查一次

        # 刷新flow_data引用
        if state in auth_flows:
            flow_data = auth_flows[state]

    log.warning(f"等待OAuth回调超时 ({timeout}秒)")
    return None


async def complete_auth_flow(
    project_id: Optional[str] = None, user_session: str = None
) -> Dict[str, Any]:
    """完成认证流程并保存凭证，支持自动检测项目ID"""
    try:
        # 查找对应的认证流程
        state = None
        flow_data = None

        # 如果指定了project_id，先尝试匹配指定的项目
        if project_id:
            for s, data in auth_flows.items():
                if data["project_id"] == project_id:
                    # 如果指定了用户会话，优先匹配相同会话的流程
                    if user_session and data.get("user_session") == user_session:
                        state = s
                        flow_data = data
                        break
                    # 如果没有指定会话，或没找到匹配会话的流程，使用第一个匹配项目ID的
                    elif not state:
                        state = s
                        flow_data = data

        # 如果没有指定项目ID或没找到匹配的，查找需要自动检测项目ID的流程
        if not state:
            for s, data in auth_flows.items():
                if data.get("auto_project_detection", False):
                    # 如果指定了用户会话，优先匹配相同会话的流程
                    if user_session and data.get("user_session") == user_session:
                        state = s
                        flow_data = data
                        break
                    # 使用第一个找到的需要自动检测的流程
                    elif not state:
                        state = s
                        flow_data = data

        if not state or not flow_data:
            return {"success": False, "error": "未找到对应的认证流程，请先点击获取认证链接"}

        if not project_id:
            project_id = flow_data.get("project_id")
            if not project_id:
                return {
                    "success": False,
                    "error": "缺少项目ID，请指定项目ID",
                    "requires_manual_project_id": True,
                }

        flow = flow_data["flow"]

        # 如果还没有授权码，需要等待回调
        if not flow_data.get("code"):
            log.info(f"等待用户完成OAuth授权 (state: {state})")
            auth_code = wait_for_callback_sync(state)

            if not auth_code:
                return {
                    "success": False,
                    "error": "未接收到授权回调，请确保完成了浏览器中的OAuth认证",
                }

            # 更新流程数据
            auth_flows[state]["code"] = auth_code
            auth_flows[state]["completed"] = True
        else:
            auth_code = flow_data["code"]

        # 使用认证代码获取凭证
        with _OAuthLibPatcher():
            try:
                credentials = await flow.exchange_code(auth_code)
                # credentials 已经在 exchange_code 中获得

                # 如果需要自动检测项目ID且没有提供项目ID
                if flow_data.get("auto_project_detection", False) and not project_id:
                    log.info("尝试通过API获取用户项目列表...")
                    log.info(f"使用的token: {credentials.access_token[:20]}...")
                    log.info(f"Token过期时间: {credentials.expires_at}")
                    user_projects = await get_user_projects(credentials)

                    if user_projects:
                        # 如果只有一个项目，自动使用
                        if len(user_projects) == 1:
                            # Google API returns projectId in camelCase
                            project_id = user_projects[0].get("projectId")
                            if project_id:
                                flow_data["project_id"] = project_id
                                log.info(f"自动选择唯一项目: {project_id}")
                        # 如果有多个项目，尝试选择默认项目
                        else:
                            project_id = await select_default_project(user_projects)
                            if project_id:
                                flow_data["project_id"] = project_id
                                log.info(f"自动选择默认项目: {project_id}")
                            else:
                                # 返回项目列表让用户选择
                                return {
                                    "success": False,
                                    "error": "请从以下项目中选择一个",
                                    "requires_project_selection": True,
                                    "available_projects": [
                                        {
                                            # Google API returns projectId in camelCase
                                            "project_id": p.get("projectId"),
                                            "name": p.get("displayName") or p.get("projectId"),
                                            "projectNumber": p.get("projectNumber"),
                                        }
                                        for p in user_projects
                                    ],
                                }
                    else:
                        # 如果无法获取项目列表，提示手动输入
                        return {
                            "success": False,
                            "error": "无法获取您的项目列表，请手动指定项目ID",
                            "requires_manual_project_id": True,
                        }

                # 如果仍然没有项目ID，返回错误
                if not project_id:
                    return {
                        "success": False,
                        "error": "缺少项目ID，请指定项目ID",
                        "requires_manual_project_id": True,
                    }

                # 保存凭证
                saved_filename = await save_credentials(credentials, project_id)

                # 准备返回的凭证数据
                creds_data = _prepare_credentials_data(credentials, project_id, is_antigravity=False)

                # 清理使用过的流程
                _cleanup_auth_flow_server(state)

                log.info("OAuth认证成功，凭证已保存")
                return {
                    "success": True,
                    "credentials": creds_data,
                    "file_path": saved_filename,
                    "auto_detected_project": flow_data.get("auto_project_detection", False),
                }

            except Exception as e:
                log.error(f"获取凭证失败: {e}")
                return {"success": False, "error": f"获取凭证失败: {str(e)}"}

    except Exception as e:
        log.error(f"完成认证流程失败: {e}")
        return {"success": False, "error": str(e)}


async def asyncio_complete_auth_flow(
    project_id: Optional[str] = None, user_session: str = None, use_antigravity: bool = False
) -> Dict[str, Any]:
    """异步完成认证流程，支持自动检测项目ID"""
    try:
        log.info(
            f"asyncio_complete_auth_flow开始执行: project_id={project_id}, user_session={user_session}"
        )

        # 查找对应的认证流程
        state = None
        flow_data = None

        log.debug(f"当前所有auth_flows: {list(auth_flows.keys())}")

        # 如果指定了project_id，先尝试匹配指定的项目
        if project_id:
            log.info(f"尝试匹配指定的项目ID: {project_id}")
            for s, data in auth_flows.items():
                if data["project_id"] == project_id:
                    # 如果指定了用户会话，优先匹配相同会话的流程
                    if user_session and data.get("user_session") == user_session:
                        state = s
                        flow_data = data
                        log.info(f"找到匹配的用户会话: {s}")
                        break
                    # 如果没有指定会话，或没找到匹配会话的流程，使用第一个匹配项目ID的
                    elif not state:
                        state = s
                        flow_data = data
                        log.info(f"找到匹配的项目ID: {s}")

        # 如果没有指定项目ID或没找到匹配的，查找需要自动检测项目ID的流程
        if not state:
            log.info("没有找到指定项目的流程，查找自动检测流程")
            # 首先尝试找到已完成的流程（有授权码的）
            completed_flows = []
            for s, data in auth_flows.items():
                if data.get("auto_project_detection", False):
                    if user_session and data.get("user_session") == user_session:
                        if data.get("code"):  # 优先选择已完成的
                            completed_flows.append((s, data, data.get("created_at", 0)))

            # 如果有已完成的流程，选择最新的
            if completed_flows:
                completed_flows.sort(key=lambda x: x[2], reverse=True)  # 按时间倒序
                state, flow_data, _ = completed_flows[0]
                log.info(f"找到已完成的最新认证流程: {state}")
            else:
                # 如果没有已完成的，找最新的未完成流程
                pending_flows = []
                for s, data in auth_flows.items():
                    if data.get("auto_project_detection", False):
                        if user_session and data.get("user_session") == user_session:
                            pending_flows.append((s, data, data.get("created_at", 0)))
                        elif not user_session:
                            pending_flows.append((s, data, data.get("created_at", 0)))

                if pending_flows:
                    pending_flows.sort(key=lambda x: x[2], reverse=True)  # 按时间倒序
                    state, flow_data, _ = pending_flows[0]
                    log.info(f"找到最新的待完成认证流程: {state}")

        if not state or not flow_data:
            log.error(f"未找到认证流程: state={state}, flow_data存在={bool(flow_data)}")
            log.debug(f"当前所有flow_data: {list(auth_flows.keys())}")
            return {"success": False, "error": "未找到对应的认证流程，请先点击获取认证链接"}

        log.info(f"找到认证流程: state={state}")
        log.info(
            f"flow_data内容: project_id={flow_data.get('project_id')}, auto_project_detection={flow_data.get('auto_project_detection')}"
        )
        log.info(f"传入的project_id参数: {project_id}")

        # 如果需要自动检测项目ID且没有提供项目ID
        log.info(
            f"检查auto_project_detection条件: auto_project_detection={flow_data.get('auto_project_detection', False)}, not project_id={not project_id}"
        )
        if flow_data.get("auto_project_detection", False) and not project_id:
            log.info("跳过自动检测项目ID，进入等待阶段")
        elif not project_id:
            log.info("进入project_id检查分支")
            project_id = flow_data.get("project_id")
            if not project_id:
                log.error("缺少项目ID，返回错误")
                return {
                    "success": False,
                    "error": "缺少项目ID，请指定项目ID",
                    "requires_manual_project_id": True,
                }
        else:
            log.info(f"使用提供的项目ID: {project_id}")

        # 检查是否已经有授权码
        log.info("开始检查OAuth授权码...")
        log.info(f"等待state={state}的授权回调，回调端口: {flow_data.get('callback_port')}")
        log.info(f"当前flow_data状态: completed={flow_data.get('completed')}, code存在={bool(flow_data.get('code'))}")
        max_wait_time = 60  # 最多等待60秒
        wait_interval = 1  # 每秒检查一次
        waited = 0

        while waited < max_wait_time:
            if flow_data.get("code"):
                log.info(f"检测到OAuth授权码，开始处理凭证 (等待时间: {waited}秒)")
                break

            # 每5秒输出一次提示
            if waited % 5 == 0 and waited > 0:
                log.info(f"仍在等待OAuth授权... ({waited}/{max_wait_time}秒)")
                log.debug(f"当前state: {state}, flow_data keys: {list(flow_data.keys())}")

            # 异步等待
            await asyncio.sleep(wait_interval)
            waited += wait_interval

            # 刷新flow_data引用，因为可能被回调更新了
            if state in auth_flows:
                flow_data = auth_flows[state]

        if not flow_data.get("code"):
            log.error(f"等待OAuth回调超时，等待了{waited}秒")
            return {
                "success": False,
                "error": "等待OAuth回调超时，请确保完成了浏览器中的认证并看到成功页面",
            }

        flow = flow_data["flow"]
        auth_code = flow_data["code"]

        log.info(f"开始使用授权码获取凭证: code={'***' + auth_code[-4:] if auth_code else 'None'}")

        # 使用认证代码获取凭证
        with _OAuthLibPatcher():
            try:
                log.info("调用flow.exchange_code...")
                credentials = await flow.exchange_code(auth_code)
                log.info(
                    f"成功获取凭证，token前缀: {credentials.access_token[:20] if credentials.access_token else 'None'}..."
                )

                log.info(
                    f"检查是否需要项目检测: auto_project_detection={flow_data.get('auto_project_detection')}, project_id={project_id}"
                )

                # 检查是否为antigravity模式
                is_antigravity = flow_data.get("use_antigravity", False) or use_antigravity
                if is_antigravity:
                    log.info("Antigravity模式：从API获取project_id...")
                    # 使用API获取project_id
                    antigravity_url = await get_antigravity_api_url()
                    project_id = await fetch_project_id(
                        credentials.access_token,
                        ANTIGRAVITY_USER_AGENT,
                        antigravity_url
                    )
                    if project_id:
                        log.info(f"成功从API获取project_id: {project_id}")
                    else:
                        log.warning("无法从API获取project_id，回退到随机生成")
                        project_id = _generate_random_project_id()
                        log.info(f"生成的随机project_id: {project_id}")

                    # 保存antigravity凭证
                    saved_filename = await save_credentials(credentials, project_id, is_antigravity=True)

                    # 准备返回的凭证数据
                    creds_data = _prepare_credentials_data(credentials, project_id, is_antigravity=True)

                    # 清理使用过的流程
                    _cleanup_auth_flow_server(state)

                    log.info("Antigravity OAuth认证成功，凭证已保存")
                    return {
                        "success": True,
                        "credentials": creds_data,
                        "file_path": saved_filename,
                        "auto_detected_project": False,
                        "is_antigravity": True,
                    }

                # 如果需要自动检测项目ID且没有提供项目ID（标准模式）
                if flow_data.get("auto_project_detection", False) and not project_id:
                    log.info("标准模式：从API获取project_id...")
                    # 使用API获取project_id（使用标准模式的User-Agent）
                    code_assist_url = await get_code_assist_endpoint()
                    project_id = await fetch_project_id(
                        credentials.access_token,
                        STANDARD_USER_AGENT,
                        code_assist_url
                    )
                    if project_id:
                        flow_data["project_id"] = project_id
                        log.info(f"成功从API获取project_id: {project_id}")
                        # 自动启用必需的API服务
                        log.info("正在自动启用必需的API服务...")
                        await enable_required_apis(credentials, project_id)
                    else:
                        log.warning("无法从API获取project_id，回退到项目列表获取方式")
                        # 回退到原来的项目列表获取方式
                        user_projects = await get_user_projects(credentials)

                        if user_projects:
                            # 如果只有一个项目，自动使用
                            if len(user_projects) == 1:
                                # Google API returns projectId in camelCase
                                project_id = user_projects[0].get("projectId")
                                if project_id:
                                    flow_data["project_id"] = project_id
                                    log.info(f"自动选择唯一项目: {project_id}")
                                    # 自动启用必需的API服务
                                    log.info("正在自动启用必需的API服务...")
                                    await enable_required_apis(credentials, project_id)
                            # 如果有多个项目，尝试选择默认项目
                            else:
                                project_id = await select_default_project(user_projects)
                                if project_id:
                                    flow_data["project_id"] = project_id
                                    log.info(f"自动选择默认项目: {project_id}")
                                    # 自动启用必需的API服务
                                    log.info("正在自动启用必需的API服务...")
                                    await enable_required_apis(credentials, project_id)
                                else:
                                    # 返回项目列表让用户选择
                                    return {
                                        "success": False,
                                        "error": "请从以下项目中选择一个",
                                        "requires_project_selection": True,
                                        "available_projects": [
                                            {
                                                # Google API returns projectId in camelCase
                                                "project_id": p.get("projectId"),
                                                "name": p.get("displayName") or p.get("projectId"),
                                                "projectNumber": p.get("projectNumber"),
                                            }
                                            for p in user_projects
                                        ],
                                    }
                        else:
                            # 如果无法获取项目列表，提示手动输入
                            return {
                                "success": False,
                                "error": "无法获取您的项目列表，请手动指定项目ID",
                                "requires_manual_project_id": True,
                            }
                elif project_id:
                    # 如果已经有项目ID（手动提供或环境检测），也尝试启用API服务
                    log.info("正在为已提供的项目ID自动启用必需的API服务...")
                    await enable_required_apis(credentials, project_id)

                # 如果仍然没有项目ID，返回错误
                if not project_id:
                    return {
                        "success": False,
                        "error": "缺少项目ID，请指定项目ID",
                        "requires_manual_project_id": True,
                    }

                # 保存凭证
                saved_filename = await save_credentials(credentials, project_id)

                # 准备返回的凭证数据
                creds_data = _prepare_credentials_data(credentials, project_id, is_antigravity=False)

                # 清理使用过的流程
                _cleanup_auth_flow_server(state)

                log.info("OAuth认证成功，凭证已保存")
                return {
                    "success": True,
                    "credentials": creds_data,
                    "file_path": saved_filename,
                    "auto_detected_project": flow_data.get("auto_project_detection", False),
                }

            except Exception as e:
                log.error(f"获取凭证失败: {e}")
                return {"success": False, "error": f"获取凭证失败: {str(e)}"}

    except Exception as e:
        log.error(f"异步完成认证流程失败: {e}")
        return {"success": False, "error": str(e)}


async def complete_auth_flow_from_callback_url(
    callback_url: str, project_id: Optional[str] = None, use_antigravity: bool = False
) -> Dict[str, Any]:
    """从回调URL直接完成认证流程，无需启动本地服务器"""
    try:
        log.info(f"开始从回调URL完成认证: {callback_url}")

        # 解析回调URL
        parsed_url = urlparse(callback_url)
        query_params = parse_qs(parsed_url.query)

        # 验证必要参数
        if "state" not in query_params or "code" not in query_params:
            return {"success": False, "error": "回调URL缺少必要参数 (state 或 code)"}

        state = query_params["state"][0]
        code = query_params["code"][0]

        log.info(f"从URL解析到: state={state}, code=xxx...")

        # 检查是否有对应的认证流程
        if state not in auth_flows:
            return {
                "success": False,
                "error": f"未找到对应的认证流程，请先启动认证 (state: {state})",
            }

        flow_data = auth_flows[state]
        flow = flow_data["flow"]

        # 构造回调URL（使用flow中存储的redirect_uri）
        redirect_uri = flow.redirect_uri
        log.info(f"使用redirect_uri: {redirect_uri}")

        try:
            # 使用authorization code获取token
            credentials = await flow.exchange_code(code)
            log.info("成功获取访问令牌")

            # 检查是否为antigravity模式
            is_antigravity = flow_data.get("use_antigravity", False) or use_antigravity
            if is_antigravity:
                log.info("Antigravity模式（从回调URL）：从API获取project_id...")
                # 使用API获取project_id
                antigravity_url = await get_antigravity_api_url()
                project_id = await fetch_project_id(
                    credentials.access_token,
                    ANTIGRAVITY_USER_AGENT,
                    antigravity_url
                )
                if project_id:
                    log.info(f"成功从API获取project_id: {project_id}")
                else:
                    log.warning("无法从API获取project_id，回退到随机生成")
                    project_id = _generate_random_project_id()
                    log.info(f"生成的随机project_id: {project_id}")

                # 保存antigravity凭证
                saved_filename = await save_credentials(credentials, project_id, is_antigravity=True)

                # 准备返回的凭证数据
                creds_data = _prepare_credentials_data(credentials, project_id, is_antigravity=True)

                # 清理使用过的流程
                _cleanup_auth_flow_server(state)

                log.info("从回调URL完成Antigravity OAuth认证成功，凭证已保存")
                return {
                    "success": True,
                    "credentials": creds_data,
                    "file_path": saved_filename,
                    "auto_detected_project": False,
                    "is_antigravity": True,
                }

            # 标准模式的项目ID处理逻辑
            detected_project_id = None
            auto_detected = False

            if not project_id:
                # 尝试使用fetch_project_id自动获取项目ID
                try:
                    log.info("标准模式：从API获取project_id...")
                    code_assist_url = await get_code_assist_endpoint()
                    detected_project_id = await fetch_project_id(
                        credentials.access_token,
                        STANDARD_USER_AGENT,
                        code_assist_url
                    )
                    if detected_project_id:
                        auto_detected = True
                        log.info(f"成功从API获取project_id: {detected_project_id}")
                    else:
                        log.warning("无法从API获取project_id，回退到项目列表获取方式")
                        # 回退到原来的项目列表获取方式
                        projects = await get_user_projects(credentials)
                        if projects:
                            if len(projects) == 1:
                                # 只有一个项目，自动使用
                                # Google API returns projectId in camelCase
                                detected_project_id = projects[0]["projectId"]
                                auto_detected = True
                                log.info(f"自动检测到唯一项目ID: {detected_project_id}")
                            else:
                                # 多个项目，自动选择第一个
                                # Google API returns projectId in camelCase
                                detected_project_id = projects[0]["projectId"]
                                auto_detected = True
                                log.info(
                                    f"检测到{len(projects)}个项目，自动选择第一个: {detected_project_id}"
                                )
                                log.debug(f"其他可用项目: {[p['projectId'] for p in projects[1:]]}")
                        else:
                            # 没有项目访问权限
                            return {
                                "success": False,
                                "error": "未检测到可访问的项目，请检查权限或手动指定项目ID",
                                "requires_manual_project_id": True,
                            }
                except Exception as e:
                    log.warning(f"自动检测项目ID失败: {e}")
                    return {
                        "success": False,
                        "error": f"自动检测项目ID失败: {str(e)}，请手动指定项目ID",
                        "requires_manual_project_id": True,
                    }
            else:
                detected_project_id = project_id

            # 启用必需的API服务
            if detected_project_id:
                try:
                    log.info(f"正在为项目 {detected_project_id} 启用必需的API服务...")
                    await enable_required_apis(credentials, detected_project_id)
                except Exception as e:
                    log.warning(f"启用API服务失败: {e}")

            # 保存凭证
            saved_filename = await save_credentials(credentials, detected_project_id)

            # 准备返回的凭证数据
            creds_data = _prepare_credentials_data(credentials, detected_project_id, is_antigravity=False)

            # 清理使用过的流程
            _cleanup_auth_flow_server(state)

            log.info("从回调URL完成OAuth认证成功，凭证已保存")
            return {
                "success": True,
                "credentials": creds_data,
                "file_path": saved_filename,
                "auto_detected_project": auto_detected,
            }

        except Exception as e:
            log.error(f"从回调URL获取凭证失败: {e}")
            return {"success": False, "error": f"获取凭证失败: {str(e)}"}

    except Exception as e:
        log.error(f"从回调URL完成认证流程失败: {e}")
        return {"success": False, "error": str(e)}


async def save_credentials(creds: Credentials, project_id: str, is_antigravity: bool = False) -> str:
    """通过统一存储系统保存凭证"""
    # 生成文件名（使用project_id和时间戳）
    timestamp = int(time.time())

    # antigravity模式使用特殊前缀
    if is_antigravity:
        filename = f"ag_{project_id}-{timestamp}.json"
    else:
        filename = f"{project_id}-{timestamp}.json"

    # 准备凭证数据
    creds_data = _prepare_credentials_data(creds, project_id, is_antigravity)

    # 通过存储适配器保存
    storage_adapter = await get_storage_adapter()
    success = await storage_adapter.store_credential(filename, creds_data, is_antigravity=is_antigravity)

    if success:
        # 创建默认状态记录
        try:
            default_state = {
                "error_codes": [],
                "disabled": False,
                "last_success": time.time(),
                "user_email": None,
            }
            await storage_adapter.update_credential_state(filename, default_state, is_antigravity=is_antigravity)
            log.info(f"凭证和状态已保存到: {filename} (antigravity={is_antigravity})")
        except Exception as e:
            log.warning(f"创建默认状态记录失败 {filename}: {e}")

        return filename
    else:
        raise Exception(f"保存凭证失败: {filename}")


def async_shutdown_server(server, port):
    """异步关闭OAuth回调服务器，避免阻塞主流程"""

    def shutdown_server_async():
        try:
            # 设置一个标志来跟踪关闭状态
            shutdown_completed = threading.Event()

            def do_shutdown():
                try:
                    server.shutdown()
                    server.server_close()
                    shutdown_completed.set()
                    log.info(f"已关闭端口 {port} 的OAuth回调服务器")
                except Exception as e:
                    shutdown_completed.set()
                    log.debug(f"关闭服务器时出错: {e}")

            # 在单独线程中执行关闭操作
            shutdown_worker = threading.Thread(target=do_shutdown, daemon=True)
            shutdown_worker.start()

            # 等待最多5秒，如果超时就放弃等待
            if shutdown_completed.wait(timeout=5):
                log.debug(f"端口 {port} 服务器关闭完成")
            else:
                log.warning(f"端口 {port} 服务器关闭超时，但不阻塞主流程")

        except Exception as e:
            log.debug(f"异步关闭服务器时出错: {e}")

    # 在后台线程中关闭服务器，不阻塞主流程
    shutdown_thread = threading.Thread(target=shutdown_server_async, daemon=True)
    shutdown_thread.start()
    log.debug(f"开始异步关闭端口 {port} 的OAuth回调服务器")


def cleanup_expired_flows():
    """清理过期的认证流程"""
    current_time = time.time()
    EXPIRY_TIME = 600  # 10分钟过期

    # 直接遍历删除，避免创建额外列表
    states_to_remove = [
        state
        for state, flow_data in auth_flows.items()
        if current_time - flow_data["created_at"] > EXPIRY_TIME
    ]

    # 批量清理，提高效率
    cleaned_count = 0
    for state in states_to_remove:
        flow_data = auth_flows.get(state)
        if flow_data:
            # 快速关闭可能存在的服务器
            try:
                if flow_data.get("server"):
                    server = flow_data["server"]
                    port = flow_data.get("callback_port")
                    async_shutdown_server(server, port)
            except Exception as e:
                log.debug(f"清理过期流程时启动异步关闭服务器失败: {e}")

            # 显式清理流程数据，释放内存
            flow_data.clear()
            del auth_flows[state]
            cleaned_count += 1

    if cleaned_count > 0:
        log.info(f"清理了 {cleaned_count} 个过期的认证流程")

    # 更积极的垃圾回收触发条件
    if len(auth_flows) > 20:  # 降低阈值
        import gc

        gc.collect()
        log.debug(f"触发垃圾回收，当前活跃认证流程数: {len(auth_flows)}")


def get_auth_status(project_id: str) -> Dict[str, Any]:
    """获取认证状态"""
    for state, flow_data in auth_flows.items():
        if flow_data["project_id"] == project_id:
            return {
                "status": "completed" if flow_data["completed"] else "pending",
                "state": state,
                "created_at": flow_data["created_at"],
            }

    return {"status": "not_found"}


# 鉴权功能 - 使用更小的数据结构
auth_tokens = {}  # 存储有效的认证令牌
TOKEN_EXPIRY = 3600  # 1小时令牌过期时间


async def verify_password(password: str) -> bool:
    """验证密码（面板登录使用）"""
    from config import get_panel_password

    correct_password = await get_panel_password()
    return password == correct_password


def generate_auth_token() -> str:
    """生成认证令牌"""
    # 清理过期令牌
    cleanup_expired_tokens()

    token = secrets.token_urlsafe(32)
    # 只存储创建时间
    auth_tokens[token] = time.time()
    return token


def verify_auth_token(token: str) -> bool:
    """验证认证令牌"""
    if not token or token not in auth_tokens:
        return False

    created_at = auth_tokens[token]

    # 检查令牌是否过期 (使用更短的过期时间)
    if time.time() - created_at > TOKEN_EXPIRY:
        del auth_tokens[token]
        return False

    return True


def cleanup_expired_tokens():
    """清理过期的认证令牌"""
    current_time = time.time()
    expired_tokens = [
        token
        for token, created_at in auth_tokens.items()
        if current_time - created_at > TOKEN_EXPIRY
    ]

    for token in expired_tokens:
        del auth_tokens[token]

    if expired_tokens:
        log.debug(f"清理了 {len(expired_tokens)} 个过期的认证令牌")


def invalidate_auth_token(token: str):
    """使认证令牌失效"""
    if token in auth_tokens:
        del auth_tokens[token]


# 文件验证和处理功能 - 使用统一存储系统
def validate_credential_content(content: str) -> Dict[str, Any]:
    """验证凭证内容格式"""
    try:
        creds_data = json.loads(content)

        # 检查必要字段
        required_fields = ["client_id", "client_secret", "refresh_token", "token_uri"]
        missing_fields = [field for field in required_fields if field not in creds_data]

        if missing_fields:
            return {"valid": False, "error": f'缺少必要字段: {", ".join(missing_fields)}'}

        # 检查project_id
        if "project_id" not in creds_data:
            log.warning("认证文件缺少project_id字段")

        return {"valid": True, "data": creds_data}

    except json.JSONDecodeError as e:
        return {"valid": False, "error": f"JSON格式错误: {str(e)}"}
    except Exception as e:
        return {"valid": False, "error": f"文件验证失败: {str(e)}"}


async def save_uploaded_credential(content: str, original_filename: str) -> Dict[str, Any]:
    """通过统一存储系统保存上传的凭证"""
    try:
        # 验证内容格式
        validation = validate_credential_content(content)
        if not validation["valid"]:
            return {"success": False, "error": validation["error"]}

        creds_data = validation["data"]

        # 生成文件名
        project_id = creds_data.get("project_id", "unknown")
        timestamp = int(time.time())

        # 从原文件名中提取有用信息
        import os

        base_name = os.path.splitext(original_filename)[0]
        filename = f"{base_name}-{timestamp}.json"

        # 通过存储适配器保存
        storage_adapter = await get_storage_adapter()
        success = await storage_adapter.store_credential(filename, creds_data)

        if success:
            log.info(f"凭证文件已上传保存: {filename}")
            return {"success": True, "file_path": filename, "project_id": project_id}
        else:
            return {"success": False, "error": "保存到存储系统失败"}

    except Exception as e:
        log.error(f"保存上传文件失败: {e}")
        return {"success": False, "error": str(e)}


async def batch_upload_credentials(files_data: List[Dict[str, str]]) -> Dict[str, Any]:
    """批量上传凭证文件到统一存储系统"""
    results = []
    success_count = 0

    for file_data in files_data:
        filename = file_data.get("filename", "unknown.json")
        content = file_data.get("content", "")

        result = await save_uploaded_credential(content, filename)
        result["filename"] = filename
        results.append(result)

        if result["success"]:
            success_count += 1

    return {"uploaded_count": success_count, "total_count": len(files_data), "results": results}

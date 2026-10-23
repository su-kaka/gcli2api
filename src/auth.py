"""
认证API模块（仅支持 Antigravity 凭证模式）
"""

import asyncio
import socket
import threading
import time
import uuid
from datetime import timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from config import get_config_value, get_antigravity_api_url
from log import log

from .google_oauth_api import (
    Credentials,
    Flow,
    fetch_project_id_and_tier,
)
from .storage.database import get_database
from .utils import (
    ANTIGRAVITY_CLIENT_ID,
    ANTIGRAVITY_CLIENT_SECRET,
    ANTIGRAVITY_SCOPES,
    ANTIGRAVITY_USER_AGENT,
    CALLBACK_HOST,
    TOKEN_URL,
)


async def get_callback_port():
    """获取OAuth回调端口"""
    return int(await get_config_value("oauth_callback_port", "11451", "OAUTH_CALLBACK_PORT"))


def _prepare_credentials_data(credentials: Credentials, project_id: str, subscription_tier: str = None) -> Dict[str, Any]:
    """准备凭证数据字典（Antigravity模式）"""
    creds_data = {
        "client_id": ANTIGRAVITY_CLIENT_ID,
        "client_secret": ANTIGRAVITY_CLIENT_SECRET,
        "token": credentials.access_token,
        "refresh_token": credentials.refresh_token,
        "project_id": project_id,
    }

    if credentials.expires_at:
        if credentials.expires_at.tzinfo is None:
            expiry_utc = credentials.expires_at.replace(tzinfo=timezone.utc)
        else:
            expiry_utc = credentials.expires_at
        creds_data["expiry"] = expiry_utc.isoformat()

    return creds_data


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
DEFAULT_PROJECT_ID = "gemini-pro-1751713012-07fc4dfd"


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

            cleaned_count += 1

    if cleaned_count > 0:
        for state in states_to_remove:
            auth_flows.pop(state, None)
        log.info(f"清理了 {cleaned_count} 个过期的认证流程")


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
    project_id: Optional[str] = None, user_session: str = None
) -> Dict[str, Any]:
    """创建认证URL，支持动态端口分配（Antigravity模式）"""
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

        # 创建OAuth流程（Antigravity配置）
        flow = Flow(
            client_id=ANTIGRAVITY_CLIENT_ID,
            client_secret=ANTIGRAVITY_CLIENT_SECRET,
            scopes=ANTIGRAVITY_SCOPES,
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


async def _save_antigravity_credential(
    credentials: Credentials,
    state: str,
    flow_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Antigravity模式：自动获取 email / project_id / tier 并保存凭证（email 为唯一键）

    Returns:
        认证成功的结果字典
    """
    from .google_oauth_api import get_user_email

    # 自动获取邮箱
    email = None
    try:
        email = await get_user_email(credentials)
    except Exception as e:
        log.warning(f"自动获取邮箱失败: {e}")

    if not email:
        _cleanup_auth_flow_server(state)
        log.error("Antigravity OAuth认证失败：无法获取邮箱")
        return {"success": False, "error": "无法获取账户邮箱，认证失败"}

    # 自动获取 project_id / tier
    log.info("Antigravity模式：从API获取project_id/tier...")
    antigravity_url = await get_antigravity_api_url()
    project_id, subscription_tier = await fetch_project_id_and_tier(
        credentials.access_token,
        ANTIGRAVITY_USER_AGENT,
        antigravity_url
    )
    if project_id:
        log.info(f"成功从API获取project_id: {project_id}, tier: {subscription_tier}")
    else:
        project_id = DEFAULT_PROJECT_ID
        log.warning(f"无法从API获取project_id，使用默认project_id: {project_id}")

    # 保存凭证（email 为唯一键，同邮箱自动覆盖去重）
    database = await get_database()
    creds_data = _prepare_credentials_data(
        credentials, project_id, subscription_tier=subscription_tier
    )
    creds_data["tier"] = subscription_tier
    await database.store_credential(email, creds_data)

    # 创建默认状态记录
    try:
        default_state = {
            "error_codes": [],
            "disabled": False,
            "last_success": time.time(),
            "tier": subscription_tier,
            "project_id": project_id,
        }
        await database.update_credential_state(email, default_state)
        log.info(f"凭证已保存: {email} (tier={subscription_tier})")
    except Exception as e:
        log.warning(f"创建默认状态记录失败 {email}: {e}")

    # 清理使用过的流程
    _cleanup_auth_flow_server(state)

    log.info("Antigravity OAuth认证成功，凭证已保存")
    return {
        "success": True,
        "credentials": creds_data,
        "email": email,
        "tier": subscription_tier,
        "auto_detected_project": bool(project_id),    }


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


async def asyncio_complete_auth_flow(
    project_id: Optional[str] = None, user_session: str = None
) -> Dict[str, Any]:
    """异步完成认证流程（Antigravity模式）"""
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

        # 检查是否已经有授权码
        log.info("开始检查OAuth授权码...")
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

                # Antigravity模式：获取project_id并保存凭证
                return await _save_antigravity_credential(credentials, state, flow_data)

            except Exception as e:
                log.error(f"获取凭证失败: {e}")
                return {"success": False, "error": f"获取凭证失败: {str(e)}"}

    except Exception as e:
        log.error(f"异步完成认证流程失败: {e}")
        return {"success": False, "error": str(e)}


async def complete_auth_flow_from_callback_url(
    callback_url: str, project_id: Optional[str] = None
) -> Dict[str, Any]:
    """从回调URL直接完成认证流程，无需启动本地服务器（Antigravity模式）"""
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

        log.info(f"使用redirect_uri: {flow.redirect_uri}")

        try:
            # 使用authorization code获取token
            credentials = await flow.exchange_code(code)
            log.info("成功获取访问令牌")

            # Antigravity模式：获取project_id并保存凭证
            return await _save_antigravity_credential(credentials, state, flow_data)

        except Exception as e:
            log.error(f"从回调URL获取凭证失败: {e}")
            return {"success": False, "error": f"获取凭证失败: {str(e)}"}

    except Exception as e:
        log.error(f"从回调URL完成认证流程失败: {e}")
        return {"success": False, "error": str(e)}


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
                except Exception:
                    pass
                finally:
                    shutdown_completed.set()

            # 在单独的线程中执行shutdown，避免死锁
            shutdown_thread = threading.Thread(target=do_shutdown, daemon=True)
            shutdown_thread.start()

            # 最多等待2秒
            shutdown_completed.wait(timeout=2.0)

            # 如果没有完成，尝试强制关闭socket
            if not shutdown_completed.is_set():
                try:
                    server.socket.close()
                except Exception:
                    pass

            log.info(f"OAuth回调服务器已关闭 (端口: {port})")

        except Exception as e:
            log.debug(f"关闭服务器时出错: {e}")

    # 在新线程中执行关闭操作
    shutdown_thread = threading.Thread(target=shutdown_server_async, daemon=True)
    shutdown_thread.start()


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


async def verify_password(password: str) -> bool:
    """验证密码（面板登录使用）"""
    from config import get_panel_password

    correct_password = await get_panel_password()
    return password == correct_password

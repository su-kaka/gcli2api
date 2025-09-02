"""
认证API模块 - 处理OAuth认证流程和批量上传
"""
import asyncio
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import uuid
from datetime import timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from config import CREDENTIALS_DIR, get_config_value
from log import log
from .memory_manager import register_memory_cleanup

# OAuth Configuration
CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
CLIENT_SECRET = "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"
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# 回调服务器配置
CALLBACK_HOST = 'localhost'
DEFAULT_CALLBACK_PORT = int(get_config_value('oauth_callback_port', '8080', 'OAUTH_CALLBACK_PORT'))

# 全局状态管理
auth_flows = {}  # 存储进行中的认证流程

def cleanup_auth_flows_for_memory():
    """清理认证流程以释放内存"""
    global auth_flows
    cleaned = cleanup_expired_flows()
    # 如果还是太多，强制清理一些旧的流程
    if len(auth_flows) > 10:
        current_time = time.time()
        # 按创建时间排序，保留最新的10个
        sorted_flows = sorted(auth_flows.items(), key=lambda x: x[1].get('created_at', 0), reverse=True)
        new_auth_flows = dict(sorted_flows[:10])
        
        # 清理被移除的流程
        for state, flow_data in auth_flows.items():
            if state not in new_auth_flows:
                try:
                    if flow_data.get('server'):
                        server = flow_data['server']
                        port = flow_data.get('callback_port')
                        async_shutdown_server(server, port)
                except Exception:
                    pass
                flow_data.clear()
        
        auth_flows = new_auth_flows
        log.info(f"强制清理认证流程，保留 {len(auth_flows)} 个最新流程")
    
    return len(auth_flows)

# 注册内存清理函数
register_memory_cleanup("auth_flows", cleanup_auth_flows_for_memory)

def find_available_port(start_port: int = None) -> int:
    """动态查找可用端口"""
    if start_port is None:
        start_port = DEFAULT_CALLBACK_PORT
    
    # 首先尝试默认端口
    for port in range(start_port, start_port + 100):  # 尝试100个端口
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('0.0.0.0', port))
                log.info(f"找到可用端口: {port}")
                return port
        except OSError:
            continue
    
    # 如果都不可用，让系统自动分配端口
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('0.0.0.0', 0))
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
            auth_flows[state]['code'] = code
            auth_flows[state]['completed'] = True
            
            log.info(f"OAuth回调成功处理: state={state}")
            
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            # 成功页面
            self.wfile.write(b"<h1>OAuth authentication successful!</h1><p>You can close this window. Please return to the original page and click 'Get Credentials' button.</p>")
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authentication failed.</h1><p>Please try again.</p>")
    
    def log_message(self, format, *args):
        # 减少日志噪音
        pass


async def enable_required_apis(credentials: Credentials, project_id: str) -> bool:
    """自动启用必需的API服务"""
    try:
        # 确保凭证有效
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(GoogleAuthRequest())
        
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
            "User-Agent": "geminicli-oauth/1.0",
        }
        
        # 需要启用的服务列表
        required_services = [
            "geminicloudassist.googleapis.com",  # Gemini Cloud Assist API
            "cloudaicompanion.googleapis.com"    # Gemini for Google Cloud API
        ]
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for service in required_services:
                log.info(f"正在检查并启用服务: {service}")
                
                # 检查服务是否已启用
                check_url = f"https://serviceusage.googleapis.com/v1/projects/{project_id}/services/{service}"
                try:
                    check_response = await client.get(check_url, headers=headers)
                    if check_response.status_code == 200:
                        service_data = check_response.json()
                        if service_data.get("state") == "ENABLED":
                            log.info(f"服务 {service} 已启用")
                            continue
                except Exception as e:
                    log.debug(f"检查服务状态失败，将尝试启用: {e}")
                
                # 启用服务
                enable_url = f"https://serviceusage.googleapis.com/v1/projects/{project_id}/services/{service}:enable"
                try:
                    enable_response = await client.post(enable_url, headers=headers, json={})
                    
                    if enable_response.status_code in [200, 201]:
                        log.info(f"✅ 成功启用服务: {service}")
                    elif enable_response.status_code == 400:
                        error_data = enable_response.json()
                        if "already enabled" in error_data.get("error", {}).get("message", "").lower():
                            log.info(f"✅ 服务 {service} 已经启用")
                        else:
                            log.warning(f"⚠️ 启用服务 {service} 时出现警告: {error_data}")
                    else:
                        log.warning(f"⚠️ 启用服务 {service} 失败: {enable_response.status_code} - {enable_response.text}")
                        
                except Exception as e:
                    log.warning(f"⚠️ 启用服务 {service} 时发生异常: {e}")
                    
        return True
        
    except Exception as e:
        log.error(f"启用API服务时发生错误: {e}")
        return False


async def get_user_projects(credentials: Credentials) -> List[Dict[str, Any]]:
    """获取用户可访问的Google Cloud项目列表"""
    try:
        # 确保凭证有效
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(GoogleAuthRequest())
        
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "User-Agent": "geminicli-oauth/1.0",
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 使用v3 API的projects:search端点
            url = "https://cloudresourcemanager.googleapis.com/v3/projects:search"
            log.info(f"正在调用API: {url}")
            response = await client.get(url, headers=headers)
            
            log.info(f"API响应状态码: {response.status_code}")
            if response.status_code != 200:
                log.error(f"API响应内容: {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                projects = data.get('projects', [])
                # 只返回活跃的项目
                active_projects = [
                    project for project in projects 
                    if project.get('state') == 'ACTIVE'
                ]
                log.info(f"获取到 {len(active_projects)} 个活跃项目")
                return active_projects
            elif response.status_code == 403:
                log.warning(f"没有权限访问项目列表: {response.text}")
                # 尝试用户信息API来获取一些线索
                try:
                    userinfo_response = await client.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers=headers
                    )
                    if userinfo_response.status_code == 200:
                        userinfo = userinfo_response.json()
                        log.info(f"获取到用户信息: {userinfo.get('email')}")
                except:
                    pass
                return []
            else:
                log.warning(f"获取项目列表失败: {response.status_code} - {response.text}")
                return []
                
    except Exception as e:
        log.error(f"获取用户项目列表失败: {e}")
        return []


async def select_default_project(projects: List[Dict[str, Any]]) -> Optional[str]:
    """从项目列表中选择默认项目"""
    if not projects:
        return None
    
    # 策略1：查找显示名称或项目ID包含"default"的项目
    for project in projects:
        display_name = project.get('displayName', '').lower()
        project_id = project.get('projectId', '')
        if 'default' in display_name or 'default' in project_id.lower():
            log.info(f"选择默认项目: {project_id} ({project.get('displayName', project_id)})")
            return project_id
    
    # 策略2：选择第一个项目
    first_project = projects[0]
    project_id = first_project.get('projectId', '')
    log.info(f"选择第一个项目作为默认: {project_id} ({first_project.get('displayName', project_id)})")
    return project_id


async def auto_detect_project_id() -> Optional[str]:
    """尝试从Google Cloud环境自动检测项目ID"""
    try:
        # 尝试从Google Cloud Metadata服务获取项目ID
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                "http://metadata.google.internal/computeMetadata/v1/project/project-id",
                headers={"Metadata-Flavor": "Google"}
            )
            if response.status_code == 200:
                project_id = response.text.strip()
                log.info(f"从Google Cloud Metadata自动检测到项目ID: {project_id}")
                return project_id
    except Exception as e:
        log.debug(f"无法从Metadata服务获取项目ID: {e}")
    
    # 尝试从gcloud配置获取默认项目
    try:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project"], 
            capture_output=True, 
            text=True, 
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            project_id = result.stdout.strip()
            if project_id != "(unset)":
                log.info(f"从gcloud配置自动检测到项目ID: {project_id}")
                return project_id
    except Exception as e:
        log.debug(f"无法从gcloud配置获取项目ID: {e}")
    
    log.info("无法自动检测项目ID，将需要用户手动输入")
    return None


def create_auth_url(project_id: Optional[str] = None, user_session: str = None) -> Dict[str, Any]:
    """创建认证URL，支持动态端口分配"""
    try:
        # 动态分配端口
        callback_port = find_available_port()
        callback_url = f"http://{CALLBACK_HOST}:{callback_port}"
        
        # 立即启动回调服务器
        try:
            callback_server = create_callback_server(callback_port)
            # 在后台线程中运行服务器
            server_thread = threading.Thread(
                target=callback_server.serve_forever, 
                daemon=True,
                name=f"OAuth-Server-{callback_port}"
            )
            server_thread.start()
            log.info(f"OAuth回调服务器已启动，端口: {callback_port}")
        except Exception as e:
            log.error(f"启动回调服务器失败: {e}")
            return {
                'success': False,
                'error': f'无法启动OAuth回调服务器，端口{callback_port}: {str(e)}'
            }
        
        # 创建OAuth流程
        client_config = {
            "installed": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=callback_url
        )
        
        flow.oauth2session.scope = SCOPES
        
        # 生成状态标识符，包含用户会话信息
        if user_session:
            state = f"{user_session}_{str(uuid.uuid4())}"
        else:
            state = str(uuid.uuid4())
        
        # 生成认证URL
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes='true',
            state=state
        )
        
        # 保存流程状态
        auth_flows[state] = {
            'flow': flow,
            'project_id': project_id,  # 可能为None，稍后在回调时确定
            'user_session': user_session,
            'callback_port': callback_port,  # 存储分配的端口
            'callback_url': callback_url,   # 存储完整回调URL
            'server': callback_server,  # 存储服务器实例
            'server_thread': server_thread,  # 存储服务器线程
            'code': None,
            'completed': False,
            'created_at': time.time(),
            'auto_project_detection': project_id is None  # 标记是否需要自动检测项目ID
        }
        
        # 清理过期的流程（30分钟）
        cleanup_expired_flows()
        
        log.info(f"OAuth流程已创建: state={state}, project_id={project_id}")
        log.info(f"用户需要访问认证URL，然后OAuth会回调到 {callback_url}")
        log.info(f"为此认证流程分配的端口: {callback_port}")
        
        return {
            'auth_url': auth_url,
            'state': state,
            'callback_port': callback_port,
            'success': True,
            'auto_project_detection': project_id is None,
            'detected_project_id': project_id
        }
        
    except Exception as e:
        log.error(f"创建认证URL失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def wait_for_callback_sync(state: str, timeout: int = 300) -> Optional[str]:
    """同步等待OAuth回调完成，使用对应流程的专用服务器"""
    if state not in auth_flows:
        log.error(f"未找到状态为 {state} 的认证流程")
        return None
    
    flow_data = auth_flows[state]
    callback_port = flow_data['callback_port']
    
    # 服务器已经在create_auth_url时启动了，这里只需要等待
    log.info(f"等待OAuth回调完成，端口: {callback_port}")
    
    # 等待回调完成
    start_time = time.time()
    while time.time() - start_time < timeout:
        if flow_data.get('code'):
            log.info(f"OAuth回调成功完成")
            return flow_data['code']
        time.sleep(0.5)  # 每0.5秒检查一次
        
        # 刷新flow_data引用
        if state in auth_flows:
            flow_data = auth_flows[state]
    
    log.warning(f"等待OAuth回调超时 ({timeout}秒)")
    return None


async def complete_auth_flow(project_id: Optional[str] = None, user_session: str = None) -> Dict[str, Any]:
    """完成认证流程并保存凭证，支持自动检测项目ID"""
    try:
        # 查找对应的认证流程
        state = None
        flow_data = None
        
        # 如果指定了project_id，先尝试匹配指定的项目
        if project_id:
            for s, data in auth_flows.items():
                if data['project_id'] == project_id:
                    # 如果指定了用户会话，优先匹配相同会话的流程
                    if user_session and data.get('user_session') == user_session:
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
                if data.get('auto_project_detection', False):
                    # 如果指定了用户会话，优先匹配相同会话的流程
                    if user_session and data.get('user_session') == user_session:
                        state = s
                        flow_data = data
                        break
                    # 使用第一个找到的需要自动检测的流程
                    elif not state:
                        state = s
                        flow_data = data
        
        if not state or not flow_data:
            return {
                'success': False,
                'error': '未找到对应的认证流程，请先点击获取认证链接'
            }
        
        # 如果需要自动检测项目ID且没有提供项目ID
        if flow_data.get('auto_project_detection', False) and not project_id:
            log.info("尝试自动检测项目ID...")
            detected_project_id = await auto_detect_project_id()
            if detected_project_id:
                project_id = detected_project_id
                flow_data['project_id'] = project_id
                log.info(f"自动检测到项目ID: {project_id}")
            else:
                return {
                    'success': False,
                    'error': '无法自动检测项目ID，请手动指定项目ID',
                    'requires_manual_project_id': True
                }
        elif not project_id:
            project_id = flow_data.get('project_id')
            if not project_id:
                return {
                    'success': False,
                    'error': '缺少项目ID，请指定项目ID',
                    'requires_manual_project_id': True
                }
        
        flow = flow_data['flow']
        
        # 如果还没有授权码，需要等待回调
        if not flow_data.get('code'):
            log.info(f"等待用户完成OAuth授权 (state: {state})")
            auth_code = wait_for_callback_sync(state)
            
            if not auth_code:
                return {
                    'success': False,
                    'error': '未接收到授权回调，请确保完成了浏览器中的OAuth认证'
                }
            
            # 更新流程数据
            auth_flows[state]['code'] = auth_code
            auth_flows[state]['completed'] = True
        else:
            auth_code = flow_data['code']
        
        # 使用认证代码获取凭证
        import oauthlib.oauth2.rfc6749.parameters
        original_validate = oauthlib.oauth2.rfc6749.parameters.validate_token_parameters
        
        def patched_validate(params):
            try:
                return original_validate(params)
            except Warning:
                pass
        
        oauthlib.oauth2.rfc6749.parameters.validate_token_parameters = patched_validate
        
        try:
            flow.fetch_token(code=auth_code)
            credentials = flow.credentials
            
            # 如果需要自动检测项目ID且没有提供项目ID
            if flow_data.get('auto_project_detection', False) and not project_id:
                log.info("尝试通过API获取用户项目列表...")
                log.info(f"使用的token: {credentials.token[:20]}...")
                log.info(f"Token过期时间: {credentials.expiry}")
                user_projects = await get_user_projects(credentials)
                
                if user_projects:
                    # 如果只有一个项目，自动使用
                    if len(user_projects) == 1:
                        project_id = user_projects[0].get('projectId')
                        if project_id:
                            flow_data['project_id'] = project_id
                            log.info(f"自动选择唯一项目: {project_id}")
                    # 如果有多个项目，尝试选择默认项目
                    else:
                        project_id = await select_default_project(user_projects)
                        if project_id:
                            flow_data['project_id'] = project_id
                            log.info(f"自动选择默认项目: {project_id}")
                        else:
                            # 返回项目列表让用户选择
                            return {
                                'success': False,
                                'error': '请从以下项目中选择一个',
                                'requires_project_selection': True,
                                'available_projects': [
                                    {
                                        'projectId': p.get('projectId'),
                                        'name': p.get('displayName') or p.get('projectId'),
                                        'projectNumber': p.get('projectNumber')
                                    }
                                    for p in user_projects
                                ]
                            }
                else:
                    # 如果无法获取项目列表，提示手动输入
                    return {
                        'success': False,
                        'error': '无法获取您的项目列表，请手动指定项目ID',
                        'requires_manual_project_id': True
                    }
            
            # 如果仍然没有项目ID，返回错误
            if not project_id:
                return {
                    'success': False,
                    'error': '缺少项目ID，请指定项目ID',
                    'requires_manual_project_id': True
                }
            
            # 保存凭证文件
            file_path = save_credentials(credentials, project_id)
            
            # 准备返回的凭证数据
            creds_data = {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "scopes": credentials.scopes if credentials.scopes else SCOPES,
                "token_uri": "https://oauth2.googleapis.com/token",
                "project_id": project_id
            }
            
            if credentials.expiry:
                if credentials.expiry.tzinfo is None:
                    expiry_utc = credentials.expiry.replace(tzinfo=timezone.utc)
                else:
                    expiry_utc = credentials.expiry
                creds_data["expiry"] = expiry_utc.isoformat()
            
            # 清理使用过的流程
            if state in auth_flows:
                flow_data_to_clean = auth_flows[state]
                # 快速关闭服务器
                try:
                    if flow_data_to_clean.get('server'):
                        server = flow_data_to_clean['server']
                        port = flow_data_to_clean.get('callback_port')
                        async_shutdown_server(server, port)
                except Exception as e:
                    log.debug(f"启动异步关闭服务器时出错: {e}")
                
                del auth_flows[state]
            
            log.info("OAuth认证成功，凭证已保存")
            return {
                'success': True,
                'credentials': creds_data,
                'file_path': os.path.basename(file_path),
                'auto_detected_project': flow_data.get('auto_project_detection', False)
            }
            
        except Exception as e:
            log.error(f"获取凭证失败: {e}")
            return {
                'success': False,
                'error': f'获取凭证失败: {str(e)}'
            }
        finally:
            oauthlib.oauth2.rfc6749.parameters.validate_token_parameters = original_validate
            
    except Exception as e:
        log.error(f"完成认证流程失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


async def asyncio_complete_auth_flow(project_id: Optional[str] = None, user_session: str = None) -> Dict[str, Any]:
    """异步完成认证流程，支持自动检测项目ID"""
    try:
        log.info(f"[ASYNC] asyncio_complete_auth_flow开始执行: project_id={project_id}, user_session={user_session}")
        
        # 查找对应的认证流程
        state = None
        flow_data = None
        
        log.debug(f"[ASYNC] 当前所有auth_flows: {list(auth_flows.keys())}")
        
        # 如果指定了project_id，先尝试匹配指定的项目
        if project_id:
            log.info(f"[ASYNC] 尝试匹配指定的项目ID: {project_id}")
            for s, data in auth_flows.items():
                if data['project_id'] == project_id:
                    # 如果指定了用户会话，优先匹配相同会话的流程
                    if user_session and data.get('user_session') == user_session:
                        state = s
                        flow_data = data
                        log.info(f"[ASYNC] 找到匹配的用户会话: {s}")
                        break
                    # 如果没有指定会话，或没找到匹配会话的流程，使用第一个匹配项目ID的
                    elif not state:
                        state = s
                        flow_data = data
                        log.info(f"[ASYNC] 找到匹配的项目ID: {s}")
        
        # 如果没有指定项目ID或没找到匹配的，查找需要自动检测项目ID的流程
        if not state:
            log.info(f"[ASYNC] 没有找到指定项目的流程，查找自动检测流程")
            for s, data in auth_flows.items():
                log.debug(f"[ASYNC] 检查流程 {s}: auto_project_detection={data.get('auto_project_detection', False)}")
                if data.get('auto_project_detection', False):
                    # 如果指定了用户会话，优先匹配相同会话的流程
                    if user_session and data.get('user_session') == user_session:
                        state = s
                        flow_data = data
                        log.info(f"[ASYNC] 找到匹配用户会话的自动检测流程: {s}")
                        break
                    # 使用第一个找到的需要自动检测的流程
                    elif not state:
                        state = s
                        flow_data = data
                        log.info(f"[ASYNC] 找到自动检测流程: {s}")
        
        if not state or not flow_data:
            log.error(f"[ASYNC] 未找到认证流程: state={state}, flow_data存在={bool(flow_data)}")
            log.debug(f"[ASYNC] 当前所有flow_data: {list(auth_flows.keys())}")
            return {
                'success': False,
                'error': '未找到对应的认证流程，请先点击获取认证链接'
            }
        
        log.info(f"[ASYNC] 找到认证流程: state={state}")
        log.info(f"[ASYNC] flow_data内容: project_id={flow_data.get('project_id')}, auto_project_detection={flow_data.get('auto_project_detection')}")
        log.info(f"[ASYNC] 传入的project_id参数: {project_id}")
        
        # 如果需要自动检测项目ID且没有提供项目ID
        log.info(f"[ASYNC] 检查auto_project_detection条件: auto_project_detection={flow_data.get('auto_project_detection', False)}, not project_id={not project_id}")
        if flow_data.get('auto_project_detection', False) and not project_id:
            log.info("[ASYNC] 进入自动检测项目ID分支")
            log.info("尝试自动检测项目ID...")
            try:
                detected_project_id = await auto_detect_project_id()
                log.info(f"[ASYNC] auto_detect_project_id返回: {detected_project_id}")
                if detected_project_id:
                    project_id = detected_project_id
                    flow_data['project_id'] = project_id
                    log.info(f"自动检测到项目ID: {project_id}")
                else:
                    log.info("[ASYNC] 环境自动检测失败，跳过OAuth检查，直接进入等待阶段")
            except Exception as e:
                log.error(f"[ASYNC] auto_detect_project_id发生异常: {e}")
        elif not project_id:
            log.info("[ASYNC] 进入project_id检查分支")
            project_id = flow_data.get('project_id')
            if not project_id:
                log.error("[ASYNC] 缺少项目ID，返回错误")
                return {
                    'success': False,
                    'error': '缺少项目ID，请指定项目ID',
                    'requires_manual_project_id': True
                }
        else:
            log.info(f"[ASYNC] 使用提供的项目ID: {project_id}")
        
        # 检查是否已经有授权码
        log.info(f"[ASYNC] 开始检查OAuth授权码...")
        max_wait_time = 60  # 最多等待60秒
        wait_interval = 1   # 每秒检查一次
        waited = 0
        
        while waited < max_wait_time:
            log.debug(f"[ASYNC] 等待OAuth授权码... ({waited}/{max_wait_time}秒)")
            if flow_data.get('code'):
                log.info(f"[ASYNC] 检测到OAuth授权码，开始处理凭证 (等待时间: {waited}秒)")
                break
            
            # 异步等待
            await asyncio.sleep(wait_interval)
            waited += wait_interval
            
            # 刷新flow_data引用，因为可能被回调更新了
            if state in auth_flows:
                flow_data = auth_flows[state]
                log.debug(f"[ASYNC] 刷新flow_data: completed={flow_data.get('completed')}, code存在={bool(flow_data.get('code'))}")
        
        if not flow_data.get('code'):
            log.error(f"[ASYNC] 等待OAuth回调超时，等待了{waited}秒")
            return {
                'success': False,
                'error': '等待OAuth回调超时，请确保完成了浏览器中的认证并看到成功页面'
            }
        
        flow = flow_data['flow']
        auth_code = flow_data['code']
        
        log.info(f"[ASYNC] 开始使用授权码获取凭证: code={'***' + auth_code[-4:] if auth_code else 'None'}")
        
        # 使用认证代码获取凭证
        import oauthlib.oauth2.rfc6749.parameters
        original_validate = oauthlib.oauth2.rfc6749.parameters.validate_token_parameters
        
        def patched_validate(params):
            try:
                return original_validate(params)
            except Warning:
                pass
        
        oauthlib.oauth2.rfc6749.parameters.validate_token_parameters = patched_validate
        
        try:
            log.info(f"[ASYNC] 调用flow.fetch_token...")
            flow.fetch_token(code=auth_code)
            credentials = flow.credentials
            log.info(f"[ASYNC] 成功获取凭证，token前缀: {credentials.token[:20] if credentials.token else 'None'}...")
            
            log.info(f"[ASYNC] 检查是否需要项目检测: auto_project_detection={flow_data.get('auto_project_detection')}, project_id={project_id}")
            
            # 如果需要自动检测项目ID且没有提供项目ID
            if flow_data.get('auto_project_detection', False) and not project_id:
                log.info("尝试通过API获取用户项目列表...")
                log.info(f"使用的token: {credentials.token[:20]}...")
                log.info(f"Token过期时间: {credentials.expiry}")
                user_projects = await get_user_projects(credentials)
                
                if user_projects:
                    # 如果只有一个项目，自动使用
                    if len(user_projects) == 1:
                        project_id = user_projects[0].get('projectId')
                        if project_id:
                            flow_data['project_id'] = project_id
                            log.info(f"自动选择唯一项目: {project_id}")
                            # 自动启用必需的API服务
                            log.info("正在自动启用必需的API服务...")
                            await enable_required_apis(credentials, project_id)
                    # 如果有多个项目，尝试选择默认项目
                    else:
                        project_id = await select_default_project(user_projects)
                        if project_id:
                            flow_data['project_id'] = project_id
                            log.info(f"自动选择默认项目: {project_id}")
                            # 自动启用必需的API服务
                            log.info("正在自动启用必需的API服务...")
                            await enable_required_apis(credentials, project_id)
                        else:
                            # 返回项目列表让用户选择
                            return {
                                'success': False,
                                'error': '请从以下项目中选择一个',
                                'requires_project_selection': True,
                                'available_projects': [
                                    {
                                        'projectId': p.get('projectId'),
                                        'name': p.get('displayName') or p.get('projectId'),
                                        'projectNumber': p.get('projectNumber')
                                    }
                                    for p in user_projects
                                ]
                            }
                else:
                    # 如果无法获取项目列表，提示手动输入
                    return {
                        'success': False,
                        'error': '无法获取您的项目列表，请手动指定项目ID',
                        'requires_manual_project_id': True
                    }
            elif project_id:
                # 如果已经有项目ID（手动提供或环境检测），也尝试启用API服务
                log.info("正在为已提供的项目ID自动启用必需的API服务...")
                await enable_required_apis(credentials, project_id)
            
            # 如果仍然没有项目ID，返回错误
            if not project_id:
                return {
                    'success': False,
                    'error': '缺少项目ID，请指定项目ID',
                    'requires_manual_project_id': True
                }
            
            # 保存凭证文件
            file_path = save_credentials(credentials, project_id)
            
            # 准备返回的凭证数据
            creds_data = {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "scopes": credentials.scopes if credentials.scopes else SCOPES,
                "token_uri": "https://oauth2.googleapis.com/token",
                "project_id": project_id
            }
            
            if credentials.expiry:
                if credentials.expiry.tzinfo is None:
                    expiry_utc = credentials.expiry.replace(tzinfo=timezone.utc)
                else:
                    expiry_utc = credentials.expiry
                creds_data["expiry"] = expiry_utc.isoformat()
            
            # 清理使用过的流程
            if state in auth_flows:
                flow_data_to_clean = auth_flows[state]
                # 快速关闭服务器
                try:
                    if flow_data_to_clean.get('server'):
                        server = flow_data_to_clean['server']
                        port = flow_data_to_clean.get('callback_port')
                        async_shutdown_server(server, port)
                except Exception as e:
                    log.debug(f"启动异步关闭服务器时出错: {e}")
                
                del auth_flows[state]
            
            log.info("OAuth认证成功，凭证已保存")
            return {
                'success': True,
                'credentials': creds_data,
                'file_path': os.path.basename(file_path),
                'auto_detected_project': flow_data.get('auto_project_detection', False)
            }
            
        except Exception as e:
            log.error(f"获取凭证失败: {e}")
            return {
                'success': False,
                'error': f'获取凭证失败: {str(e)}'
            }
        finally:
            oauthlib.oauth2.rfc6749.parameters.validate_token_parameters = original_validate
            
    except Exception as e:
        log.error(f"异步完成认证流程失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def save_credentials(creds: Credentials, project_id: str) -> str:
    """保存凭证到文件"""
    # 确保目录存在
    os.makedirs(CREDENTIALS_DIR, exist_ok=True)
    
    # 生成文件名（使用project_id和时间戳）
    timestamp = int(time.time())
    filename = f"{project_id}-{timestamp}.json"
    file_path = os.path.join(CREDENTIALS_DIR, filename)
    
    # 准备凭证数据
    creds_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "scopes": creds.scopes if creds.scopes else SCOPES,
        "token_uri": "https://oauth2.googleapis.com/token",
        "project_id": project_id
    }
    
    if creds.expiry:
        if creds.expiry.tzinfo is None:
            expiry_utc = creds.expiry.replace(tzinfo=timezone.utc)
        else:
            expiry_utc = creds.expiry
        creds_data["expiry"] = expiry_utc.isoformat()
    
    # 保存到文件
    with open(file_path, "w", encoding='utf-8') as f:
        json.dump(creds_data, f, indent=2, ensure_ascii=False)
    
    log.info(f"凭证已保存到: {os.path.basename(file_path)}")
    return file_path


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
    
    # 使用更短的过期时间，减少内存占用
    EXPIRY_TIME = 600  # 10分钟过期，减少内存占用
    
    # 直接遍历删除，避免创建额外列表
    states_to_remove = [
        state for state, flow_data in auth_flows.items()
        if current_time - flow_data['created_at'] > EXPIRY_TIME
    ]
    
    # 批量清理，提高效率
    cleaned_count = 0
    for state in states_to_remove:
        flow_data = auth_flows.get(state)
        if flow_data:
            # 快速关闭可能存在的服务器
            try:
                if flow_data.get('server'):
                    server = flow_data['server']
                    port = flow_data.get('callback_port')
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
        if flow_data['project_id'] == project_id:
            return {
                'status': 'completed' if flow_data['completed'] else 'pending',
                'state': state,
                'created_at': flow_data['created_at']
            }
    
    return {
        'status': 'not_found'
    }


# 鉴权功能 - 使用更小的数据结构
auth_tokens = {}  # 存储有效的认证令牌
TOKEN_EXPIRY = 21600  # 6小时令牌过期时间，减少内存占用


def verify_password(password: str) -> bool:
    """验证密码（面板登录使用）"""
    from config import get_panel_password
    correct_password = get_panel_password()
    return password == correct_password


def generate_auth_token() -> str:
    """生成认证令牌"""
    # 清理过期令牌
    cleanup_expired_tokens()
    
    token = secrets.token_urlsafe(32)
    # 只存储创建时间，节省内存
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
        token for token, created_at in auth_tokens.items()
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


# 批量上传功能
def validate_credential_file(file_content: str) -> Dict[str, Any]:
    """验证认证文件格式"""
    try:
        creds_data = json.loads(file_content)
        
        # 检查必要字段
        required_fields = ['client_id', 'client_secret', 'refresh_token', 'token_uri']
        missing_fields = [field for field in required_fields if field not in creds_data]
        
        if missing_fields:
            return {
                'valid': False,
                'error': f'缺少必要字段: {", ".join(missing_fields)}'
            }
        
        # 检查project_id
        if 'project_id' not in creds_data:
            log.warning("认证文件缺少project_id字段")
        
        return {
            'valid': True,
            'data': creds_data
        }
        
    except json.JSONDecodeError as e:
        return {
            'valid': False,
            'error': f'JSON格式错误: {str(e)}'
        }
    except Exception as e:
        return {
            'valid': False,
            'error': f'文件验证失败: {str(e)}'
        }


def save_uploaded_credential(file_content: str, original_filename: str) -> Dict[str, Any]:
    """保存上传的认证文件"""
    try:
        # 验证文件格式
        validation = validate_credential_file(file_content)
        if not validation['valid']:
            return {
                'success': False,
                'error': validation['error']
            }
        
        creds_data = validation['data']
        
        # 确保目录存在
        os.makedirs(CREDENTIALS_DIR, exist_ok=True)
        
        # 生成文件名
        project_id = creds_data.get('project_id', 'unknown')
        timestamp = int(time.time())
        
        # 从原文件名中提取有用信息
        base_name = os.path.splitext(original_filename)[0]
        filename = f"{base_name}-{timestamp}.json"
        file_path = os.path.join(CREDENTIALS_DIR, filename)
        
        # 确保文件名唯一
        counter = 1
        while os.path.exists(file_path):
            filename = f"{base_name}-{timestamp}-{counter}.json"
            file_path = os.path.join(CREDENTIALS_DIR, filename)
            counter += 1
        
        # 保存文件
        with open(file_path, "w", encoding='utf-8') as f:
            json.dump(creds_data, f, indent=2, ensure_ascii=False)
        
        log.info(f"认证文件已上传保存: {os.path.basename(file_path)}")
        
        return {
            'success': True,
            'file_path': os.path.basename(file_path),
            'project_id': project_id
        }
        
    except Exception as e:
        log.error(f"保存上传文件失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def batch_upload_credentials(files_data: List[Dict[str, str]]) -> Dict[str, Any]:
    """批量上传认证文件"""
    results = []
    success_count = 0
    
    for file_data in files_data:
        filename = file_data.get('filename', 'unknown.json')
        content = file_data.get('content', '')
        
        result = save_uploaded_credential(content, filename)
        result['filename'] = filename
        results.append(result)
        
        if result['success']:
            success_count += 1
    
    return {
        'uploaded_count': success_count,
        'total_count': len(files_data),
        'results': results
    }


# 环境变量批量导入功能
def load_credentials_from_env() -> Dict[str, Any]:
    """
    从环境变量加载多个凭证文件
    支持两种环境变量格式:
    1. GCLI_CREDS_1, GCLI_CREDS_2, ... (编号格式)
    2. GCLI_CREDS_projectname1, GCLI_CREDS_projectname2, ... (项目名格式)
    """
    results = []
    success_count = 0
    
    log.info("开始从环境变量加载认证凭证...")
    
    # 获取所有以GCLI_CREDS_开头的环境变量
    creds_env_vars = {key: value for key, value in os.environ.items() 
                      if key.startswith('GCLI_CREDS_') and value.strip()}
    
    if not creds_env_vars:
        log.info("未找到GCLI_CREDS_*环境变量")
        return {
            'loaded_count': 0,
            'total_count': 0,
            'results': [],
            'message': '未找到GCLI_CREDS_*环境变量'
        }
    
    log.info(f"找到 {len(creds_env_vars)} 个凭证环境变量")
    
    for env_name, creds_content in creds_env_vars.items():
        # 从环境变量名提取标识符
        identifier = env_name.replace('GCLI_CREDS_', '')
        
        try:
            # 验证JSON格式
            validation = validate_credential_file(creds_content)
            if not validation['valid']:
                result = {
                    'env_name': env_name,
                    'identifier': identifier,
                    'success': False,
                    'error': validation['error']
                }
                results.append(result)
                log.error(f"环境变量 {env_name} 验证失败: {validation['error']}")
                continue
            
            creds_data = validation['data']
            project_id = creds_data.get('project_id', 'unknown')
            
            # 生成文件名 (使用标识符和项目ID)
            timestamp = int(time.time())
            if identifier.isdigit():
                # 如果标识符是数字，使用项目ID作为主要标识
                filename = f"env-{project_id}-{identifier}-{timestamp}.json"
            else:
                # 如果标识符是项目名，直接使用
                filename = f"env-{identifier}-{timestamp}.json"
            
            # 确保目录存在
            os.makedirs(CREDENTIALS_DIR, exist_ok=True)
            file_path = os.path.join(CREDENTIALS_DIR, filename)
            
            # 确保文件名唯一
            counter = 1
            original_file_path = file_path
            while os.path.exists(file_path):
                name, ext = os.path.splitext(original_file_path)
                file_path = f"{name}-{counter}{ext}"
                counter += 1
            
            # 保存文件
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(creds_data, f, indent=2, ensure_ascii=False)
            
            result = {
                'env_name': env_name,
                'identifier': identifier,
                'success': True,
                'file_path': os.path.basename(file_path),
                'project_id': project_id,
                'filename': os.path.basename(file_path)
            }
            results.append(result)
            success_count += 1
            
            log.info(f"成功从环境变量 {env_name} 保存凭证到: {os.path.basename(file_path)}")
            
        except Exception as e:
            result = {
                'env_name': env_name,
                'identifier': identifier,
                'success': False,
                'error': str(e)
            }
            results.append(result)
            log.error(f"处理环境变量 {env_name} 时发生错误: {e}")
    
    message = f"成功导入 {success_count}/{len(creds_env_vars)} 个凭证文件"
    log.info(message)
    
    return {
        'loaded_count': success_count,
        'total_count': len(creds_env_vars),
        'results': results,
        'message': message
    }


def auto_load_env_credentials_on_startup() -> None:
    """
    程序启动时自动从环境变量加载凭证
    如果设置了 AUTO_LOAD_ENV_CREDS=true，则会自动执行
    """
    from config import get_auto_load_env_creds
    auto_load = get_auto_load_env_creds()
    
    if not auto_load:
        log.debug("AUTO_LOAD_ENV_CREDS未启用，跳过自动加载")
        return
    
    log.info("AUTO_LOAD_ENV_CREDS已启用，开始自动加载环境变量中的凭证...")
    
    try:
        result = load_credentials_from_env()
        if result['loaded_count'] > 0:
            log.info(f"启动时成功自动导入 {result['loaded_count']} 个凭证文件")
        else:
            log.info("启动时未找到可导入的环境变量凭证")
    except Exception as e:
        log.error(f"启动时自动加载环境变量凭证失败: {e}")


def clear_env_credentials() -> Dict[str, Any]:
    """
    清除所有从环境变量导入的凭证文件
    仅删除文件名包含'env-'前缀的文件
    """
    if not os.path.exists(CREDENTIALS_DIR):
        return {
            'deleted_count': 0,
            'message': '凭证目录不存在'
        }
    
    deleted_files = []
    deleted_count = 0
    
    try:
        for filename in os.listdir(CREDENTIALS_DIR):
            if filename.startswith('env-') and filename.endswith('.json'):
                file_path = os.path.join(CREDENTIALS_DIR, filename)
                try:
                    os.remove(file_path)
                    deleted_files.append(filename)
                    deleted_count += 1
                    log.info(f"删除环境变量凭证文件: {filename}")
                except Exception as e:
                    log.error(f"删除文件 {filename} 失败: {e}")
        
        message = f"成功删除 {deleted_count} 个环境变量凭证文件"
        log.info(message)
        
        return {
            'deleted_count': deleted_count,
            'deleted_files': deleted_files,
            'message': message
        }
        
    except Exception as e:
        error_message = f"清除环境变量凭证文件时发生错误: {e}"
        log.error(error_message)
        return {
            'deleted_count': 0,
            'error': error_message
        }



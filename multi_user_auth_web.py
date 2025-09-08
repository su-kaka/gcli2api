#!/usr/bin/env python3
"""
OAuth Web 服务器 - 独立的OAuth认证服务
提供简化的OAuth认证界面，只包含验证功能，不包含上传和管理功能
"""

from log import log
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from src.auth import (
    create_auth_url, 
    verify_password, 
    generate_auth_token, 
    verify_auth_token,
    asyncio_complete_auth_flow,
    complete_auth_flow_from_callback_url,
    CALLBACK_HOST,
)

# 创建FastAPI应用
app = FastAPI(
    title="Google OAuth 认证服务",
    description="独立的OAuth认证服务，用于获取Google Cloud认证文件",
)

# HTTP Bearer认证
security = HTTPBearer()

# 请求模型
class LoginRequest(BaseModel):
    password: str

class AuthStartRequest(BaseModel):
    project_id: str = None  # 现在是可选的，支持自动检测

class AuthCallbackRequest(BaseModel):
    project_id: str = None  # 现在是可选的，支持自动检测

class AuthCallbackUrlRequest(BaseModel):
    callback_url: str  # OAuth回调完整URL
    project_id: str = None  # 可选的项目ID

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """验证认证令牌"""
    if not verify_auth_token(credentials.credentials):
        raise HTTPException(status_code=401, detail="无效的认证令牌")
    return credentials.credentials


@app.get("/", response_class=HTMLResponse)
async def serve_oauth_page():
    """提供OAuth认证页面"""
    try:
        # 读取HTML文件
        html_file_path = "./front/multi_user_auth_web.html"
        
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="认证页面不存在")
    except Exception as e:
        log.error(f"加载认证页面失败: {e}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

@app.post("/auth/login")
async def login(request: LoginRequest):
    """用户登录"""
    try:
        if await verify_password(request.password):
            token = generate_auth_token()
            return JSONResponse(content={"token": token, "message": "登录成功"})
        else:
            raise HTTPException(status_code=401, detail="密码错误")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"登录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/start")
async def start_auth(request: AuthStartRequest, token: str = Depends(verify_token)):
    """开始认证流程，支持自动检测项目ID"""
    try:
        # 如果没有提供项目ID，尝试自动检测
        project_id = request.project_id
        if not project_id:
            log.info("未提供项目ID，后续将尝试自动检测...")
        
        # 使用认证令牌作为用户会话标识
        user_session = token if token else None
        result = await create_auth_url(project_id, user_session)
        
        if result['success']:
            # 构建动态回调URL
            callback_port = result.get('callback_port')
            callback_url = f"http://{CALLBACK_HOST}:{callback_port}" if callback_port else None
            
            response_data = {
                "auth_url": result['auth_url'],
                "state": result['state'],
                "auto_project_detection": result.get('auto_project_detection', False),
                "detected_project_id": result.get('detected_project_id')
            }
            
            # 如果有回调端口信息，添加到响应中
            if callback_port:
                response_data["callback_port"] = callback_port
                response_data["callback_url"] = callback_url
            
            return JSONResponse(content=response_data)
        else:
            raise HTTPException(status_code=500, detail=result['error'])
            
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"开始认证流程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/callback")
async def auth_callback(request: AuthCallbackRequest, token: str = Depends(verify_token)):
    """处理认证回调（异步等待），支持自动检测项目ID"""
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


@app.post("/auth/callback-url")
async def auth_callback_url(request: AuthCallbackUrlRequest, token: str = Depends(verify_token)):
    """从回调URL直接完成认证，无需启动本地服务器"""
    try:
        # 验证URL格式
        if not request.callback_url or not request.callback_url.startswith(('http://', 'https://')):
            raise HTTPException(status_code=400, detail="请提供有效的回调URL")
        
        # 从回调URL完成认证
        result = await complete_auth_flow_from_callback_url(request.callback_url, request.project_id)
        
        if result['success']:
            return JSONResponse(content={
                "credentials": result['credentials'],
                "file_path": result['file_path'],
                "message": "从回调URL认证成功，凭证已保存",
                "auto_detected_project": result.get('auto_detected_project', False)
            })
        else:
            # 处理各种错误情况
            if result.get('requires_manual_project_id'):
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": result['error'],
                        "requires_manual_project_id": True
                    }
                )
            elif result.get('requires_project_selection'):
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
        log.error(f"从回调URL处理认证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("OAuth认证服务启动中...")

    # OAuth回调服务器现在动态按需启动，每个认证流程使用独立端口
    log.info("OAuth回调服务器将为每个认证流程动态分配端口")

    # 从配置获取密码和端口
    from config import get_panel_password, get_server_port
    password = await get_panel_password()
    port = await get_server_port()

    log.info("Web服务已由 ASGI 服务器启动")
    
    print("\n" + "="*60)
    print("🚀 Google OAuth 认证服务已启动")
    print("="*60)
    print(f"📱 Web界面: http://localhost:{port}")
    print(f"🔐 默认密码: {'已设置' if password else 'pwd (请设置PASSWORD环境变量)'}")
    print(f"🔄 多用户并发: 支持多用户同时认证（动态端口分配）")
    print("="*60 + "\n")

    try:
        yield
    finally:
        log.info("OAuth认证服务关闭中...")
        # OAuth服务器由认证流程自动管理，无需手动清理
        log.info("OAuth认证服务已关闭")

# 注册 lifespan 处理器
app.router.lifespan_context = lifespan

if __name__ == "__main__":
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    async def main():
        # 从配置获取端口
        from config import get_server_port
        PORT = await get_server_port()
        
        config = Config()
        config.bind = [f"0.0.0.0:{PORT}"]
        config.accesslog = "-"
        config.errorlog = "-"
        config.loglevel = "INFO"
        
        await serve(app, config)
    
    asyncio.run(main())
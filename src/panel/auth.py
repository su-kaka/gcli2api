"""
认证路由模块 - 处理 /auth/* 相关的HTTP请求
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from log import log
from src.auth import (
    asyncio_complete_auth_flow,
    complete_auth_flow_from_callback_url,
    create_auth_url,
    get_auth_status,
    verify_password,
)
from src.models import (
    LoginRequest,
    AuthStartRequest,
    AuthCallbackRequest,
    AuthCallbackUrlRequest,
)
from src.utils import verify_panel_token


# 创建路由器
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
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


@router.post("/start")
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
            project_id, user_session, mode=request.mode
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


@router.post("/callback")
async def auth_callback(request: AuthCallbackRequest, token: str = Depends(verify_panel_token)):
    """处理认证回调，支持自动检测项目ID"""
    try:
        # 项目ID现在是可选的，在回调处理中进行自动检测
        project_id = request.project_id

        # 使用认证令牌作为用户会话标识
        user_session = token if token else None
        # 异步等待OAuth回调完成
        result = await asyncio_complete_auth_flow(
            project_id, user_session, mode=request.mode
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


@router.post("/callback-url")
async def auth_callback_url(request: AuthCallbackUrlRequest, token: str = Depends(verify_panel_token)):
    """从回调URL直接完成认证"""
    try:
        # 验证URL格式
        if not request.callback_url or not request.callback_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="请提供有效的回调URL")

        # 从回调URL完成认证
        result = await complete_auth_flow_from_callback_url(
            request.callback_url, request.project_id, mode=request.mode
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


@router.get("/status/{project_id}")
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

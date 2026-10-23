"""
凭证管理路由模块 - 处理 /creds/* 相关的HTTP请求（仅 Antigravity 模式）
凭证以 email 为唯一键；导入/导出使用规范 JSON 格式：
  { "refresh_token": "...", "email": "...", "tier": "...", "project_id": "..." }
"""

import asyncio
import io
import json
import time
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Response
from fastapi.responses import JSONResponse

from log import log
from src.credential_manager import (
    add_credential,
    remove_credential,
    set_cred_disabled,
)
from src.models import (
    CredFileActionRequest,
    CredFileBatchActionRequest
)
from src.storage.database import get_database
from src.utils import (
    verify_panel_token,
    ANTIGRAVITY_USER_AGENT,
    ANTIGRAVITY_CLIENT_ID,
    ANTIGRAVITY_CLIENT_SECRET,
)
from src.api.antigravity import fetch_quota_info, build_antigravity_headers
from src.google_oauth_api import (
    Credentials,
    fetch_project_id_and_tier,
    get_user_email,
)
from config import get_antigravity_api_url


# 创建路由器
router = APIRouter(prefix="/creds", tags=["credentials"])


# =============================================================================
# 凭证导入核心
# =============================================================================

async def _import_single_credential(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    导入单个凭证（规范格式）。
    必须字段: refresh_token
    可选字段: email / tier / project_id（缺失时自动获取）

    Returns:
        {"status": "success"|"error", "email": ..., "message": ...}
    """
    try:
        refresh_token = (data.get("refresh_token") or "").strip()
        if not refresh_token:
            return {"status": "error", "message": "缺少 refresh_token"}

        email = (data.get("email") or "").strip().lower() or None
        tier = (data.get("tier") or "").strip().lower() or None
        project_id = (data.get("project_id") or "").strip() or None

        # 用 refresh_token 换 access_token，同时验证凭证有效性
        creds = Credentials(
            access_token="",
            refresh_token=refresh_token,
            client_id=ANTIGRAVITY_CLIENT_ID,
            client_secret=ANTIGRAVITY_CLIENT_SECRET,
        )
        try:
            await creds.refresh()
        except Exception as e:
            return {"status": "error", "message": f"refresh_token 无效: {str(e)[:200]}"}

        if not creds.access_token:
            return {"status": "error", "message": "refresh_token 无效: 未获取到 access_token"}

        # 自动获取缺失的 email
        if not email:
            try:
                email = await get_user_email(creds)
            except Exception as e:
                log.warning(f"自动获取邮箱失败: {e}")
        if not email:
            return {"status": "error", "message": "无法获取邮箱（JSON 中未提供且自动获取失败）"}

        # 自动获取缺失的 project_id / tier
        if not project_id or not tier:
            try:
                api_base_url = await get_antigravity_api_url()
                fetched_project, fetched_tier = await fetch_project_id_and_tier(
                    access_token=creds.access_token,
                    user_agent=ANTIGRAVITY_USER_AGENT,
                    api_base_url=api_base_url,
                )
                if not project_id:
                    project_id = fetched_project
                if not tier:
                    tier = fetched_tier
            except Exception as e:
                log.warning(f"自动获取 project_id/tier 失败: {e}")

        # project_id 获取失败时回退为 aicode-consumers，避免空值入库
        if not project_id:
            log.warning(f"导入凭证 {email}: project_id 获取失败，使用回退值 aicode-consumers")
            project_id = "aicode-consumers"

        credential_data = {
            "access_token": creds.access_token,
            "refresh_token": refresh_token,
            "client_id": ANTIGRAVITY_CLIENT_ID,
            "client_secret": ANTIGRAVITY_CLIENT_SECRET,
            "project_id": project_id,
            "expiry": creds.expires_at.isoformat() if creds.expires_at else "",
            "tier": tier,
        }

        await add_credential(email, credential_data)
        return {
            "status": "success",
            "email": email,
            "tier": tier,
            "message": "导入成功",
        }

    except Exception as e:
        return {"status": "error", "message": f"处理失败: {str(e)[:200]}"}


# =============================================================================
# 工具函数
# =============================================================================


async def extract_json_files_from_zip(zip_file: UploadFile) -> List[dict]:
    """从ZIP文件中提取JSON文件"""
    try:
        zip_content = await zip_file.read()
        files_data = []

        with zipfile.ZipFile(io.BytesIO(zip_content), "r") as zip_ref:
            file_list = zip_ref.namelist()
            json_files = [
                f for f in file_list if f.endswith(".json") and not f.startswith("__MACOSX/")
            ]

            if not json_files:
                raise HTTPException(status_code=400, detail="ZIP文件中没有找到JSON文件")

            log.info(f"从ZIP文件 {zip_file.filename} 中找到 {len(json_files)} 个JSON文件")

            for json_filename in json_files:
                try:
                    with zip_ref.open(json_filename) as json_file:
                        content = json_file.read()

                        try:
                            content_str = content.decode("utf-8")
                        except UnicodeDecodeError:
                            log.warning(f"跳过编码错误的文件: {json_filename}")
                            continue

                        filename = json_filename.rsplit("/", 1)[-1]
                        files_data.append({"filename": filename, "content": content_str})

                except Exception as e:
                    log.warning(f"处理ZIP中的文件 {json_filename} 时出错: {e}")
                    continue

        log.info(f"成功从ZIP文件中提取 {len(files_data)} 个有效的JSON文件")
        return files_data

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="无效的ZIP文件格式")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        log.error(f"处理ZIP文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"处理ZIP文件失败: {str(e)}")


async def upload_credentials_common( files: List[UploadFile] ) -> JSONResponse:
    """批量导入凭证文件（规范 JSON：refresh_token 必填，email/tier/project_id 可选自动获取）"""

    if not files:
        raise HTTPException(status_code=400, detail="请选择要上传的文件")

    if len(files) > 100:
        raise HTTPException(
            status_code=400, detail=f"文件数量过多，最多支持100个文件，当前：{len(files)}个"
        )

    files_data = []
    for file in files:
        if file.filename.endswith(".zip"):
            zip_files_data = await extract_json_files_from_zip(file)
            files_data.extend(zip_files_data)
            log.info(f"从ZIP文件 {file.filename} 中提取了 {len(zip_files_data)} 个JSON文件")

        elif file.filename.endswith(".json"):
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

    batch_size = 100
    all_results = []
    total_success = 0

    for i in range(0, len(files_data), batch_size):
        batch_files = files_data[i : i + batch_size]

        async def process_single_file(file_data):
            try:
                data = json.loads(file_data["content"])
                if not isinstance(data, dict):
                    return {
                        "filename": file_data["filename"],
                        "status": "error",
                        "message": "JSON格式错误: 顶层必须是对象",
                    }
                result = await _import_single_credential(data)
                result["filename"] = file_data["filename"]
                return result
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
                    "message": f"处理失败: {str(e)[:200]}",
                }

        log.info(f"开始并发处理 {len(batch_files)} 个凭证...")
        concurrent_tasks = [process_single_file(file_data) for file_data in batch_files]
        batch_results = await asyncio.gather(*concurrent_tasks, return_exceptions=True)

        batch_uploaded_count = 0
        for result in batch_results:
            if isinstance(result, Exception):
                all_results.append(
                    {
                        "filename": "unknown",
                        "status": "error",
                        "message": f"处理异常: {str(result)[:200]}",
                    }
                )
            else:
                all_results.append(result)
                if result["status"] == "success":
                    batch_uploaded_count += 1

        total_success += batch_uploaded_count

        batch_num = (i // batch_size) + 1
        total_batches = (len(files_data) + batch_size - 1) // batch_size
        log.info(
            f"批次 {batch_num}/{total_batches} 完成: 成功 "
            f"{batch_uploaded_count}/{len(batch_files)} 个凭证"
        )

    return JSONResponse(
        content={
            "uploaded_count": total_success,
            "total_count": len(files_data),
            "results": all_results,
            "message": f"批量导入完成: 成功 {total_success}/{len(files_data)} 个凭证",
        }
    )


async def get_creds_status_common(
    offset: int, limit: int, status_filter: str,
    error_code_filter: str = None, cooldown_filter: str = None, tier_filter: str = None
) -> JSONResponse:
    """获取凭证状态"""
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset 必须大于等于 0")
    if limit not in [20, 50, 100, 200, 500, 1000]:
        raise HTTPException(status_code=400, detail="limit 只能是 20、50、100、200、500 或 1000")
    if status_filter not in ["all", "enabled", "disabled"]:
        raise HTTPException(status_code=400, detail="status_filter 只能是 all、enabled 或 disabled")
    if cooldown_filter and cooldown_filter not in ["all", "in_cooldown", "no_cooldown"]:
        raise HTTPException(status_code=400, detail="cooldown_filter 只能是 all、in_cooldown 或 no_cooldown")
    if tier_filter and tier_filter not in ["all", "free", "pro", "ultra"]:
        raise HTTPException(status_code=400, detail="tier_filter 只能是 all、free、pro 或 ultra")

    database = await get_database()
    result = await database.get_credentials_summary(
        offset=offset,
        limit=limit,
        status_filter=status_filter,
        error_code_filter=error_code_filter if error_code_filter and error_code_filter != "all" else None,
        cooldown_filter=cooldown_filter if cooldown_filter and cooldown_filter != "all" else None,
        tier_filter=tier_filter if tier_filter and tier_filter != "all" else None
    )

    return JSONResponse(content={
        "items": result["items"],
        "total": result["total"],
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < result["total"],
        "stats": result.get("stats", {"total": 0, "normal": 0, "disabled": 0}),
    })


async def download_all_creds_common() -> Response:
    """打包导出所有凭证（规范格式，文件名=邮箱.json）"""
    zip_filename = "antigravity_credentials.zip"

    database = await get_database()
    emails = await database.list_credentials()

    if not emails:
        raise HTTPException(status_code=404, detail="没有找到凭证")

    log.info(f"开始打包 {len(emails)} 个凭证...")

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        success_count = 0
        for email in emails:
            try:
                state = await database.get_credential_state(email)
                export_data = {
                    "refresh_token": "",
                    "email": email,
                    "tier": state.get("tier", "pro"),
                    "project_id": state.get("project_id", ""),
                }
                # 从凭证数据中取 refresh_token
                cred = await database.get_credential(email)
                if cred:
                    export_data["refresh_token"] = cred.get("refresh_token", "")
                else:
                    continue

                content = json.dumps(export_data, ensure_ascii=False, indent=2)
                zip_file.writestr(f"{email}.json", content)
                success_count += 1
            except Exception as e:
                log.warning(f"导出凭证 {email} 时出错: {e}")
                continue

    log.info(f"打包完成: 成功 {success_count}/{len(emails)} 个凭证")

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_filename}"},
    )


async def verify_credential_project_common(email: str) -> JSONResponse:
    """验证并重新获取凭证的 project id / tier"""

    database = await get_database()

    credential_data = await database.get_credential(email)
    if not credential_data:
        raise HTTPException(status_code=404, detail="凭证不存在")

    credentials = Credentials.from_dict(credential_data)

    token_refreshed = await credentials.refresh_if_needed()

    if token_refreshed:
        log.info(f"Token已自动刷新: {email}")
        credential_data = credentials.to_dict()
        await database.store_credential(email, credential_data)

    # 重新获取 project id / tier
    api_base_url = await get_antigravity_api_url()
    project_id, subscription_tier = await fetch_project_id_and_tier(
        access_token=credentials.access_token,
        user_agent=ANTIGRAVITY_USER_AGENT,
        api_base_url=api_base_url,
    )

    # project_id 获取失败时回退，避免空值入库
    if not project_id:
        log.warning(f"校验凭证 {email}: project_id 获取失败，使用回退值 aicode-consumers")
        project_id = "aicode-consumers"

    credential_data["project_id"] = project_id

    if project_id or subscription_tier:
        await database.store_credential(email, credential_data)

        state_update = {
            "disabled": False,
            "error_codes": [],
            "tier": subscription_tier,
            "project_id": project_id,
        }

        await database.update_credential_state(email, state_update)

        log.info(f"检验凭证成功: {email} - Project ID: {project_id}, Tier: {subscription_tier}")

        return JSONResponse(content={
            "success": True,
            "email": email,
            "project_id": project_id,
            "subscription_tier": subscription_tier,
            "message": "检验成功！已解除禁用状态并清除错误码",
        })
    else:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "email": email,
                "message": "检验失败：无法获取Project ID，请检查凭证是否有效",
            }
        )


# =============================================================================
# 路由处理函数
# =============================================================================


@router.post("/upload")
async def upload_credentials( files: List[UploadFile] = File(...),
    token: str = Depends(verify_panel_token),
):
    """批量导入凭证文件（规范 JSON / ZIP）"""
    try:
        return await upload_credentials_common(files)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"批量导入失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_creds_status(
    token: str = Depends(verify_panel_token),
    offset: int = 0,
    limit: int = 50,
    status_filter: str = "all",
    error_code_filter: str = "all",
    cooldown_filter: str = "all",
    tier_filter: str = "all"):
    """获取凭证状态（轻量级摘要，支持分页和状态筛选）"""
    try:
        return await get_creds_status_common(
            offset, limit, status_filter,
            error_code_filter=error_code_filter,
            cooldown_filter=cooldown_filter,
            tier_filter=tier_filter
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取凭证状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/action")
async def creds_action(
    request: CredFileActionRequest,
    token: str = Depends(verify_panel_token)):
    """对凭证执行操作（启用/禁用/删除）"""
    try:

        email = request.email
        action = request.action

        log.info(f"Performing action '{action}' on credential: {email}")

        database = await get_database()

        if action == "enable":
            result = await set_cred_disabled(email, False)
            if result:
                return JSONResponse(content={"message": f"已启用凭证 {email}"})
            raise HTTPException(status_code=500, detail="启用凭证失败，可能凭证不存在")

        elif action == "disable":
            result = await set_cred_disabled(email, True)
            if result:
                return JSONResponse(content={"message": f"已禁用凭证 {email}"})
            raise HTTPException(status_code=500, detail="禁用凭证失败，可能凭证不存在")

        elif action == "delete":
            success = await remove_credential(email)
            if success:
                return JSONResponse(content={"message": f"已删除凭证 {email}"})
            raise HTTPException(status_code=500, detail="删除凭证失败")

        else:
            raise HTTPException(status_code=400, detail="无效的操作类型")

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"凭证操作失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch-action")
async def creds_batch_action(
    request: CredFileBatchActionRequest,
    token: str = Depends(verify_panel_token)):
    """批量对凭证执行操作（启用/禁用/删除）"""
    try:

        action = request.action
        emails = request.emails

        if not emails:
            raise HTTPException(status_code=400, detail="凭证列表不能为空")

        log.info(f"对 {len(emails)} 个凭证执行批量操作 '{action}'")

        success_count = 0
        errors = []

        for email in emails:
            try:
                if action == "enable":
                    await set_cred_disabled(email, False)
                    success_count += 1
                elif action == "disable":
                    await set_cred_disabled(email, True)
                    success_count += 1
                elif action == "delete":
                    if await remove_credential(email):
                        success_count += 1
                    else:
                        errors.append(f"{email}: 删除失败")
                        continue
                else:
                    errors.append(f"{email}: 无效的操作类型")
                    continue
            except Exception as e:
                log.error(f"处理 {email} 时出错: {e}")
                errors.append(f"{email}: 处理失败 - {str(e)}")
                continue

        result_message = f"批量操作完成：成功处理 {success_count}/{len(emails)} 个凭证"
        if errors:
            result_message += "\n错误详情:\n" + "\n".join(errors)

        return JSONResponse(content={
            "success_count": success_count,
            "total_count": len(emails),
            "errors": errors,
            "message": result_message,
        })

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"批量凭证操作失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export/{email}")
async def export_credential(
    email: str,
    token: str = Depends(verify_panel_token)):
    """导出单个凭证（规范格式，文件名=邮箱.json）"""
    try:
        database = await get_database()

        cred = await database.get_credential(email)
        if not cred:
            raise HTTPException(status_code=404, detail="凭证不存在")

        state = await database.get_credential_state(email)

        export_data = {
            "refresh_token": cred.get("refresh_token", ""),
            "email": email,
            "tier": state.get("tier", "pro"),
            "project_id": cred.get("project_id", ""),
        }

        content = json.dumps(export_data, ensure_ascii=False, indent=2)

        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{email}.json"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"导出凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-all")
async def download_all_creds(
    token: str = Depends(verify_panel_token)):
    """打包导出所有凭证（每个文件为 邮箱.json）"""
    try:
        return await download_all_creds_common()
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"打包导出失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-project/{email}")
async def verify_credential_project(
    email: str,
    token: str = Depends(verify_panel_token)):
    """检验凭证（重新获取 Project ID / Tier，可修复 403）"""
    try:
        return await verify_credential_project_common(email)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"检验凭证失败 {email}: {e}")
        raise HTTPException(status_code=500, detail=f"检验失败: {str(e)}")


@router.get("/errors/{email}")
async def get_credential_errors(
    email: str,
    token: str = Depends(verify_panel_token)):
    """获取凭证的错误信息"""
    try:
        database = await get_database()
        error_info = await database.get_credential_errors(email)
        return JSONResponse(content=error_info)

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取凭证错误信息失败 {email}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quota/{email}")
async def get_credential_quota(
    email: str,
    token: str = Depends(verify_panel_token)):
    """获取凭证的额度信息"""
    try:
        database = await get_database()

        credential_data = await database.get_credential(email)
        if not credential_data:
            raise HTTPException(status_code=404, detail="凭证不存在")

        creds = Credentials.from_dict(credential_data)

        await creds.refresh_if_needed()

        updated_data = creds.to_dict()
        if updated_data != credential_data:
            log.info(f"Token已自动刷新: {email}")
            await database.store_credential(email, updated_data)
            credential_data = updated_data

        access_token = credential_data.get("access_token") or credential_data.get("token")
        if not access_token:
            raise HTTPException(status_code=400, detail="凭证中没有访问令牌")

        quota_info = await fetch_quota_info(access_token, credential_data.get("project_id", ""))

        if quota_info.get("success"):
            result = {
                "success": True,
                "email": email,
                "models": quota_info.get("models", {})
            }
            for extra_key in ("minResetDays", "accountTier", "isPro", "quotaGroups", "source"):
                if extra_key in quota_info:
                    result[extra_key] = quota_info[extra_key]
            return JSONResponse(content=result)
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "email": email,
                    "error": quota_info.get("error", "未知错误")
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取凭证额度失败 {email}: {e}")
        raise HTTPException(status_code=500, detail=f"获取额度失败: {str(e)}")


@router.post("/test/{email}")
async def test_credential(
    email: str,
    _token: str = Depends(verify_panel_token)
):
    """
    测试凭证是否可用

    Returns:
        - 200: 凭证可用
        - 429: 凭证被限流但有效
        - 其他: 凭证失败（返回实际错误码）
    """
    try:
        database = await get_database()

        credential_data = await database.get_credential(email)
        if not credential_data:
            raise HTTPException(status_code=404, detail="凭证不存在")

        credentials = Credentials.from_dict(credential_data)
        token_refreshed = await credentials.refresh_if_needed()

        if token_refreshed:
            log.info(f"Token已自动刷新: {email}")
            credential_data = credentials.to_dict()
            await database.store_credential(email, credential_data)

        access_token = credential_data.get("access_token") or credential_data.get("token")
        if not access_token:
            raise HTTPException(status_code=400, detail="凭证中没有访问令牌")

        from src.httpx_client import post_async

        project_id = credential_data.get("project_id", "")
        if not project_id:
            raise HTTPException(status_code=400, detail="凭证中没有项目ID")

        test_model = "gemini-2.5-flash"

        api_base_url = await get_antigravity_api_url()
        headers = build_antigravity_headers(access_token)

        response = await post_async(
            url=f"{api_base_url}/v1internal:generateContent",
            json={
                "model": test_model,
                "project": project_id,
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                    "generationConfig": {"maxOutputTokens": 1}
                }
            },
            headers=headers,
            timeout=30.0
        )

        status_code = response.status_code

        if status_code == 200 or status_code == 429:
            log.info(f"凭证测试成功: {email} (status={status_code})")
            if status_code == 200:
                await database.update_credential_state(email, {
                    "error_codes": [],
                    "error_messages": {}
                })

            return JSONResponse(
                status_code=status_code,
                content={
                    "success": True,
                    "status_code": status_code,
                    "message": "测试成功",
                    "email": email
                }
            )
        else:
            log.warning(f"凭证测试失败: {email} (status={status_code})")
            try:
                error_text = response.text if hasattr(response, "text") else ""
                error_codes = [status_code]
                error_messages = {str(status_code): error_text if error_text else f"HTTP {status_code}"}

                await database.update_credential_state(email, {
                    "error_codes": error_codes,
                    "error_messages": error_messages
                })
            except Exception as e:
                log.error(f"保存测试错误信息失败: {e}")

        error_text = response.text if hasattr(response, "text") else ""

        return JSONResponse(
            status_code=status_code,
            content={
                "success": False,
                "status_code": status_code,
                "message": f"测试失败: HTTP {status_code}",
                "error": error_text,
                "email": email
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"测试凭证失败 {email}: {e}")
        raise HTTPException(status_code=500, detail=f"测试失败: {str(e)}")

"""
凭证管理路由模块 - 处理 /creds/* 相关的HTTP请求
"""

import asyncio
import io
import json
import os
import time
import zipfile
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Response
from fastapi.responses import JSONResponse

from log import log
from src.credential_manager import credential_manager
from src.models import (
    CredFileActionRequest,
    CredFileBatchActionRequest
)
from src.storage_adapter import get_storage_adapter
from src.utils import verify_panel_token, GEMINICLI_USER_AGENT, ANTIGRAVITY_USER_AGENT
from src.api.antigravity import fetch_quota_info
from src.google_oauth_api import Credentials, fetch_project_id
from config import get_code_assist_endpoint, get_antigravity_api_url
from .utils import validate_mode


# 创建路由器
router = APIRouter(prefix="/creds", tags=["credentials"])


# =============================================================================
# 工具函数 (Helper Functions)
# =============================================================================


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
    files: List[UploadFile], mode: str = "geminicli"
) -> JSONResponse:
    """批量上传凭证文件的通用函数"""
    mode = validate_mode(mode)

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
                if mode == "antigravity":
                    await credential_manager.add_antigravity_credential(filename, credential_data)
                else:
                    await credential_manager.add_credential(filename, credential_data)

                log.debug(f"成功上传 {mode} 凭证文件: {filename}")
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

        log.info(f"开始并发处理 {len(batch_files)} 个 {mode} 文件...")
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
            f"{batch_uploaded_count}/{len(batch_files)} 个 {mode} 文件"
        )

    if total_success > 0:
        return JSONResponse(
            content={
                "uploaded_count": total_success,
                "total_count": len(files_data),
                "results": all_results,
                "message": f"批量上传完成: 成功 {total_success}/{len(files_data)} 个 {mode} 文件",
            }
        )
    else:
        raise HTTPException(status_code=400, detail=f"没有 {mode} 文件上传成功")


async def get_creds_status_common(
    offset: int, limit: int, status_filter: str, mode: str = "geminicli",
    error_code_filter: str = None, cooldown_filter: str = None, preview_filter: str = None
) -> JSONResponse:
    """获取凭证文件状态的通用函数"""
    mode = validate_mode(mode)
    # 验证分页参数
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset 必须大于等于 0")
    if limit not in [20, 50, 100, 200, 500, 1000]:
        raise HTTPException(status_code=400, detail="limit 只能是 20、50、100、200、500 或 1000")
    if status_filter not in ["all", "enabled", "disabled"]:
        raise HTTPException(status_code=400, detail="status_filter 只能是 all、enabled 或 disabled")
    if cooldown_filter and cooldown_filter not in ["all", "in_cooldown", "no_cooldown"]:
        raise HTTPException(status_code=400, detail="cooldown_filter 只能是 all、in_cooldown 或 no_cooldown")
    if preview_filter and preview_filter not in ["all", "preview", "no_preview"]:
        raise HTTPException(status_code=400, detail="preview_filter 只能是 all、preview 或 no_preview")



    storage_adapter = await get_storage_adapter()
    backend_info = await storage_adapter.get_backend_info()
    backend_type = backend_info.get("backend_type", "unknown")

    # 使用高性能的分页摘要查询
    result = await storage_adapter._backend.get_credentials_summary(
        offset=offset,
        limit=limit,
        status_filter=status_filter,
        mode=mode,
        error_code_filter=error_code_filter if error_code_filter and error_code_filter != "all" else None,
        cooldown_filter=cooldown_filter if cooldown_filter and cooldown_filter != "all" else None,
        preview_filter=preview_filter if preview_filter and preview_filter != "all" else None
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

        # 只对 geminicli 模式添加 preview 字段
        if mode == "geminicli":
            cred_info["preview"] = summary.get("preview", True)

        creds_list.append(cred_info)

    return JSONResponse(content={
        "items": creds_list,
        "total": result["total"],
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < result["total"],
        "stats": result.get("stats", {"total": 0, "normal": 0, "disabled": 0}),
    })


async def download_all_creds_common(mode: str = "geminicli") -> Response:
    """打包下载所有凭证文件的通用函数"""
    mode = validate_mode(mode)
    zip_filename = "antigravity_credentials.zip" if mode == "antigravity" else "credentials.zip"

    storage_adapter = await get_storage_adapter()
    credential_filenames = await storage_adapter.list_credentials(mode=mode)

    if not credential_filenames:
        raise HTTPException(status_code=404, detail=f"没有找到 {mode} 凭证文件")

    log.info(f"开始打包 {len(credential_filenames)} 个 {mode} 凭证文件...")

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        success_count = 0
        for idx, filename in enumerate(credential_filenames, 1):
            try:
                credential_data = await storage_adapter.get_credential(filename, mode=mode)
                if credential_data:
                    content = json.dumps(credential_data, ensure_ascii=False, indent=2)
                    zip_file.writestr(os.path.basename(filename), content)
                    success_count += 1

                    if idx % 10 == 0:
                        log.debug(f"打包进度: {idx}/{len(credential_filenames)}")

            except Exception as e:
                log.warning(f"处理 {mode} 凭证文件 {filename} 时出错: {e}")
                continue

    log.info(f"打包完成: 成功 {success_count}/{len(credential_filenames)} 个文件")

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_filename}"},
    )


async def fetch_user_email_common(filename: str, mode: str = "geminicli") -> JSONResponse:
    """获取指定凭证文件用户邮箱的通用函数"""
    mode = validate_mode(mode)

    filename_only = os.path.basename(filename)
    if not filename_only.endswith(".json"):
        raise HTTPException(status_code=404, detail="无效的文件名")

    storage_adapter = await get_storage_adapter()
    credential_data = await storage_adapter.get_credential(filename_only, mode=mode)
    if not credential_data:
        raise HTTPException(status_code=404, detail="凭证文件不存在")

    email = await credential_manager.get_or_fetch_user_email(filename_only, mode=mode)

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


async def refresh_all_user_emails_common(mode: str = "geminicli") -> JSONResponse:
    """刷新所有凭证文件用户邮箱的通用函数 - 只为没有邮箱的凭证获取

    利用 get_all_credential_states 批量获取状态
    """
    mode = validate_mode(mode)

    storage_adapter = await get_storage_adapter()

    # 一次性批量获取所有凭证的状态
    all_states = await storage_adapter.get_all_credential_states(mode=mode)

    results = []
    success_count = 0
    skipped_count = 0

    # 在内存中筛选出需要获取邮箱的凭证
    for filename, state in all_states.items():
        try:
            cached_email = state.get("user_email")

            if cached_email:
                # 已有邮箱，跳过获取
                skipped_count += 1
                results.append({
                    "filename": os.path.basename(filename),
                    "user_email": cached_email,
                    "success": True,
                    "skipped": True,
                })
                continue

            # 没有邮箱，尝试获取
            email = await credential_manager.get_or_fetch_user_email(filename, mode=mode)
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

    total_count = len(all_states)
    return JSONResponse(
        content={
            "success_count": success_count,
            "total_count": total_count,
            "skipped_count": skipped_count,
            "results": results,
            "message": f"成功获取 {success_count}/{total_count} 个邮箱地址，跳过 {skipped_count} 个已有邮箱的凭证",
        }
    )


async def deduplicate_credentials_by_email_common(mode: str = "geminicli") -> JSONResponse:
    """批量去重凭证文件的通用函数 - 删除邮箱相同的凭证（只保留一个）"""
    mode = validate_mode(mode)
    storage_adapter = await get_storage_adapter()

    try:
        duplicate_info = await storage_adapter._backend.get_duplicate_credentials_by_email(
            mode=mode
        )

        duplicate_groups = duplicate_info.get("duplicate_groups", [])
        no_email_files = duplicate_info.get("no_email_files", [])
        total_count = duplicate_info.get("total_count", 0)

        if not duplicate_groups:
            return JSONResponse(
                content={
                    "deleted_count": 0,
                    "kept_count": total_count,
                    "total_count": total_count,
                    "unique_emails_count": duplicate_info.get("unique_email_count", 0),
                    "no_email_count": len(no_email_files),
                    "duplicate_groups": [],
                    "delete_errors": [],
                    "message": "没有发现重复的凭证（相同邮箱）",
                }
            )

        # 执行删除操作
        deleted_count = 0
        delete_errors = []
        result_duplicate_groups = []

        for group in duplicate_groups:
            email = group["email"]
            kept_file = group["kept_file"]
            duplicate_files = group["duplicate_files"]

            deleted_files_in_group = []
            for filename in duplicate_files:
                try:
                    success = await credential_manager.remove_credential(filename, mode=mode)
                    if success:
                        deleted_count += 1
                        deleted_files_in_group.append(os.path.basename(filename))
                        log.info(f"去重删除凭证: {filename} (邮箱: {email}) (mode={mode})")
                    else:
                        delete_errors.append(f"{os.path.basename(filename)}: 删除失败")
                except Exception as e:
                    delete_errors.append(f"{os.path.basename(filename)}: {str(e)}")
                    log.error(f"去重删除凭证 {filename} 时出错: {e}")

            result_duplicate_groups.append({
                "email": email,
                "kept_file": os.path.basename(kept_file),
                "deleted_files": deleted_files_in_group,
                "duplicate_count": len(deleted_files_in_group),
            })

        kept_count = total_count - deleted_count

        return JSONResponse(
            content={
                "deleted_count": deleted_count,
                "kept_count": kept_count,
                "total_count": total_count,
                "unique_emails_count": duplicate_info.get("unique_email_count", 0),
                "no_email_count": len(no_email_files),
                "duplicate_groups": result_duplicate_groups,
                "delete_errors": delete_errors,
                "message": f"去重完成：删除 {deleted_count} 个重复凭证，保留 {kept_count} 个凭证（{duplicate_info.get('unique_email_count', 0)} 个唯一邮箱）",
            }
        )

    except Exception as e:
        log.error(f"批量去重凭证时出错: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "deleted_count": 0,
                "kept_count": 0,
                "total_count": 0,
                "message": f"去重操作失败: {str(e)}",
            }
        )


async def verify_credential_project_common(filename: str, mode: str = "geminicli") -> JSONResponse:
    """验证并重新获取凭证的project id的通用函数"""
    mode = validate_mode(mode)

    # 验证文件名
    if not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="无效的文件名")


    storage_adapter = await get_storage_adapter()

    # 获取凭证数据
    credential_data = await storage_adapter.get_credential(filename, mode=mode)
    if not credential_data:
        raise HTTPException(status_code=404, detail="凭证不存在")

    # 创建凭证对象
    credentials = Credentials.from_dict(credential_data)

    # 确保token有效（自动刷新）
    token_refreshed = await credentials.refresh_if_needed()

    # 如果token被刷新了，更新存储
    if token_refreshed:
        log.info(f"Token已自动刷新: {filename} (mode={mode})")
        credential_data = credentials.to_dict()
        await storage_adapter.store_credential(filename, credential_data, mode=mode)

    # 获取API端点和对应的User-Agent
    if mode == "antigravity":
        api_base_url = await get_antigravity_api_url()
        user_agent = ANTIGRAVITY_USER_AGENT
    else:
        api_base_url = await get_code_assist_endpoint()
        user_agent = GEMINICLI_USER_AGENT

    # 重新获取project id
    project_id = await fetch_project_id(
        access_token=credentials.access_token,
        user_agent=user_agent,
        api_base_url=api_base_url
    )

    if project_id:
        # 更新凭证数据中的project_id
        credential_data["project_id"] = project_id
        await storage_adapter.store_credential(filename, credential_data, mode=mode)

        # 检验成功后自动解除禁用状态并清除错误码
        state_update = {
            "disabled": False,
            "error_codes": []
        }

        # 如果是 geminicli 模式，直接设置 preview=True
        if mode == "geminicli":
            state_update["preview"] = True

        await storage_adapter.update_credential_state(filename, state_update, mode=mode)

        log.info(f"检验 {mode} 凭证成功: {filename} - Project ID: {project_id} - 已解除禁用并清除错误码")

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


# =============================================================================
# 路由处理函数 (Route Handlers)
# =============================================================================


@router.post("/upload")
async def upload_credentials(
    files: List[UploadFile] = File(...),
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """批量上传凭证文件"""
    try:
        mode = validate_mode(mode)
        return await upload_credentials_common(files, mode=mode)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"批量上传失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_creds_status(
    token: str = Depends(verify_panel_token),
    offset: int = 0,
    limit: int = 50,
    status_filter: str = "all",
    error_code_filter: str = "all",
    cooldown_filter: str = "all",
    preview_filter: str = "all",
    mode: str = "geminicli"
):
    """
    获取凭证文件的状态（轻量级摘要，不包含完整凭证数据，支持分页和状态筛选）

    Args:
        offset: 跳过的记录数（默认0）
        limit: 每页返回的记录数（默认50，可选：20, 50, 100, 200, 500, 1000）
        status_filter: 状态筛选（all=全部, enabled=仅启用, disabled=仅禁用）
        error_code_filter: 错误码筛选（all=全部, 或具体错误码如"400", "403"）
        cooldown_filter: 冷却状态筛选（all=全部, in_cooldown=冷却中, no_cooldown=未冷却）
        preview_filter: Preview筛选（all=全部, preview=支持preview, no_preview=不支持preview，仅geminicli模式有效）
        mode: 凭证模式（geminicli 或 antigravity）

    Returns:
        包含凭证列表、总数、分页信息的响应
    """
    try:
        mode = validate_mode(mode)
        return await get_creds_status_common(
            offset, limit, status_filter, mode=mode,
            error_code_filter=error_code_filter,
            cooldown_filter=cooldown_filter,
            preview_filter=preview_filter
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取凭证状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detail/{filename}")
async def get_cred_detail(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """
    按需获取单个凭证的详细数据（包含完整凭证内容）
    用于用户查看/编辑凭证详情
    """
    try:
        mode = validate_mode(mode)
        # 验证文件名
        if not filename.endswith(".json"):
            raise HTTPException(status_code=400, detail="无效的文件名")



        storage_adapter = await get_storage_adapter()
        backend_info = await storage_adapter.get_backend_info()
        backend_type = backend_info.get("backend_type", "unknown")

        # 获取凭证数据
        credential_data = await storage_adapter.get_credential(filename, mode=mode)
        if not credential_data:
            raise HTTPException(status_code=404, detail="凭证不存在")

        # 获取状态信息
        file_status = await storage_adapter.get_credential_state(filename, mode=mode)
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

        # 只对 geminicli 模式添加 preview 字段
        if mode == "geminicli":
            result["preview"] = file_status.get("preview", True)

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


@router.post("/action")
async def creds_action(
    request: CredFileActionRequest,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """对凭证文件执行操作（启用/禁用/删除）"""
    try:
        mode = validate_mode(mode)

        log.info(f"Received request: {request}")

        filename = request.filename
        action = request.action

        log.info(f"Performing action '{action}' on file: {filename} (mode={mode})")

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
            credential_data = await storage_adapter.get_credential(filename, mode=mode)
            if not credential_data:
                log.error(f"凭证未找到: {filename} (mode={mode})")
                raise HTTPException(status_code=404, detail="凭证文件不存在")

        if action == "enable":
            log.info(f"Web请求: 启用文件 {filename} (mode={mode})")
            result = await credential_manager.set_cred_disabled(filename, False, mode=mode)
            log.info(f"[WebRoute] set_cred_disabled 返回结果: {result}")
            if result:
                log.info(f"Web请求: 文件 {filename} 已成功启用 (mode={mode})")
                return JSONResponse(content={"message": f"已启用凭证文件 {os.path.basename(filename)}"})
            else:
                log.error(f"Web请求: 文件 {filename} 启用失败 (mode={mode})")
                raise HTTPException(status_code=500, detail="启用凭证失败，可能凭证不存在")

        elif action == "disable":
            log.info(f"Web请求: 禁用文件 {filename} (mode={mode})")
            result = await credential_manager.set_cred_disabled(filename, True, mode=mode)
            log.info(f"[WebRoute] set_cred_disabled 返回结果: {result}")
            if result:
                log.info(f"Web请求: 文件 {filename} 已成功禁用 (mode={mode})")
                return JSONResponse(content={"message": f"已禁用凭证文件 {os.path.basename(filename)}"})
            else:
                log.error(f"Web请求: 文件 {filename} 禁用失败 (mode={mode})")
                raise HTTPException(status_code=500, detail="禁用凭证失败，可能凭证不存在")

        elif action == "delete":
            try:
                # 使用 CredentialManager 删除凭证（包含队列/状态同步）
                success = await credential_manager.remove_credential(filename, mode=mode)
                if success:
                    log.info(f"通过管理器成功删除凭证: {filename} (mode={mode})")
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


@router.post("/batch-action")
async def creds_batch_action(
    request: CredFileBatchActionRequest,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """批量对凭证文件执行操作（启用/禁用/删除）"""
    try:
        mode = validate_mode(mode)

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
                    credential_data = await storage_adapter.get_credential(filename, mode=mode)
                    if not credential_data:
                        errors.append(f"{filename}: 凭证不存在")
                        continue

                # 执行相应操作
                if action == "enable":
                    await credential_manager.set_cred_disabled(filename, False, mode=mode)
                    success_count += 1

                elif action == "disable":
                    await credential_manager.set_cred_disabled(filename, True, mode=mode)
                    success_count += 1

                elif action == "delete":
                    try:
                        delete_success = await credential_manager.remove_credential(filename, mode=mode)
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


@router.get("/download/{filename}")
async def download_cred_file(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """下载单个凭证文件"""
    try:
        mode = validate_mode(mode)
        # 验证文件名安全性
        if not filename.endswith(".json"):
            raise HTTPException(status_code=404, detail="无效的文件名")

        # 获取存储适配器
        storage_adapter = await get_storage_adapter()

        # 从存储系统获取凭证数据
        credential_data = await storage_adapter.get_credential(filename, mode=mode)
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


@router.post("/fetch-email/{filename}")
async def fetch_user_email(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """获取指定凭证文件的用户邮箱地址"""
    try:
        mode = validate_mode(mode)
        return await fetch_user_email_common(filename, mode=mode)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取用户邮箱失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh-all-emails")
async def refresh_all_user_emails(
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """刷新所有凭证文件的用户邮箱地址"""
    try:
        mode = validate_mode(mode)
        return await refresh_all_user_emails_common(mode=mode)
    except Exception as e:
        log.error(f"批量获取用户邮箱失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/deduplicate-by-email")
async def deduplicate_credentials_by_email(
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """批量去重凭证文件 - 删除邮箱相同的凭证（只保留一个）"""
    try:
        mode = validate_mode(mode)
        return await deduplicate_credentials_by_email_common(mode=mode)
    except Exception as e:
        log.error(f"批量去重凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-all")
async def download_all_creds(
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """
    打包下载所有凭证文件（流式处理，按需加载每个凭证数据）
    只在实际下载时才加载完整凭证内容，最大化性能
    """
    try:
        mode = validate_mode(mode)
        return await download_all_creds_common(mode=mode)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"打包下载失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-project/{filename}")
async def verify_credential_project(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """
    检验凭证的project id，重新获取project id
    检验成功可以使403错误恢复
    """
    try:
        mode = validate_mode(mode)
        return await verify_credential_project_common(filename, mode=mode)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"检验凭证Project ID失败 {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"检验失败: {str(e)}")


@router.get("/errors/{filename}")
async def get_credential_errors(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """
    获取指定凭证的错误信息（包含 error_codes 和 error_messages）

    Args:
        filename: 凭证文件名
        mode: 凭证模式（geminicli 或 antigravity）

    Returns:
        包含 error_codes 和 error_messages 的 JSON 响应
    """
    try:
        mode = validate_mode(mode)

        # 验证文件名
        if not filename.endswith(".json"):
            raise HTTPException(status_code=400, detail="无效的文件名")

        storage_adapter = await get_storage_adapter()

        # 检查后端是否支持 get_credential_errors 方法
        if not hasattr(storage_adapter._backend, 'get_credential_errors'):
            raise HTTPException(
                status_code=501,
                detail="当前存储后端不支持获取错误信息"
            )

        # 获取错误信息
        error_info = await storage_adapter._backend.get_credential_errors(filename, mode=mode)

        return JSONResponse(content=error_info)

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取凭证错误信息失败 {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quota/{filename}")
async def get_credential_quota(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "antigravity"
):
    """
    获取指定凭证的额度信息（仅支持 antigravity 模式）
    """
    try:
        mode = validate_mode(mode)
        # 验证文件名
        if not filename.endswith(".json"):
            raise HTTPException(status_code=400, detail="无效的文件名")


        storage_adapter = await get_storage_adapter()

        # 获取凭证数据
        credential_data = await storage_adapter.get_credential(filename, mode=mode)
        if not credential_data:
            raise HTTPException(status_code=404, detail="凭证不存在")

        # 使用 Credentials 对象自动处理 token 刷新
        from src.google_oauth_api import Credentials

        creds = Credentials.from_dict(credential_data)

        # 自动刷新 token（如果需要）
        await creds.refresh_if_needed()

        # 如果 token 被刷新了，更新存储
        updated_data = creds.to_dict()
        if updated_data != credential_data:
            log.info(f"Token已自动刷新: {filename}")
            await storage_adapter.store_credential(filename, updated_data, mode=mode)
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
        log.error(f"获取凭证额度失败 {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"获取额度失败: {str(e)}")


@router.post("/configure-preview/{filename}")
async def configure_preview_channel(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """
    为 geminicli 凭证配置 preview 通道

    通过调用 Google Cloud API 设置 release_channel 为 EXPERIMENTAL,
    配置成功后将 preview 属性设置为 true

    Args:
        filename: 凭证文件名
        mode: 凭证模式（仅支持 geminicli）

    Returns:
        配置结果信息
    """
    try:
        mode = validate_mode(mode)

        # 只支持 geminicli 模式
        if mode != "geminicli":
            raise HTTPException(
                status_code=400,
                detail="配置 preview 通道仅支持 geminicli 模式"
            )

        # 验证文件名
        if not filename.endswith(".json"):
            raise HTTPException(status_code=400, detail="无效的文件名")

        storage_adapter = await get_storage_adapter()

        # 获取凭证数据
        credential_data = await storage_adapter.get_credential(filename, mode=mode)
        if not credential_data:
            raise HTTPException(status_code=404, detail="凭证不存在")

        # 创建凭证对象并刷新 token（如果需要）
        credentials = Credentials.from_dict(credential_data)
        token_refreshed = await credentials.refresh_if_needed()

        if token_refreshed:
            log.info(f"Token已自动刷新: {filename}")
            credential_data = credentials.to_dict()
            await storage_adapter.store_credential(filename, credential_data, mode=mode)

        # 获取 access_token 和 project_id
        access_token = credential_data.get("access_token") or credential_data.get("token")
        project_id = credential_data.get("project_id", "")

        if not access_token:
            raise HTTPException(status_code=400, detail="凭证中没有访问令牌")
        if not project_id:
            raise HTTPException(status_code=400, detail="凭证中没有项目ID")

        # 调用 Google Cloud API 配置 preview 通道
        # 根据文档，需要两个步骤：
        # 1. 创建 Release Channel Setting (EXPERIMENTAL)
        # 2. 创建 Setting Binding (绑定到目标项目)
        from src.httpx_client import post_async
        import uuid

        # 生成唯一的 ID
        setting_id = f"preview-setting-{uuid.uuid4().hex[:8]}"
        binding_id = f"preview-binding-{uuid.uuid4().hex[:8]}"

        base_url = f"https://cloudaicompanion.googleapis.com/v1/projects/{project_id}/locations/global"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        log.info(f"开始配置 preview 通道: {filename} (project_id={project_id})")

        # 步骤 1: 创建 Release Channel Setting
        setting_url = f"{base_url}/releaseChannelSettings"
        setting_response = await post_async(
            url=setting_url,
            json={"release_channel": "EXPERIMENTAL"},
            headers=headers,
            params={"release_channel_setting_id": setting_id},
            timeout=30.0
        )

        setting_status = setting_response.status_code

        if setting_status == 200 or setting_status == 201:
            log.info(f"步骤 1/2: Release Channel Setting 创建成功 (setting_id={setting_id})")
        elif setting_status == 409:
            # Setting 已存在，继续下一步
            log.info(f"步骤 1/2: Release Channel Setting 已存在")
        else:
            # 步骤 1 失败
            error_text = setting_response.text if hasattr(setting_response, 'text') else ""
            log.error(f"步骤 1/2 失败: {filename} - Status: {setting_status}, Error: {error_text}")

            return JSONResponse(
                status_code=setting_status,
                content={
                    "success": False,
                    "filename": filename,
                    "preview": False,
                    "message": f"创建 Release Channel Setting 失败: HTTP {setting_status}",
                    "error": error_text,
                    "step": "create_setting"
                }
            )

        # 步骤 2: 创建 Setting Binding (绑定到当前项目)
        binding_url = f"{base_url}/releaseChannelSettings/{setting_id}/settingBindings"
        binding_response = await post_async(
            url=binding_url,
            json={
                "target": f"projects/{project_id}",
                "product": "GEMINI_CODE_ASSIST"
            },
            headers=headers,
            params={"setting_binding_id": binding_id},
            timeout=30.0
        )

        binding_status = binding_response.status_code

        if binding_status == 200 or binding_status == 201:
            # 配置成功，设置 preview=True
            await storage_adapter.update_credential_state(filename, {
                "preview": True
            }, mode=mode)

            log.info(f"步骤 2/2: Setting Binding 创建成功 - Preview 通道配置完成: {filename}")

            return JSONResponse(content={
                "success": True,
                "filename": filename,
                "preview": True,
                "message": "Preview 通道配置成功，已将 preview 属性设置为 true",
                "setting_id": setting_id,
                "binding_id": binding_id
            })
        elif binding_status == 409:
            # Binding 已存在，说明已经配置过了
            await storage_adapter.update_credential_state(filename, {
                "preview": True
            }, mode=mode)

            log.info(f"步骤 2/2: Setting Binding 已存在 - Preview 通道已配置: {filename}")

            return JSONResponse(content={
                "success": True,
                "filename": filename,
                "preview": True,
                "message": "Preview 通道配置已存在，已将 preview 属性设置为 true"
            })
        else:
            # 步骤 2 失败
            error_text = binding_response.text if hasattr(binding_response, 'text') else ""
            log.error(f"步骤 2/2 失败: {filename} - Status: {binding_status}, Error: {error_text}")

            return JSONResponse(
                status_code=binding_status,
                content={
                    "success": False,
                    "filename": filename,
                    "preview": False,
                    "message": f"创建 Setting Binding 失败: HTTP {binding_status}",
                    "error": error_text,
                    "step": "create_binding"
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"配置 preview 通道失败 {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"配置失败: {str(e)}")


@router.post("/test/{filename}")
async def test_credential(
    filename: str,
    mode: str = "geminicli",
    _token: str = Depends(verify_panel_token)
):
    """
    测试指定凭证是否可用

    Args:
        filename: 凭证文件名
        mode: 凭证模式（geminicli 或 antigravity）

    Returns:
        返回状态码：
        - 200: 凭证可用
        - 429: 凭证被限流但有效
        - 其他: 凭证失败（返回实际错误码）
    """
    try:
        mode = validate_mode(mode)

        # 验证文件名
        if not filename.endswith(".json"):
            raise HTTPException(status_code=400, detail="无效的文件名")

        storage_adapter = await get_storage_adapter()

        # 获取凭证数据
        credential_data = await storage_adapter.get_credential(filename, mode=mode)
        if not credential_data:
            raise HTTPException(status_code=404, detail="凭证不存在")

        # 创建凭证对象并尝试刷新 token（如果需要）
        credentials = Credentials.from_dict(credential_data)
        token_refreshed = await credentials.refresh_if_needed()

        # 如果 token 被刷新了，更新存储
        if token_refreshed:
            log.info(f"Token已自动刷新: {filename} (mode={mode})")
            credential_data = credentials.to_dict()
            await storage_adapter.store_credential(filename, credential_data, mode=mode)

        # 获取访问令牌
        access_token = credential_data.get("access_token") or credential_data.get("token")
        if not access_token:
            raise HTTPException(status_code=400, detail="凭证中没有访问令牌")

        # 根据模式构造测试请求
        from src.httpx_client import post_async

        # 获取 project_id
        project_id = credential_data.get("project_id", "")
        if not project_id:
            raise HTTPException(status_code=400, detail="凭证中没有项目ID")

        # 根据模式选择 API 端点和请求头
        # 对于 geminicli 模式，使用两次测试：gemini-2.5-flash 和 gemini-3-flash-preview
        # 对于 antigravity 模式，只使用 gemini-2.5-flash
        test_model = "gemini-2.5-flash"

        if mode == "antigravity":
            api_base_url = await get_antigravity_api_url()
            from src.api.antigravity import build_antigravity_headers
            headers = build_antigravity_headers(access_token, test_model)
        else:
            api_base_url = await get_code_assist_endpoint()
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "User-Agent": GEMINICLI_USER_AGENT,
            }

        # 第一次测试：使用 gemini-2.5-flash
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

        # 返回实际的状态码
        status_code = response.status_code

        if status_code == 200 or status_code == 429:
            log.info(f"凭证测试成功: {filename} (mode={mode}, model={test_model}, status={status_code})")
            # 测试成功时清除错误状态
            if status_code == 200:
                await storage_adapter.update_credential_state(filename, {
                    "error_codes": [],
                    "error_messages": {}
                }, mode=mode)

                # 如果是 geminicli 模式且第一次测试成功，继续测试 gemini-3-flash-preview
                if mode == "geminicli":
                    preview_model = "gemini-3-flash-preview"
                    log.info(f"开始测试 preview 模型: {filename} (model={preview_model})")

                    try:
                        preview_response = await post_async(
                            url=f"{api_base_url}/v1internal:generateContent",
                            json={
                                "model": preview_model,
                                "project": project_id,
                                "request": {
                                    "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                                    "generationConfig": {"maxOutputTokens": 1}
                                }
                            },
                            headers=headers,
                            timeout=30.0
                        )

                        preview_status = preview_response.status_code

                        if preview_status == 200 or preview_status == 429:
                            # preview 模型测试成功，设置 preview=True
                            log.info(f"Preview 模型测试成功: {filename} (status={preview_status})")
                            await storage_adapter.update_credential_state(filename, {
                                "preview": True
                            }, mode=mode)
                        elif preview_status == 404:
                            # preview 模型返回 404，说明不支持，设置 preview=False
                            log.warning(f"Preview 模型不支持: {filename} (status=404)")
                            await storage_adapter.update_credential_state(filename, {
                                "preview": False
                            }, mode=mode)
                        else:
                            # 其他错误，保持默认 preview 状态
                            log.warning(f"Preview 模型测试失败: {filename} (status={preview_status})")
                    except Exception as e:
                        log.error(f"Preview 模型测试异常: {filename} - {e}")
        else:
            log.warning(f"凭证测试失败: {filename} (mode={mode}, status={status_code})")
            # 测试失败时保存错误码和错误消息（覆盖模式，只保存最新的一个错误）
            try:
                error_text = response.text if hasattr(response, 'text') else ""

                # 打印详细错误内容到日志
                log.error(f"凭证测试错误详情 - 文件: {filename}, 模式: {mode}, 状态码: {status_code}, 错误内容: {error_text}")

                # 使用覆盖模式保存错误（与 credential_manager 保持一致）
                error_codes = [status_code]
                error_messages = {str(status_code): error_text if error_text else f"HTTP {status_code}"}

                # 更新状态
                await storage_adapter.update_credential_state(filename, {
                    "error_codes": error_codes,
                    "error_messages": error_messages
                }, mode=mode)

                log.info(f"已保存测试错误信息: {filename} - 错误码 {status_code}")
            except Exception as e:
                log.error(f"保存测试错误信息失败: {e}")

        return Response(status_code=status_code)

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"测试凭证失败 {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"测试失败: {str(e)}")

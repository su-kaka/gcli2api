"""
健康检查与自动处置模块
- 定时或手动探活全部凭证
- 成功：如被禁用则重新启用
- 429：禁用
- 其他错误：按配置（默认删除）删除或禁用
"""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from config import (
    PUBLIC_API_MODELS,
    get_base_model_name,
    get_code_assist_endpoint,
    get_healthcheck_concurrency,
    get_healthcheck_delete_on_error,
    get_healthcheck_enabled,
    get_healthcheck_interval,
    get_healthcheck_model,
    get_healthcheck_timeout,
)
from log import log

from .credential_manager import CredentialManager
from .google_chat_api import _prepare_request_headers_and_payload
from .httpx_client import http_client
from .storage_adapter import get_storage_adapter
from .task_manager import create_managed_task
from .utils import parse_quota_reset_timestamp

# 运行状态
_healthcheck_lock = asyncio.Lock()
_healthcheck_running = False
_last_result: Optional[Dict[str, Any]] = None
_last_started_at: Optional[float] = None
_last_finished_at: Optional[float] = None
_scheduler_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None


def _build_probe_payload(model: str) -> Dict[str, Any]:
    """构造最小探活请求payload"""
    return {
        "model": model,
        "request": {
            "contents": [{"role": "user", "parts": [{"text": "Hi"}]}],
            "generationConfig": {"topK": 1},
            "safetySettings": [],
        },
    }


async def _send_probe(
    credential_data: Dict[str, Any],
    model: str,
    timeout: float,
) -> Tuple[int, Optional[float], Optional[str]]:
    """
    对单个凭证发送探活请求

    Returns:
        (status_code, cooldown_until_ts, response_text)
    """
    payload = _build_probe_payload(model)
    base_model = get_base_model_name(model)
    use_public_api = base_model in PUBLIC_API_MODELS
    target_url = f"{await get_code_assist_endpoint()}/v1internal:generateContent"

    headers, final_payload, target_url = await _prepare_request_headers_and_payload(
        payload, credential_data, use_public_api, target_url
    )
    post_data = json.dumps(final_payload)

    async with http_client.get_client(timeout=timeout) as client:
        resp = await client.post(target_url, content=post_data, headers=headers)
        content_text: Optional[str] = None
        cooldown_until = None
        try:
            if resp.content:
                content_text = resp.text
                if resp.status_code == 429 and content_text:
                    try:
                        cooldown_until = parse_quota_reset_timestamp(json.loads(content_text))
                    except Exception:
                        cooldown_until = None
        except Exception:
            content_text = None

        return resp.status_code, cooldown_until, content_text


async def _process_credential(
    credential_name: str,
    credential_data: Dict[str, Any],
    cred_mgr: CredentialManager,
    delete_on_error: bool,
    model: str,
    timeout: float,
    dry_run: bool,
) -> Dict[str, Any]:
    """探活并按规则处理单个凭证"""
    result: Dict[str, Any] = {
        "credential": credential_name,
        "action": None,
        "status_code": None,
        "error": None,
    }

    try:
        # 确保token可用（必要时刷新）
        try:
            # 内部方法：检查是否需要刷新
            if await cred_mgr._should_refresh_token(credential_data):  # type: ignore[attr-defined]
                refreshed = await cred_mgr._refresh_token(credential_data, credential_name)  # type: ignore[attr-defined]
                if refreshed:
                    credential_data = refreshed
        except Exception as refresh_err:
            log.debug(f"健康检查刷新token失败 {credential_name}: {refresh_err}")

        status_code, cooldown_until, _ = await _send_probe(credential_data, model, timeout)
        result["status_code"] = status_code

        # 处理结果
        if dry_run:
            # 只记录预期动作
            if status_code == 200:
                result["action"] = "enable_if_disabled"
            elif status_code == 429:
                result["action"] = "disable"
            else:
                result["action"] = "delete" if delete_on_error else "disable"
            return result

        if status_code == 200:
            # 成功：如被禁用则恢复
            state = await cred_mgr._storage_adapter.get_credential_state(credential_name)
            if state.get("disabled"):
                await cred_mgr.set_cred_disabled(credential_name, False)
                result["action"] = "enabled"
            else:
                result["action"] = "unchanged"
            # 清理错误码与冷却
            await cred_mgr.update_credential_state(
                credential_name, {"error_codes": [], "cooldown_until": None, "last_success": time.time()}
            )
        elif status_code == 429:
            await cred_mgr.set_cred_disabled(credential_name, True)
            await cred_mgr.record_api_call_result(
                credential_name, False, 429, cooldown_until
            )
            result["action"] = "disabled_429"
            if cooldown_until:
                result["cooldown_until"] = cooldown_until
        else:
            if delete_on_error:
                removed = await cred_mgr.remove_credential(credential_name)
                result["action"] = "deleted" if removed else "delete_failed"
            else:
                await cred_mgr.set_cred_disabled(credential_name, True)
                await cred_mgr.record_api_call_result(credential_name, False, status_code)
                result["action"] = "disabled_error"
    except Exception as e:
        result["error"] = str(e)
        if not dry_run:
            if delete_on_error:
                removed = await cred_mgr.remove_credential(credential_name)
                result["action"] = "deleted_exception" if removed else "delete_failed"
            else:
                await cred_mgr.set_cred_disabled(credential_name, True)
                result["action"] = "disabled_exception"

    return result


async def run_healthcheck(dry_run: bool = False, limit: Optional[int] = None) -> Dict[str, Any]:
    """
    手动执行一次健康检查（可选dry_run）
    """
    global _healthcheck_running, _last_result, _last_started_at, _last_finished_at

    async with _healthcheck_lock:
        if _healthcheck_running:
            raise RuntimeError("健康检查任务正在运行")
        _healthcheck_running = True

    _last_started_at = time.time()
    start = time.time()

    try:
        model = await get_healthcheck_model()
        timeout = await get_healthcheck_timeout()
        concurrency = await get_healthcheck_concurrency()
        delete_on_error = await get_healthcheck_delete_on_error()

        storage_adapter = await get_storage_adapter()
        cred_mgr = CredentialManager()
        await cred_mgr.initialize()

        # 获取凭证列表，保持持久顺序，同时确保禁用凭证也被纳入
        ordered = []
        try:
            ordered = await storage_adapter.get_credential_order()
        except Exception:
            ordered = []

        # 合并所有凭证（包含禁用的）
        try:
            all_creds = await storage_adapter.list_credentials()
        except Exception:
            all_creds = []

        # 将不在顺序中的凭证追加到末尾，保证禁用凭证也参与验活
        for name in all_creds:
            if name not in ordered:
                ordered.append(name)

        # 仍然容错为空
        if not ordered:
            ordered = []

        if limit is not None and limit > 0:
            ordered = ordered[:limit]

        results: List[Dict[str, Any]] = []
        sem = asyncio.Semaphore(concurrency if concurrency > 0 else 1)

        async def worker(name: str):
            async with sem:
                cred_data = await storage_adapter.get_credential(name)
                if not cred_data:
                    results.append(
                        {"credential": name, "action": "missing", "status_code": None, "error": "credential not found"}
                    )
                    return
                res = await _process_credential(
                    name, cred_data, cred_mgr, delete_on_error, model, timeout, dry_run
                )
                results.append(res)

        tasks = [create_managed_task(worker(name), name=f"healthcheck-{name}") for name in ordered]
        if tasks:
            await asyncio.gather(*tasks)

        summary = {
            "total": len(ordered),
            "dry_run": dry_run,
            "success_enabled": [r["credential"] for r in results if r.get("action") == "enabled"],
            "success_unchanged": [r["credential"] for r in results if r.get("action") == "unchanged"],
            "disabled_429": [r["credential"] for r in results if r.get("action") == "disabled_429"],
            "deleted": [r["credential"] for r in results if str(r.get("action", "")).startswith("deleted")],
            "failed": [r for r in results if r.get("error")],
            "delete_failed": [r["credential"] for r in results if r.get("action") == "delete_failed"],
            "details": results,
            "started_at": _last_started_at,
            "finished_at": time.time(),
            "duration_sec": round(time.time() - start, 3),
        }

        _last_result = summary
        _last_finished_at = summary["finished_at"]
        log.info(
            f"健康检查完成，总计 {summary['total']}，启用 {len(summary['success_enabled'])}，"
            f"429禁用 {len(summary['disabled_429'])}，删除 {len(summary['deleted'])}，失败 {len(summary['failed'])}"
        )
        return summary
    finally:
        async with _healthcheck_lock:
            _healthcheck_running = False


async def _scheduler_loop():
    """周期性健康检查调度"""
    global _stop_event
    _stop_event = asyncio.Event()
    while not _stop_event.is_set():
        try:
            enabled = await get_healthcheck_enabled()
            if enabled:
                try:
                    await run_healthcheck(dry_run=False)
                except Exception as e:
                    log.error(f"自动健康检查失败: {e}")
            interval = await get_healthcheck_interval()
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            log.error(f"健康检查调度循环异常: {e}")
            await asyncio.sleep(5)


async def start_healthcheck_scheduler():
    """启动后台调度"""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = create_managed_task(_scheduler_loop(), name="healthcheck-scheduler")


async def stop_healthcheck_scheduler():
    """停止后台调度"""
    global _scheduler_task, _stop_event
    if _stop_event:
        _stop_event.set()
    if _scheduler_task:
        try:
            await asyncio.wait_for(_scheduler_task, timeout=5)
        except Exception:
            _scheduler_task.cancel()
    _scheduler_task = None


def get_healthcheck_status() -> Dict[str, Any]:
    """获取当前健康检查状态"""
    return {
        "running": _healthcheck_running,
        "last_result": _last_result,
        "last_started_at": _last_started_at,
        "last_finished_at": _last_finished_at,
    }

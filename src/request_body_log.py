"""
请求体详细日志服务（磁盘 JSON 文件）
- 开关 request_log_enabled 控制；仅记录命中指定错误码的请求
- 文件保存在 data/request_logs/<YYYY-MM-DD>/<毫秒时间戳>_<request_id>.json
- usage_log 数据库行只保存相对路径索引（request_log_file 字段）
- 前端查看详情时按索引从磁盘读取；清理/清空日志时同步删除对应文件
"""

import json
import os
import shutil
import time
from typing import Any, Dict, Optional

from log import log

# 详细日志根目录（相对工作目录）
REQUEST_LOG_DIR = os.path.join("data", "request_logs")

# 记录时需要脱敏的请求头（小写）
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-goog-api-key", "x-api-key"}


def _sanitize_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    safe: Dict[str, str] = {}
    for key, value in (headers or {}).items():
        if str(key).lower() in _SENSITIVE_HEADERS:
            safe[key] = "(已脱敏)"
        else:
            safe[str(key)] = str(value)
    return safe


async def record_request_body(
    *,
    request_id: str,
    model_name: str,
    upstream_model_name: str,
    credential_email: str,
    is_stream: bool,
    status_code: int,
    url: str,
    headers: Optional[Dict[str, str]],
    payload: Any,
    response_body: str = "",
    error_message: str = "",
) -> Optional[str]:
    """
    写入一条请求体详细日志（磁盘 JSON）。
    返回相对路径索引（写入失败返回 None）。任何异常不影响主流程。
    """
    import aiofiles

    ts = time.time()
    date_dir = time.strftime("%Y-%m-%d", time.localtime(ts))
    rel_dir = os.path.join(REQUEST_LOG_DIR, date_dir)
    file_name = f"{int(ts * 1000)}_{request_id or 'unknown'}.json"
    # 数据库索引：相对 request_logs 根目录的路径（正斜杠）
    rel_path = f"{date_dir}/{file_name}"
    full_path = os.path.join(rel_dir, file_name)

    entry = {
        "created_at": ts,
        "request_id": request_id,
        "model_name": model_name,
        "upstream_model_name": upstream_model_name,
        "credential_email": credential_email,
        "is_stream": is_stream,
        "status_code": status_code,
        "url": url,
        "request_headers": _sanitize_headers(headers),
        "request_body": payload,
        "response_body": response_body,
        "error_message": error_message,
    }

    try:
        os.makedirs(rel_dir, exist_ok=True)
        async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(entry, ensure_ascii=False, indent=2))
        return rel_path
    except Exception as e:
        log.warning(f"请求体详细日志写入失败（不影响主流程）: {e}")
        return None


def _resolve_path(rel_path: str) -> Optional[str]:
    """把数据库中的相对路径索引解析为磁盘绝对路径（防目录穿越）"""
    if not rel_path:
        return None
    normalized = rel_path.replace("\\", "/")
    root = os.path.normpath(REQUEST_LOG_DIR)
    full = os.path.normpath(os.path.join(root, normalized))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full


def read_request_body_log(rel_path: str) -> Optional[Dict[str, Any]]:
    """按索引读取详细日志文件；不存在或损坏时返回 None（文件可能已被清理或手动删除）"""
    full = _resolve_path(rel_path)
    if not full or not os.path.isfile(full):
        return None
    try:
        with open(full, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        # 文件损坏（可能被手动编辑/删除到一半）也不报错，视为不可读
        return None


def clear_request_body_logs(before_timestamp: Optional[float] = None) -> int:
    """
    清理详细日志文件，返回删除的文件数。
    - before_timestamp=None: 清空全部（删除整个 request_logs 目录后重建）
    - 否则: 只删除文件名时间戳早于该值的文件
    """
    root = REQUEST_LOG_DIR
    if not os.path.isdir(root):
        return 0

    if before_timestamp is None:
        count = 0
        for _dirpath, _dirnames, filenames in os.walk(root):
            count += len(filenames)
        try:
            shutil.rmtree(root, ignore_errors=True)
            os.makedirs(root, exist_ok=True)
        except Exception as e:
            log.warning(f"清空请求体详细日志目录失败: {e}")
        return count

    cutoff_ms = int(before_timestamp * 1000)
    deleted = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                ts_ms = int(name.split("_", 1)[0])
            except ValueError:
                continue
            if ts_ms < cutoff_ms:
                try:
                    os.remove(os.path.join(dirpath, name))
                    deleted += 1
                except OSError:
                    pass
    return deleted

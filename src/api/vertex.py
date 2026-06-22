"""
Vertex AI Anonymous API Client
通过 reCAPTCHA Enterprise 匿名访问 Google Vertex AI API
使用 wreq 库进行 TLS 指纹伪装
"""

import asyncio
import base64
import json
import re
import random
import string
from typing import Any, Dict, Optional, Tuple

from fastapi import Response
from log import log
from src.converter.thoughtSignature_fix import decode_tool_id_and_signature

try:
    import wreq
    from wreq.emulation import Emulation
    WREQ_AVAILABLE = True
except ImportError:
    wreq = None
    Emulation = None
    WREQ_AVAILABLE = False


# ==================== 常量 ====================

RECAPTCHA_BASE = "https://www.google.com"
SITE_KEY = "6LdCjtspAAAAAMcV4TGdWLJqRTEk1TfpdLqEnKdj"
RECAPTCHA_CO = "aHR0cHM6Ly9jb25zb2xlLmNsb3VkLmdvb2dsZS5jb206NDQz"
RECAPTCHA_HL = "zh-CN"
RECAPTCHA_V = "jdMmXeCQEkPbnFDy9T04NbgJ"
RECAPTCHA_VH = "6581054572"

ANON_API_KEY = "AIzaSyCI-zsRP85UVOi0DjtiCwWBwQ1djDy741g"
BATCH_GRAPHQL_URL = (
    "https://cloudconsole-pa.clients6.google.com"
    "/v3/entityServices/AiplatformEntityService/schemas/AIPLATFORM_GRAPHQL:batchGraphql"
    f"?key={ANON_API_KEY}&prettyPrint=false"
)

QUERY_SIGNATURE = "2/l8eCsMMY49imcDQ/lwwXyL8cYtTjxZBF2dNqy69LodY="
OPERATION_NAME = "StreamGenerateContentAnonymous"
CLIENT_VERSION = "boq_cloud-boq-clientweb-vertexaistudio_20260402.09_p0"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"'
SEC_CH_UA_FULL_VERSION_LIST = (
    '"Not;A=Brand";v="8.0.0.0", "Chromium";v="150.0.7871.13", "Google Chrome";v="150.0.7871.13"'
)

# 正则：从 anchor HTML 提取 base token
_TOKEN_RE = re.compile(r'id="recaptcha-token"[^>]*value="([^"]+)"')
# 正则：从 reload 响应提取最终 token
_RRESP_RE = re.compile(r'rresp","(.*?)"')

# finishReason 为此值时不结束流（protobuf 默认无意义值）
_FINISH_REASON_UNSPECIFIED = "FINISH_REASON_UNSPECIFIED"

# upstream errors 中表示 429 限流的关键词
_QUOTA_KEYWORDS = ("Resource has been exhausted", "quota", "RESOURCE_EXHAUSTED", "429")

# 支持透传进 variables 的字段
_SUPPORTED_VAR_FIELDS = [
    "contents", "tools", "toolConfig", "systemInstruction",
    "safetySettings", "generationConfig",
]


def _random_string(n: int) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ==================== 请求头 ====================

def _anchor_headers() -> Dict[str, str]:
    return {
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version": '"150.0.7871.13"',
        "sec-ch-ua-full-version-list": SEC_CH_UA_FULL_VERSION_LIST,
        "sec-ch-ua-platform-version": '"19.0.0"',
        "sec-ch-ua-model": '""',
        "sec-ch-ua-wow64": "?0",
        "sec-ch-ua-form-factors": '"Desktop"',
        "upgrade-insecure-requests": "1",
        "user-agent": USER_AGENT,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "navigate",
        "sec-fetch-dest": "iframe",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    }


def _xhr_headers(content_type: str, accept: str, origin: str, referer: str, site: str) -> Dict[str, str]:
    h = {
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version": '"150.0.7871.13"',
        "sec-ch-ua-full-version-list": SEC_CH_UA_FULL_VERSION_LIST,
        "sec-ch-ua-platform-version": '"19.0.0"',
        "sec-ch-ua-model": '""',
        "sec-ch-ua-wow64": "?0",
        "sec-ch-ua-form-factors": '"Desktop"',
        "user-agent": USER_AGENT,
        "accept": accept,
        "origin": origin,
        "sec-fetch-site": site,
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "referer": referer,
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "priority": "u=1, i",
    }
    if content_type:
        h["content-type"] = content_type
    return h


def _batch_graphql_headers() -> Dict[str, str]:
    h = _xhr_headers(
        "application/json",
        "*/*",
        "https://console.cloud.google.com",
        "https://console.cloud.google.com/",
        "cross-site",
    )
    h["x-goog-authuser"] = "0"
    h["x-browser-channel"] = "stable"
    h["x-browser-copyright"] = "Copyright 2026 Google LLC. All Rights Reserved."
    h["x-browser-year"] = "2026"
    h["x-goog-ext-353267353-jspb"] = "[null,null,null,194274]"
    return h


# ==================== reCAPTCHA ====================

async def _fetch_recaptcha_token_once() -> Optional[str]:
    """单次尝试获取 reCAPTCHA token，失败返回 None。"""
    if not WREQ_AVAILABLE:
        return None
    try:
        _EMULATION_POOL = [
            Emulation.Chrome131, Emulation.Chrome132, Emulation.Chrome133,
            Emulation.Chrome134, Emulation.Chrome135, Emulation.Chrome136,
            Emulation.Chrome137, Emulation.Chrome138, Emulation.Chrome139,
            Emulation.Chrome140, Emulation.Chrome141, Emulation.Chrome142,
            Emulation.Chrome143, Emulation.Chrome144, Emulation.Chrome145,
            Emulation.Chrome146, Emulation.Chrome147,
            Emulation.Edge131, Emulation.Edge134, Emulation.Edge135,
            Emulation.Edge136, Emulation.Edge137, Emulation.Edge138,
            Emulation.Edge139, Emulation.Edge140, Emulation.Edge141,
            Emulation.Edge142, Emulation.Edge143, Emulation.Edge144,
            Emulation.Edge145, Emulation.Edge146, Emulation.Edge147,
        ]
        emulation = random.choice(_EMULATION_POOL)

        cb = _random_string(10)
        anchor_url = (
            f"{RECAPTCHA_BASE}/recaptcha/enterprise/anchor"
            f"?ar=1&k={SITE_KEY}&co={RECAPTCHA_CO}&hl={RECAPTCHA_HL}"
            f"&v={RECAPTCHA_V}&size=invisible&anchor-ms=20000&execute-ms=15000&cb={cb}"
        )

        # 1. GET anchor page
        anchor_resp = await wreq.get(
            anchor_url,
            headers=_anchor_headers(),
            emulation=emulation,
        )
        if anchor_resp.status != 200:
            log.warning(f"[VERTEX RECAPTCHA] anchor GET failed: status={anchor_resp.status}")
            return None

        anchor_body = await anchor_resp.text()
        m = _TOKEN_RE.search(anchor_body)
        if not m:
            log.warning(f"[VERTEX RECAPTCHA] anchor token regex miss, body[:200]={anchor_body[:200]}")
            return None
        base_token = m.group(1)

        # 2. POST reload to exchange base token for final token
        reload_url = f"{RECAPTCHA_BASE}/recaptcha/enterprise/reload?k={SITE_KEY}"
        form_data = {
            "v": RECAPTCHA_V,
            "reason": "q",
            "k": SITE_KEY,
            "c": base_token,
            "co": RECAPTCHA_CO,
            "hl": RECAPTCHA_HL,
            "size": "invisible",
            "vh": RECAPTCHA_VH,
            "chr": "",
            "bg": "",
        }
        reload_headers = _xhr_headers(
            "application/x-www-form-urlencoded;charset=UTF-8",
            "*/*",
            RECAPTCHA_BASE,
            anchor_url,
            "same-origin",
        )
        reload_resp = await wreq.post(
            reload_url,
            form=form_data,
            headers=reload_headers,
            emulation=emulation,
        )
        if reload_resp.status != 200:
            log.warning(f"[VERTEX RECAPTCHA] reload POST failed: status={reload_resp.status}")
            return None

        reload_body = await reload_resp.text()
        rm = _RRESP_RE.search(reload_body)
        if not rm:
            log.warning(f"[VERTEX RECAPTCHA] rresp regex miss in reload response")
            return None

        return rm.group(1)

    except Exception as e:
        log.warning(f"[VERTEX RECAPTCHA] exception: {e}")
        return None


async def fetch_recaptcha_token() -> Optional[str]:
    """获取 reCAPTCHA token，最多重试 3 次。"""
    for attempt in range(3):
        log.debug(f"[VERTEX RECAPTCHA] attempt {attempt + 1}/3")
        token = await _fetch_recaptcha_token_once()
        if token:
            log.debug(f"[VERTEX RECAPTCHA] token obtained on attempt {attempt + 1}")
            return token
        log.warning(f"[VERTEX RECAPTCHA] attempt {attempt + 1} failed")
    log.error("[VERTEX RECAPTCHA] all 3 attempts failed")
    return None


# ==================== Payload 构建 ====================

_SKIP_THOUGHT_SENTINEL = "skip_thought_signature_validator"
_SKIP_THOUGHT_SENTINEL_B64 = base64.b64encode(_SKIP_THOUGHT_SENTINEL.encode()).decode()


def _drop_invalid_tool_turns(contents: list) -> list:
    """
    删除 name 为空的 functionCall 所在的整轮工具调用（model + user functionResponse 一起删）。
    Gemini 不接受 name 为空的 functionCall，这类 part 是上游客户端产生的无效历史记录。
    """
    # 先找出哪些 model 消息的所有 functionCall 都是空 name
    invalid_indices = set()
    for i, msg in enumerate(contents):
        if not isinstance(msg, dict) or msg.get("role") != "model":
            continue
        parts = msg.get("parts", [])
        fc_parts = [p for p in parts if isinstance(p, dict) and (
            "functionCall" in p or "function_call" in p
        )]
        if not fc_parts:
            continue
        all_empty = all(
            not (p.get("functionCall") or p.get("function_call") or {}).get("name", "").strip()
            for p in fc_parts
        )
        if all_empty:
            invalid_indices.add(i)
            # 紧随其后的 user functionResponse 消息也一起删
            if i + 1 < len(contents):
                next_msg = contents[i + 1]
                if isinstance(next_msg, dict) and next_msg.get("role") == "user":
                    next_parts = next_msg.get("parts", [])
                    if next_parts and all(
                        isinstance(p, dict) and ("functionResponse" in p or "function_response" in p)
                        for p in next_parts
                    ):
                        invalid_indices.add(i + 1)

    if not invalid_indices:
        return contents
    result = [msg for i, msg in enumerate(contents) if i not in invalid_indices]
    log.debug(f"[VERTEX] dropped {len(invalid_indices)} invalid tool turn messages (empty functionCall name)")
    return result


def _fix_thought_signatures(contents: list) -> list:
    """
    补全所有 functionCall/thought part 缺失的 thoughtSignature。
    只要 part 含 functionCall、thought 或 thoughtSignature 字段，就确保 thoughtSignature 存在。
    没有真实签名时注入 base64(sentinel)
    name 为空的 functionCall 同样处理（历史消息里可能出现）。
    """
    for msg in contents:
        if not isinstance(msg, dict):
            continue
        for part in msg.get("parts", []):
            if not isinstance(part, dict):
                continue

            # functionResponse 不得有 thoughtSignature
            if "functionResponse" in part or "function_response" in part:
                part.pop("thoughtSignature", None)
                part.pop("thought_signature", None)
                continue

            fc = part.get("functionCall") or part.get("function_call")
            has_fc = isinstance(fc, dict)
            has_thought = "thought" in part
            has_sig = "thoughtSignature" in part or "thought_signature" in part

            if not (has_fc or has_thought or has_sig):
                continue

            # 从 id 里尝试解出真实签名
            signature = None
            if has_fc:
                raw_id = fc.get("id") or ""
                real_id, signature = decode_tool_id_and_signature(raw_id)
                fc["id"] = real_id

            existing_sig = part.get("thoughtSignature")
            if not existing_sig or existing_sig == _SKIP_THOUGHT_SENTINEL:
                part["thoughtSignature"] = signature if signature else _SKIP_THOUGHT_SENTINEL_B64

    return contents


def _fix_function_response_names(contents: list) -> list:
    """
    补全 function_response 中缺失的 name 字段。
    按顺序收集所有 function_call 的 name，再按顺序填给缺 name 的 function_response。
    """
    # 第一遍：收集所有 function_call name（按出现顺序）
    fc_names = []
    for msg in contents:
        if not isinstance(msg, dict):
            continue
        for part in msg.get("parts", []):
            if not isinstance(part, dict):
                continue
            fc = part.get("functionCall") or part.get("function_call")
            if isinstance(fc, dict) and fc.get("name"):
                fc_names.append(fc["name"])

    # 第二遍：给缺 name 的 function_response 按顺序补名
    name_iter = iter(fc_names)
    for msg in contents:
        if not isinstance(msg, dict):
            continue
        for part in msg.get("parts", []):
            if not isinstance(part, dict):
                continue
            fr = part.get("functionResponse") or part.get("function_response")
            if isinstance(fr, dict) and not fr.get("name"):
                fr["name"] = next(name_iter, "unknown")
    return contents


def _build_variables(model: str, gemini_payload: Dict[str, Any]) -> Dict[str, Any]:
    """从 gemini_payload 提取 variables（不含 region/recaptchaToken）。"""
    if "contents" in gemini_payload and isinstance(gemini_payload["contents"], list):
        gemini_payload["contents"] = _drop_invalid_tool_turns(gemini_payload["contents"])
        gemini_payload["contents"] = _fix_thought_signatures(gemini_payload["contents"])
        gemini_payload["contents"] = _fix_function_response_names(gemini_payload["contents"])
    log.debug(f"[VERTEX] contents to upstream: {json.dumps(gemini_payload.get('contents', []), ensure_ascii=False)}")
    vars_: Dict[str, Any] = {"model": model}
    for field in _SUPPORTED_VAR_FIELDS:
        if field in gemini_payload:
            vars_[field] = gemini_payload[field]
    return vars_


def _build_request_payload(model: str, gemini_payload: Dict[str, Any], recaptcha_token: str) -> Dict[str, Any]:
    """构建发往上游的完整 batchGraphql 请求体。"""
    vars_ = _build_variables(model, gemini_payload)
    vars_["region"] = "global"
    vars_["recaptchaToken"] = recaptcha_token
    return {
        "requestContext": {
            "clientVersion": CLIENT_VERSION,
            "pagePath": "/vertex-ai/studio/multimodal",
            "jurisdiction": "global",
            "localizationData": {
                "locale": "zh_CN",
                "timezone": "Asia/Shanghai",
            },
        },
        "querySignature": QUERY_SIGNATURE,
        "operationName": OPERATION_NAME,
        "variables": vars_,
    }


# ==================== 响应解析 ====================

def _is_auth_error(text: str) -> bool:
    return "Failed to verify action" in text or "The caller does not have permission" in text


def _is_quota_error(text: str) -> bool:
    return any(kw in text for kw in _QUOTA_KEYWORDS)


def _parse_json_objects(raw_text: str):
    """
    从上游响应文本中花括号配对扫描提取所有 JSON 对象。
    yield (dict, end_pos) — end_pos 是该对象在 raw_text 中结束后的位置。
    """
    i = 0
    while i < len(raw_text):
        start = raw_text.find("{", i)
        if start == -1:
            break
        depth = 0
        in_string = False
        escape = False
        j = start
        while j < len(raw_text):
            ch = raw_text[j]
            if escape:
                escape = False
                j += 1
                continue
            if ch == "\\":
                escape = True
                j += 1
                continue
            if ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = raw_text[start:j + 1]
                        try:
                            obj = json.loads(json_str)
                            yield obj, j + 1
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        break
            j += 1
        else:
            break


def _process_object(obj: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str], bool]:
    """
    处理单个上游 JSON 对象，提取 Gemini chunk。
    返回 (chunk, auth_error_msg, is_quota_error)。
    """
    results = obj.get("results", [])
    if not isinstance(results, list):
        return None, None, False

    for result in results:
        if not isinstance(result, dict):
            continue

        # 检查 errors 字段
        errors = result.get("errors", [])
        if errors and isinstance(errors, list):
            first = errors[0]
            err_msg = first.get("message", "") if isinstance(first, dict) else str(first)
            if _is_auth_error(err_msg):
                return None, err_msg, False
            if _is_quota_error(err_msg):
                log.warning(f"[VERTEX] upstream quota/429 error: {err_msg}")
                return None, None, True
            log.warning(f"[VERTEX] upstream errors in result: {err_msg}")

        data = result.get("data")
        if not isinstance(data, dict):
            continue

        # unwrap data.ui.streamGenerateContentAnonymous
        ui = data.get("ui")
        if isinstance(ui, dict) and "streamGenerateContentAnonymous" in ui:
            inner = ui["streamGenerateContentAnonymous"]
            if isinstance(inner, dict):
                data = inner
            elif isinstance(inner, list):
                for item in inner:
                    if isinstance(item, dict):
                        chunk = _extract_chunk(item)
                        if chunk:
                            return chunk, None, False
                continue

        chunk = _extract_chunk(data)
        if chunk:
            return chunk, None, False

    return None, None, False


def _clean_part(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """清洗单个 part，去除空垃圾字段，返回 None 表示整个 part 无效。"""
    out: Dict[str, Any] = {}

    # thought 标记
    thought = part.get("thought")
    if thought is True:
        out["thought"] = True

    # thoughtSignature
    if part.get("thoughtSignature"):
        out["thoughtSignature"] = part["thoughtSignature"]

    # 文本内容
    text = part.get("text")
    if isinstance(text, str) and text:
        out["text"] = text

    # functionCall：name 非空才保留
    fc = part.get("functionCall")
    if isinstance(fc, dict) and fc.get("name", "").strip():
        out["functionCall"] = fc

    # functionResponse：name 非空才保留
    fr = part.get("functionResponse")
    if isinstance(fr, dict) and fr.get("name", "").strip():
        out["functionResponse"] = fr

    # inlineData：mimeType 和 data 都非空才保留
    inline = part.get("inlineData")
    if isinstance(inline, dict) and inline.get("mimeType") and inline.get("data"):
        out["inlineData"] = inline

    # fileData：fileUri 非空才保留
    fd = part.get("fileData")
    if isinstance(fd, dict) and fd.get("fileUri"):
        out["fileData"] = fd

    return out if out else None


def _extract_chunk(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从单个数据对象提取标准化 Gemini chunk。"""
    chunk: Dict[str, Any] = {}

    if "candidates" in data and data["candidates"] is not None:
        candidates_raw = data["candidates"] if isinstance(data["candidates"], list) else []
        if candidates_raw:
            cleaned = []
            for cand in candidates_raw:
                if not isinstance(cand, dict):
                    continue
                content = cand.get("content")
                if isinstance(content, dict):
                    raw_parts = content.get("parts", [])
                    role = content.get("role") or "model"
                    clean_parts = [cp for p in raw_parts if isinstance(p, dict) for cp in [_clean_part(p)] if cp]
                    cleaned.append({**cand, "content": {"role": role, "parts": clean_parts}})
                else:
                    cleaned.append(cand)
            chunk["candidates"] = cleaned if cleaned else candidates_raw
        else:
            chunk["candidates"] = candidates_raw

    for key in ("usageMetadata", "modelVersion", "responseId", "promptFeedback", "createTime"):
        if key in data and data[key]:
            chunk[key] = data[key]

    return chunk if chunk else None


def _get_finish_reason(chunk: Dict[str, Any]) -> str:
    candidates = chunk.get("candidates", [])
    if candidates and isinstance(candidates, list):
        c = candidates[0]
        if isinstance(c, dict):
            return c.get("finishReason") or ""
    return ""


# ==================== 核心请求函数 ====================

async def stream_request(
    body: Dict[str, Any],
    native: bool = False,
):
    """
    流式请求入口。

    body 格式: {"model": str, "request": {...gemini payload...}}

    Yields:
        Response 对象（错误时）或 str/bytes（SSE 格式数据）
    """
    if not WREQ_AVAILABLE:
        yield Response(
            content='{"error":{"code":503,"message":"wreq not installed, vertex channel unavailable","status":"UNAVAILABLE"}}',
            status_code=503,
            media_type="application/json",
        )
        return

    model = body.get("model", "")
    gemini_payload = body.get("request", {})

    max_retries = 3
    recaptcha_token: Optional[str] = None
    is_first_auth = True

    for attempt in range(max_retries + 1):
        log.debug(f"[VERTEX STREAM] attempt {attempt + 1}/{max_retries + 1}, model={model}")

        if not recaptcha_token:
            recaptcha_token = await fetch_recaptcha_token()
            is_first_auth = True

        if not recaptcha_token:
            if attempt >= max_retries:
                yield Response(
                    content=json.dumps({"error": {"code": 401, "message": "Could not fetch reCAPTCHA token", "status": "UNAUTHENTICATED"}}),
                    status_code=401,
                    media_type="application/json",
                )
                return
            await asyncio.sleep(1)
            continue

        payload = _build_request_payload(model, gemini_payload, recaptcha_token)
        headers = _batch_graphql_headers()

        content_yielded = False
        need_retry = False
        auth_retry = False
        quota_retry = False

        try:
            resp = await wreq.post(
                BATCH_GRAPHQL_URL,
                json=payload,
                headers=headers,
                emulation=Emulation.Chrome131,
            )
        except Exception as e:
            log.error(f"[VERTEX STREAM] wreq post exception: {e}")
            if attempt < max_retries:
                await asyncio.sleep(1 + attempt)
                recaptcha_token = None
                continue
            yield Response(
                content=json.dumps({"error": {"code": 500, "message": str(e), "status": "INTERNAL"}}),
                status_code=500,
                media_type="application/json",
            )
            return

        if resp.status != 200:
            try:
                err_body = await resp.text()
            except Exception:
                err_body = ""
            log.error(f"[VERTEX STREAM] HTTP {resp.status}: {err_body[:300]}")

            if _is_auth_error(err_body):
                if is_first_auth:
                    is_first_auth = False
                    auth_retry = True
                else:
                    recaptcha_token = None
                    need_retry = True
            elif resp.status == 429 and attempt < max_retries:
                quota_retry = True
                recaptcha_token = None  # 换新 token 重试
            elif resp.status in (500, 503) and attempt < max_retries:
                need_retry = True
            else:
                if attempt >= max_retries or content_yielded:
                    yield Response(
                        content=err_body.encode("utf-8"),
                        status_code=resp.status,
                        media_type="application/json",
                    )
                    return
                need_retry = True

            if auth_retry:
                await asyncio.sleep(0.5)
                continue
            if quota_retry:
                log.warning(f"[VERTEX STREAM] HTTP 429, retry {attempt + 1}/{max_retries}")
                continue
            if need_retry and attempt < max_retries:
                await asyncio.sleep(1 + attempt)
                continue
            yield Response(
                content=err_body.encode("utf-8") if err_body else b"",
                status_code=resp.status,
                media_type="application/json",
            )
            return

        # 读取流式响应
        try:
            async with resp:
                async with resp.stream() as streamer:
                    buffer = b""
                    async for raw_chunk in streamer:
                        if raw_chunk:
                            buffer += raw_chunk
                            text = buffer.decode("utf-8", errors="replace")
                            log.debug(f"[VERTEX STREAM] raw buffer: {text[:500]}")
                            last_end = 0
                            for obj, end_pos in _parse_json_objects(text):
                                last_end = end_pos
                                chunk, auth_err, quota_err = _process_object(obj)
                                if auth_err:
                                    if is_first_auth:
                                        is_first_auth = False
                                        auth_retry = True
                                    else:
                                        recaptcha_token = None
                                        need_retry = True
                                    break
                                if quota_err:
                                    quota_retry = True
                                    recaptcha_token = None
                                    break
                                if chunk is not None:
                                    content_yielded = True
                                    is_first_auth = False
                                    sse = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                                    yield sse.encode("utf-8") if native else sse

                                    fr = _get_finish_reason(chunk)
                                    if fr and fr != _FINISH_REASON_UNSPECIFIED:
                                        return

                            if auth_retry or need_retry or quota_retry:
                                break
                            # 只保留未被成功解析的尾部
                            buffer = text[last_end:].encode("utf-8") if last_end < len(text) else b""
        except Exception as e:
            log.error(f"[VERTEX STREAM] stream read error: {e}")
            if not content_yielded and attempt < max_retries:
                need_retry = True

        if content_yielded:
            return

        if auth_retry:
            await asyncio.sleep(0.5)
            continue

        if quota_retry and attempt < max_retries:
            log.warning(f"[VERTEX STREAM] upstream 429, retry {attempt + 1}/{max_retries}")
            continue

        if need_retry and attempt < max_retries:
            await asyncio.sleep(1 + attempt)
            continue

        if not content_yielded and not need_retry:
            # 流结束但没内容，换 token 重试
            if attempt < max_retries:
                recaptcha_token = None
                await asyncio.sleep(1)
                continue

        break

    # 所有重试耗尽
    yield Response(
        content=json.dumps({"error": {"code": 500, "message": "All retries exhausted", "status": "INTERNAL"}}),
        status_code=500,
        media_type="application/json",
    )


async def non_stream_request(
    body: Dict[str, Any],
) -> Response:
    """
    非流式请求入口。读取完整响应后解析为 Gemini generateContent 格式。

    body 格式: {"model": str, "request": {...gemini payload...}}
    """
    if not WREQ_AVAILABLE:
        return Response(
            content='{"error":{"code":503,"message":"wreq not installed, vertex channel unavailable","status":"UNAVAILABLE"}}',
            status_code=503,
            media_type="application/json",
        )

    model = body.get("model", "")
    gemini_payload = body.get("request", {})

    max_retries = 3

    for attempt in range(max_retries + 1):
        log.debug(f"[VERTEX NON-STREAM] attempt {attempt + 1}/{max_retries + 1}, model={model}")

        recaptcha_token = await fetch_recaptcha_token()
        if not recaptcha_token:
            if attempt >= max_retries:
                return Response(
                    content=json.dumps({"error": {"code": 401, "message": "Could not fetch reCAPTCHA token", "status": "UNAUTHENTICATED"}}),
                    status_code=401,
                    media_type="application/json",
                )
            await asyncio.sleep(1)
            continue

        payload = _build_request_payload(model, gemini_payload, recaptcha_token)
        headers = _batch_graphql_headers()

        try:
            resp = await wreq.post(
                BATCH_GRAPHQL_URL,
                json=payload,
                headers=headers,
                emulation=Emulation.Chrome131,
            )
        except Exception as e:
            log.error(f"[VERTEX NON-STREAM] wreq exception: {e}")
            if attempt < max_retries:
                await asyncio.sleep(1)
                continue
            return Response(
                content=json.dumps({"error": {"code": 500, "message": str(e), "status": "INTERNAL"}}),
                status_code=500,
                media_type="application/json",
            )

        status = resp.status
        try:
            raw_text = await resp.text()
        except Exception:
            raw_text = ""

        if status != 200:
            log.error(f"[VERTEX NON-STREAM] HTTP {status}: {raw_text[:300]}")
            if _is_auth_error(raw_text) and attempt < max_retries:
                await asyncio.sleep(1)
                continue
            if status == 429 and attempt < max_retries:
                log.warning(f"[VERTEX NON-STREAM] HTTP 429, retry {attempt + 1}/{max_retries}")
                recaptcha_token = await fetch_recaptcha_token()
                continue
            if status in (500, 503) and attempt < max_retries:
                await asyncio.sleep(2 + attempt)
                continue
            return Response(
                content=raw_text.encode("utf-8"),
                status_code=status,
                media_type="application/json",
            )

        # 解析响应
        result = _build_non_stream_response(raw_text)
        if result is None:
            log.error(f"[VERTEX NON-STREAM] parse failed: {raw_text[:300]}")
            if attempt < max_retries:
                await asyncio.sleep(1)
                continue
            return Response(
                content=json.dumps({"error": {"code": 500, "message": "Failed to parse upstream response", "status": "INTERNAL"}}),
                status_code=500,
                media_type="application/json",
            )

        if isinstance(result, dict) and "auth_error" in result:
            if attempt < max_retries:
                await asyncio.sleep(1)
                continue
            return Response(
                content=json.dumps({"error": {"code": 401, "message": result["auth_error"], "status": "UNAUTHENTICATED"}}),
                status_code=401,
                media_type="application/json",
            )

        if isinstance(result, dict) and "quota_error" in result:
            if attempt < max_retries:
                log.warning(f"[VERTEX NON-STREAM] upstream 429, retry {attempt + 1}/{max_retries}")
                continue  # 下一轮循环会重新 fetch_recaptcha_token
            return Response(
                content=json.dumps({"error": {"code": 429, "message": result["quota_error"], "status": "RESOURCE_EXHAUSTED"}}),
                status_code=429,
                media_type="application/json",
            )

        return Response(
            content=json.dumps(result, ensure_ascii=False).encode("utf-8"),
            status_code=200,
            media_type="application/json",
        )

    return Response(
        content=json.dumps({"error": {"code": 500, "message": "All retries exhausted", "status": "INTERNAL"}}),
        status_code=500,
        media_type="application/json",
    )


def _build_non_stream_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    从 batchGraphql 响应中提取并组装标准 Gemini generateContent 响应体。
    返回 Gemini 格式 dict，None（解析失败），或 {"auth_error": msg}。
    """
    all_parts: list = []
    finish_reason = ""
    usage_metadata: Dict[str, Any] = {}
    model_version = ""
    response_id = ""
    prompt_feedback: Dict[str, Any] = {}

    found_any = False

    for obj, _ in _parse_json_objects(raw_text):
        results = obj.get("results", [])
        if not isinstance(results, list):
            continue

        for result in results:
            if not isinstance(result, dict):
                continue

            errors = result.get("errors", [])
            if errors and isinstance(errors, list):
                first = errors[0]
                err_msg = first.get("message", "") if isinstance(first, dict) else str(first)
                if _is_auth_error(err_msg):
                    return {"auth_error": err_msg}
                if _is_quota_error(err_msg):
                    return {"quota_error": err_msg}
                log.warning(f"[VERTEX NON-STREAM] upstream error: {err_msg}")
                continue

            data = result.get("data")
            if not isinstance(data, dict):
                continue

            ui = data.get("ui")
            if isinstance(ui, dict) and "streamGenerateContentAnonymous" in ui:
                inner = ui["streamGenerateContentAnonymous"]
                if isinstance(inner, dict):
                    data = inner
                elif isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict):
                            _accumulate_parts(item, all_parts)
                            found_any = True
                    continue

            _accumulate_parts(data, all_parts)
            found_any = True

            if not usage_metadata and data.get("usageMetadata"):
                usage_metadata = data["usageMetadata"]
            if not model_version and data.get("modelVersion"):
                model_version = data["modelVersion"]
            if not response_id and data.get("responseId"):
                response_id = data["responseId"]
            if not prompt_feedback and data.get("promptFeedback"):
                prompt_feedback = data["promptFeedback"]

            candidates = data.get("candidates", [])
            if candidates and isinstance(candidates, list):
                c = candidates[0]
                if isinstance(c, dict):
                    fr = c.get("finishReason") or ""
                    if fr and fr != _FINISH_REASON_UNSPECIFIED:
                        finish_reason = fr

    if not found_any:
        return None

    if not all_parts:
        all_parts = [{"text": " "}]

    candidate: Dict[str, Any] = {
        "index": 0,
        "content": {"parts": all_parts, "role": "model"},
    }
    if finish_reason:
        candidate["finishReason"] = finish_reason.upper()

    resp: Dict[str, Any] = {"candidates": [candidate]}
    if usage_metadata:
        resp["usageMetadata"] = usage_metadata
    if model_version:
        resp["modelVersion"] = model_version
    if response_id:
        resp["responseId"] = response_id
    if prompt_feedback:
        resp["promptFeedback"] = prompt_feedback

    return resp


def _accumulate_parts(data: Dict[str, Any], all_parts: list) -> None:
    """从单个数据对象中提取 parts 并追加到 all_parts。"""
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        return
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        content = cand.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts", [])
        if isinstance(parts, list):
            for p in parts:
                if isinstance(p, dict) and any(
                    v not in (None, "", {}, [])
                    for k, v in p.items()
                    if k != "thought"
                ):
                    all_parts.append(p)

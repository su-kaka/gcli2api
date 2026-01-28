from src.i18n import ts
"""
Antigravity API Client - Handles communication with Google's Antigravity API
{ts("id_1468")} Google Antigravity API {ts("id_1467")}
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Response
from config import (
    get_antigravity_api_url,
    get_antigravity_stream2nostream,
    get_auto_ban_error_codes,
)
from log import log

from src.credential_manager import credential_manager
from src.httpx_client import stream_post_async, post_async
from src.models import Model, model_to_dict
from src.utils import ANTIGRAVITY_USER_AGENT

# {ts("id_1469")}
from src.api.utils import (
    handle_error_with_retry,
    get_retry_config,
    record_api_call_success,
    record_api_call_error,
    parse_and_log_cooldown,
    collect_streaming_response,
)

# ==================== {ts("id_1470")} ====================

# {ts("id_1472")} credential_manager{ts("id_1471")}


# ==================== {ts("id_1473")} ====================

def build_antigravity_headers(access_token: str, model_name: str = "") -> Dict[str, str]:
    """
    {ts("id_1475")} Antigravity API {ts("id_1474")}

    Args:
        access_token: {ts("id_1476")}
        model_name: {ts("id_1477")} request_type

    Returns:
        {ts("id_1478")}
    """
    headers = {
        'User-Agent': ANTIGRAVITY_USER_AGENT,
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip',
        'requestId': f"req-{uuid.uuid4()}"
    }

    # {ts("id_1479")} request_type
    if model_name:
        # {ts("id_1480")}
        if "image" in model_name.lower():
            request_type = "image_gen"
            headers['requestType'] = request_type
        else:
            request_type = "agent"
            headers['requestType'] = request_type

    return headers


# ==================== {ts("id_1481")} ====================

async def stream_request(
    body: Dict[str, Any],
    native: bool = False,
    headers: Optional[Dict[str, str]] = None,
):
    """
    {ts("id_1482")}

    Args:
        body: {ts("id_1447")}
        native: {ts(f"id_1483")}bytes{ts("id_1485")}False{ts("id_1484")}str{ts("id_1486")}
        headers: {ts("id_1487")}

    Yields:
        Response{ts(f"id_1488")} bytes{ts("id_1486")}/str{ts("id_1489")}
    """
    model_name = body.get("model", "")

    # 1. {ts("id_1490")}
    cred_result = await credential_manager.get_valid_credential(
        mode="antigravity", model_key=model_name
    )

    if not cred_result:
        # {ts("id_1492")}None{ts("id_1491500")}
        log.error(f"[ANTIGRAVITY STREAM] {ts("id_1493")}")
        yield Response(
            content=json.dumps({f"error": "{ts("id_1493")}"}),
            status_code=500,
            media_type="application/json"
        )
        return

    current_file, credential_data = cred_result
    access_token = credential_data.get("access_token") or credential_data.get("token")
    project_id = credential_data.get("project_id", "")

    if not access_token:
        log.error(f"[ANTIGRAVITY STREAM] No access token in credential: {current_file}")
        yield Response(
            content=json.dumps({f"error": "{ts("id_1494")}"}),
            status_code=500,
            media_type="application/json"
        )
        return

    # 2. {ts("id_1475")}URL{ts("id_1495")}
    antigravity_url = await get_antigravity_api_url()
    target_url = f"{antigravity_url}/v1internal:streamGenerateContent?alt=sse"

    auth_headers = build_antigravity_headers(access_token, model_name)

    # {ts("id_1496")}headers
    if headers:
        auth_headers.update(headers)

    # {ts("id_1497")}project{ts("id_61")}payload
    final_payload = {
        "model": body.get("model"),
        "project": project_id,
        "request": body.get("request", {}),
    }

    # 3. {ts("id_1095")}stream_post_async{ts("id_1498")}
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    DISABLE_ERROR_CODES = await get_auto_ban_error_codes()  # {ts("id_1499")}
    last_error_response = None  # {ts("id_1500")}
    next_cred_task = None  # {ts("id_1501")}

    # {ts(f"id_1502")}({ts("id_1504")}token{ts("id_15")}project_id,{ts("id_15")}03)
    async def refresh_credential_fast():
        nonlocal current_file, access_token, auth_headers, project_id, final_payload
        cred_result = await credential_manager.get_valid_credential(
            mode="antigravity", model_key=model_name
        )
        if not cred_result:
            return None
        current_file, credential_data = cred_result
        access_token = credential_data.get("access_token") or credential_data.get("token")
        project_id = credential_data.get("project_id", "")
        if not access_token:
            return None
        # {ts(f"id_1504")}token{ts("id_15")}project_id,{ts("id_15")}05headers{ts("id_15")}payload
        auth_headers["Authorization"] = f"Bearer {access_token}"
        final_payload["project"] = project_id
        return True

    for attempt in range(max_retries + 1):
        success_recorded = False  # {ts("id_1506")}
        need_retry = False  # {ts("id_1507")}

        try:
            async for chunk in stream_post_async(
                url=target_url,
                body=final_payload,
                native=native,
                headers=auth_headers
            ):
                # {ts("id_1508")}Response{ts("id_1509")}
                if isinstance(chunk, Response):
                    status_code = chunk.status_code
                    last_error_response = chunk  # {ts("id_1510")}

                    # {ts("id_1511")},{ts("id_1512")}decode
                    error_body = None
                    try:
                        error_body = chunk.body.decode('utf-8') if isinstance(chunk.body, bytes) else str(chunk.body)
                    except Exception:
                        error_body = ""

                    # {ts(f"id_1514429")}{ts("id_189503")}{ts("id_1513")}
                    if status_code == 429 or status_code == 503 or status_code in DISABLE_ERROR_CODES:
                        log.warning(ff"[ANTIGRAVITY STREAM] {ts("id_1515")} (status={status_code}), {ts("id_100f")}: {current_file}, {ts("id_1516")}: {error_body[:500] if error_body else '{ts("id_39")}'}")

                        # {ts("id_1517")},{ts("id_1518")}
                        if next_cred_task is None and attempt < max_retries:
                            next_cred_task = asyncio.create_task(
                                credential_manager.get_valid_credential(
                                    mode="antigravity", model_key=model_name
                                )
                            )

                        # {ts("id_1519")}
                        cooldown_until = None
                        if (status_code == 429 or status_code == 503) and error_body:
                            # {ts("id_1520")}error_body{ts("id_1521")}
                            try:
                                cooldown_until = await parse_and_log_cooldown(error_body, mode="antigravity")
                            except Exception:
                                pass

                        await record_api_call_error(
                            credential_manager, current_file, status_code,
                            cooldown_until, mode="antigravity", model_key=model_name
                        )

                        # {ts("id_1522")}
                        should_retry = await handle_error_with_retry(
                            credential_manager, status_code, current_file,
                            retry_config["retry_enabled"], attempt, max_retries, retry_interval,
                            mode="antigravity"
                        )

                        if should_retry and attempt < max_retries:
                            need_retry = True
                            break  # {ts("id_1523")}
                        else:
                            # {ts("id_1524")}
                            log.error(ff"[ANTIGRAVITY STREAM] {ts("id_1525")}")
                            yield chunk
                            return
                    else:
                        # {ts("id_1526")}
                        log.error(ff"[ANTIGRAVITY STREAM] {ts("id_1527")} (status={status_code}), {ts("id_100f")}: {current_file}, {ts("id_1516")}: {error_body[:500] if error_body else '{ts("id_39")}'}")
                        await record_api_call_error(
                            credential_manager, current_file, status_code,
                            None, mode="antigravity", model_key=model_name
                        )
                        yield chunk
                        return
                else:
                    # {ts(f"id_1529")}Response{ts("id_1528")}yield{ts("id_1530")}
                    # {ts("id_1531")}chunk{ts("id_1532")}
                    if not success_recorded:
                        await record_api_call_success(
                            credential_manager, current_file, mode="antigravity", model_key=model_name
                        )
                        success_recorded = True
                        log.debug(ff"[ANTIGRAVITY STREAM] {ts("id_1533")}: {model_name}")

                    # {ts("id_1535")}chunk{ts("id_1534")}
                    if isinstance(chunk, bytes):
                        log.debug(f"[ANTIGRAVITY STREAM RAW] chunk(bytes): {chunk}")
                    else:
                        log.debug(f"[ANTIGRAVITY STREAM RAW] chunk(str): {chunk}")

                    yield chunk

            # {ts("id_1536")}
            if success_recorded:
                log.debug(ff"[ANTIGRAVITY STREAM] {ts("id_1537")}: {model_name}")
                return
            elif not need_retry:
                # {ts("id_1538")}
                log.warning(ff"[ANTIGRAVITY STREAM] {ts("id_1539")}: {current_file}")
                await record_api_call_error(
                    credential_manager, current_file, 200,
                    None, mode="antigravity", model_key=model_name
                )
                
                if attempt < max_retries:
                    need_retry = True
                else:
                    log.error(ff"[ANTIGRAVITY STREAM] {ts("id_1540")}")
                    yield Response(
                        content=json.dumps({f"error": "{ts("id_1541")}"}),
                        status_code=500,
                        media_type="application/json"
                    )
                    return
            
            # {ts("id_1542")}
            if need_retry:
                log.info(ff"[ANTIGRAVITY STREAM] {ts("id_1543")} (attempt {attempt + 2}/{max_retries + 1})...")

                # {ts("id_1544")},{ts("id_1545")}
                if next_cred_task is not None:
                    try:
                        cred_result = await next_cred_task
                        next_cred_task = None  # {ts("id_1546")}

                        if cred_result:
                            current_file, credential_data = cred_result
                            access_token = credential_data.get("access_token") or credential_data.get("token")
                            project_id = credential_data.get("project_id", "")
                            if access_token and project_id:
                                auth_headers["Authorization"] = f"Bearer {access_token}"
                                final_payload["project"] = project_id
                                await asyncio.sleep(retry_interval)
                                continue  # {ts("id_1279")}
                    except Exception as e:
                        log.warning(ff"[ANTIGRAVITY STREAM] {ts("id_1547")}: {e}")
                        next_cred_task = None

                # {ts("id_1548")},{ts("id_1549")}
                await asyncio.sleep(retry_interval)

                if not await refresh_credential_fast():
                    log.error(f"[ANTIGRAVITY STREAM] {ts("id_1550")}")
                    yield Response(
                        content=json.dumps({f"error": "{ts("id_1493")}"}),
                        status_code=500,
                        media_type="application/json"
                    )
                    return
                continue  # {ts("id_1279")}

        except Exception as e:
            log.error(ff"[ANTIGRAVITY STREAM] {ts("id_1551")}: {e}, {ts("id_100")}: {current_file}")
            if attempt < max_retries:
                log.info(ff"[ANTIGRAVITY STREAM] {ts("id_1552")} (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            else:
                # {ts("id_1553")}
                log.error(ff"[ANTIGRAVITY STREAM] {ts("id_1554")}: {e}")
                yield last_error_response


async def non_stream_request(
    body: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Response:
    """
    {ts("id_1555")}

    Args:
        body: {ts("id_1447")}
        headers: {ts("id_1487")}

    Returns:
        Response{ts("id_1509")}
    """
    # {ts("id_1556")}
    if await get_antigravity_stream2nostream():
        log.debug(f"[ANTIGRAVITY] {ts("id_1557")}")

        # {ts("id_1095")}stream_request{ts("id_1558")}
        stream = stream_request(body=body, native=False, headers=headers)

        # {ts("id_1559")}
        # stream_request{ts("id_1560")}yield Response{ts("id_1561")}
        # collect_streaming_response{ts("id_1562")}
        return await collect_streaming_response(stream)

    # {ts("id_1563")}
    log.debug(f"[ANTIGRAVITY] {ts("id_1564")}")

    model_name = body.get("model", "")

    # 1. {ts("id_1490")}
    cred_result = await credential_manager.get_valid_credential(
        mode="antigravity", model_key=model_name
    )

    if not cred_result:
        # {ts("id_1492")}None{ts("id_1491500")}
        log.error(f"[ANTIGRAVITY] {ts("id_1493")}")
        return Response(
            content=json.dumps({f"error": "{ts("id_1493")}"}),
            status_code=500,
            media_type="application/json"
        )

    current_file, credential_data = cred_result
    access_token = credential_data.get("access_token") or credential_data.get("token")
    project_id = credential_data.get("project_id", "")

    if not access_token:
        log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
        return Response(
            content=json.dumps({f"error": "{ts("id_1494")}"}),
            status_code=500,
            media_type="application/json"
        )

    # 2. {ts("id_1475")}URL{ts("id_1495")}
    antigravity_url = await get_antigravity_api_url()
    target_url = f"{antigravity_url}/v1internal:generateContent"

    auth_headers = build_antigravity_headers(access_token, model_name)

    # {ts("id_1496")}headers
    if headers:
        auth_headers.update(headers)

    # {ts("id_1497")}project{ts("id_61")}payload
    final_payload = {
        "model": body.get("model"),
        "project": project_id,
        "request": body.get("request", {}),
    }

    # 3. {ts("id_1095")}post_async{ts("id_1498")}
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    DISABLE_ERROR_CODES = await get_auto_ban_error_codes()  # {ts("id_1499")}
    last_error_response = None  # {ts("id_1500")}
    next_cred_task = None  # {ts("id_1501")}

    # {ts(f"id_1502")}({ts("id_1504")}token{ts("id_15")}project_id,{ts("id_15")}03)
    async def refresh_credential_fast():
        nonlocal current_file, access_token, auth_headers, project_id, final_payload
        cred_result = await credential_manager.get_valid_credential(
            mode="antigravity", model_key=model_name
        )
        if not cred_result:
            return None
        current_file, credential_data = cred_result
        access_token = credential_data.get("access_token") or credential_data.get("token")
        project_id = credential_data.get("project_id", "")
        if not access_token:
            return None
        # {ts(f"id_1504")}token{ts("id_15")}project_id,{ts("id_15")}05headers{ts("id_15")}payload
        auth_headers["Authorization"] = f"Bearer {access_token}"
        final_payload["project"] = project_id
        return True

    for attempt in range(max_retries + 1):
        need_retry = False  # {ts("id_1507")}
        
        try:
            response = await post_async(
                url=target_url,
                json=final_payload,
                headers=auth_headers,
                timeout=300.0
            )

            status_code = response.status_code

            # {ts("id_984")}
            if status_code == 200:
                # {ts("id_1565")}
                if not response.content or len(response.content) == 0:
                    log.warning(ff"[ANTIGRAVITY] {ts("id_1567200")}{ts("id_1566")}: {current_file}")
                    
                    # {ts("id_1519")}
                    await record_api_call_error(
                        credential_manager, current_file, 200,
                        None, mode="antigravity", model_key=model_name
                    )
                    
                    if attempt < max_retries:
                        need_retry = True
                    else:
                        log.error(ff"[ANTIGRAVITY] {ts("id_1540")}")
                        return Response(
                            content=json.dumps({f"error": "{ts("id_1541")}"}),
                            status_code=500,
                            media_type="application/json"
                        )
                else:
                    # {ts("id_1568")}
                    await record_api_call_success(
                        credential_manager, current_file, mode="antigravity", model_key=model_name
                    )
                    return Response(
                        content=response.content,
                        status_code=200,
                        headers=dict(response.headers)
                    )

            # {ts("id_979")} - {ts("id_1510")}
            if status_code != 200:
                last_error_response = Response(
                    content=response.content,
                    status_code=status_code,
                    headers=dict(response.headers)
                )

                # {ts("id_1569")}
                # {ts("id_1570")},{ts("id_1571")}
                error_text = ""
                try:
                    error_text = response.text
                except Exception:
                    pass

                if status_code == 429 or status_code == 503 or status_code in DISABLE_ERROR_CODES:
                    log.warning(ff"[ANTIGRAVITY] {ts("id_1572")} (status={status_code}), {ts("id_100f")}: {current_file}, {ts("id_1516")}: {error_text[:500] if error_text else '{ts("id_39")}'}")

                    # {ts("id_1517")},{ts("id_1518")}
                    if next_cred_task is None and attempt < max_retries:
                        next_cred_task = asyncio.create_task(
                            credential_manager.get_valid_credential(
                                mode="antigravity", model_key=model_name
                            )
                        )

                    # {ts("id_1519")}
                    cooldown_until = None
                    if status_code == 429 or status_code == 503 and error_text:
                        # {ts("id_1520")}error_text{ts("id_1521")}
                        try:
                            cooldown_until = await parse_and_log_cooldown(error_text, mode="antigravity")
                        except Exception:
                            pass

                    await record_api_call_error(
                        credential_manager, current_file, status_code,
                        cooldown_until, mode="antigravity", model_key=model_name
                    )

                    # {ts("id_1522")}
                    should_retry = await handle_error_with_retry(
                        credential_manager, status_code, current_file,
                        retry_config["retry_enabled"], attempt, max_retries, retry_interval,
                        mode="antigravity"
                    )

                    if should_retry and attempt < max_retries:
                        need_retry = True
                    else:
                        # {ts("id_1524")}
                        log.error(ff"[ANTIGRAVITY] {ts("id_1525")}")
                        return last_error_response
                else:
                    # {ts("id_1526")}
                    log.error(ff"[ANTIGRAVITY] {ts("id_1573")} (status={status_code}), {ts("id_100f")}: {current_file}, {ts("id_1516")}: {error_text[:500] if error_text else '{ts("id_39")}'}")
                    await record_api_call_error(
                        credential_manager, current_file, status_code,
                        None, mode="antigravity", model_key=model_name
                    )
                    return last_error_response
            
            # {ts("id_1542")}
            if need_retry:
                log.info(ff"[ANTIGRAVITY] {ts("id_1543")} (attempt {attempt + 2}/{max_retries + 1})...")

                # {ts("id_1544")},{ts("id_1545")}
                if next_cred_task is not None:
                    try:
                        cred_result = await next_cred_task
                        next_cred_task = None  # {ts("id_1546")}

                        if cred_result:
                            current_file, credential_data = cred_result
                            access_token = credential_data.get("access_token") or credential_data.get("token")
                            project_id = credential_data.get("project_id", "")
                            if access_token and project_id:
                                auth_headers["Authorization"] = f"Bearer {access_token}"
                                final_payload["project"] = project_id
                                await asyncio.sleep(retry_interval)
                                continue  # {ts("id_1279")}
                    except Exception as e:
                        log.warning(ff"[ANTIGRAVITY] {ts("id_1547")}: {e}")
                        next_cred_task = None

                # {ts("id_1548")},{ts("id_1549")}
                await asyncio.sleep(retry_interval)

                if not await refresh_credential_fast():
                    log.error(f"[ANTIGRAVITY] {ts("id_1550")}")
                    return Response(
                        content=json.dumps({f"error": "{ts("id_1493")}"}),
                        status_code=500,
                        media_type="application/json"
                    )
                continue  # {ts("id_1279")}

        except Exception as e:
            log.error(ff"[ANTIGRAVITY] {ts("id_1574")}: {e}, {ts("id_100")}: {current_file}")
            if attempt < max_retries:
                log.info(ff"[ANTIGRAVITY] {ts("id_1552")} (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            else:
                # {ts("id_1553")}
                log.error(ff"[ANTIGRAVITY] {ts("id_1554")}: {e}")
                return last_error_response

    # {ts("id_1575")}
    log.error(f"[ANTIGRAVITY] {ts("id_1576")}")
    return last_error_response


# ==================== {ts("id_1577")} ====================

async def fetch_available_models() -> List[Dict[str, Any]]:
    """
    {ts("id_1578")} OpenAI API {ts("id_1579")}
    
    Returns:
        {ts("id_1580")}
        
    Raises:
        {ts("id_1581")}
    """
    # {ts("id_1582")}
    cred_result = await credential_manager.get_valid_credential(mode="antigravity")
    if not cred_result:
        log.error("[ANTIGRAVITY] No valid credentials available for fetching models")
        return []

    current_file, credential_data = cred_result
    access_token = credential_data.get("access_token") or credential_data.get("token")

    if not access_token:
        log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
        return []

    # {ts("id_1583")}
    headers = build_antigravity_headers(access_token)

    try:
        # {ts("id_463")} POST {ts("id_1584")}
        antigravity_url = await get_antigravity_api_url()

        response = await post_async(
            url=f"{antigravity_url}/v1internal:fetchAvailableModels",
            json={},  # {ts("id_1585")}
            headers=headers
        )

        if response.status_code == 200:
            data = response.json()
            log.debug(f"[ANTIGRAVITY] Raw models response: {json.dumps(data, ensure_ascii=False)[:500]}")

            # {ts(f"id_188")} OpenAI {ts("id_1586")} Model {ts("id_1587")}
            model_list = []
            current_timestamp = int(datetime.now(timezone.utc).timestamp())

            if 'models' in data and isinstance(data['models'], dict):
                # {ts("id_1588")}
                for model_id in data['models'].keys():
                    model = Model(
                        id=model_id,
                        object='model',
                        created=current_timestamp,
                        owned_by='google'
                    )
                    model_list.append(model_to_dict(model))

            # {ts("id_1589")} claude-opus-4-5 {ts("id_794")}
            claude_opus_model = Model(
                id='claude-opus-4-5',
                object='model',
                created=current_timestamp,
                owned_by='google'
            )
            model_list.append(model_to_dict(claude_opus_model))

            log.info(f"[ANTIGRAVITY] Fetched {len(model_list)} available models")
            return model_list
        else:
            log.error(f"[ANTIGRAVITY] Failed to fetch models ({response.status_code}): {response.text[:500]}")
            return []

    except Exception as e:
        import traceback
        log.error(f"[ANTIGRAVITY] Failed to fetch models: {e}")
        log.error(f"[ANTIGRAVITY] Traceback: {traceback.format_exc()}")
        return []


async def fetch_quota_info(access_token: str) -> Dict[str, Any]:
    """
    {ts("id_1590")}
    
    Args:
        access_token: Antigravity {ts("id_1476")}
        
    Returns:
        {ts("id_1591")}
        {
            "success": True/False,
            "models": {
                "model_name": {
                    "remaining": 0.95,
                    "resetTime": "12-20 10:30",
                    "resetTimeRaw": "2025-12-20T02:30:00Z"
                }
            },
            f"error": "{ts("id_1593")}" ({ts("id_1592")})
        }
    """

    headers = build_antigravity_headers(access_token)

    try:
        antigravity_url = await get_antigravity_api_url()

        response = await post_async(
            url=f"{antigravity_url}/v1internal:fetchAvailableModels",
            json={},
            headers=headers,
            timeout=30.0
        )

        if response.status_code == 200:
            data = response.json()
            log.debug(f"[ANTIGRAVITY QUOTA] Raw response: {json.dumps(data, ensure_ascii=False)[:500]}")

            quota_info = {}

            if 'models' in data and isinstance(data['models'], dict):
                for model_id, model_data in data['models'].items():
                    if isinstance(model_data, dict) and 'quotaInfo' in model_data:
                        quota = model_data['quotaInfo']
                        remaining = quota.get('remainingFraction', 0)
                        reset_time_raw = quota.get('resetTime', '')

                        # {ts("id_1594")}
                        reset_time_beijing = 'N/A'
                        if reset_time_raw:
                            try:
                                utc_date = datetime.fromisoformat(reset_time_raw.replace('Z', '+00:00'))
                                # {ts("id_1594")} (UTC+8)
                                from datetime import timedelta
                                beijing_date = utc_date + timedelta(hours=8)
                                reset_time_beijing = beijing_date.strftime('%m-%d %H:%M')
                            except Exception as e:
                                log.warning(f"[ANTIGRAVITY QUOTA] Failed to parse reset time: {e}")

                        quota_info[model_id] = {
                            "remaining": remaining,
                            "resetTime": reset_time_beijing,
                            "resetTimeRaw": reset_time_raw
                        }

            return {
                "success": True,
                "models": quota_info
            }
        else:
            log.error(f"[ANTIGRAVITY QUOTA] Failed to fetch quota ({response.status_code}): {response.text[:500]}")
            return {
                "success": False,
                f"error": f"API{ts("id_1595")}: {response.status_code}"
            }

    except Exception as e:
        import traceback
        log.error(f"[ANTIGRAVITY QUOTA] Failed to fetch quota: {e}")
        log.error(f"[ANTIGRAVITY QUOTA] Traceback: {traceback.format_exc()}")
        return {
            "success": False,
            "error": str(e)
        }
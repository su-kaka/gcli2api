from src.i18n import ts
"""
GeminiCli API Client - Handles all communication with GeminiCli API.
This module is used by both OpenAI compatibility layer and native Gemini endpoints.
GeminiCli API {ts(f"id_1597")} - {ts('id_1468')} GeminiCli API {ts('id_1596')}
"""

import sys
from pathlib import Path

# {ts(f"id_1599")}Python{ts('id_1598')}
if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import Response
from config import get_code_assist_endpoint, get_auto_ban_error_codes
from src.api.utils import get_model_group
from log import log

from src.credential_manager import credential_manager
from src.httpx_client import stream_post_async, post_async

# {ts(f"id_1469")}
from src.api.utils import (
    handle_error_with_retry,
    get_retry_config,
    record_api_call_success,
    record_api_call_error,
    parse_and_log_cooldown,
)
from src.utils import GEMINICLI_USER_AGENT

# ==================== {ts(f"id_1470")} ====================

# {ts(f"id_1472")} credential_manager{ts('id_1471')}


# ==================== {ts(f"id_1600")} ====================

async def prepare_request_headers_and_payload(
    payload: dict, credential_data: dict, target_url: str
):
    """
    {ts(f"id_1601")}payload
    
    Args:
        payload: {ts(f"id_1602")}payload
        credential_data: {ts(f"id_1603")}
        target_url: {ts(f"id_1604")}URL
        
    Returns:
        {ts(f"id_1605")}: (headers, final_payload, target_url)
        
    Raises:
        Exception: {ts(f"id_1606")}
    """
    token = credential_data.get("token") or credential_data.get("access_token", "")
    if not token:
        raise Exception(f"{ts('id_1607')}token{ts('id_413f')}access_token{ts('id_1608')}")

    source_request = payload.get("request", {})

    # {ts(f"id_1610")}API{ts('id_463')}Bearer Token{ts('id_1609')}ID
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": GEMINICLI_USER_AGENT,
    }
    project_id = credential_data.get("project_id", "")
    if not project_id:
        raise Exception(f"{ts('id_884')}ID{ts('id_1611')}")
    final_payload = {
        "model": payload.get("model"),
        "project": project_id,
        "request": source_request,
    }

    return headers, final_payload, target_url


# ==================== {ts(f"id_1481")} ====================

async def stream_request(
    body: Dict[str, Any],
    native: bool = False,
    headers: Optional[Dict[str, str]] = None,
):
    """
    {ts(f"id_1482")}

    Args:
        body: {ts(f"id_1447")}
        native: {ts(f"id_1483")}bytes{ts('id_1485')}False{ts('id_1484')}str{ts('id_1486')}
        headers: {ts(f"id_1487")}

    Yields:
        Response{ts(f"id_1488")} bytes{ts('id_1486')}/str{ts('id_1489')}
    """
    # {ts(f"id_1490")}
    model_name = body.get("model", "")
    model_group = get_model_group(model_name)

    # 1. {ts(f"id_1490")}
    cred_result = await credential_manager.get_valid_credential(
        mode="geminicli", model_key=model_group
    )

    if not cred_result:
        # {ts(f"id_1492")}None{ts('id_1491500')}
        yield Response(
            content=json.dumps({f"error": "{ts('id_1493')}"}),
            status_code=500,
            media_type="application/json"
        )
        return

    current_file, credential_data = cred_result

    # 2. {ts(f"id_1475")}URL{ts('id_1495')}
    try:
        auth_headers, final_payload, target_url = await prepare_request_headers_and_payload(
            body, credential_data,
            f"{await get_code_assist_endpoint()}/v1internal:streamGenerateContent?alt=sse"
        )

        # {ts(f"id_1496")}headers
        if headers:
            auth_headers.update(headers)

    except Exception as e:
        log.error(f"{ts('id_1612')}: {e}")
        yield Response(
            content=json.dumps({f"error": f"{ts('id_1612')}: {str(e)}"}),
            status_code=500,
            media_type="application/json"
        )
        return

    # 3. {ts(f"id_1095")}stream_post_async{ts('id_1498')}
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    DISABLE_ERROR_CODES = await get_auto_ban_error_codes()  # {ts(f"id_1499")}
    last_error_response = None  # {ts(f"id_1500")}
    next_cred_task = None  # {ts(f"id_1501")}

    # {ts(f"id_1502")}({ts('id_1504')}token{ts('id_15')}project_id,{ts('id_15')}03)
    async def refresh_credential_fast():
        nonlocal current_file, credential_data, auth_headers, final_payload
        cred_result = await credential_manager.get_valid_credential(
            mode="geminicli", model_key=model_group
        )
        if not cred_result:
            return None
        current_file, credential_data = cred_result
        try:
            # {ts(f"id_1504")}token{ts('id_15')}project_id,{ts('id_15')}05headers{ts('id_15')}payload
            token = credential_data.get("token") or credential_data.get("access_token", "")
            project_id = credential_data.get("project_id", "")
            if not token or not project_id:
                return None

            # {ts(f"id_1613")}headers{ts('id_15')}payload
            auth_headers["Authorization"] = f"Bearer {token}"
            final_payload["project"] = project_id
            return True
        except Exception:
            return None

    for attempt in range(max_retries + 1):
        success_recorded = False  # {ts(f"id_1506")}
        need_retry = False  # {ts(f"id_1507")}

        try:
            async for chunk in stream_post_async(
                url=target_url,
                body=final_payload,
                native=native,
                headers=auth_headers
            ):
                # {ts(f"id_1508")}Response{ts('id_1509')}
                if isinstance(chunk, Response):
                    status_code = chunk.status_code
                    last_error_response = chunk  # {ts(f"id_1510")}

                    # {ts(f"id_1511")},{ts('id_1512')}decode
                    error_body = None
                    try:
                        error_body = chunk.body.decode('utf-8') if isinstance(chunk.body, bytes) else str(chunk.body)
                    except Exception:
                        error_body = ""

                    # {ts(f"id_1514429")}{ts('id_189503')}{ts('id_1513')}
                    if status_code == 429 or status_code == 503 or status_code in DISABLE_ERROR_CODES:
                        log.warning(f"[GEMINICLI STREAM] {ts('id_1515')} (status={status_code}), {ts('id_100f')}: {current_file}, {ts('id_1516')}: {error_body[:500] if error_body else '{ts('id_39')}'}")

                        # {ts(f"id_1517")},{ts('id_1518')}
                        if next_cred_task is None and attempt < max_retries:
                            next_cred_task = asyncio.create_task(
                                credential_manager.get_valid_credential(
                                    mode="geminicli", model_key=model_group
                                )
                            )

                        # {ts(f"id_1519")}
                        cooldown_until = None
                        if (status_code == 429 or status_code == 503) and error_body:
                            # {ts(f"id_1520")}error_body{ts('id_1521')}
                            try:
                                cooldown_until = await parse_and_log_cooldown(error_body, mode="geminicli")
                            except Exception:
                                pass

                        await record_api_call_error(
                            credential_manager, current_file, status_code,
                            cooldown_until, mode="geminicli", model_key=model_group
                        )

                        # {ts(f"id_1522")}
                        should_retry = await handle_error_with_retry(
                            credential_manager, status_code, current_file,
                            retry_config["retry_enabled"], attempt, max_retries, retry_interval,
                            mode="geminicli"
                        )

                        if should_retry and attempt < max_retries:
                            need_retry = True
                            break  # {ts(f"id_1523")}
                        else:
                            # {ts(f"id_1524")}
                            log.error(f"[GEMINICLI STREAM] {ts('id_1525')}")
                            yield chunk
                            return
                    else:
                        # {ts(f"id_1526")}
                        log.error(f"[GEMINICLI STREAM] {ts('id_1527')} (status={status_code}), {ts('id_100f')}: {current_file}, {ts('id_1516')}: {error_body[:500] if error_body else '{ts('id_39')}'}")
                        await record_api_call_error(
                            credential_manager, current_file, status_code,
                            None, mode="geminicli", model_key=model_group
                        )
                        yield chunk
                        return
                else:
                    # {ts(f"id_1529")}Response{ts('id_1528')}yield{ts('id_1530')}
                    # {ts(f"id_1531")}chunk{ts('id_1532')}
                    if not success_recorded:
                        await record_api_call_success(
                            credential_manager, current_file, mode="geminicli", model_key=model_group
                        )
                        success_recorded = True
                        log.debug(f"[GEMINICLI STREAM] {ts('id_1533')}: {model_name}")

                    yield chunk

            # {ts(f"id_1536")}
            if success_recorded:
                log.debug(f"[GEMINICLI STREAM] {ts('id_1537')}: {model_name}")
                return

            # {ts(f"id_1542")}
            if need_retry:
                log.info(f"[GEMINICLI STREAM] {ts('id_1543')} (attempt {attempt + 2}/{max_retries + 1})...")

                # {ts(f"id_1544")},{ts('id_1545')}
                if next_cred_task is not None:
                    try:
                        cred_result = await next_cred_task
                        next_cred_task = None  # {ts(f"id_1546")}

                        if cred_result:
                            current_file, credential_data = cred_result
                            # {ts(f"id_1614")}
                            token = credential_data.get("token") or credential_data.get("access_token", "")
                            project_id = credential_data.get("project_id", "")
                            if token and project_id:
                                auth_headers["Authorization"] = f"Bearer {token}"
                                final_payload["project"] = project_id
                                await asyncio.sleep(retry_interval)
                                continue  # {ts(f"id_1279")}
                    except Exception as e:
                        log.warning(f"[GEMINICLI STREAM] {ts('id_1547')}: {e}")
                        next_cred_task = None

                # {ts(f"id_1548")},{ts('id_1549')}
                await asyncio.sleep(retry_interval)

                if not await refresh_credential_fast():
                    log.error(f"[GEMINICLI STREAM] {ts('id_1615')}")
                    yield Response(
                        content=json.dumps({f"error": "{ts('id_1493')}"}),
                        status_code=500,
                        media_type="application/json"
                    )
                    return
                continue  # {ts(f"id_1279")}

        except Exception as e:
            log.error(f"[GEMINICLI STREAM] {ts('id_1551')}: {e}, {ts('id_100')}: {current_file}")
            if attempt < max_retries:
                log.info(f"[GEMINICLI STREAM] {ts('id_1552')} (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            else:
                # {ts(f"id_1553")}
                log.error(f"[GEMINICLI STREAM] {ts('id_1554')}: {e}")
                yield last_error_response


async def non_stream_request(
    body: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Response:
    """
    {ts(f"id_1555")}

    Args:
        body: {ts(f"id_1447")}
        native: {ts(f"id_1616")}
        headers: {ts(f"id_1487")}

    Returns:
        Response{ts(f"id_1509")}
    """
    # {ts(f"id_1490")}
    model_name = body.get("model", "")
    model_group = get_model_group(model_name)

    # 1. {ts(f"id_1490")}
    cred_result = await credential_manager.get_valid_credential(
        mode="geminicli", model_key=model_group
    )

    if not cred_result:
        # {ts(f"id_1492")}None{ts('id_1491500')}
        return Response(
            content=json.dumps({f"error": "{ts('id_1493')}"}),
            status_code=500,
            media_type="application/json"
        )

    current_file, credential_data = cred_result

    # 2. {ts(f"id_1475")}URL{ts('id_1495')}
    try:
        auth_headers, final_payload, target_url = await prepare_request_headers_and_payload(
            body, credential_data,
            f"{await get_code_assist_endpoint()}/v1internal:generateContent"
        )

        # {ts(f"id_1496")}headers
        if headers:
            auth_headers.update(headers)

    except Exception as e:
        log.error(f"{ts('id_1612')}: {e}")
        return Response(
            content=json.dumps({f"error": f"{ts('id_1612')}: {str(e)}"}),
            status_code=500,
            media_type="application/json"
        )

    # 3. {ts(f"id_1095")}post_async{ts('id_1498')}
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    DISABLE_ERROR_CODES = await get_auto_ban_error_codes()  # {ts(f"id_1499")}
    last_error_response = None  # {ts(f"id_1500")}
    next_cred_task = None  # {ts(f"id_1501")}

    # {ts(f"id_1502")}({ts('id_1504')}token{ts('id_15')}project_id,{ts('id_15')}03)
    async def refresh_credential_fast():
        nonlocal current_file, credential_data, auth_headers, final_payload
        cred_result = await credential_manager.get_valid_credential(
            mode="geminicli", model_key=model_group
        )
        if not cred_result:
            return None
        current_file, credential_data = cred_result
        try:
            # {ts(f"id_1504")}token{ts('id_15')}project_id,{ts('id_15')}05headers{ts('id_15')}payload
            token = credential_data.get("token") or credential_data.get("access_token", "")
            project_id = credential_data.get("project_id", "")
            if not token or not project_id:
                return None

            # {ts(f"id_1613")}headers{ts('id_15')}payload
            auth_headers["Authorization"] = f"Bearer {token}"
            final_payload["project"] = project_id
            return True
        except Exception:
            return None

    for attempt in range(max_retries + 1):
        try:
            response = await post_async(
                url=target_url,
                json=final_payload,
                headers=auth_headers,
                timeout=300.0
            )

            status_code = response.status_code

            # {ts(f"id_984")}
            if status_code == 200:
                await record_api_call_success(
                    credential_manager, current_file, mode="geminicli", model_key=model_group
                )
                # {ts(f"id_1619")},{ts('id_1617')}header{ts('id_1618')}
                response_headers = dict(response.headers)
                response_headers.pop('content-encoding', None)
                response_headers.pop('content-length', None)

                return Response(
                    content=response.content,
                    status_code=200,
                    headers=response_headers
                )

            # {ts(f"id_979")} - {ts('id_1510')}
            # {ts(f"id_1619")},{ts('id_1617')}header{ts('id_1618')}
            error_headers = dict(response.headers)
            error_headers.pop('content-encoding', None)
            error_headers.pop('content-length', None)

            last_error_response = Response(
                content=response.content,
                status_code=status_code,
                headers=error_headers
            )

            # {ts(f"id_1569")}
            # {ts(f"id_1570")},{ts('id_1571')}
            error_text = ""
            try:
                error_text = response.text
            except Exception:
                pass

            # {ts(f"id_1620")}
            if status_code in DISABLE_ERROR_CODES:
                log.error(f"{ts('id_1621')} (status={status_code}), {ts('id_100f')}: {current_file}, {ts('id_1516')}: {error_text[:500] if error_text else '{ts('id_39')}'}")

                # {ts(f"id_1517")},{ts('id_1518')}
                if next_cred_task is None and attempt < max_retries:
                    next_cred_task = asyncio.create_task(
                        credential_manager.get_valid_credential(
                            mode="geminicli", model_key=model_group
                        )
                    )

                # {ts(f"id_1622")}
                await record_api_call_error(
                    credential_manager, current_file, status_code,
                    None, mode="geminicli", model_key=model_group
                )
                # {ts(f"id_1623")}
                if attempt < max_retries:
                    log.info(f"[NON-STREAM] {ts('id_1624')} (attempt {attempt + 2}/{max_retries + 1})...")

                    # {ts(f"id_1544")},{ts('id_1545')}
                    if next_cred_task is not None:
                        try:
                            cred_result = await next_cred_task
                            next_cred_task = None  # {ts(f"id_1546")}

                            if cred_result:
                                current_file, credential_data = cred_result
                                # {ts(f"id_1614")}
                                token = credential_data.get("token") or credential_data.get("access_token", "")
                                project_id = credential_data.get("project_id", "")
                                if token and project_id:
                                    auth_headers["Authorization"] = f"Bearer {token}"
                                    final_payload["project"] = project_id
                                    await asyncio.sleep(retry_interval)
                                    continue  # {ts(f"id_1279")}
                        except Exception as e:
                            log.warning(f"[NON-STREAM] {ts('id_1547')}: {e}")
                            next_cred_task = None

                    # {ts(f"id_1548")},{ts('id_1549')}
                    await asyncio.sleep(retry_interval)

                    if not await refresh_credential_fast():
                        log.error(f"[NON-STREAM] {ts('id_1615')}")
                        return Response(
                            content=json.dumps({f"error": "{ts('id_1493')}"}),
                            status_code=500,
                            media_type="application/json"
                        )
                    continue  # {ts(f"id_1279")}
                else:
                    # {ts(f"id_1625")}
                    log.error(f"[NON-STREAM] {ts('id_1626')}")
                    return last_error_response
            else:
                # {ts(f"id_1628429")}{ts('id_1627')}
                log.warning(f"{ts('id_1572')} (status={status_code}), {ts('id_100f')}: {current_file}, {ts('id_1516')}: {error_text[:500] if error_text else '{ts('id_39')}'}")

                # {ts(f"id_1517")},{ts('id_1518')}
                if next_cred_task is None and attempt < max_retries:
                    next_cred_task = asyncio.create_task(
                        credential_manager.get_valid_credential(
                            mode="geminicli", model_key=model_group
                        )
                    )

                # {ts(f"id_1519")}
                cooldown_until = None
                if status_code == 429 or status_code == 503 and error_text:
                    # {ts(f"id_1520")}error_text{ts('id_1521')}
                    try:
                        cooldown_until = await parse_and_log_cooldown(error_text, mode="geminicli")
                    except Exception:
                        pass

                await record_api_call_error(
                    credential_manager, current_file, status_code,
                    cooldown_until, mode="geminicli", model_key=model_group
                )

                # {ts(f"id_1522")}
                should_retry = await handle_error_with_retry(
                    credential_manager, status_code, current_file,
                    retry_config["retry_enabled"], attempt, max_retries, retry_interval,
                    mode="geminicli"
                )

                if should_retry and attempt < max_retries:
                    # {ts(f"id_1629")}
                    log.info(f"[NON-STREAM] {ts('id_1543')} (attempt {attempt + 2}/{max_retries + 1})...")

                    # {ts(f"id_1544")},{ts('id_1545')}
                    if next_cred_task is not None:
                        try:
                            cred_result = await next_cred_task
                            next_cred_task = None  # {ts(f"id_1546")}

                            if cred_result:
                                current_file, credential_data = cred_result
                                # {ts(f"id_1614")}
                                token = credential_data.get("token") or credential_data.get("access_token", "")
                                project_id = credential_data.get("project_id", "")
                                if token and project_id:
                                    auth_headers["Authorization"] = f"Bearer {token}"
                                    final_payload["project"] = project_id
                                    await asyncio.sleep(retry_interval)
                                    continue  # {ts(f"id_1279")}
                        except Exception as e:
                            log.warning(f"[NON-STREAM] {ts('id_1547')}: {e}")
                            next_cred_task = None

                    # {ts(f"id_1548")},{ts('id_1549')}
                    await asyncio.sleep(retry_interval)

                    if not await refresh_credential_fast():
                        log.error(f"[NON-STREAM] {ts('id_1615')}")
                        return Response(
                            content=json.dumps({f"error": "{ts('id_1493')}"}),
                            status_code=500,
                            media_type="application/json"
                        )
                    continue  # {ts(f"id_1279")}
                else:
                    # {ts(f"id_1524")}
                    log.error(f"[NON-STREAM] {ts('id_1525')}")
                    return last_error_response

        except Exception as e:
            log.error(f"{ts('id_1574')}: {e}, {ts('id_100')}: {current_file}")
            if attempt < max_retries:
                log.info(f"[NON-STREAM] {ts('id_1552')} (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            else:
                # {ts(f"id_1630500")}{ts('id_806')}
                log.error(f"[NON-STREAM] {ts('id_1554')}: {e}")
                if last_error_response:
                    return last_error_response
                else:
                    return Response(
                        content=json.dumps({f"error": f"{ts('id_1631')}: {str(e)}"}),
                        status_code=500,
                        media_type="application/json"
                    )

    # {ts(f"id_1575")}
    log.error(f"[NON-STREAM] {ts('id_1576')}")
    return last_error_response


# ==================== {ts(f"id_1632")} ====================

if __name__ == "__main__":
    """
    {ts(f"id_1634")}API{ts('id_1633')}
    {ts(f"id_1635")}: python src/api/geminicli.py
    """
    print("=" * 80)
    print(f"GeminiCli API {ts('id_1444')}")
    print("=" * 80)

    # {ts(f"id_1636")}
    test_body = {
        "model": "gemini-2.5-flash",
        "request": {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "Hello, tell me a joke in one sentence."}]
                }
            ]
        }
    }

    async def test_stream_request():
        f"""{ts('id_1637')}"""
        print("\n" + "=" * 80)
        print(f"{ts('id_14461')}{ts('id_1445')} (stream_request with native=False)")
        print("=" * 80)
        print(f"{ts('id_1447')}: {json.dumps(test_body, indent=2, ensure_ascii=False)}\n")

        print(f"{ts('id_1448')} ({ts('id_1449')}chunk):")
        print("-" * 80)

        chunk_count = 0
        async for chunk in stream_request(body=test_body, native=False):
            chunk_count += 1
            if isinstance(chunk, Response):
                # {ts(f"id_1638")}
                print(f"\n❌ {ts('id_1638')}:")
                print(f"  {ts('id_1461')}: {chunk.status_code}")
                print(f"  Content-Type: {chunk.headers.get('content-type', 'N/A')}")
                try:
                    content = chunk.body.decode('utf-8') if isinstance(chunk.body, bytes) else str(chunk.body)
                    print(f"  {ts('id_1639')}: {content}")
                except Exception as e:
                    print(f"  {ts('id_1640')}: {e}")
            else:
                # {ts(f"id_1641")} (str{ts('id_1454')})
                print(f"\nChunk #{chunk_count}:")
                print(f"  {ts('id_1454')}: {type(chunk).__name__}")
                print(f"  {ts('id_1455')}: {len(chunk) if hasattr(chunk, '__len__') else 'N/A'}")
                print(f"  {ts('id_1456')}: {repr(chunk[:200] if len(chunk) > 200 else chunk)}")

                # {ts(f"id_1643")}SSE{ts('id_1642')}
                if isinstance(chunk, str) and chunk.startswith("data: "):
                    try:
                        data_line = chunk.strip()
                        if data_line.startswith("data: "):
                            json_str = data_line[6:]  # {ts(f"id_1644")} "data: " {ts('id_365')}
                            json_data = json.loads(json_str)
                            print(f"  {ts('id_1457')}JSON: {json.dumps(json_data, indent=4, ensure_ascii=False)}")
                    except Exception as e:
                        print(f"  SSE{ts('id_1645')}: {e}")

        print(f"\n{ts('id_1458')} {chunk_count} {ts('id_723')}chunk")

    async def test_non_stream_request():
        f"""{ts('id_1646')}"""
        print("\n" + "=" * 80)
        print(f"{ts('id_14462')}{ts('id_1459')} (non_stream_request)")
        print("=" * 80)
        print(f"{ts('id_1447')}: {json.dumps(test_body, indent=2, ensure_ascii=False)}\n")

        response = await non_stream_request(body=test_body)

        print(f"{ts('id_1460')}:")
        print("-" * 80)
        print(f"{ts('id_1461')}: {response.status_code}")
        print(f"Content-Type: {response.headers.get('content-type', 'N/A')}")
        print(f"\n{ts('id_1462')}: {dict(response.headers)}\n")

        try:
            content = response.body.decode('utf-8') if isinstance(response.body, bytes) else str(response.body)
            print(f"{ts('id_1463')} ({ts('id_1464')}):\n{content}\n")

            # {ts(f"id_1647")}JSON
            try:
                json_data = json.loads(content)
                print(f"{ts('id_1463')} ({ts('id_1465')}JSON):")
                print(json.dumps(json_data, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print(f"({ts('id_1648')}JSON{ts('id_57')})")
        except Exception as e:
            print(f"{ts('id_1640')}: {e}")

    async def main():
        f"""{ts('id_1649')}"""
        try:
            # {ts(f"id_1637")}
            await test_stream_request()

            # {ts(f"id_1646")}
            await test_non_stream_request()

            print("\n" + "=" * 80)
            print(f"{ts('id_1466')}")
            print("=" * 80)

        except Exception as e:
            print(f"\n❌ {ts('id_1650')}: {e}")
            import traceback
            traceback.print_exc()

    # {ts(f"id_1651")}
    asyncio.run(main())

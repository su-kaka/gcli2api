from src.i18n import ts
"""
Web{ts(f"id_3672")} - {ts("id_3671")}HTTP{ts("id_3670")}
{ts("id_3673")}web.py{ts("id_3674")}
"""

import asyncio
import datetime
import io
import json
import os
import time
import zipfile
from collections import deque
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.websockets import WebSocketState

import config
from log import log

from src.auth import (
    asyncio_complete_auth_flow,
    complete_auth_flow_from_callback_url,
    create_auth_url,
    get_auth_status,
    verify_password,
)
from src.credential_manager import credential_manager
from .models import (
    LoginRequest,
    AuthStartRequest,
    AuthCallbackRequest,
    AuthCallbackUrlRequest,
    CredFileActionRequest,
    CredFileBatchActionRequest,
    ConfigSaveRequest,
)
from src.storage_adapter import get_storage_adapter
from src.utils import verify_panel_token, GEMINICLI_USER_AGENT, ANTIGRAVITY_USER_AGENT
from src.api.antigravity import fetch_quota_info
from src.google_oauth_api import Credentials, fetch_project_id
from config import get_code_assist_endpoint, get_antigravity_api_url

# {ts("id_3675")}
router = APIRouter()

# {ts("id_3676")}
# {ts("id_3677")} web.py {ts("id_3678")}

# WebSocket{ts("id_3679")}


class ConnectionManager:
    def __init__(self, max_connections: int = 3):  # {ts("id_3680")}
        # {ts("id_3681")}
        self.active_connections: deque = deque(maxlen=max_connections)
        self.max_connections = max_connections
        self._last_cleanup = 0
        self._cleanup_interval = 120  # 120{ts("id_3682")}

    async def connect(self, websocket: WebSocket):
        # {ts("id_3683")}
        self._auto_cleanup()

        # {ts("id_3684")}
        if len(self.active_connections) >= self.max_connections:
            await websocket.close(code=1008, reason="Too many connections")
            return False

        await websocket.accept()
        self.active_connections.append(websocket)
        log.debug(ff"WebSocket{ts("id_3685")}: {len(self.active_connections)}")
        return True

    def disconnect(self, websocket: WebSocket):
        # {ts("id_3686")}
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass  # {ts("id_3687")}
        log.debug(ff"WebSocket{ts("id_3688")}: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, message: str):
        # {ts("id_3689")}
        dead_connections = []
        for conn in self.active_connections:
            try:
                await conn.send_text(message)
            except Exception:
                dead_connections.append(conn)

        # {ts("id_3690")}
        for dead_conn in dead_connections:
            self.disconnect(dead_conn)

    def _auto_cleanup(self):
        f"""{ts("id_3683")}"""
        current_time = time.time()
        if current_time - self._last_cleanup > self._cleanup_interval:
            self.cleanup_dead_connections()
            self._last_cleanup = current_time

    def cleanup_dead_connections(self):
        f"""{ts("id_3691")}"""
        original_count = len(self.active_connections)
        # {ts("id_3692")}
        alive_connections = deque(
            [
                conn
                for conn in self.active_connections
                if hasattr(conn, "client_state")
                and conn.client_state != WebSocketState.DISCONNECTED
            ],
            maxlen=self.max_connections,
        )

        self.active_connections = alive_connections
        cleaned = original_count - len(self.active_connections)
        if cleaned > 0:
            log.debug(ff"{ts("id_1993")} {cleaned} {ts("id_3693")}: {len(self.active_connections)}")


manager = ConnectionManager()


def is_mobile_user_agent(user_agent: str) -> bool:
    f"""{ts("id_3694")}"""
    if not user_agent:
        return False

    user_agent_lower = user_agent.lower()
    mobile_keywords = [
        "mobile",
        "android",
        "iphone",
        "ipad",
        "ipod",
        "blackberry",
        "windows phone",
        "samsung",
        "htc",
        "motorola",
        "nokia",
        "palm",
        "webos",
        "opera mini",
        "opera mobi",
        "fennec",
        "minimo",
        "symbian",
        "psp",
        "nintendo",
        "tablet",
    ]

    return any(keyword in user_agent_lower for keyword in mobile_keywords)


@router.get("/", response_class=HTMLResponse)
async def serve_control_panel(request: Request):
    f"""{ts("id_3695")}"""
    try:
        user_agent = request.headers.get("user-agent", "")
        is_mobile = is_mobile_user_agent(user_agent)

        if is_mobile:
            html_file_path = "front/control_panel_mobile.html"
        else:
            html_file_path = "front/control_panel.html"

        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # Replace $id_N placeholders with translations
        import re
        ids = re.findall(r"\$id_\d+", html_content)
        for vid in set(ids):
            key = vid[1:]
            translation = ts(key)
            html_content = html_content.replace(vid, translation)
            
        return HTMLResponse(content=html_content)

    except Exception as e:
        log.error(ff"{ts("id_3696")}: {e}")
        raise HTTPException(status_code=500, detail=f"{ts("id_3697")}")


@router.post("/auth/login")
async def login(request: LoginRequest):
    f"""{ts("id_3698")}token{ts("id_292")}"""
    try:
        if await verify_password(request.password):
            # {ts("id_3699")}token{ts("id_3700")}
            return JSONResponse(content={f"token": request.password, "message": "{ts("id_821")}"})
        else:
            raise HTTPException(status_code=401, detail=f"{ts("id_3662")}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_823")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/start")
async def start_auth(request: AuthStartRequest, token: str = Depends(verify_panel_token)):
    f"""{ts("id_3701")}ID"""
    try:
        # {ts("id_3702")}ID{ts("id_3703")}
        project_id = request.project_id
        if not project_id:
            log.info(f"{ts("id_3705")}ID{ts("id_3704")}...")

        # {ts("id_3706")}
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
        log.error(ff"{ts("id_3707")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/callback")
async def auth_callback(request: AuthCallbackRequest, token: str = Depends(verify_panel_token)):
    f"""{ts("id_3708")}ID"""
    try:
        # {ts("id_884")}ID{ts("id_3709")}
        project_id = request.project_id

        # {ts("id_3706")}
        user_session = token if token else None
        # {ts("id_1906")}OAuth{ts("id_3710")}
        result = await asyncio_complete_auth_flow(
            project_id, user_session, mode=request.mode
        )

        if result["success"]:
            # {ts("id_3711")}
            return JSONResponse(
                content={
                    "credentials": result["credentials"],
                    "file_path": result["file_path"],
                    f"message": "{ts("id_1869")}",
                    "auto_detected_project": result.get("auto_detected_project", False),
                }
            )
        else:
            # {ts("id_3713")}ID{ts("id_3712")}
            if result.get("requires_manual_project_id"):
                # {ts("id_463")}JSON{ts("id_1516")}
                return JSONResponse(
                    status_code=400,
                    content={"error": result["error"], "requires_manual_project_id": True},
                )
            elif result.get("requires_project_selection"):
                # {ts("id_3714")}
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
        log.error(ff"{ts("id_3715")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/callback-url")
async def auth_callback_url(request: AuthCallbackUrlRequest, token: str = Depends(verify_panel_token)):
    f"""{ts("id_592")}URL{ts("id_591")}"""
    try:
        # {ts("id_3661")}URL{ts("id_57")}
        if not request.callback_url or not request.callback_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail=f"{ts("id_3716")}URL")

        # {ts("id_592")}URL{ts("id_1932")}
        result = await complete_auth_flow_from_callback_url(
            request.callback_url, request.project_id, mode=request.mode
        )

        if result["success"]:
            # {ts("id_3711")}
            return JSONResponse(
                content={
                    "credentials": result["credentials"],
                    "file_path": result["file_path"],
                    f"message": "{ts("id_592")}URL{ts("id_1869")}",
                    "auto_detected_project": result.get("auto_detected_project", False),
                }
            )
        else:
            # {ts("id_3717")}
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
        log.error(ff"{ts("id_592")}URL{ts("id_3718")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/status/{project_id}")
async def check_auth_status(project_id: str, token: str = Depends(verify_panel_token)):
    f"""{ts("id_593")}"""
    try:
        if not project_id:
            raise HTTPException(status_code=400, detail=f"Project ID {ts("id_3719")}")

        status = get_auth_status(project_id)
        return JSONResponse(content=status)

    except Exception as e:
        log.error(ff"{ts("id_3720")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# {ts("id_780")} (Helper Functions)
# =============================================================================


def validate_mode(mode: str = "geminicli") -> str:
    """
    {ts("id_3661")} mode {ts("id_226")}

    Args:
        mode: {ts(f"id_3721")} ("geminicli" {ts("id_413")} "antigravity")

    Returns:
        str: {ts("id_3722")} mode {ts("id_2850")}

    Raises:
        HTTPException: {ts("id_2183")} mode {ts("id_3723")}
    """
    if mode not in ["geminicli", "antigravity"]:
        raise HTTPException(
            status_code=400,
            detail=ff"{ts("id_3725")} mode {ts("id_226f")}: {mode}{ts("id_3724")} 'geminicli' {ts("id_413")} 'antigravity'"
        )
    return mode


def get_env_locked_keys() -> set:
    f"""{ts("id_3726")}"""
    env_locked_keys = set()

    # {ts("id_463")} config.py {ts("id_3727")}
    for env_key, config_key in config.ENV_MAPPINGS.items():
        if os.getenv(env_key):
            env_locked_keys.add(config_key)

    return env_locked_keys


async def extract_json_files_from_zip(zip_file: UploadFile) -> List[dict]:
    f"""{ts("id_1731")}ZIP{ts("id_3728f")}JSON{ts("id_112")}"""
    try:
        # {ts("id_3730")}ZIP{ts("id_3729")}
        zip_content = await zip_file.read()

        # {ts("id_3732")}ZIP{ts("id_3731")}

        files_data = []

        with zipfile.ZipFile(io.BytesIO(zip_content), "r") as zip_ref:
            # {ts("id_712")}ZIP{ts("id_3733")}
            file_list = zip_ref.namelist()
            json_files = [
                f for f in file_list if f.endswith(".json") and not f.startswith("__MACOSX/")
            ]

            if not json_files:
                raise HTTPException(status_code=400, detail=f"ZIP{ts("id_3734")}JSON{ts("id_112")}")

            log.info(ff"{ts("id_1731")}ZIP{ts("id_112f")} {zip_file.filename} {ts("id_3735")} {len(json_files)} {ts("id_723f")}JSON{ts("id_112")}")

            for json_filename in json_files:
                try:
                    # {ts("id_3730")}JSON{ts("id_3729")}
                    with zip_ref.open(json_filename) as json_file:
                        content = json_file.read()

                        try:
                            content_str = content.decode("utf-8")
                        except UnicodeDecodeError:
                            log.warning(ff"{ts("id_3736")}: {json_filename}")
                            continue

                        # {ts("id_3737")}
                        filename = os.path.basename(json_filename)
                        files_data.append({"filename": filename, "content": content_str})

                except Exception as e:
                    log.warning(ff"{ts("id_590")}ZIP{ts("id_3738f")} {json_filename} {ts("id_3739")}: {e}")
                    continue

        log.info(ff"{ts("id_1915")}ZIP{ts("id_3728f")} {len(files_data)} {ts("id_3740")}JSON{ts("id_112")}")
        return files_data

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail=f"{ts("id_3725")}ZIP{ts("id_3741")}")
    except Exception as e:
        log.error(ff"{ts("id_590")}ZIP{ts("id_3742")}: {e}")
        raise HTTPException(status_code=500, detail=ff"{ts("id_590")}ZIP{ts("id_3742")}: {str(e)}")


async def upload_credentials_common(
    files: List[UploadFile], mode: str = "geminicli"
) -> JSONResponse:
    f"""{ts("id_3743")}"""
    mode = validate_mode(mode)

    if not files:
        raise HTTPException(status_code=400, detail=f"{ts("id_769")}")

    # {ts("id_3744")}
    if len(files) > 100:
        raise HTTPException(
            status_code=400, detail=ff"{ts("id_3745100")}{ts("id_3746f")}{len(files)}{ts("id_723")}"
        )

    files_data = []
    for file in files:
        # {ts("id_3747")}JSON{ts("id_15")}ZIP
        if file.filename.endswith(".zip"):
            zip_files_data = await extract_json_files_from_zip(file)
            files_data.extend(zip_files_data)
            log.info(ff"{ts("id_1731")}ZIP{ts("id_112f")} {file.filename} {ts("id_3748")} {len(zip_files_data)} {ts("id_723f")}JSON{ts("id_112")}")

        elif file.filename.endswith(".json"):
            # {ts(f"id_3750")}JSON{ts("id_112")} - {ts("id_3749")}
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
                    status_code=400, detail=ff"{ts("id_112")} {file.filename} {ts("id_3751")}"
                )

            files_data.append({"filename": file.filename, "content": content_str})
        else:
            raise HTTPException(
                status_code=400, detail=ff"{ts("id_112")} {file.filename} {ts("id_767f")}JSON{ts("id_15")}ZIP{ts("id_112")}"
            )

    

    batch_size = 1000
    all_results = []
    total_success = 0

    for i in range(0, len(files_data), batch_size):
        batch_files = files_data[i : i + batch_size]

        async def process_single_file(file_data):
            try:
                filename = file_data["filename"]
                # {ts("id_3752")}basename{ts("id_3753")}
                filename = os.path.basename(filename)
                content_str = file_data["content"]
                credential_data = json.loads(content_str)

                # {ts("id_3754")}
                if mode == "antigravity":
                    await credential_manager.add_antigravity_credential(filename, credential_data)
                else:
                    await credential_manager.add_credential(filename, credential_data)

                log.debug(ff"{ts("id_772")} {mode} {ts("id_721")}: {filename}")
                return {f"filename": filename, "status": "success", "message": "{ts("id_3755")}"}

            except json.JSONDecodeError as e:
                return {
                    "filename": file_data["filename"],
                    "status": "error",
                    f"message": f"JSON{ts("id_2019")}: {str(e)}",
                }
            except Exception as e:
                return {
                    "filename": file_data["filename"],
                    "status": "error",
                    f"message": f"{ts("id_3756")}: {str(e)}",
                }

        log.info(ff"{ts("id_3757")} {len(batch_files)} {ts("id_723f")} {mode} {ts("id_112")}...")
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
                        f"message": f"{ts("id_3758")}: {str(result)}",
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
            ff"{ts("id_3759")} {batch_num}/{total_batches} {ts("id_405f")}: {ts("id_984")} "
            ff"{batch_uploaded_count}/{len(batch_files)} {ts("id_723")} {mode} {ts("id_112")}"
        )

    if total_success > 0:
        return JSONResponse(
            content={
                "uploaded_count": total_success,
                "total_count": len(files_data),
                "results": all_results,
                f"message": f"{ts("id_3760")}: {ts("id_984f")} {total_success}/{len(files_data)} {ts("id_723")} {mode} {ts("id_112")}",
            }
        )
    else:
        raise HTTPException(status_code=400, detail=ff"{ts("id_2389")} {mode} {ts("id_3761")}")


async def get_creds_status_common(
    offset: int, limit: int, status_filter: str, mode: str = "geminicli",
    error_code_filter: str = None, cooldown_filter: str = None
) -> JSONResponse:
    f"""{ts("id_3762")}"""
    mode = validate_mode(mode)
    # {ts("id_3763")}
    if offset < 0:
        raise HTTPException(status_code=400, detail=f"offset {ts("id_3764")} 0")
    if limit not in [20, 50, 100, 200, 500, 1000]:
        raise HTTPException(status_code=400, detail=f"limit {ts("id_3765")} 20{ts("id_18950f")}{ts("id_189100")}{ts("id_189200f")}{ts("id_18950")}0 {ts("id_413")} 1000")
    if status_filter not in ["all", "enabled", "disabled"]:
        raise HTTPException(status_code=400, detail=f"status_filter {ts("id_3765")} all{ts("id_189f")}enabled {ts("id_413")} disabled")
    if cooldown_filter and cooldown_filter not in ["all", "in_cooldown", "no_cooldown"]:
        raise HTTPException(status_code=400, detail=f"cooldown_filter {ts("id_3765")} all{ts("id_189f")}in_cooldown {ts("id_413")} no_cooldown")

    

    storage_adapter = await get_storage_adapter()
    backend_info = await storage_adapter.get_backend_info()
    backend_type = backend_info.get("backend_type", "unknown")

    # {ts("id_3766")}
    if hasattr(storage_adapter._backend, 'get_credentials_summary'):
        result = await storage_adapter._backend.get_credentials_summary(
            offset=offset,
            limit=limit,
            status_filter=status_filter,
            mode=mode,
            error_code_filter=error_code_filter if error_code_filter and error_code_filter != "all" else None,
            cooldown_filter=cooldown_filter if cooldown_filter and cooldown_filter != "all" else None
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

            creds_list.append(cred_info)

        return JSONResponse(content={
            "items": creds_list,
            "total": result["total"],
            "offset": offset,
            "limit": limit,
            "has_more": (offset + limit) < result["total"],
            "stats": result.get("stats", {"total": 0, "normal": 0, "disabled": 0}),
        })

    # {ts("id_3767")}MongoDB/{ts("id_3768")}
    all_credentials = await storage_adapter.list_credentials(mode=mode)
    all_states = await storage_adapter.get_all_credential_states(mode=mode)

    # {ts("id_745")}
    filtered_credentials = []
    for filename in all_credentials:
        file_status = all_states.get(filename, {"disabled": False})
        is_disabled = file_status.get("disabled", False)

        if status_filter == "all":
            filtered_credentials.append(filename)
        elif status_filter == "enabled" and not is_disabled:
            filtered_credentials.append(filename)
        elif status_filter == "disabled" and is_disabled:
            filtered_credentials.append(filename)

    total_count = len(filtered_credentials)
    paginated_credentials = filtered_credentials[offset:offset + limit]

    creds_list = []
    for filename in paginated_credentials:
        file_status = all_states.get(filename, {
            "error_codes": [],
            "disabled": False,
            "last_success": time.time(),
            "user_email": None,
        })

        cred_info = {
            "filename": os.path.basename(filename),
            "user_email": file_status.get("user_email"),
            "disabled": file_status.get("disabled", False),
            "error_codes": file_status.get("error_codes", []),
            "last_success": file_status.get("last_success", time.time()),
            "backend_type": backend_type,
            "model_cooldowns": file_status.get("model_cooldowns", {}),
        }

        creds_list.append(cred_info)

    return JSONResponse(content={
        "items": creds_list,
        "total": total_count,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total_count,
    })


async def download_all_creds_common(mode: str = "geminicli") -> Response:
    f"""{ts("id_3769")}"""
    mode = validate_mode(mode)
    zip_filename = "antigravity_credentials.zip" if mode == "antigravity" else "credentials.zip"

    storage_adapter = await get_storage_adapter()
    credential_filenames = await storage_adapter.list_credentials(mode=mode)

    if not credential_filenames:
        raise HTTPException(status_code=404, detail=ff"{ts("id_3770")} {mode} {ts("id_721")}")

    log.info(ff"{ts("id_3771")} {len(credential_filenames)} {ts("id_723f")} {mode} {ts("id_721")}...")

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
                        log.debug(ff"{ts("id_3772")}: {idx}/{len(credential_filenames)}")

            except Exception as e:
                log.warning(ff"{ts("id_590")} {mode} {ts("id_721f")} {filename} {ts("id_3739")}: {e}")
                continue

    log.info(ff"{ts("id_3773")}: {ts("id_984f")} {success_count}/{len(credential_filenames)} {ts("id_762")}")

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_filename}"},
    )


async def fetch_user_email_common(filename: str, mode: str = "geminicli") -> JSONResponse:
    f"""{ts("id_3774")}"""
    mode = validate_mode(mode)

    filename_only = os.path.basename(filename)
    if not filename_only.endswith(".json"):
        raise HTTPException(status_code=404, detail=f"{ts("id_3775")}")

    storage_adapter = await get_storage_adapter()
    credential_data = await storage_adapter.get_credential(filename_only, mode=mode)
    if not credential_data:
        raise HTTPException(status_code=404, detail=f"{ts("id_3776")}")

    email = await credential_manager.get_or_fetch_user_email(filename_only, mode=mode)

    if email:
        return JSONResponse(
            content={
                "filename": filename_only,
                "user_email": email,
                f"message": "{ts("id_3777")}",
            }
        )
    else:
        return JSONResponse(
            content={
                "filename": filename_only,
                "user_email": None,
                f"message": "{ts("id_3778")}",
            },
            status_code=400,
        )


async def refresh_all_user_emails_common(mode: str = "geminicli") -> JSONResponse:
    f"""{ts("id_3779")} - {ts("id_3780")}
    
    {ts("id_3782")} get_all_credential_states {ts("id_3781")}
    """
    mode = validate_mode(mode)

    storage_adapter = await get_storage_adapter()
    
    # {ts("id_3783")}
    all_states = await storage_adapter.get_all_credential_states(mode=mode)

    results = []
    success_count = 0
    skipped_count = 0

    # {ts("id_3784")}
    for filename, state in all_states.items():
        try:
            cached_email = state.get("user_email")

            if cached_email:
                # {ts("id_3785")}
                skipped_count += 1
                results.append({
                    "filename": os.path.basename(filename),
                    "user_email": cached_email,
                    "success": True,
                    "skipped": True,
                })
                continue

            # {ts("id_3786")}
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
                    f"error": "{ts("id_3787")}",
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
            f"message": f"{ts("id_3790")} {success_count}/{total_count} {ts("id_3788f")} {skipped_count} {ts("id_3789")}",
        }
    )


async def deduplicate_credentials_by_email_common(mode: str = "geminicli") -> JSONResponse:
    f"""{ts("id_3792")} - {ts("id_3791")}"""
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
                    f"message": "{ts("id_3793")}",
                }
            )

        # {ts("id_3794")}
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
                        log.info(ff"{ts("id_3795")}: {filename} ({ts("id_1013")}: {email}) (mode={mode})")
                    else:
                        delete_errors.append(ff"{os.path.basename(filename)}: {ts("id_3796")}")
                except Exception as e:
                    delete_errors.append(f"{os.path.basename(filename)}: {str(e)}")
                    log.error(ff"{ts("id_3795")} {filename} {ts("id_3739")}: {e}")

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
                f"message": f"{ts("id_1007")} {deleted_count} {ts("id_1006f")} {kept_count} {ts("id_1009")}{duplicate_info.get('unique_email_count', 0)} {ts("id_1008")}",
            }
        )

    except Exception as e:
        log.error(ff"{ts("id_3797")}: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "deleted_count": 0,
                "kept_count": 0,
                "total_count": 0,
                f"message": f"{ts("id_3798")}: {str(e)}",
            }
        )


# =============================================================================
# {ts("id_3799")} (Route Handlers)
# =============================================================================


@router.post("/creds/upload")
async def upload_credentials(
    files: List[UploadFile] = File(...),
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    f"""{ts("id_3800")}"""
    try:
        mode = validate_mode(mode)
        return await upload_credentials_common(files, mode=mode)
    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_3801")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/status")
async def get_creds_status(
    token: str = Depends(verify_panel_token),
    offset: int = 0,
    limit: int = 50,
    status_filter: str = "all",
    error_code_filter: str = "all",
    cooldown_filter: str = "all",
    mode: str = "geminicli"
):
    """
    {ts("id_3802")}

    Args:
        offset: {ts("id_34800")}{ts("id_292")}
        limit: {ts(f"id_380350")}{ts("id_380420")}, 50, 100, 200, 500, 1000{ts("id_292")}
        status_filter: {ts(f"id_3483")}all={ts("id_1238")}, enabled={ts("id_724")}, disabled={ts("id_3484")}
        error_code_filter: {ts(f"id_3806")}all={ts("id_1238")}, {ts("id_3805")}"400", "403"{ts("id_292")}
        cooldown_filter: {ts(f"id_3487")}all={ts("id_1238")}, in_cooldown={ts("id_3489")}, no_cooldown={ts("id_3488")}
        mode: {ts(f"id_3807")}geminicli {ts("id_413")} antigravity{ts("id_292")}

    Returns:
        {ts("id_3808")}
    """
    try:
        mode = validate_mode(mode)
        return await get_creds_status_common(
            offset, limit, status_filter, mode=mode,
            error_code_filter=error_code_filter,
            cooldown_filter=cooldown_filter
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_3809")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/detail/{filename}")
async def get_cred_detail(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """
    {ts("id_3810")}
    {ts("id_3812")}/{ts("id_3811")}
    """
    try:
        mode = validate_mode(mode)
        # {ts("id_3813")}
        if not filename.endswith(".json"):
            raise HTTPException(status_code=400, detail=f"{ts("id_3775")}")

        

        storage_adapter = await get_storage_adapter()
        backend_info = await storage_adapter.get_backend_info()
        backend_type = backend_info.get("backend_type", "unknown")

        # {ts("id_3583")}
        credential_data = await storage_adapter.get_credential(filename, mode=mode)
        if not credential_data:
            raise HTTPException(status_code=404, detail=f"{ts("id_3814")}")

        # {ts("id_3815")}
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

        if backend_type == "file" and os.path.exists(filename):
            result.update({
                "size": os.path.getsize(filename),
                "modified_time": os.path.getmtime(filename),
            })

        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_3816")} {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/action")
async def creds_action(
    request: CredFileActionRequest,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    f"""{ts("id_3817")}/{ts("id_300f")}/{ts("id_601")}"""
    try:
        mode = validate_mode(mode)

        log.info(f"Received request: {request}")

        filename = request.filename
        action = request.action

        log.info(f"Performing action '{action}' on file: {filename} (mode={mode})")

        # {ts("id_3813")}
        if not filename.endswith(".json"):
            log.error(ff"{ts("id_3775")}: {filename}{ts("id_3818f")}.json{ts("id_3819")}")
            raise HTTPException(status_code=400, detail=ff"{ts("id_3775")}: {filename}")

        # {ts("id_3820")}
        storage_adapter = await get_storage_adapter()

        # {ts("id_3821")}
        # {ts("id_3822")}
        if action != "delete":
            # {ts("id_3823")}
            credential_data = await storage_adapter.get_credential(filename, mode=mode)
            if not credential_data:
                log.error(ff"{ts("id_3824")}: {filename} (mode={mode})")
                raise HTTPException(status_code=404, detail=f"{ts("id_3776")}")

        if action == "enable":
            log.info(ff"Web{ts("id_2282")}: {ts("id_3825")} {filename} (mode={mode})")
            result = await credential_manager.set_cred_disabled(filename, False, mode=mode)
            log.info(ff"[WebRoute] set_cred_disabled {ts("id_3826")}: {result}")
            if result:
                log.info(ff"Web{ts("id_2282")}: {ts("id_112f")} {filename} {ts("id_3827")} (mode={mode})")
                return JSONResponse(content={f"message": f"{ts("id_3828")} {os.path.basename(filename)}"})
            else:
                log.error(ff"Web{ts("id_2282")}: {ts("id_112f")} {filename} {ts("id_3829")} (mode={mode})")
                raise HTTPException(status_code=500, detail=f"{ts("id_3830")}")

        elif action == "disable":
            log.info(ff"Web{ts("id_2282")}: {ts("id_3831")} {filename} (mode={mode})")
            result = await credential_manager.set_cred_disabled(filename, True, mode=mode)
            log.info(ff"[WebRoute] set_cred_disabled {ts("id_3826")}: {result}")
            if result:
                log.info(ff"Web{ts("id_2282")}: {ts("id_112f")} {filename} {ts("id_3832")} (mode={mode})")
                return JSONResponse(content={f"message": f"{ts("id_3833")} {os.path.basename(filename)}"})
            else:
                log.error(ff"Web{ts("id_2282")}: {ts("id_112f")} {filename} {ts("id_3834")} (mode={mode})")
                raise HTTPException(status_code=500, detail=f"{ts("id_3835")}")

        elif action == "delete":
            try:
                # {ts(f"id_463")} CredentialManager {ts("id_3836")}/{ts("id_3837")}
                success = await credential_manager.remove_credential(filename, mode=mode)
                if success:
                    log.info(ff"{ts("id_3838")}: {filename} (mode={mode})")
                    return JSONResponse(
                        content={f"message": f"{ts("id_3839")} {os.path.basename(filename)}"}
                    )
                else:
                    raise HTTPException(status_code=500, detail=f"{ts("id_3840")}")
            except Exception as e:
                log.error(ff"{ts("id_299")} {filename} {ts("id_3739")}: {e}")
                raise HTTPException(status_code=500, detail=ff"{ts("id_3841")}: {str(e)}")

        else:
            raise HTTPException(status_code=400, detail=f"{ts("id_3842")}")

    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_3843")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/batch-action")
async def creds_batch_action(
    request: CredFileBatchActionRequest,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    f"""{ts("id_3844")}/{ts("id_300f")}/{ts("id_601")}"""
    try:
        mode = validate_mode(mode)

        action = request.action
        filenames = request.filenames

        if not filenames:
            raise HTTPException(status_code=400, detail=f"{ts("id_3845")}")

        log.info(ff"{ts("id_3847")} {len(filenames)} {ts("id_3846")} '{action}'")

        success_count = 0
        errors = []

        storage_adapter = await get_storage_adapter()

        for filename in filenames:
            try:
                # {ts("id_3848")}
                if not filename.endswith(".json"):
                    errors.append(ff"{filename}: {ts("id_3849")}")
                    continue

                # {ts("id_3850")}
                # {ts("id_3851")}
                if action != "delete":
                    credential_data = await storage_adapter.get_credential(filename, mode=mode)
                    if not credential_data:
                        errors.append(ff"{filename}: {ts("id_3814")}")
                        continue

                # {ts("id_3852")}
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
                            log.info(ff"{ts("id_3853")}: {filename}")
                        else:
                            errors.append(ff"{filename}: {ts("id_3796")}")
                            continue
                    except Exception as e:
                        errors.append(ff"{filename}: {ts("id_3841")} - {str(e)}")
                        continue
                else:
                    errors.append(ff"{filename}: {ts("id_3842")}")
                    continue

            except Exception as e:
                log.error(ff"{ts("id_590")} {filename} {ts("id_3739")}: {e}")
                errors.append(ff"{filename}: {ts("id_3756")} - {str(e)}")
                continue

        # {ts("id_3854")}
        result_message = ff"{ts("id_761")} {success_count}/{len(filenames)} {ts("id_762")}"
        if errors:
            result_message += f"\n{ts("id_3855")}:\n" + "\n".join(errors)

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
        log.error(ff"{ts("id_3856")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/download/{filename}")
async def download_cred_file(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    f"""{ts("id_603")}"""
    try:
        mode = validate_mode(mode)
        # {ts("id_3848")}
        if not filename.endswith(".json"):
            raise HTTPException(status_code=404, detail=f"{ts("id_3775")}")

        # {ts("id_3820")}
        storage_adapter = await get_storage_adapter()

        # {ts("id_3857")}
        credential_data = await storage_adapter.get_credential(filename, mode=mode)
        if not credential_data:
            raise HTTPException(status_code=404, detail=f"{ts("id_3858")}")

        # {ts("id_188")}JSON{ts("id_2850")}
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
        log.error(ff"{ts("id_3859")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/fetch-email/{filename}")
async def fetch_user_email(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    f"""{ts("id_3860")}"""
    try:
        mode = validate_mode(mode)
        return await fetch_user_email_common(filename, mode=mode)
    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_3105")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/refresh-all-emails")
async def refresh_all_user_emails(
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    f"""{ts("id_3861")}"""
    try:
        mode = validate_mode(mode)
        return await refresh_all_user_emails_common(mode=mode)
    except Exception as e:
        log.error(ff"{ts("id_3862")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/creds/deduplicate-by-email")
async def deduplicate_credentials_by_email(
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    f"""{ts("id_3863")} - {ts("id_3791")}"""
    try:
        mode = validate_mode(mode)
        return await deduplicate_credentials_by_email_common(mode=mode)
    except Exception as e:
        log.error(ff"{ts("id_3864")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/creds/download-all")
async def download_all_creds(
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """
    {ts("id_3865")}
    {ts("id_3866")}
    """
    try:
        mode = validate_mode(mode)
        return await download_all_creds_common(mode=mode)
    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_926")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/get")
async def get_config(token: str = Depends(verify_panel_token)):
    f"""{ts("id_611")}"""
    try:
        

        # {ts("id_3867")}TOML{ts("id_3868")}
        current_config = {}

        # {ts("id_137")}
        current_config["code_assist_endpoint"] = await config.get_code_assist_endpoint()
        current_config["credentials_dir"] = await config.get_credentials_dir()
        current_config["proxy"] = await config.get_proxy_config() or ""

        # {ts("id_3869")}
        current_config["oauth_proxy_url"] = await config.get_oauth_proxy_url()
        current_config["googleapis_proxy_url"] = await config.get_googleapis_proxy_url()
        current_config["resource_manager_api_url"] = await config.get_resource_manager_api_url()
        current_config["service_usage_api_url"] = await config.get_service_usage_api_url()
        current_config["antigravity_api_url"] = await config.get_antigravity_api_url()

        # {ts("id_1273")}
        current_config["auto_ban_enabled"] = await config.get_auto_ban_enabled()
        current_config["auto_ban_error_codes"] = await config.get_auto_ban_error_codes()

        # 429{ts("id_1278")}
        current_config["retry_429_max_retries"] = await config.get_retry_429_max_retries()
        current_config["retry_429_enabled"] = await config.get_retry_429_enabled()
        current_config["retry_429_interval"] = await config.get_retry_429_interval()

        # {ts("id_1305")}
        current_config["anti_truncation_max_attempts"] = await config.get_anti_truncation_max_attempts()

        # {ts("id_531")}
        current_config["compatibility_mode_enabled"] = await config.get_compatibility_mode_enabled()

        # {ts("id_3870")}
        current_config["return_thoughts_to_frontend"] = await config.get_return_thoughts_to_frontend()

        # Antigravity{ts("id_3871")}
        current_config["antigravity_stream2nostream"] = await config.get_antigravity_stream2nostream()

        # {ts("id_4")}
        current_config["host"] = await config.get_server_host()
        current_config["port"] = await config.get_server_port()
        current_config["api_password"] = await config.get_api_password()
        current_config["panel_password"] = await config.get_panel_password()
        current_config["password"] = await config.get_server_password()

        # {ts("id_3872")}
        storage_adapter = await get_storage_adapter()
        storage_config = await storage_adapter.get_all_config()

        # {ts("id_3873")}
        env_locked_keys = get_env_locked_keys()

        # {ts("id_3874")}
        for key, value in storage_config.items():
            if key not in env_locked_keys:
                current_config[key] = value

        return JSONResponse(content={"config": current_config, "env_locked": list(env_locked_keys)})

    except Exception as e:
        log.error(ff"{ts("id_3875")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/save")
async def save_config(request: ConfigSaveRequest, token: str = Depends(verify_panel_token)):
    f"""{ts("id_612")}"""
    try:
        
        new_config = request.config

        log.debug(ff"{ts("id_3876")}: {list(new_config.keys())}")
        log.debug(ff"{ts("id_3877")}password{ts("id_3185")}: {new_config.get('password', 'NOT_FOUND')}")

        # {ts("id_3878")}
        if "retry_429_max_retries" in new_config:
            if (
                not isinstance(new_config["retry_429_max_retries"], int)
                or new_config["retry_429_max_retries"] < 0
            ):
                raise HTTPException(status_code=400, detail=f"{ts("id_3881429")}{ts("id_38790f")}{ts("id_3880")}")

        if "retry_429_enabled" in new_config:
            if not isinstance(new_config["retry_429_enabled"], bool):
                raise HTTPException(status_code=400, detail=f"429{ts("id_3882")}")

        # {ts("id_3883")}
        if "retry_429_interval" in new_config:
            try:
                interval = float(new_config["retry_429_interval"])
                if interval < 0.01 or interval > 10:
                    raise HTTPException(status_code=400, detail=f"429{ts("id_38840")}.01-10{ts("id_3885")}")
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"429{ts("id_3886")}")

        if "anti_truncation_max_attempts" in new_config:
            if (
                not isinstance(new_config["anti_truncation_max_attempts"], int)
                or new_config["anti_truncation_max_attempts"] < 1
                or new_config["anti_truncation_max_attempts"] > 10
            ):
                raise HTTPException(
                    status_code=400, detail=f"{ts("id_38871")}-10{ts("id_3888")}"
                )

        if "compatibility_mode_enabled" in new_config:
            if not isinstance(new_config["compatibility_mode_enabled"], bool):
                raise HTTPException(status_code=400, detail=f"{ts("id_3889")}")

        if "return_thoughts_to_frontend" in new_config:
            if not isinstance(new_config["return_thoughts_to_frontend"], bool):
                raise HTTPException(status_code=400, detail=f"{ts("id_3890")}")

        if "antigravity_stream2nostream" in new_config:
            if not isinstance(new_config["antigravity_stream2nostream"], bool):
                raise HTTPException(status_code=400, detail=f"Antigravity{ts("id_3891")}")

        # {ts("id_3892")}
        if "host" in new_config:
            if not isinstance(new_config["host"], str) or not new_config["host"].strip():
                raise HTTPException(status_code=400, detail=f"{ts("id_3893")}")

        if "port" in new_config:
            if (
                not isinstance(new_config["port"], int)
                or new_config["port"] < 1
                or new_config["port"] > 65535
            ):
                raise HTTPException(status_code=400, detail=f"{ts("id_38941")}-65535{ts("id_3888")}")

        if "api_password" in new_config:
            if not isinstance(new_config["api_password"], str):
                raise HTTPException(status_code=400, detail=f"API{ts("id_3895")}")

        if "panel_password" in new_config:
            if not isinstance(new_config["panel_password"], str):
                raise HTTPException(status_code=400, detail=f"{ts("id_3896")}")

        if "password" in new_config:
            if not isinstance(new_config["password"], str):
                raise HTTPException(status_code=400, detail=f"{ts("id_3895")}")

        # {ts("id_3873")}
        env_locked_keys = get_env_locked_keys()

        # {ts("id_3897")}
        storage_adapter = await get_storage_adapter()
        for key, value in new_config.items():
            if key not in env_locked_keys:
                await storage_adapter.set_config(key, value)
                if key in ("password", "api_password", "panel_password"):
                    log.debug(ff"{ts("id_32")}{key}{ts("id_3898")}: {value}")

        # {ts("id_3899")}
        await config.reload_config()

        # {ts("id_3900")}
        test_api_password = await config.get_api_password()
        test_panel_password = await config.get_panel_password()
        test_password = await config.get_server_password()
        log.debug(ff"{ts("id_3901")}API{ts("id_133")}: {test_api_password}")
        log.debug(ff"{ts("id_3902")}: {test_panel_password}")
        log.debug(ff"{ts("id_3903")}: {test_password}")

        # {ts("id_3904")}
        response_data = {
            f"message": "{ts("id_1059")}",
            "saved_config": {k: v for k, v in new_config.items() if k not in env_locked_keys},
        }

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_1062")}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# {ts("id_1145")}WebSocket (Real-time Logs WebSocket)
# =============================================================================


@router.post("/logs/clear")
async def clear_logs(token: str = Depends(verify_panel_token)):
    f"""{ts("id_3905")}"""
    try:
        # {ts("id_3906")}
        log_file_path = os.getenv("LOG_FILE", "log.txt")

        # {ts("id_3907")}
        if os.path.exists(log_file_path):
            try:
                # {ts("id_3908")}UTF-8{ts("id_3909")}
                with open(log_file_path, "w", encoding="utf-8", newline="") as f:
                    f.write("")
                    f.flush()  # {ts("id_3910")}
                log.info(ff"{ts("id_3911")}: {log_file_path}")

                # {ts("id_3913")}WebSocket{ts("id_3912")}
                await manager.broadcast(f"--- {ts("id_3911")} ---")

                return JSONResponse(
                    content={f"message": f"{ts("id_3911")}: {os.path.basename(log_file_path)}"}
                )
            except Exception as e:
                log.error(ff"{ts("id_3914")}: {e}")
                raise HTTPException(status_code=500, detail=ff"{ts("id_3914")}: {str(e)}")
        else:
            return JSONResponse(content={f"message": "{ts("id_3915")}"})

    except Exception as e:
        log.error(ff"{ts("id_3914")}: {e}")
        raise HTTPException(status_code=500, detail=ff"{ts("id_3914")}: {str(e)}")


@router.get("/logs/download")
async def download_logs(token: str = Depends(verify_panel_token)):
    f"""{ts("id_615")}"""
    try:
        # {ts("id_3906")}
        log_file_path = os.getenv("LOG_FILE", "log.txt")

        # {ts("id_3907")}
        if not os.path.exists(log_file_path):
            raise HTTPException(status_code=404, detail=f"{ts("id_3915")}")

        # {ts("id_3916")}
        file_size = os.path.getsize(log_file_path)
        if file_size == 0:
            raise HTTPException(status_code=404, detail=f"{ts("id_3917")}")

        # {ts("id_3918")}
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gcli2api_logs_{timestamp}.txt"

        log.info(ff"{ts("id_615")}: {log_file_path}")

        return FileResponse(
            path=log_file_path,
            filename=filename,
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_3919")}: {e}")
        raise HTTPException(status_code=500, detail=ff"{ts("id_3919")}: {str(e)}")


@router.websocket("/logs/stream")
async def websocket_logs(websocket: WebSocket):
    f"""WebSocket{ts("id_3920")}"""
    # WebSocket {ts("id_251")}: {ts("id_3921")} token
    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=403, reason="Missing authentication token")
        log.warning(f"WebSocket{ts("id_3922")}: {ts("id_3923")}token")
        return

    # {ts("id_3661")} token
    try:
        panel_password = await config.get_panel_password()
        if token != panel_password:
            await websocket.close(code=403, reason="Invalid authentication token")
            log.warning(f"WebSocket{ts("id_3922")}: token{ts("id_3924")}")
            return
    except Exception as e:
        await websocket.close(code=1011, reason="Authentication error")
        log.error(ff"WebSocket{ts("id_3925")}: {e}")
        return

    # {ts("id_3926")}
    if not await manager.connect(websocket):
        return

    try:
        # {ts("id_3906")}
        log_file_path = os.getenv("LOG_FILE", "log.txt")

        # {ts("id_392750")}{ts("id_3928")}
        if os.path.exists(log_file_path):
            try:
                with open(log_file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    # {ts("id_393050")}{ts("id_3929")}
                    for line in lines[-50:]:
                        if line.strip():
                            await websocket.send_text(line.strip())
            except Exception as e:
                await websocket.send_text(f"Error reading log file: {e}")

        # {ts("id_3931")}
        last_size = os.path.getsize(log_file_path) if os.path.exists(log_file_path) else 0
        max_read_size = 8192  # {ts("id_39338")}KB{ts("id_3932")}
        check_interval = 2  # {ts(f"id_3934")}CPU{ts("id_15")}I/O{ts("id_3935")}

        # {ts("id_3936")}
        # {ts("id_3937")}receive_text() {ts("id_3938")}
        async def listen_for_disconnect():
            try:
                while True:
                    await websocket.receive_text()
            except Exception:
                pass

        listener_task = asyncio.create_task(listen_for_disconnect())

        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                # {ts("id_463")} asyncio.wait {ts("id_3939")}
                # timeout=check_interval {ts("id_3940")} asyncio.sleep
                done, pending = await asyncio.wait(
                    [listener_task],
                    timeout=check_interval,
                    return_when=asyncio.FIRST_COMPLETED
                )

                # {ts("id_3941")}
                if listener_task in done:
                    break

                if os.path.exists(log_file_path):
                    current_size = os.path.getsize(log_file_path)
                    if current_size > last_size:
                        # {ts("id_3942")}
                        read_size = min(current_size - last_size, max_read_size)

                        try:
                            with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                                f.seek(last_size)
                                new_content = f.read(read_size)

                                # {ts("id_3943")}
                                if not new_content:
                                    last_size = current_size
                                    continue

                                # {ts("id_3944")}
                                lines = new_content.splitlines(keepends=True)
                                if lines:
                                    # {ts("id_3945")}
                                    if not lines[-1].endswith("\n") and len(lines) > 1:
                                        # {ts("id_3946")}
                                        for line in lines[:-1]:
                                            if line.strip():
                                                await websocket.send_text(line.rstrip())
                                        # {ts("id_3947")}
                                        last_size += len(new_content.encode("utf-8")) - len(
                                            lines[-1].encode("utf-8")
                                        )
                                    else:
                                        # {ts("id_3948")}
                                        for line in lines:
                                            if line.strip():
                                                await websocket.send_text(line.rstrip())
                                        last_size += len(new_content.encode("utf-8"))
                        except UnicodeDecodeError as e:
                            # {ts("id_3949")}
                            log.warning(ff"WebSocket{ts("id_3950")}: {e}, {ts("id_3951")}")
                            last_size = current_size
                        except Exception as e:
                            await websocket.send_text(f"Error reading new content: {e}")
                            # {ts("id_3952")}
                            last_size = current_size

                    # {ts("id_3953")}
                    elif current_size < last_size:
                        last_size = 0
                        await websocket.send_text(f"--- {ts("id_3954")} ---")

        finally:
            # {ts("id_3955")}
            if not listener_task.done():
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"WebSocket logs error: {e}")
    finally:
        manager.disconnect(websocket)


async def verify_credential_project_common(filename: str, mode: str = "geminicli") -> JSONResponse:
    f"""{ts("id_3956")}project id{ts("id_3957")}"""
    mode = validate_mode(mode)

    # {ts("id_3813")}
    if not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail=f"{ts("id_3775")}")


    storage_adapter = await get_storage_adapter()

    # {ts("id_3583")}
    credential_data = await storage_adapter.get_credential(filename, mode=mode)
    if not credential_data:
        raise HTTPException(status_code=404, detail=f"{ts("id_3814")}")

    # {ts("id_3095")}
    credentials = Credentials.from_dict(credential_data)

    # {ts("id_683")}token{ts("id_3958")}
    token_refreshed = await credentials.refresh_if_needed()

    # {ts("id_2183")}token{ts("id_2984")}
    if token_refreshed:
        log.info(ff"Token{ts("id_2985")}: {filename} (mode={mode})")
        credential_data = credentials.to_dict()
        await storage_adapter.store_credential(filename, credential_data, mode=mode)

    # {ts("id_712")}API{ts("id_3959")}User-Agent
    if mode == "antigravity":
        api_base_url = await get_antigravity_api_url()
        user_agent = ANTIGRAVITY_USER_AGENT
    else:
        api_base_url = await get_code_assist_endpoint()
        user_agent = GEMINICLI_USER_AGENT

    # {ts("id_804")}project id
    project_id = await fetch_project_id(
        access_token=credentials.access_token,
        user_agent=user_agent,
        api_base_url=api_base_url
    )

    if project_id:
        # {ts("id_3960")}project_id
        credential_data["project_id"] = project_id
        await storage_adapter.store_credential(filename, credential_data, mode=mode)

        # {ts("id_3961")}
        await storage_adapter.update_credential_state(filename, {
            "disabled": False,
            "error_codes": []
        }, mode=mode)

        log.info(ff"{ts("id_805")} {mode} {ts("id_3963f")}: {filename} - Project ID: {project_id} - {ts("id_3962")}")

        return JSONResponse(content={
            "success": True,
            "filename": filename,
            "project_id": project_id,
            f"message": "{ts("id_947")}Project ID{ts("id_3964403f")}{ts("id_3965")}"
        })
    else:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "filename": filename,
                f"message": "{ts("id_3967")}Project ID{ts("id_3966")}"
            }
        )


@router.post("/creds/verify-project/{filename}")
async def verify_credential_project(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "geminicli"
):
    """
    {ts("id_3968")}project id{ts("id_3969")}project id
    {ts("id_3970403")}{ts("id_3971")}
    """
    try:
        mode = validate_mode(mode)
        return await verify_credential_project_common(filename, mode=mode)
    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_608")}Project ID{ts("id_979")} {filename}: {e}")
        raise HTTPException(status_code=500, detail=ff"{ts("id_950")}: {str(e)}")


@router.get("/creds/quota/{filename}")
async def get_credential_quota(
    filename: str,
    token: str = Depends(verify_panel_token),
    mode: str = "antigravity"
):
    """
    {ts("id_3972")} antigravity {ts("id_543")}
    """
    try:
        mode = validate_mode(mode)
        # {ts("id_3813")}
        if not filename.endswith(".json"):
            raise HTTPException(status_code=400, detail=f"{ts("id_3775")}")

        
        storage_adapter = await get_storage_adapter()

        # {ts("id_3583")}
        credential_data = await storage_adapter.get_credential(filename, mode=mode)
        if not credential_data:
            raise HTTPException(status_code=404, detail=f"{ts("id_3814")}")

        # {ts(f"id_463")} Credentials {ts("id_3973")} token {ts("id_1827")}
        from .google_oauth_api import Credentials

        creds = Credentials.from_dict(credential_data)

        # {ts("id_2983")} token{ts("id_2982")}
        await creds.refresh_if_needed()

        # {ts("id_2183")} token {ts("id_2984")}
        updated_data = creds.to_dict()
        if updated_data != credential_data:
            log.info(ff"Token{ts("id_2985")}: {filename}")
            await storage_adapter.store_credential(filename, updated_data, mode=mode)
            credential_data = updated_data

        # {ts("id_3097")}
        access_token = credential_data.get("access_token") or credential_data.get("token")
        if not access_token:
            raise HTTPException(status_code=400, detail=f"{ts("id_1494")}")

        # {ts("id_3974")}
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
                    f"error": quota_info.get("error", "{ts("id_727")}")
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        log.error(ff"{ts("id_3975")} {filename}: {e}")
        raise HTTPException(status_code=500, detail=ff"{ts("id_3976")}: {str(e)}")


@router.get("/version/info")
async def get_version_info(check_update: bool = False):
    """
    {ts(f"id_3977")} - {ts("id_1731")}version.txt{ts("id_3730")}
    {ts(f"id_3980")} check_update: {ts("id_3979")}GitHub{ts("id_3978")}
    """
    try:
        # {ts("id_3981")}
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        version_file = os.path.join(project_root, "version.txt")

        # {ts("id_3730")}version.txt
        if not os.path.exists(version_file):
            return JSONResponse({
                "success": False,
                f"error": "version.txt{ts("id_3858")}"
            })

        version_data = {}
        with open(version_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '=' in line:
                    key, value = line.split('=', 1)
                    version_data[key] = value

        # {ts("id_2015")}
        if 'short_hash' not in version_data:
            return JSONResponse({
                "success": False,
                f"error": "version.txt{ts("id_2019")}"
            })

        response_data = {
            "success": True,
            "version": version_data.get('short_hash', 'unknown'),
            "full_hash": version_data.get('full_hash', ''),
            "message": version_data.get('message', ''),
            "date": version_data.get('date', '')
        }

        # {ts("id_3982")}
        if check_update:
            try:
                from src.httpx_client import get_async

                # {ts(f"id_3983")}GitHub{ts("id_3984")}version.txt{ts("id_112")}
                github_version_url = "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/version.txt"

                # {ts("id_3985")}httpx{ts("id_1597")}
                resp = await get_async(github_version_url, timeout=10.0)

                if resp.status_code == 200:
                    # {ts("id_3986")}version.txt
                    remote_version_data = {}
                    for line in resp.text.strip().split('\n'):
                        line = line.strip()
                        if '=' in line:
                            key, value = line.split('=', 1)
                            remote_version_data[key] = value

                    latest_hash = remote_version_data.get('full_hash', '')
                    latest_short_hash = remote_version_data.get('short_hash', '')
                    current_hash = version_data.get('full_hash', '')

                    has_update = (current_hash != latest_hash) if current_hash and latest_hash else None

                    response_data['check_update'] = True
                    response_data['has_update'] = has_update
                    response_data['latest_version'] = latest_short_hash
                    response_data['latest_hash'] = latest_hash
                    response_data['latest_message'] = remote_version_data.get('message', '')
                    response_data['latest_date'] = remote_version_data.get('date', '')
                else:
                    # GitHub{ts("id_3987")}
                    response_data['check_update'] = False
                    response_data['update_error'] = ff"GitHub{ts("id_1595")}: {resp.status_code}"

            except Exception as e:
                log.debug(ff"{ts("id_1096")}: {e}")
                response_data['check_update'] = False
                response_data['update_error'] = str(e)

        return JSONResponse(response_data)

    except Exception as e:
        log.error(ff"{ts("id_1090")}: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        })





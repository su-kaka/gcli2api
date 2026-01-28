from src.i18n import ts
"""
{ts(f"id_251")}API{ts('id_702')}
"""

import asyncio
import json
import secrets
import socket
import threading
import time
import uuid
from datetime import timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from config import get_config_value, get_antigravity_api_url, get_code_assist_endpoint
from log import log

from .google_oauth_api import (
    Credentials,
    Flow,
    enable_required_apis,
    fetch_project_id,
    get_user_projects,
    select_default_project,
)
from .storage_adapter import get_storage_adapter
from .utils import (
    ANTIGRAVITY_CLIENT_ID,
    ANTIGRAVITY_CLIENT_SECRET,
    ANTIGRAVITY_SCOPES,
    ANTIGRAVITY_USER_AGENT,
    CALLBACK_HOST,
    CLIENT_ID,
    CLIENT_SECRET,
    SCOPES,
    GEMINICLI_USER_AGENT,
    TOKEN_URL,
)


async def get_callback_port():
    f"""{ts('id_712')}OAuth{ts('id_1744')}"""
    return int(await get_config_value("oauth_callback_port", "11451", "OAUTH_CALLBACK_PORT"))


def _prepare_credentials_data(credentials: Credentials, project_id: str, mode: str = "geminicli") -> Dict[str, Any]:
    f"""{ts('id_1745')}"""
    if mode == "antigravity":
        creds_data = {
            "client_id": ANTIGRAVITY_CLIENT_ID,
            "client_secret": ANTIGRAVITY_CLIENT_SECRET,
            "token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
            "scopes": ANTIGRAVITY_SCOPES,
            "token_uri": TOKEN_URL,
            "project_id": project_id,
        }
    else:
        creds_data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
            "scopes": SCOPES,
            "token_uri": TOKEN_URL,
            "project_id": project_id,
        }

    if credentials.expires_at:
        if credentials.expires_at.tzinfo is None:
            expiry_utc = credentials.expires_at.replace(tzinfo=timezone.utc)
        else:
            expiry_utc = credentials.expires_at
        creds_data["expiry"] = expiry_utc.isoformat()

    return creds_data


def _generate_random_project_id() -> str:
    f"""{ts('id_1747')}project_id{ts('id_1748f')}antigravity{ts('id_1746')}"""
    random_id = uuid.uuid4().hex[:8]
    return f"projects/random-{random_id}/locations/global"


def _cleanup_auth_flow_server(state: str):
    f"""{ts('id_1749')}"""
    if state in auth_flows:
        flow_data_to_clean = auth_flows[state]
        try:
            if flow_data_to_clean.get("server"):
                server = flow_data_to_clean["server"]
                port = flow_data_to_clean.get("callback_port")
                async_shutdown_server(server, port)
        except Exception as e:
            log.debug(f"{ts('id_1750')}: {e}")
        del auth_flows[state]


class _OAuthLibPatcher:
    f"""oauthlib{ts('id_1751')}"""
    def __init__(self):
        import oauthlib.oauth2.rfc6749.parameters
        self.module = oauthlib.oauth2.rfc6749.parameters
        self.original_validate = None

    def __enter__(self):
        self.original_validate = self.module.validate_token_parameters

        def patched_validate(params):
            try:
                return self.original_validate(params)
            except Warning:
                pass

        self.module.validate_token_parameters = patched_validate
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.original_validate:
            self.module.validate_token_parameters = self.original_validate


# {ts(f"id_703")} - {ts('id_1752')}
auth_flows = {}  # {ts(f"id_1753")}
MAX_AUTH_FLOWS = 20  # {ts(f"id_1754")}


def cleanup_auth_flows_for_memory():
    f"""{ts('id_1755')}"""
    global auth_flows
    cleanup_expired_flows()
    # {ts(f"id_1756")}
    if len(auth_flows) > 10:
        # {ts(f"id_175710")}{ts('id_723')}
        sorted_flows = sorted(
            auth_flows.items(), key=lambda x: x[1].get("created_at", 0), reverse=True
        )
        new_auth_flows = dict(sorted_flows[:10])

        # {ts(f"id_1758")}
        for state, flow_data in auth_flows.items():
            if state not in new_auth_flows:
                try:
                    if flow_data.get("server"):
                        server = flow_data["server"]
                        port = flow_data.get("callback_port")
                        async_shutdown_server(server, port)
                except Exception:
                    pass
                flow_data.clear()

        auth_flows = new_auth_flows
        log.info(f"{ts('id_1759')} {len(auth_flows)} {ts('id_1760')}")

    return len(auth_flows)


async def find_available_port(start_port: int = None) -> int:
    f"""{ts('id_1761')}"""
    if start_port is None:
        start_port = await get_callback_port()

    # {ts(f"id_1762")}
    for port in range(start_port, start_port + 100):  # {ts(f"id_1764100")}{ts('id_1763')}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                log.info(f"{ts('id_1765')}: {port}")
                return port
        except OSError:
            continue

    # {ts(f"id_1766")}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", 0))
            port = s.getsockname()[1]
            log.info(f"{ts('id_1767')}: {port}")
            return port
    except OSError as e:
        log.error(f"{ts('id_1768')}: {e}")
        raise RuntimeError(f"{ts('id_1768')}")


def create_callback_server(port: int) -> HTTPServer:
    f"""{ts('id_1769')}"""
    try:
        # {ts(f"id_17700")}.0.0.0
        server = HTTPServer(("0.0.0.0", port), AuthCallbackHandler)

        # {ts(f"id_32")}socket{ts('id_1771')}
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # {ts(f"id_1772")}
        server.timeout = 1.0

        log.info(f"{ts('id_1029')}OAuth{ts('id_1773')}: {port}")
        return server
    except OSError as e:
        log.error(f"{ts('id_1775')}{port}{ts('id_1774')}: {e}")
        raise


class AuthCallbackHandler(BaseHTTPRequestHandler):
    f"""OAuth{ts('id_1776')}"""

    def do_GET(self):
        query_components = parse_qs(urlparse(self.path).query)
        code = query_components.get("code", [None])[0]
        state = query_components.get("state", [None])[0]

        log.info(f"{ts('id_1567')}OAuth{ts('id_589f')}: code={ts('id_1777') if code else ts('id_1778')}, state={state}")

        if code and state and state in auth_flows:
            # {ts(f"id_1779")}
            auth_flows[state]["code"] = code
            auth_flows[state]["completed"] = True

            log.info(f"OAuth{ts('id_1780')}: state={state}")

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            # {ts(f"id_1781")}
            self.wfile.write(
                b"<h1>OAuth authentication successful!</h1><p>You can close this window. Please return to the original page and click 'Get Credentials' button.</p>"
            )
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authentication failed.</h1><p>Please try again.</p>")

    def log_message(self, format, *args):
        # {ts(f"id_1782")}
        pass


async def create_auth_url(
    project_id: Optional[str] = None, user_session: str = None, mode: str = "geminicli"
) -> Dict[str, Any]:
    f"""{ts('id_1784')}URL{ts('id_1783')}"""
    try:
        # {ts(f"id_1785")}
        callback_port = await find_available_port()
        callback_url = f"http://{CALLBACK_HOST}:{callback_port}"

        # {ts(f"id_1786")}
        try:
            callback_server = create_callback_server(callback_port)
            # {ts(f"id_1787")}
            server_thread = threading.Thread(
                target=callback_server.serve_forever,
                daemon=True,
                name=f"OAuth-Server-{callback_port}",
            )
            server_thread.start()
            log.info(f"OAuth{ts('id_1788')}: {callback_port}")
        except Exception as e:
            log.error(f"{ts('id_1789')}: {e}")
            return {
                "success": False,
                f"error": f"{ts('id_1791')}OAuth{ts('id_1790')}{callback_port}: {str(e)}",
            }

        # {ts(f"id_1029")}OAuth{ts('id_1792')}
        # {ts(f"id_1793")}
        if mode == "antigravity":
            client_id = ANTIGRAVITY_CLIENT_ID
            client_secret = ANTIGRAVITY_CLIENT_SECRET
            scopes = ANTIGRAVITY_SCOPES
        else:
            client_id = CLIENT_ID
            client_secret = CLIENT_SECRET
            scopes = SCOPES

        flow = Flow(
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            redirect_uri=callback_url,
        )

        # {ts(f"id_1794")}
        if user_session:
            state = f"{user_session}_{str(uuid.uuid4())}"
        else:
            state = str(uuid.uuid4())

        # {ts(f"id_1795")}URL
        auth_url = flow.get_auth_url(state=state)

        # {ts(f"id_1797")} - {ts('id_1796')}
        if len(auth_flows) >= MAX_AUTH_FLOWS:
            # {ts(f"id_1798")}
            oldest_state = min(auth_flows.keys(), key=lambda k: auth_flows[k].get("created_at", 0))
            try:
                # {ts(f"id_1799")}
                old_flow = auth_flows[oldest_state]
                if old_flow.get("server"):
                    server = old_flow["server"]
                    port = old_flow.get("callback_port")
                    async_shutdown_server(server, port)
            except Exception as e:
                log.warning(f"Failed to cleanup old auth flow {oldest_state}: {e}")

            del auth_flows[oldest_state]
            log.debug(f"Removed oldest auth flow: {oldest_state}")

        # {ts(f"id_1800")}
        auth_flows[state] = {
            "flow": flow,
            f"project_id": project_id,  # {ts('id_1802')}None{ts('id_1801')}
            "user_session": user_session,
            f"callback_port": callback_port,  # {ts('id_1803')}
            f"callback_url": callback_url,  # {ts('id_1804')}URL
            f"server": callback_server,  # {ts('id_1805')}
            f"server_thread": server_thread,  # {ts('id_1806')}
            "code": None,
            "completed": False,
            "created_at": time.time(),
            f"auto_project_detection": project_id is None,  # {ts('id_1807')}ID
            f"mode": mode,  # {ts('id_1808')}
        }

        # {ts(f"id_180930")}{ts('id_1810')}
        cleanup_expired_flows()

        log.info(f"OAuth{ts('id_1811')}: state={state}, project_id={project_id}")
        log.info(f"{ts('id_1812')}URL{ts('id_1814f')}OAuth{ts('id_1813')} {callback_url}")
        log.info(f"{ts('id_1815')}: {callback_port}")

        return {
            "auth_url": auth_url,
            "state": state,
            "callback_port": callback_port,
            "success": True,
            "auto_project_detection": project_id is None,
            "detected_project_id": project_id,
        }

    except Exception as e:
        log.error(f"{ts('id_1784')}URL{ts('id_979')}: {e}")
        return {"success": False, "error": str(e)}


def wait_for_callback_sync(state: str, timeout: int = 300) -> Optional[str]:
    f"""{ts('id_1817')}OAuth{ts('id_1816')}"""
    if state not in auth_flows:
        log.error(f"{ts('id_1818')} {state} {ts('id_1819')}")
        return None

    flow_data = auth_flows[state]
    callback_port = flow_data["callback_port"]

    # {ts(f"id_1821")}create_auth_url{ts('id_1820')}
    log.info(f"{ts('id_870')}OAuth{ts('id_1822')}: {callback_port}")

    # {ts(f"id_1823")}
    start_time = time.time()
    while time.time() - start_time < timeout:
        if flow_data.get("code"):
            log.info(f"OAuth{ts('id_1824')}")
            return flow_data["code"]
        time.sleep(0.5)  # {ts(f"id_18260")}.5{ts('id_1825')}

        # {ts(f"id_1827")}flow_data{ts('id_1828')}
        if state in auth_flows:
            flow_data = auth_flows[state]

    log.warning(f"{ts('id_870')}OAuth{ts('id_1829f')} ({timeout}{ts('id_72')})")
    return None


async def complete_auth_flow(
    project_id: Optional[str] = None, user_session: str = None
) -> Dict[str, Any]:
    f"""{ts('id_1830')}ID"""
    try:
        # {ts(f"id_1831")}
        state = None
        flow_data = None

        # {ts(f"id_1833")}project_id{ts('id_1832')}
        if project_id:
            for s, data in auth_flows.items():
                if data["project_id"] == project_id:
                    # {ts(f"id_1834")}
                    if user_session and data.get("user_session") == user_session:
                        state = s
                        flow_data = data
                        break
                    # {ts(f"id_1835")}ID{ts('id_61')}
                    elif not state:
                        state = s
                        flow_data = data

        # {ts(f"id_1837")}ID{ts('id_1836')}ID{ts('id_1838')}
        if not state:
            for s, data in auth_flows.items():
                if data.get("auto_project_detection", False):
                    # {ts(f"id_1834")}
                    if user_session and data.get("user_session") == user_session:
                        state = s
                        flow_data = data
                        break
                    # {ts(f"id_1839")}
                    elif not state:
                        state = s
                        flow_data = data

        if not state or not flow_data:
            return {f"success": False, "error": "{ts('id_1840')}"}

        if not project_id:
            project_id = flow_data.get("project_id")
            if not project_id:
                return {
                    "success": False,
                    f"error": "{ts('id_1842')}ID{ts('id_1841')}ID",
                    "requires_manual_project_id": True,
                }

        flow = flow_data["flow"]

        # {ts(f"id_1843")}
        if not flow_data.get("code"):
            log.info(f"{ts('id_1844')}OAuth{ts('id_907')} (state: {state})")
            auth_code = wait_for_callback_sync(state)

            if not auth_code:
                return {
                    "success": False,
                    f"error": "{ts('id_1845')}OAuth{ts('id_251')}",
                }

            # {ts(f"id_1846")}
            auth_flows[state]["code"] = auth_code
            auth_flows[state]["completed"] = True
        else:
            auth_code = flow_data["code"]

        # {ts(f"id_1847")}
        with _OAuthLibPatcher():
            try:
                credentials = await flow.exchange_code(auth_code)
                # credentials {ts(f"id_1848")} exchange_code {ts('id_1849')}

                # {ts(f"id_1850")}ID{ts('id_1851')}ID
                if flow_data.get("auto_project_detection", False) and not project_id:
                    log.info(f"{ts('id_1853')}API{ts('id_1852')}...")
                    log.info(f"{ts('id_1854')}token: {credentials.access_token[:20]}...")
                    log.info(f"Token{ts('id_1855')}: {credentials.expires_at}")
                    user_projects = await get_user_projects(credentials)

                    if user_projects:
                        # {ts(f"id_1856")}
                        if len(user_projects) == 1:
                            # Google API returns projectId in camelCase
                            project_id = user_projects[0].get("projectId")
                            if project_id:
                                flow_data["project_id"] = project_id
                                log.info(f"{ts('id_1857')}: {project_id}")
                        # {ts(f"id_1858")}
                        else:
                            project_id = await select_default_project(user_projects)
                            if project_id:
                                flow_data["project_id"] = project_id
                                log.info(f"{ts('id_1859')}: {project_id}")
                            else:
                                # {ts(f"id_1860")}
                                return {
                                    "success": False,
                                    f"error": "{ts('id_1861')}",
                                    "requires_project_selection": True,
                                    "available_projects": [
                                        {
                                            # Google API returns projectId in camelCase
                                            "project_id": p.get("projectId"),
                                            "name": p.get("displayName") or p.get("projectId"),
                                            "projectNumber": p.get("projectNumber"),
                                        }
                                        for p in user_projects
                                    ],
                                }
                    else:
                        # {ts(f"id_1862")}
                        return {
                            "success": False,
                            f"error": "{ts('id_1863')}ID",
                            "requires_manual_project_id": True,
                        }

                # {ts(f"id_1864")}ID{ts('id_1865')}
                if not project_id:
                    return {
                        "success": False,
                        f"error": "{ts('id_1842')}ID{ts('id_1841')}ID",
                        "requires_manual_project_id": True,
                    }

                # {ts(f"id_1866")}
                saved_filename = await save_credentials(credentials, project_id)

                # {ts(f"id_1867")}
                creds_data = _prepare_credentials_data(credentials, project_id, mode="geminicli")

                # {ts(f"id_1868")}
                _cleanup_auth_flow_server(state)

                log.info(f"OAuth{ts('id_1869')}")
                return {
                    "success": True,
                    "credentials": creds_data,
                    "file_path": saved_filename,
                    "auto_detected_project": flow_data.get("auto_project_detection", False),
                }

            except Exception as e:
                log.error(f"{ts('id_916')}: {e}")
                return {f"success": False, "error": f"{ts('id_916')}: {str(e)}"}

    except Exception as e:
        log.error(f"{ts('id_1870')}: {e}")
        return {"success": False, "error": str(e)}


async def asyncio_complete_auth_flow(
    project_id: Optional[str] = None, user_session: str = None, mode: str = "geminicli"
) -> Dict[str, Any]:
    f"""{ts('id_1871')}ID"""
    try:
        log.info(
            f"asyncio_complete_auth_flow{ts('id_1872')}: project_id={project_id}, user_session={user_session}"
        )

        # {ts(f"id_1831")}
        state = None
        flow_data = None

        log.debug(f"{ts('id_1873')}auth_flows: {list(auth_flows.keys())}")

        # {ts(f"id_1833")}project_id{ts('id_1832')}
        if project_id:
            log.info(f"{ts('id_1874')}ID: {project_id}")
            for s, data in auth_flows.items():
                if data["project_id"] == project_id:
                    # {ts(f"id_1834")}
                    if user_session and data.get("user_session") == user_session:
                        state = s
                        flow_data = data
                        log.info(f"{ts('id_1875')}: {s}")
                        break
                    # {ts(f"id_1835")}ID{ts('id_61')}
                    elif not state:
                        state = s
                        flow_data = data
                        log.info(f"{ts('id_1876')}ID: {s}")

        # {ts(f"id_1837")}ID{ts('id_1836')}ID{ts('id_1838')}
        if not state:
            log.info(f"{ts('id_1877')}")
            # {ts(f"id_1878")}
            completed_flows = []
            for s, data in auth_flows.items():
                if data.get("auto_project_detection", False):
                    if user_session and data.get("user_session") == user_session:
                        if data.get(f"code"):  # {ts('id_1879')}
                            completed_flows.append((s, data, data.get("created_at", 0)))

            # {ts(f"id_1880")}
            if completed_flows:
                completed_flows.sort(key=lambda x: x[2], reverse=True)  # {ts(f"id_1881")}
                state, flow_data, _ = completed_flows[0]
                log.info(f"{ts('id_1882')}: {state}")
            else:
                # {ts(f"id_1883")}
                pending_flows = []
                for s, data in auth_flows.items():
                    if data.get("auto_project_detection", False):
                        if user_session and data.get("user_session") == user_session:
                            pending_flows.append((s, data, data.get("created_at", 0)))
                        elif not user_session:
                            pending_flows.append((s, data, data.get("created_at", 0)))

                if pending_flows:
                    pending_flows.sort(key=lambda x: x[2], reverse=True)  # {ts(f"id_1881")}
                    state, flow_data, _ = pending_flows[0]
                    log.info(f"{ts('id_1884')}: {state}")

        if not state or not flow_data:
            log.error(f"{ts('id_1885')}: state={state}, flow_data{ts('id_1886')}={bool(flow_data)}")
            log.debug(f"{ts('id_1873')}flow_data: {list(auth_flows.keys())}")
            return {f"success": False, "error": "{ts('id_1840')}"}

        log.info(f"{ts('id_1887')}: state={state}")
        log.info(
            f"flow_data{ts('id_1639')}: project_id={flow_data.get('project_id')}, auto_project_detection={flow_data.get('auto_project_detection')}"
        )
        log.info(f"{ts('id_1888')}project_id{ts('id_226')}: {project_id}")

        # {ts(f"id_1850")}ID{ts('id_1851')}ID
        log.info(
            f"{ts('id_1890')}auto_project_detection{ts('id_1889')}: auto_project_detection={flow_data.get('auto_project_detection', False)}, not project_id={not project_id}"
        )
        if flow_data.get("auto_project_detection", False) and not project_id:
            log.info(f"{ts('id_1891')}ID{ts('id_1892')}")
        elif not project_id:
            log.info(f"{ts('id_1894')}project_id{ts('id_1893')}")
            project_id = flow_data.get("project_id")
            if not project_id:
                log.error(f"{ts('id_1842')}ID{ts('id_1865')}")
                return {
                    "success": False,
                    f"error": "{ts('id_1842')}ID{ts('id_1841')}ID",
                    "requires_manual_project_id": True,
                }
        else:
            log.info(f"{ts('id_1895')}ID: {project_id}")

        # {ts(f"id_1896")}
        log.info(f"{ts('id_1897')}OAuth{ts('id_1898')}...")
        log.info(f"{ts('id_870')}state={state}{ts('id_1899')}: {flow_data.get('callback_port')}")
        log.info(f"{ts('id_392')}flow_data{ts('id_838f')}: completed={flow_data.get('completed')}, code{ts('id_1886')}={bool(flow_data.get('code'))}")
        max_wait_time = 60  # {ts(f"id_190060")}{ts('id_72')}
        wait_interval = 1  # {ts(f"id_1901")}
        waited = 0

        while waited < max_wait_time:
            if flow_data.get("code"):
                log.info(f"{ts('id_1693')}OAuth{ts('id_1902f')} ({ts('id_1903')}: {waited}{ts('id_72')})")
                break

            # {ts(f"id_18265")}{ts('id_1904')}
            if waited % 5 == 0 and waited > 0:
                log.info(f"{ts('id_1905')}OAuth{ts('id_907f')}... ({waited}/{max_wait_time}{ts('id_72')})")
                log.debug(f"{ts('id_392')}state: {state}, flow_data keys: {list(flow_data.keys())}")

            # {ts(f"id_1906")}
            await asyncio.sleep(wait_interval)
            waited += wait_interval

            # {ts(f"id_1827")}flow_data{ts('id_1907')}
            if state in auth_flows:
                flow_data = auth_flows[state]

        if not flow_data.get("code"):
            log.error(f"{ts('id_870')}OAuth{ts('id_1908f')}{waited}{ts('id_72')}")
            return {
                "success": False,
                f"error": "{ts('id_870')}OAuth{ts('id_1909')}",
            }

        flow = flow_data["flow"]
        auth_code = flow_data["code"]

        log.info(f"{ts('id_1910')}: code={'***' + auth_code[-4:] if auth_code else 'None'}")

        # {ts(f"id_1847")}
        with _OAuthLibPatcher():
            try:
                log.info(f"{ts('id_1095')}flow.exchange_code...")
                credentials = await flow.exchange_code(auth_code)
                log.info(
                    f"{ts('id_1911')}token{ts('id_365')}: {credentials.access_token[:20] if credentials.access_token else 'None'}..."
                )

                log.info(
                    f"{ts('id_1912')}: auto_project_detection={flow_data.get('auto_project_detection')}, project_id={project_id}"
                )

                # {ts(f"id_1913")}
                cred_mode = flow_data.get("mode", "geminicli") if flow_data.get("mode") else mode
                if cred_mode == "antigravity":
                    log.info(f"Antigravity{ts('id_1914')}API{ts('id_712')}project_id...")
                    # {ts(f"id_463")}API{ts('id_712')}project_id
                    antigravity_url = await get_antigravity_api_url()
                    project_id = await fetch_project_id(
                        credentials.access_token,
                        ANTIGRAVITY_USER_AGENT,
                        antigravity_url
                    )
                    if project_id:
                        log.info(f"{ts('id_1915')}API{ts('id_712')}project_id: {project_id}")
                    else:
                        log.warning(f"{ts('id_1917')}API{ts('id_712f')}project_id{ts('id_1916')}")
                        project_id = _generate_random_project_id()
                        log.info(f"{ts('id_1918')}project_id: {project_id}")

                    # {ts(f"id_1919")}antigravity{ts('id_100')}
                    saved_filename = await save_credentials(credentials, project_id, mode="antigravity")

                    # {ts(f"id_1867")}
                    creds_data = _prepare_credentials_data(credentials, project_id, mode="antigravity")

                    # {ts(f"id_1868")}
                    _cleanup_auth_flow_server(state)

                    log.info(f"Antigravity OAuth{ts('id_1869')}")
                    return {
                        "success": True,
                        "credentials": creds_data,
                        "file_path": saved_filename,
                        "auto_detected_project": False,
                        "mode": "antigravity",
                    }

                # {ts(f"id_1850")}ID{ts('id_1851')}ID{ts('id_1920')}
                if flow_data.get("auto_project_detection", False) and not project_id:
                    log.info(f"{ts('id_1921')}API{ts('id_712')}project_id...")
                    # {ts(f"id_463")}API{ts('id_712')}project_id{ts('id_1922')}User-Agent{ts('id_292')}
                    code_assist_url = await get_code_assist_endpoint()
                    project_id = await fetch_project_id(
                        credentials.access_token,
                        GEMINICLI_USER_AGENT,
                        code_assist_url
                    )
                    if project_id:
                        flow_data["project_id"] = project_id
                        log.info(f"{ts('id_1915')}API{ts('id_712')}project_id: {project_id}")
                        # {ts(f"id_420")}API{ts('id_1151')}
                        log.info(f"{ts('id_1923')}API{ts('id_1151')}...")
                        await enable_required_apis(credentials, project_id)
                    else:
                        log.warning(f"{ts('id_1917')}API{ts('id_712f')}project_id{ts('id_1924')}")
                        # {ts(f"id_1925")}
                        user_projects = await get_user_projects(credentials)

                        if user_projects:
                            # {ts(f"id_1856")}
                            if len(user_projects) == 1:
                                # Google API returns projectId in camelCase
                                project_id = user_projects[0].get("projectId")
                                if project_id:
                                    flow_data["project_id"] = project_id
                                    log.info(f"{ts('id_1857')}: {project_id}")
                                    # {ts(f"id_420")}API{ts('id_1151')}
                                    log.info(f"{ts('id_1923')}API{ts('id_1151')}...")
                                    await enable_required_apis(credentials, project_id)
                            # {ts(f"id_1858")}
                            else:
                                project_id = await select_default_project(user_projects)
                                if project_id:
                                    flow_data["project_id"] = project_id
                                    log.info(f"{ts('id_1859')}: {project_id}")
                                    # {ts(f"id_420")}API{ts('id_1151')}
                                    log.info(f"{ts('id_1923')}API{ts('id_1151')}...")
                                    await enable_required_apis(credentials, project_id)
                                else:
                                    # {ts(f"id_1860")}
                                    return {
                                        "success": False,
                                        f"error": "{ts('id_1861')}",
                                        "requires_project_selection": True,
                                        "available_projects": [
                                            {
                                                # Google API returns projectId in camelCase
                                                "project_id": p.get("projectId"),
                                                "name": p.get("displayName") or p.get("projectId"),
                                                "projectNumber": p.get("projectNumber"),
                                            }
                                            for p in user_projects
                                        ],
                                    }
                        else:
                            # {ts(f"id_1862")}
                            return {
                                "success": False,
                                f"error": "{ts('id_1863')}ID",
                                "requires_manual_project_id": True,
                            }
                elif project_id:
                    # {ts(f"id_1927")}ID{ts('id_1926')}API{ts('id_1151')}
                    log.info(f"{ts('id_1928')}ID{ts('id_420f')}API{ts('id_1151')}...")
                    await enable_required_apis(credentials, project_id)

                # {ts(f"id_1864")}ID{ts('id_1865')}
                if not project_id:
                    return {
                        "success": False,
                        f"error": "{ts('id_1842')}ID{ts('id_1841')}ID",
                        "requires_manual_project_id": True,
                    }

                # {ts(f"id_1866")}
                saved_filename = await save_credentials(credentials, project_id)

                # {ts(f"id_1867")}
                creds_data = _prepare_credentials_data(credentials, project_id, mode="geminicli")

                # {ts(f"id_1868")}
                _cleanup_auth_flow_server(state)

                log.info(f"OAuth{ts('id_1869')}")
                return {
                    "success": True,
                    "credentials": creds_data,
                    "file_path": saved_filename,
                    "auto_detected_project": flow_data.get("auto_project_detection", False),
                }

            except Exception as e:
                log.error(f"{ts('id_916')}: {e}")
                return {f"success": False, "error": f"{ts('id_916')}: {str(e)}"}

    except Exception as e:
        log.error(f"{ts('id_1929')}: {e}")
        return {"success": False, "error": str(e)}


async def complete_auth_flow_from_callback_url(
    callback_url: str, project_id: Optional[str] = None, mode: str = "geminicli"
) -> Dict[str, Any]:
    f"""{ts('id_592')}URL{ts('id_1930')}"""
    try:
        log.info(f"{ts('id_1931')}URL{ts('id_1932')}: {callback_url}")

        # {ts(f"id_1933")}URL
        parsed_url = urlparse(callback_url)
        query_params = parse_qs(parsed_url.query)

        # {ts(f"id_1934")}
        if "state" not in query_params or "code" not in query_params:
            return {f"success": False, "error": "{ts('id_589')}URL{ts('id_1935f')} (state {ts('id_413')} code)"}

        state = query_params["state"][0]
        code = query_params["code"][0]

        log.info(f"{ts('id_1731')}URL{ts('id_1936')}: state={state}, code=xxx...")

        # {ts(f"id_1937")}
        if state not in auth_flows:
            return {
                "success": False,
                f"error": f"{ts('id_1938')} (state: {state})",
            }

        flow_data = auth_flows[state]
        flow = flow_data["flow"]

        # {ts(f"id_1940")}URL{ts('id_1941')}flow{ts('id_1939')}redirect_uri{ts('id_292')}
        redirect_uri = flow.redirect_uri
        log.info(f"{ts('id_463')}redirect_uri: {redirect_uri}")

        try:
            # {ts(f"id_463")}authorization code{ts('id_712')}token
            credentials = await flow.exchange_code(code)
            log.info(f"{ts('id_1942')}")

            # {ts(f"id_1913")}
            cred_mode = flow_data.get("mode", "geminicli") if flow_data.get("mode") else mode
            if cred_mode == "antigravity":
                log.info(f"Antigravity{ts('id_1943')}URL{ts('id_1944f')}API{ts('id_712')}project_id...")
                # {ts(f"id_463")}API{ts('id_712')}project_id
                antigravity_url = await get_antigravity_api_url()
                project_id = await fetch_project_id(
                    credentials.access_token,
                    ANTIGRAVITY_USER_AGENT,
                    antigravity_url
                )
                if project_id:
                    log.info(f"{ts('id_1915')}API{ts('id_712')}project_id: {project_id}")
                else:
                    log.warning(f"{ts('id_1917')}API{ts('id_712f')}project_id{ts('id_1916')}")
                    project_id = _generate_random_project_id()
                    log.info(f"{ts('id_1918')}project_id: {project_id}")

                # {ts(f"id_1919")}antigravity{ts('id_100')}
                saved_filename = await save_credentials(credentials, project_id, mode="antigravity")

                # {ts(f"id_1867")}
                creds_data = _prepare_credentials_data(credentials, project_id, mode="antigravity")

                # {ts(f"id_1868")}
                _cleanup_auth_flow_server(state)

                log.info(f"{ts('id_592')}URL{ts('id_405f')}Antigravity OAuth{ts('id_1869')}")
                return {
                    "success": True,
                    "credentials": creds_data,
                    "file_path": saved_filename,
                    "auto_detected_project": False,
                    "mode": "antigravity",
                }

            # {ts(f"id_1945")}ID{ts('id_1946')}
            detected_project_id = None
            auto_detected = False

            if not project_id:
                # {ts(f"id_1948")}fetch_project_id{ts('id_1947')}ID
                try:
                    log.info(f"{ts('id_1921')}API{ts('id_712')}project_id...")
                    code_assist_url = await get_code_assist_endpoint()
                    detected_project_id = await fetch_project_id(
                        credentials.access_token,
                        GEMINICLI_USER_AGENT,
                        code_assist_url
                    )
                    if detected_project_id:
                        auto_detected = True
                        log.info(f"{ts('id_1915')}API{ts('id_712')}project_id: {detected_project_id}")
                    else:
                        log.warning(f"{ts('id_1917')}API{ts('id_712f')}project_id{ts('id_1924')}")
                        # {ts(f"id_1925")}
                        projects = await get_user_projects(credentials)
                        if projects:
                            if len(projects) == 1:
                                # {ts(f"id_1949")}
                                # Google API returns projectId in camelCase
                                detected_project_id = projects[0]["projectId"]
                                auto_detected = True
                                log.info(f"{ts('id_1950')}ID: {detected_project_id}")
                            else:
                                # {ts(f"id_1951")}
                                # Google API returns projectId in camelCase
                                detected_project_id = projects[0]["projectId"]
                                auto_detected = True
                                log.info(
                                    f"{ts('id_1693')}{len(projects)}{ts('id_1952')}: {detected_project_id}"
                                )
                                log.debug(f"{ts('id_1953')}: {[p['projectId'] for p in projects[1:]]}")
                        else:
                            # {ts(f"id_1954")}
                            return {
                                "success": False,
                                f"error": "{ts('id_1955')}ID",
                                "requires_manual_project_id": True,
                            }
                except Exception as e:
                    log.warning(f"{ts('id_1956')}ID{ts('id_979')}: {e}")
                    return {
                        "success": False,
                        f"error": f"{ts('id_1956')}ID{ts('id_979f')}: {str(e)}{ts('id_1957')}ID",
                        "requires_manual_project_id": True,
                    }
            else:
                detected_project_id = project_id

            # {ts(f"id_1958")}API{ts('id_1151')}
            if detected_project_id:
                try:
                    log.info(f"{ts('id_1959')} {detected_project_id} {ts('id_1958f')}API{ts('id_1151')}...")
                    await enable_required_apis(credentials, detected_project_id)
                except Exception as e:
                    log.warning(f"{ts('id_126')}API{ts('id_1960')}: {e}")

            # {ts(f"id_1866")}
            saved_filename = await save_credentials(credentials, detected_project_id)

            # {ts(f"id_1867")}
            creds_data = _prepare_credentials_data(credentials, detected_project_id, mode="geminicli")

            # {ts(f"id_1868")}
            _cleanup_auth_flow_server(state)

            log.info(f"{ts('id_592')}URL{ts('id_405f')}OAuth{ts('id_1869')}")
            return {
                "success": True,
                "credentials": creds_data,
                "file_path": saved_filename,
                "auto_detected_project": auto_detected,
            }

        except Exception as e:
            log.error(f"{ts('id_592')}URL{ts('id_916')}: {e}")
            return {f"success": False, "error": f"{ts('id_916')}: {str(e)}"}

    except Exception as e:
        log.error(f"{ts('id_592')}URL{ts('id_1870')}: {e}")
        return {"success": False, "error": str(e)}


async def save_credentials(creds: Credentials, project_id: str, mode: str = "geminicli") -> str:
    f"""{ts('id_1961')}"""
    # {ts(f"id_1962")}project_id{ts('id_1963')}
    timestamp = int(time.time())

    # antigravity{ts(f"id_1964")}
    if mode == "antigravity":
        filename = f"ag_{project_id}-{timestamp}.json"
    else:
        filename = f"{project_id}-{timestamp}.json"

    # {ts(f"id_1965")}
    creds_data = _prepare_credentials_data(creds, project_id, mode)

    # {ts(f"id_1966")}
    storage_adapter = await get_storage_adapter()
    success = await storage_adapter.store_credential(filename, creds_data, mode=mode)

    if success:
        # {ts(f"id_1967")}
        try:
            default_state = {
                "error_codes": [],
                "disabled": False,
                "last_success": time.time(),
                "user_email": None,
            }
            await storage_adapter.update_credential_state(filename, default_state, mode=mode)
            log.info(f"{ts('id_1968')}: {filename} (mode={mode})")
        except Exception as e:
            log.warning(f"{ts('id_1969')} {filename}: {e}")

        return filename
    else:
        raise Exception(f"{ts('id_1970')}: {filename}")


def async_shutdown_server(server, port):
    f"""{ts('id_1972')}OAuth{ts('id_1971')}"""

    def shutdown_server_async():
        try:
            # {ts(f"id_1973")}
            shutdown_completed = threading.Event()

            def do_shutdown():
                try:
                    server.shutdown()
                    server.server_close()
                    shutdown_completed.set()
                    log.info(f"{ts('id_1974')} {port} {ts('id_61f')}OAuth{ts('id_1975')}")
                except Exception as e:
                    shutdown_completed.set()
                    log.debug(f"{ts('id_1750')}: {e}")

            # {ts(f"id_1976")}
            shutdown_worker = threading.Thread(target=do_shutdown, daemon=True)
            shutdown_worker.start()

            # {ts(f"id_19785")}{ts('id_1977')}
            if shutdown_completed.wait(timeout=5):
                log.debug(f"{ts('id_1980')} {port} {ts('id_1979')}")
            else:
                log.warning(f"{ts('id_1980')} {port} {ts('id_1981')}")

        except Exception as e:
            log.debug(f"{ts('id_1982')}: {e}")

    # {ts(f"id_1983")}
    shutdown_thread = threading.Thread(target=shutdown_server_async, daemon=True)
    shutdown_thread.start()
    log.debug(f"{ts('id_1984')} {port} {ts('id_61f')}OAuth{ts('id_1975')}")


def cleanup_expired_flows():
    f"""{ts('id_1985')}"""
    current_time = time.time()
    EXPIRY_TIME = 600  # 10{ts(f"id_1986")}

    # {ts(f"id_1987")}
    states_to_remove = [
        state
        for state, flow_data in auth_flows.items()
        if current_time - flow_data["created_at"] > EXPIRY_TIME
    ]

    # {ts(f"id_1988")}
    cleaned_count = 0
    for state in states_to_remove:
        flow_data = auth_flows.get(state)
        if flow_data:
            # {ts(f"id_1989")}
            try:
                if flow_data.get("server"):
                    server = flow_data["server"]
                    port = flow_data.get("callback_port")
                    async_shutdown_server(server, port)
            except Exception as e:
                log.debug(f"{ts('id_1990')}: {e}")

            # {ts(f"id_1991")}
            flow_data.clear()
            del auth_flows[state]
            cleaned_count += 1

    if cleaned_count > 0:
        log.info(f"{ts('id_1993')} {cleaned_count} {ts('id_1992')}")

    # {ts(f"id_1994")}
    if len(auth_flows) > 20:  # {ts(f"id_1995")}
        import gc

        gc.collect()
        log.debug(f"{ts('id_1996')}: {len(auth_flows)}")


def get_auth_status(project_id: str) -> Dict[str, Any]:
    f"""{ts('id_1997')}"""
    for state, flow_data in auth_flows.items():
        if flow_data["project_id"] == project_id:
            return {
                "status": "completed" if flow_data["completed"] else "pending",
                "state": state,
                "created_at": flow_data["created_at"],
            }

    return {"status": "not_found"}


# {ts(f"id_1999")} - {ts('id_1998')}
auth_tokens = {}  # {ts(f"id_2000")}
TOKEN_EXPIRY = 3600  # 1{ts(f"id_2001")}


async def verify_password(password: str) -> bool:
    f"""{ts('id_2002')}"""
    from config import get_panel_password

    correct_password = await get_panel_password()
    return password == correct_password


def generate_auth_token() -> str:
    f"""{ts('id_2003')}"""
    # {ts(f"id_2004")}
    cleanup_expired_tokens()

    token = secrets.token_urlsafe(32)
    # {ts(f"id_2005")}
    auth_tokens[token] = time.time()
    return token


def verify_auth_token(token: str) -> bool:
    f"""{ts('id_2006')}"""
    if not token or token not in auth_tokens:
        return False

    created_at = auth_tokens[token]

    # {ts(f"id_2008")} ({ts('id_2007')})
    if time.time() - created_at > TOKEN_EXPIRY:
        del auth_tokens[token]
        return False

    return True


def cleanup_expired_tokens():
    f"""{ts('id_2009')}"""
    current_time = time.time()
    expired_tokens = [
        token
        for token, created_at in auth_tokens.items()
        if current_time - created_at > TOKEN_EXPIRY
    ]

    for token in expired_tokens:
        del auth_tokens[token]

    if expired_tokens:
        log.debug(f"{ts('id_1993')} {len(expired_tokens)} {ts('id_2010')}")


def invalidate_auth_token(token: str):
    f"""{ts('id_2011')}"""
    if token in auth_tokens:
        del auth_tokens[token]


# {ts(f"id_2012")} - {ts('id_2013')}
def validate_credential_content(content: str) -> Dict[str, Any]:
    f"""{ts('id_2014')}"""
    try:
        creds_data = json.loads(content)

        # {ts(f"id_2015")}
        required_fields = ["client_id", "client_secret", "refresh_token", "token_uri"]
        missing_fields = [field for field in required_fields if field not in creds_data]

        if missing_fields:
            return {f"valid": False, "error": f'{ts('id_2016')}: {", ".join(missing_fields)}'}

        # {ts(f"id_1890")}project_id
        if "project_id" not in creds_data:
            log.warning(f"{ts('id_2017')}project_id{ts('id_2018')}")

        return {"valid": True, "data": creds_data}

    except json.JSONDecodeError as e:
        return {f"valid": False, "error": f"JSON{ts('id_2019')}: {str(e)}"}
    except Exception as e:
        return {f"valid": False, "error": f"{ts('id_2020')}: {str(e)}"}


async def save_uploaded_credential(content: str, original_filename: str) -> Dict[str, Any]:
    f"""{ts('id_2021')}"""
    try:
        # {ts(f"id_2022")}
        validation = validate_credential_content(content)
        if not validation["valid"]:
            return {"success": False, "error": validation["error"]}

        creds_data = validation["data"]

        # {ts(f"id_2023")}
        project_id = creds_data.get("project_id", "unknown")
        timestamp = int(time.time())

        # {ts(f"id_2024")}
        import os

        base_name = os.path.splitext(original_filename)[0]
        filename = f"{base_name}-{timestamp}.json"

        # {ts(f"id_1966")}
        storage_adapter = await get_storage_adapter()
        success = await storage_adapter.store_credential(filename, creds_data)

        if success:
            log.info(f"{ts('id_2025')}: {filename}")
            return {"success": True, "file_path": filename, "project_id": project_id}
        else:
            return {f"success": False, "error": "{ts('id_2026')}"}

    except Exception as e:
        log.error(f"{ts('id_2027')}: {e}")
        return {"success": False, "error": str(e)}


async def batch_upload_credentials(files_data: List[Dict[str, str]]) -> Dict[str, Any]:
    f"""{ts('id_2028')}"""
    results = []
    success_count = 0

    for file_data in files_data:
        filename = file_data.get("filename", "unknown.json")
        content = file_data.get("content", "")

        result = await save_uploaded_credential(content, filename)
        result["filename"] = filename
        results.append(result)

        if result["success"]:
            success_count += 1

    return {"uploaded_count": success_count, "total_count": len(files_data), "results": results}

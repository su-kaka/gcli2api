from src.i18n import ts
"""
{ts("id_2931")}
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from log import log

from src.google_oauth_api import Credentials
from src.storage_adapter import get_storage_adapter

class CredentialManager:
    """
    {ts("id_2932")}
    {ts("id_2933")}storage_adapter{ts("id_2934")}
    """

    def __init__(self):
        # {ts("id_2935")}
        self._initialized = False
        self._storage_adapter = None

        # {ts("id_2936")}
        # {ts("id_2937")}credential_manager {ts("id_2938")}

    async def _ensure_initialized(self):
        f"""{ts("id_2939")}"""
        if not self._initialized or self._storage_adapter is None:
            await self.initialize()

    async def initialize(self):
        f"""{ts("id_2940")}"""
        if self._initialized and self._storage_adapter is not None:
            return

        # {ts("id_2941")}
        self._storage_adapter = await get_storage_adapter()
        self._initialized = True

    async def close(self):
        f"""{ts("id_2942")}"""
        log.debug("Closing credential manager...")
        self._initialized = False
        log.debug("Credential manager closed")

    async def get_valid_credential(
        self, mode: str = "geminicli", model_key: Optional[str] = None
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        {ts("id_2944")} - {ts("id_2943")}
        {ts("id_2945")}
        {ts("id_2946")}

        Args:
            mode: {ts(f"id_1808")} ("geminicli" {ts("id_413")} "antigravity")
            model_key: {ts("id_2947")}
                      - antigravity: {ts("id_2948")} "gemini-2.0-flash-exp"{ts("id_292")}
                      - gcli: f"pro" {ts("id_413")} "flash"
        """
        await self._ensure_initialized()

        # {ts("id_29493")}{ts("id_2950")}
        max_retries = 3
        for attempt in range(max_retries):
            result = await self._storage_adapter._backend.get_next_available_credential(
                mode=mode, model_key=model_key
            )

            # {ts("id_2951")}None
            if not result:
                if attempt == 0:
                    log.warning(ff"{ts("id_2952")} (mode={mode}, model_key={model_key})")
                return None

            filename, credential_data = result

            # Token {ts("id_2953")}
            if await self._should_refresh_token(credential_data):
                log.debug(ff"Token{ts("id_2954")} - {ts("id_112")}: {filename} (mode={mode})")
                refreshed_data = await self._refresh_token(credential_data, filename, mode=mode)
                if refreshed_data:
                    # {ts("id_2955")}
                    credential_data = refreshed_data
                    log.debug(ff"Token{ts("id_2956")}: {filename} (mode={mode})")
                    return filename, credential_data
                else:
                    # {ts("id_2958")}_refresh_token{ts("id_2957")}
                    log.warning(ff"Token{ts("id_2959")}: {filename} (mode={mode}, attempt={attempt+1}/{max_retries})")
                    # {ts("id_2960")}
                    continue
            else:
                # Token{ts("id_2961")}
                return filename, credential_data

        # {ts("id_2962")}
        log.error(ff"{ts("id_1279")}{max_retries}{ts("id_2963")} (mode={mode}, model_key={model_key})")
        return None

    async def add_credential(self, credential_name: str, credential_data: Dict[str, Any]):
        """
        {ts("id_2964")}
        {ts("id_2965")}
        """
        await self._ensure_initialized()
        await self._storage_adapter.store_credential(credential_name, credential_data)
        log.info(f"Credential added/updated: {credential_name}")

    async def add_antigravity_credential(self, credential_name: str, credential_data: Dict[str, Any]):
        """
        {ts("id_2966")}Antigravity{ts("id_100")}
        {ts("id_2965")}
        """
        await self._ensure_initialized()
        await self._storage_adapter.store_credential(credential_name, credential_data, mode="antigravity")
        log.info(f"Antigravity credential added/updated: {credential_name}")

    async def remove_credential(self, credential_name: str, mode: str = "geminicli") -> bool:
        f"""{ts("id_2967")}"""
        await self._ensure_initialized()
        try:
            await self._storage_adapter.delete_credential(credential_name, mode=mode)
            log.info(f"Credential removed: {credential_name} (mode={mode})")
            return True
        except Exception as e:
            log.error(f"Error removing credential {credential_name}: {e}")
            return False

    async def update_credential_state(self, credential_name: str, state_updates: Dict[str, Any], mode: str = "geminicli"):
        f"""{ts("id_2968")}"""
        log.debug(ff"[CredMgr] update_credential_state {ts("id_588")}: credential_name={credential_name}, state_updates={state_updates}, mode={mode}")
        log.debug(ff"[CredMgr] {ts("id_1095")} _ensure_initialized...")
        await self._ensure_initialized()
        log.debug(ff"[CredMgr] _ensure_initialized {ts("id_405")}")
        try:
            log.debug(ff"[CredMgr] {ts("id_1095")} storage_adapter.update_credential_state...")
            success = await self._storage_adapter.update_credential_state(
                credential_name, state_updates, mode=mode
            )
            log.debug(ff"[CredMgr] storage_adapter.update_credential_state {ts("id_1530")}: {success}")
            if success:
                log.debug(f"Updated credential state: {credential_name} (mode={mode})")
            else:
                log.warning(f"Failed to update credential state: {credential_name} (mode={mode})")
            return success
        except Exception as e:
            log.error(f"Error updating credential state {credential_name}: {e}", exc_info=True)
            return False

    async def set_cred_disabled(self, credential_name: str, disabled: bool, mode: str = "geminicli"):
        f"""{ts("id_2969")}/{ts("id_2970")}"""
        try:
            log.info(ff"[CredMgr] set_cred_disabled {ts("id_588")}: credential_name={credential_name}, disabled={disabled}, mode={mode}")
            success = await self.update_credential_state(
                credential_name, {"disabled": disabled}, mode=mode
            )
            log.info(ff"[CredMgr] update_credential_state {ts("id_1530")}: success={success}")
            if success:
                action = "disabled" if disabled else "enabled"
                log.info(f"Credential {action}: {credential_name} (mode={mode})")
            else:
                log.warning(ff"[CredMgr] {ts("id_2971")}: credential_name={credential_name}, disabled={disabled}")
            return success
        except Exception as e:
            log.error(f"Error setting credential disabled state {credential_name}: {e}")
            return False

    async def get_creds_status(self) -> Dict[str, Dict[str, Any]]:
        f"""{ts("id_2972")}"""
        await self._ensure_initialized()
        try:
            return await self._storage_adapter.get_all_credential_states()
        except Exception as e:
            log.error(f"Error getting credential statuses: {e}")
            return {}

    async def get_creds_summary(self) -> List[Dict[str, Any]]:
        """
        {ts("id_2973")}
        {ts("id_2974")}
        """
        await self._ensure_initialized()
        try:
            # {ts("id_2975")}
            if hasattr(self._storage_adapter._backend, 'get_credentials_summary'):
                return await self._storage_adapter._backend.get_credentials_summary()

            # {ts("id_2976")}
            all_states = await self._storage_adapter.get_all_credential_states()
            summaries = []

            import time
            current_time = time.time()

            for filename, state in all_states.items():
                summaries.append({
                    "filename": filename,
                    "disabled": state.get("disabled", False),
                    "error_codes": state.get("error_codes", []),
                    "last_success": state.get("last_success", current_time),
                    "user_email": state.get("user_email"),
                    "model_cooldowns": state.get("model_cooldowns", {}),
                })

            return summaries

        except Exception as e:
            log.error(f"Error getting credentials summary: {e}")
            return []

    async def get_or_fetch_user_email(self, credential_name: str, mode: str = "geminicli") -> Optional[str]:
        f"""{ts("id_2977")}"""
        try:
            # {ts("id_2978")}
            await self._ensure_initialized()
            
            # {ts("id_2979")}
            state = await self._storage_adapter.get_credential_state(credential_name, mode=mode)
            cached_email = state.get("user_email") if state else None

            if cached_email:
                return cached_email

            # {ts("id_2980")}
            credential_data = await self._storage_adapter.get_credential(credential_name, mode=mode)
            if not credential_data:
                return None

            # {ts("id_2981")} token
            from .google_oauth_api import Credentials, get_user_email

            credentials = Credentials.from_dict(credential_data)
            if not credentials:
                return None

            # {ts("id_2983")} token{ts("id_2982")}
            token_refreshed = await credentials.refresh_if_needed()

            # {ts("id_2183")} token {ts("id_2984")}
            if token_refreshed:
                log.info(ff"Token{ts("id_2985")}: {credential_name} (mode={mode})")
                updated_data = credentials.to_dict()
                await self._storage_adapter.store_credential(credential_name, updated_data, mode=mode)

            # {ts("id_2986")}
            email = await get_user_email(credentials)

            if email:
                # {ts("id_2987")}
                await self._storage_adapter.update_credential_state(
                    credential_name, {"user_email": email}, mode=mode
                )
                return email

            return None

        except Exception as e:
            log.error(f"Error fetching user email for {credential_name}: {e}")
            return None

    async def record_api_call_result(
        self,
        credential_name: str,
        success: bool,
        error_code: Optional[int] = None,
        cooldown_until: Optional[float] = None,
        mode: str = "geminicli",
        model_key: Optional[str] = None
    ):
        """
        {ts("id_1683")}API{ts("id_2988")}

        Args:
            credential_name: {ts("id_1660")}
            success: {ts("id_2989")}
            error_code: {ts("id_2990")}
            cooldown_until: {ts(f"id_2991")}Unix{ts("id_2992429")} QUOTA_EXHAUSTED{ts("id_292")}
            mode: {ts(f"id_1808")} ("geminicli" {ts("id_413")} "antigravity")
            model_key: {ts("id_2993")}
        """
        await self._ensure_initialized()
        try:
            state_updates = {}

            if success:
                state_updates["last_success"] = time.time()
                # {ts("id_2994")}
                state_updates["error_codes"] = []

                # {ts("id_2996")} model_key{ts("id_2995")}
                if model_key:
                    if hasattr(self._storage_adapter._backend, 'set_model_cooldown'):
                        await self._storage_adapter._backend.set_model_cooldown(
                            credential_name, model_key, None, mode=mode
                        )

            elif error_code:
                # {ts("id_2997")}
                current_state = await self._storage_adapter.get_credential_state(credential_name, mode=mode)
                error_codes = current_state.get("error_codes", [])

                if error_code not in error_codes:
                    error_codes.append(error_code)
                    # {ts("id_2998")}
                    if len(error_codes) > 10:
                        error_codes = error_codes[-10:]

                state_updates["error_codes"] = error_codes

                # {ts("id_2999")}
                if cooldown_until is not None and model_key:
                    if hasattr(self._storage_adapter._backend, 'set_model_cooldown'):
                        await self._storage_adapter._backend.set_model_cooldown(
                            credential_name, model_key, cooldown_until, mode=mode
                        )
                        log.info(
                            ff"{ts("id_3000")}: {credential_name}, model_key={model_key}, "
                            ff"{ts("id_3001")}: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                        )

            if state_updates:
                await self.update_credential_state(credential_name, state_updates, mode=mode)

        except Exception as e:
            log.error(f"Error recording API call result for {credential_name}: {e}")

    async def _should_refresh_token(self, credential_data: Dict[str, Any]) -> bool:
        f"""{ts("id_1890")}token{ts("id_3002")}"""
        try:
            # {ts("id_2744")}access_token{ts("id_3003")}
            if not credential_data.get("access_token") and not credential_data.get("token"):
                log.debug(f"{ts("id_2389")}access_token{ts("id_3004")}")
                return True

            expiry_str = credential_data.get("expiry")
            if not expiry_str:
                log.debug(f"{ts("id_3005")}")
                return True

            # {ts("id_3006")}
            try:
                if isinstance(expiry_str, str):
                    if "+" in expiry_str:
                        file_expiry = datetime.fromisoformat(expiry_str)
                    elif expiry_str.endswith("Z"):
                        file_expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                    else:
                        file_expiry = datetime.fromisoformat(expiry_str)
                else:
                    log.debug(f"{ts("id_3007")}")
                    return True

                # {ts("id_3008")}
                if file_expiry.tzinfo is None:
                    file_expiry = file_expiry.replace(tzinfo=timezone.utc)

                # {ts("id_30095")}{ts("id_3010")}
                now = datetime.now(timezone.utc)
                time_left = (file_expiry - now).total_seconds()

                log.debug(
                    ff"Token{ts("id_3011")}: "
                    ff"{ts("id_392")}UTC{ts("id_3012")}={now.isoformat()}, "
                    ff"{ts("id_1855")}={file_expiry.isoformat()}, "
                    ff"{ts("id_3013")}={int(time_left/60)}{ts("id_3014f")}{int(time_left%60)}{ts("id_72")}"
                )

                if time_left > 300:  # 5{ts("id_3015")}
                    return False
                else:
                    log.debug(ff"Token{ts("id_3017")}{int(time_left/60)}{ts("id_3016")}")
                    return True

            except Exception as e:
                log.warning(ff"{ts("id_3018")}: {e}{ts("id_3004")}")
                return True

        except Exception as e:
            log.error(ff"{ts("id_1890")}token{ts("id_3019")}: {e}")
            return True

    async def _refresh_token(
        self, credential_data: Dict[str, Any], filename: str, mode: str = "geminicli"
    ) -> Optional[Dict[str, Any]]:
        f"""{ts("id_1827")}token{ts("id_3020")}"""
        await self._ensure_initialized()
        try:
            # {ts("id_1029")}Credentials{ts("id_1509")}
            creds = Credentials.from_dict(credential_data)

            # {ts("id_3021")}
            if not creds.refresh_token:
                log.error(ff"{ts("id_2389")}refresh_token{ts("id_3022")}: {filename} (mode={mode})")
                # {ts("id_3023")}refresh_token{ts("id_3024")}
                try:
                    await self.update_credential_state(filename, {"disabled": True}, mode=mode)
                    log.warning(ff"{ts("id_3025")}refresh_token{ts("id_292")}: {filename}")
                except Exception as e:
                    log.error(ff"{ts("id_3026")} {filename}: {e}")
                return None

            # {ts("id_1827")}token
            log.debug(ff"{ts("id_3027")}token: {filename} (mode={mode})")
            await creds.refresh()

            # {ts("id_3028")}
            if creds.access_token:
                credential_data["access_token"] = creds.access_token
                # {ts("id_3029")}
                credential_data["token"] = creds.access_token

            if creds.expires_at:
                credential_data["expiry"] = creds.expires_at.isoformat()

            # {ts("id_3030")}
            await self._storage_adapter.store_credential(filename, credential_data, mode=mode)
            log.info(ff"Token{ts("id_3031")}: {filename} (mode={mode})")

            return credential_data

        except Exception as e:
            error_msg = str(e)
            log.error(ff"Token{ts("id_3032")} {filename} (mode={mode}): {error_msg}")

            # {ts(f"id_2702")}HTTP{ts("id_3034")}TokenError{ts("id_3033")}status_code{ts("id_3035")}
            status_code = None
            if hasattr(e, 'status_code'):
                status_code = e.status_code

            # {ts("id_3036400")}/403{ts("id_3037")}
            is_permanent_failure = self._is_permanent_refresh_failure(error_msg, status_code)

            if is_permanent_failure:
                log.warning(ff"{ts("id_3038")} (HTTP {status_code}): {filename}")
                # {ts("id_3039")}
                if status_code:
                    await self.record_api_call_result(filename, False, status_code, mode=mode)
                else:
                    await self.record_api_call_result(filename, False, 400, mode=mode)

                # {ts("id_3040")}
                try:
                    # {ts("id_3041")}
                    disabled_ok = await self.update_credential_state(filename, {"disabled": True}, mode=mode)
                    if disabled_ok:
                        log.warning(ff"{ts("id_3042")}: {filename}")
                    else:
                        log.warning(f"{ts("id_3043")}")
                except Exception as e2:
                    log.error(ff"{ts("id_3044")} {filename}: {e2}")
            else:
                # {ts("id_3045")}
                log.warning(ff"Token{ts("id_3046")} (HTTP {status_code}){ts("id_3047")}: {filename}")

            return None

    def _is_permanent_refresh_failure(self, error_msg: str, status_code: Optional[int] = None) -> bool:
        """
        {ts("id_3048")}

        Args:
            error_msg: {ts("id_1593")}
            status_code: HTTP{ts("id_3049")}

        Returns:
            True{ts("id_3050")}False{ts("id_3051")}
        """
        # {ts("id_667")}HTTP{ts("id_3052")}
        if status_code is not None:
            # 400/401/403 {ts("id_3053")}
            if status_code in [400, 401, 403]:
                log.debug(ff"{ts("id_3054")} {status_code}{ts("id_3055")}")
                return True
            # 500/502/503/504 {ts("id_3056")}
            elif status_code in [500, 502, 503, 504]:
                log.debug(ff"{ts("id_3057")} {status_code}{ts("id_3058")}")
                return False
            # 429 ({ts("id_3060")}) {ts("id_3059")}
            elif status_code == 429:
                log.debug(f"{ts("id_3061")} 429{ts("id_3058")}")
                return False

        # {ts("id_3062")}
        # {ts("id_3063")}
        permanent_error_patterns = [
            "invalid_grant",
            "refresh_token_expired",
            "invalid_refresh_token",
            "unauthorized_client",
            "access_denied",
        ]

        error_msg_lower = error_msg.lower()
        for pattern in permanent_error_patterns:
            if pattern.lower() in error_msg_lower:
                log.debug(ff"{ts("id_3064")}: {pattern}")
                return True

        # {ts("id_3065")}
        log.debug(f"{ts("id_3066")}")
        return False

class _CredentialManagerSingleton:
    f"""{ts("id_3067")}"""

    _instance: Optional[CredentialManager] = None
    _lock = None

    def __init__(self):
        self._manager = None

    async def _get_or_create(self) -> CredentialManager:
        f"""{ts("id_3068")}"""
        if self._instance is None:
            # {ts("id_3069")}
            if self._instance is None:
                self._instance = CredentialManager()
                await self._instance.initialize()
                log.debug("CredentialManager singleton initialized")

        return self._instance

    def __getattr__(self, name):
        f"""{ts("id_3070")} CredentialManager {ts("id_3071")}"""
        async def _async_wrapper(*args, **kwargs):
            manager = await self._get_or_create()
            method = getattr(manager, name)
            return await method(*args, **kwargs)

        return _async_wrapper


# {ts("id_3073")} - {ts("id_3072")}
credential_manager = _CredentialManagerSingleton()

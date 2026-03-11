import asyncio
from datetime import datetime, timedelta, timezone

from src import auth
from src.google_oauth_api import Credentials


class _DummyFlow:
    def __init__(self):
        self.calls = 0

    async def exchange_code(self, code: str):
        self.calls += 1
        return Credentials(
            access_token=f"token-{self.calls}",
            refresh_token="refresh",
            client_id="client",
            client_secret="secret",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )


def test_exchange_or_reuse_credentials_is_idempotent_for_repeated_calls():
    state = "state-test-idempotent"
    flow = _DummyFlow()
    auth.auth_flows[state] = {
        "flow": flow,
        "code": "auth-code",
        "exchange_in_progress": False,
        "code_redeemed": False,
        "exchanged_credentials": None,
    }

    try:
        first_credentials, first_error = asyncio.run(
            auth._exchange_or_reuse_credentials(state, flow, "auth-code")
        )
        second_credentials, second_error = asyncio.run(
            auth._exchange_or_reuse_credentials(state, flow, "auth-code")
        )

        assert first_error is None
        assert second_error is None
        assert first_credentials is not None
        assert second_credentials is first_credentials
        assert flow.calls == 1
        assert auth.auth_flows[state]["code_redeemed"] is True
        assert auth.auth_flows[state]["code"] is None
    finally:
        auth.auth_flows.pop(state, None)
        auth.auth_flow_locks.pop(state, None)


def test_exchange_or_reuse_credentials_blocks_when_exchange_in_progress():
    state = "state-test-in-progress"
    flow = _DummyFlow()
    auth.auth_flows[state] = {
        "flow": flow,
        "code": "auth-code",
        "exchange_in_progress": True,
        "code_redeemed": False,
        "exchanged_credentials": None,
    }

    try:
        credentials, error = asyncio.run(
            auth._exchange_or_reuse_credentials(state, flow, "auth-code")
        )
        assert credentials is None
        assert error is not None
        assert "正在处理中" in error
        assert flow.calls == 0
    finally:
        auth.auth_flows.pop(state, None)
        auth.auth_flow_locks.pop(state, None)


def test_asyncio_complete_auth_flow_uses_cached_credentials_without_wait(monkeypatch):
    state = "pwd-state-cached"
    user_session = "pwd"
    cached = Credentials(
        access_token="cached-token",
        refresh_token="refresh",
        client_id="client",
        client_secret="secret",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    class _NoopFlow:
        async def exchange_code(self, code: str):
            raise AssertionError(
                "exchange_code should not be called when cached exists"
            )

    async def fake_sleep(_seconds):
        raise AssertionError(
            "should not wait for OAuth code when cached credentials exist"
        )

    async def fake_enable_required_apis(credentials, project_id):
        return True

    async def fake_save_credentials(credentials, project_id, mode="geminicli"):
        return "cached.json"

    monkeypatch.setattr(auth.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(auth, "enable_required_apis", fake_enable_required_apis)
    monkeypatch.setattr(auth, "save_credentials", fake_save_credentials)

    auth.auth_flows[state] = {
        "flow": _NoopFlow(),
        "project_id": "proj-cached",
        "user_session": user_session,
        "callback_port": 11451,
        "callback_url": "http://localhost:11451",
        "server": None,
        "server_thread": None,
        "code": None,
        "completed": True,
        "exchange_in_progress": False,
        "code_redeemed": True,
        "exchanged_credentials": cached,
        "created_at": datetime.now(timezone.utc).timestamp(),
        "auto_project_detection": False,
        "mode": "geminicli",
    }

    try:
        result = asyncio.run(
            auth.asyncio_complete_auth_flow(
                project_id="proj-cached", user_session=user_session, mode="geminicli"
            )
        )
        assert result["success"] is True
        assert result["file_path"] == "cached.json"
        assert result["credentials"]["token"] == "cached-token"
    finally:
        auth.auth_flows.pop(state, None)
        auth.auth_flow_locks.pop(state, None)

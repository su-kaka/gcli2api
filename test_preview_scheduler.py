import asyncio
import time
from typing import Any, cast

import src.credential_manager as credential_manager_module
from src.credential_manager import CredentialManager
from src.storage.sqlite_manager import SQLiteManager


def test_sqlite_preview_selection_keeps_preview_constraints(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path))

    async def _run():
        manager = SQLiteManager()
        await manager.initialize()

        await manager.store_credential(
            "non_preview.json", {"token": "t1", "project_id": "p1"}, mode="geminicli"
        )
        await manager.store_credential(
            "preview.json", {"token": "t2", "project_id": "p2"}, mode="geminicli"
        )
        await manager.update_credential_state(
            "non_preview.json", {"preview": False}, mode="geminicli"
        )
        await manager.update_credential_state(
            "preview.json", {"preview": True}, mode="geminicli"
        )

        preview_pick = await manager.get_next_available_credential(
            mode="geminicli",
            model_name="gemini-3-pro-preview",
            scheduling_hints={"in_flight": {"preview.json": 5}},
        )
        assert preview_pick is not None
        assert preview_pick[0] == "preview.json"

        non_preview_pick = await manager.get_next_available_credential(
            mode="geminicli",
            model_name="gemini-2.5-pro",
            scheduling_hints={"in_flight": {"non_preview.json": 0}},
        )
        assert non_preview_pick is not None
        assert non_preview_pick[0] == "non_preview.json"

    asyncio.run(_run())


def test_sqlite_preview_health_scoring_uses_429_and_inflight(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDENTIALS_DIR", str(tmp_path))

    async def _run():
        manager = SQLiteManager()
        await manager.initialize()

        for name in ("p1.json", "p2.json", "p3.json"):
            await manager.store_credential(
                name, {"token": name, "project_id": name}, mode="geminicli"
            )
            await manager.update_credential_state(
                name, {"preview": True}, mode="geminicli"
            )

        await manager.update_credential_state(
            "p1.json", {"error_codes": [429]}, mode="geminicli"
        )

        await manager.set_model_cooldown(
            "p2.json", "gemini-3-pro-preview", time.time() + 60, mode="geminicli"
        )

        pick = await manager.get_next_available_credential(
            mode="geminicli",
            model_name="gemini-3-pro-preview",
            scheduling_hints={"in_flight": {"p3.json": 0, "p1.json": 0}},
        )
        assert pick is not None
        assert pick[0] == "p3.json"

    asyncio.run(_run())


def test_credential_manager_passes_inflight_hints_to_scheduler(monkeypatch):
    class _Backend:
        def __init__(self):
            self.hints = []

        async def get_next_available_credential(
            self, mode="geminicli", model_name=None, scheduling_hints=None
        ):
            self.hints.append(scheduling_hints or {})
            inflight = (scheduling_hints or {}).get("in_flight", {})
            if inflight.get("a.json", 0) > 0:
                return "b.json", {
                    "token": "tb",
                    "project_id": "pb",
                    "expiry": "2999-01-01T00:00:00+00:00",
                }
            return "a.json", {
                "token": "ta",
                "project_id": "pa",
                "expiry": "2999-01-01T00:00:00+00:00",
            }

    class _Adapter:
        def __init__(self):
            self._backend = _Backend()

    manager = CredentialManager()
    manager_any = cast(Any, manager)
    manager_any._initialized = True
    manager_any._storage_adapter = _Adapter()

    async def _flag_on():
        return True

    monkeypatch.setattr(
        credential_manager_module,
        "get_ff_preview_credential_scheduler_v2",
        _flag_on,
    )

    async def _always_valid_token(_):
        return False

    monkeypatch.setattr(manager, "_should_refresh_token", _always_valid_token)

    async def _run():
        first = await manager.get_valid_credential(
            mode="geminicli", model_name="gemini-3-pro-preview"
        )
        second = await manager.get_valid_credential(
            mode="geminicli", model_name="gemini-3-pro-preview"
        )

        assert first is not None and second is not None
        assert first[0] == "a.json"
        assert second[0] == "b.json"

        hints = manager_any._storage_adapter._backend.hints
        assert hints[0].get("in_flight", {}) == {}
        assert hints[1].get("in_flight", {}).get("a.json") == 1

        await manager._release_inflight("geminicli", "a.json")
        await manager._release_inflight("geminicli", "b.json")

    asyncio.run(_run())

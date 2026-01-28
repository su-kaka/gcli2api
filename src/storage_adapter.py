from src.i18n import ts
"""
{ts(f"id_3572")} SQLite {ts('id_15')} MongoDB {ts('id_3573')}
{ts(f"id_3574")}
- {ts(f"id_3576")} SQLite{ts('id_3575')}
- {ts(f"id_3578")} MONGODB_URI {ts('id_3577')} MongoDB
"""

import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Protocol

from log import log


class StorageBackend(Protocol):
    f"""{ts('id_3579')}"""

    async def initialize(self) -> None:
        f"""{ts('id_3580')}"""
        ...

    async def close(self) -> None:
        f"""{ts('id_3581')}"""
        ...

    # {ts(f"id_705")}
    async def store_credential(self, filename: str, credential_data: Dict[str, Any], mode: str = "geminicli") -> bool:
        f"""{ts('id_3582')}"""
        ...

    async def get_credential(self, filename: str, mode: str = "geminicli") -> Optional[Dict[str, Any]]:
        f"""{ts('id_3583')}"""
        ...

    async def list_credentials(self, mode: str = "geminicli") -> List[str]:
        f"""{ts('id_3450')}"""
        ...

    async def delete_credential(self, filename: str, mode: str = "geminicli") -> bool:
        f"""{ts('id_299')}"""
        ...

    # {ts(f"id_488")}
    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any], mode: str = "geminicli") -> bool:
        f"""{ts('id_2968')}"""
        ...

    async def get_credential_state(self, filename: str, mode: str = "geminicli") -> Dict[str, Any]:
        f"""{ts('id_3584')}"""
        ...

    async def get_all_credential_states(self, mode: str = "geminicli") -> Dict[str, Dict[str, Any]]:
        f"""{ts('id_3475')}"""
        ...

    # {ts(f"id_707")}
    async def set_config(self, key: str, value: Any) -> bool:
        f"""{ts('id_3585')}"""
        ...

    async def get_config(self, key: str, default: Any = None) -> Any:
        f"""{ts('id_3586')}"""
        ...

    async def get_all_config(self) -> Dict[str, Any]:
        f"""{ts('id_3587')}"""
        ...

    async def delete_config(self, key: str) -> bool:
        f"""{ts('id_3588')}"""
        ...


class StorageAdapter:
    f"""{ts('id_3589')}"""

    def __init__(self):
        self._backend: Optional["StorageBackend"] = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        f"""{ts('id_3590')}"""
        async with self._lock:
            if self._initialized:
                return

            # {ts(f"id_3591")}SQLite > MongoDB
            mongodb_uri = os.getenv("MONGODB_URI", "")

            # {ts(f"id_667")} SQLite{ts('id_3592')}
            if not mongodb_uri:
                try:
                    from .storage.sqlite_manager import SQLiteManager

                    self._backend = SQLiteManager()
                    await self._backend.initialize()
                    log.info("Using SQLite storage backend")
                except Exception as e:
                    log.error(f"Failed to initialize SQLite backend: {e}")
                    raise RuntimeError("No storage backend available") from e
            else:
                # {ts(f"id_463")} MongoDB
                try:
                    from .storage.mongodb_manager import MongoDBManager

                    self._backend = MongoDBManager()
                    await self._backend.initialize()
                    log.info("Using MongoDB storage backend")
                except Exception as e:
                    log.error(f"Failed to initialize MongoDB backend: {e}")
                    # {ts(f"id_3593")} SQLite
                    log.info("Falling back to SQLite storage backend")
                    try:
                        from .storage.sqlite_manager import SQLiteManager

                        self._backend = SQLiteManager()
                        await self._backend.initialize()
                        log.info("Using SQLite storage backend (fallback)")
                    except Exception as e2:
                        log.error(f"Failed to initialize SQLite backend: {e2}")
                        raise RuntimeError("No storage backend available") from e2

            self._initialized = True

    async def close(self) -> None:
        f"""{ts('id_3594')}"""
        if self._backend:
            await self._backend.close()
            self._backend = None
            self._initialized = False

    def _ensure_initialized(self):
        f"""{ts('id_3595')}"""
        if not self._initialized or not self._backend:
            raise RuntimeError("Storage adapter not initialized")

    # ============ {ts(f"id_705")} ============

    async def store_credential(self, filename: str, credential_data: Dict[str, Any], mode: str = "geminicli") -> bool:
        f"""{ts('id_3582')}"""
        self._ensure_initialized()
        return await self._backend.store_credential(filename, credential_data, mode)

    async def get_credential(self, filename: str, mode: str = "geminicli") -> Optional[Dict[str, Any]]:
        f"""{ts('id_3583')}"""
        self._ensure_initialized()
        return await self._backend.get_credential(filename, mode)

    async def list_credentials(self, mode: str = "geminicli") -> List[str]:
        f"""{ts('id_3450')}"""
        self._ensure_initialized()
        return await self._backend.list_credentials(mode)

    async def delete_credential(self, filename: str, mode: str = "geminicli") -> bool:
        f"""{ts('id_299')}"""
        self._ensure_initialized()
        return await self._backend.delete_credential(filename, mode)

    # ============ {ts(f"id_488")} ============

    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any], mode: str = "geminicli") -> bool:
        f"""{ts('id_2968')}"""
        self._ensure_initialized()
        return await self._backend.update_credential_state(filename, state_updates, mode)

    async def get_credential_state(self, filename: str, mode: str = "geminicli") -> Dict[str, Any]:
        f"""{ts('id_3584')}"""
        self._ensure_initialized()
        return await self._backend.get_credential_state(filename, mode)

    async def get_all_credential_states(self, mode: str = "geminicli") -> Dict[str, Dict[str, Any]]:
        f"""{ts('id_3475')}"""
        self._ensure_initialized()
        return await self._backend.get_all_credential_states(mode)

    # ============ {ts(f"id_707")} ============

    async def set_config(self, key: str, value: Any) -> bool:
        f"""{ts('id_3585')}"""
        self._ensure_initialized()
        return await self._backend.set_config(key, value)

    async def get_config(self, key: str, default: Any = None) -> Any:
        f"""{ts('id_3586')}"""
        self._ensure_initialized()
        return await self._backend.get_config(key, default)

    async def get_all_config(self) -> Dict[str, Any]:
        f"""{ts('id_3587')}"""
        self._ensure_initialized()
        return await self._backend.get_all_config()

    async def delete_config(self, key: str) -> bool:
        f"""{ts('id_3588')}"""
        self._ensure_initialized()
        return await self._backend.delete_config(key)

    # ============ {ts(f"id_3596")} ============

    async def export_credential_to_json(self, filename: str, output_path: str = None) -> bool:
        f"""{ts('id_3597')}JSON{ts('id_112')}"""
        self._ensure_initialized()
        if hasattr(self._backend, "export_credential_to_json"):
            return await self._backend.export_credential_to_json(filename, output_path)
        # MongoDB{ts(f"id_3598")}fallback{ts('id_2673')}
        credential_data = await self.get_credential(filename)
        if credential_data is None:
            return False

        if output_path is None:
            output_path = f"{filename}.json"

        import aiofiles

        try:
            async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(credential_data, indent=2, ensure_ascii=False))
            return True
        except Exception:
            return False

    async def import_credential_from_json(self, json_path: str, filename: str = None) -> bool:
        f"""{ts('id_1731')}JSON{ts('id_3599')}"""
        self._ensure_initialized()
        if hasattr(self._backend, "import_credential_from_json"):
            return await self._backend.import_credential_from_json(json_path, filename)
        # MongoDB{ts(f"id_3598")}fallback{ts('id_2673')}
        try:
            import aiofiles

            async with aiofiles.open(json_path, "r", encoding="utf-8") as f:
                content = await f.read()

            credential_data = json.loads(content)

            if filename is None:
                filename = os.path.basename(json_path)

            return await self.store_credential(filename, credential_data)
        except Exception:
            return False

    def get_backend_type(self) -> str:
        f"""{ts('id_3600')}"""
        if not self._backend:
            return "none"

        # {ts(f"id_3601")}
        backend_class_name = self._backend.__class__.__name__
        if "SQLite" in backend_class_name or "sqlite" in backend_class_name.lower():
            return "sqlite"
        elif "MongoDB" in backend_class_name or "mongo" in backend_class_name.lower():
            return "mongodb"
        else:
            return "unknown"

    async def get_backend_info(self) -> Dict[str, Any]:
        f"""{ts('id_3602')}"""
        self._ensure_initialized()

        backend_type = self.get_backend_type()
        info = {"backend_type": backend_type, "initialized": self._initialized}

        # {ts(f"id_3603")}
        if hasattr(self._backend, "get_database_info"):
            try:
                db_info = await self._backend.get_database_info()
                info.update(db_info)
            except Exception as e:
                info["database_error"] = str(e)
        else:
            backend_type = self.get_backend_type()
            if backend_type == "sqlite":
                info.update(
                    {
                        "database_path": getattr(self._backend, "_db_path", None),
                        "credentials_dir": getattr(self._backend, "_credentials_dir", None),
                    }
                )
            elif backend_type == "mongodb":
                info.update(
                    {
                        "database_name": getattr(self._backend, "_db", {}).name if hasattr(self._backend, "_db") else None,
                    }
                )

        return info


# {ts(f"id_3604")}
_storage_adapter: Optional[StorageAdapter] = None


async def get_storage_adapter() -> StorageAdapter:
    f"""{ts('id_3605')}"""
    global _storage_adapter

    if _storage_adapter is None:
        _storage_adapter = StorageAdapter()
        await _storage_adapter.initialize()

    return _storage_adapter


async def close_storage_adapter():
    f"""{ts('id_3606')}"""
    global _storage_adapter

    if _storage_adapter:
        await _storage_adapter.close()
        _storage_adapter = None

from src.i18n import ts
"""
MongoDB {ts(f"id_3386")}
"""

import os
import time
import re
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from log import log


class MongoDBManager:
    f"""MongoDB {ts('id_3387')}"""

    # {ts(f"id_3388")}
    STATE_FIELDS = {
        "error_codes",
        "disabled",
        "last_success",
        "user_email",
        "model_cooldowns",
    }

    @staticmethod
    def _escape_model_key(model_key: str) -> str:
        """
        {ts(f"id_3390")},{ts('id_3391')} MongoDB {ts('id_3389')}

        Args:
            model_key: {ts(f"id_3392")} ({ts('id_716')} "gemini-2.5-flash")

        Returns:
            {ts(f"id_3393")} ({ts('id_716')} "gemini-2-5-flash")
        """
        return model_key.replace(".", "-")

    def __init__(self):
        self._client: Optional[AsyncIOMotorClient] = None
        self._db: Optional[AsyncIOMotorDatabase] = None
        self._initialized = False

        # {ts(f"id_3395")} - {ts('id_3394')}
        self._config_cache: Dict[str, Any] = {}
        self._config_loaded = False

    async def initialize(self) -> None:
        f"""{ts('id_1111')} MongoDB {ts('id_451')}"""
        if self._initialized:
            return

        try:
            mongodb_uri = os.getenv("MONGODB_URI")
            if not mongodb_uri:
                raise ValueError("MONGODB_URI environment variable not set")

            database_name = os.getenv("MONGODB_DATABASE", "gcli2api")

            self._client = AsyncIOMotorClient(mongodb_uri)
            self._db = self._client[database_name]

            # {ts(f"id_3396")}
            await self._db.command("ping")

            # {ts(f"id_3397")}
            await self._create_indexes()

            # {ts(f"id_3398")}
            await self._load_config_cache()

            self._initialized = True
            log.info(f"MongoDB storage initialized (database: {database_name})")

        except Exception as e:
            log.error(f"Error initializing MongoDB: {e}")
            raise

    async def _create_indexes(self):
        f"""{ts('id_3397')}"""
        credentials_collection = self._db["credentials"]
        antigravity_credentials_collection = self._db["antigravity_credentials"]

        # {ts(f"id_3399")}
        await credentials_collection.create_index("filename", unique=True)
        await credentials_collection.create_index("disabled")
        await credentials_collection.create_index("rotation_order")

        # {ts(f"id_3400")}
        await credentials_collection.create_index([("disabled", 1), ("rotation_order", 1)])

        # {ts(f"id_3401")}
        await credentials_collection.create_index("error_codes")

        # {ts(f"id_1029")} Antigravity {ts('id_3402')}
        await antigravity_credentials_collection.create_index("filename", unique=True)
        await antigravity_credentials_collection.create_index("disabled")
        await antigravity_credentials_collection.create_index("rotation_order")

        # {ts(f"id_3400")}
        await antigravity_credentials_collection.create_index([("disabled", 1), ("rotation_order", 1)])

        # {ts(f"id_3401")}
        await antigravity_credentials_collection.create_index("error_codes")

        log.debug("MongoDB indexes created")

    async def _load_config_cache(self):
        f"""{ts('id_3403')}"""
        if self._config_loaded:
            return

        try:
            config_collection = self._db["config"]
            cursor = config_collection.find({})

            async for doc in cursor:
                self._config_cache[doc["key"]] = doc.get("value")

            self._config_loaded = True
            log.debug(f"Loaded {len(self._config_cache)} config items into cache")

        except Exception as e:
            log.error(f"Error loading config cache: {e}")
            self._config_cache = {}

    async def close(self) -> None:
        f"""{ts('id_1169')} MongoDB {ts('id_451')}"""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
        self._initialized = False
        log.debug("MongoDB storage closed")

    def _ensure_initialized(self):
        f"""{ts('id_2978')}"""
        if not self._initialized:
            raise RuntimeError("MongoDB manager not initialized")

    def _get_collection_name(self, mode: str) -> str:
        f"""{ts('id_2136')} mode {ts('id_3404')}"""
        if mode == "antigravity":
            return "antigravity_credentials"
        elif mode == "geminicli":
            return "credentials"
        else:
            raise ValueError(f"Invalid mode: {mode}. Must be 'geminicli' or 'antigravity'")

    # ============ SQL {ts(f"id_3405")} ============

    async def get_next_available_credential(
        self, mode: str = "geminicli", model_key: Optional[str] = None
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        """
        {ts(f"id_3406")}
        - {ts(f"id_3407")}
        - {ts(f"id_2996")} model_key{ts('id_3408')}
        - {ts(f"id_3409")}

        Args:
            mode: {ts(f"id_1808")} ("geminicli" {ts('id_413')} "antigravity")
            model_key: {ts(f"id_3410")}antigravity {ts('id_3411')}gcli {ts('id_3412')} pro/flash{ts('id_292')}

        Note:
            - {ts(f"id_2608")} antigravity: model_key {ts('id_3413')} "gemini-2.0-flash-exp"{ts('id_292')}
            - {ts(f"id_2608")} gcli: model_key {ts('id_150')} "prof" {ts('id_413')} "flash"
            - {ts(f"id_3414")}
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]
            current_time = time.time()

            # {ts(f"id_3415")}
            pipeline = [
                # {ts(f"id_3417")}: {ts('id_3416')}
                {"$match": {"disabled": False}},
            ]

            # {ts(f"id_2996")} model_key{ts('id_3418')}
            if model_key:
                # {ts(f"id_3390")}
                escaped_model_key = self._escape_model_key(model_key)
                pipeline.extend([
                    # {ts(f"id_3420")}: {ts('id_3419')}
                    {
                        "$addFields": {
                            "is_available": {
                                "$or": [
                                    # model_cooldowns {ts(f"id_3421")} model_key
                                    {"$not": {"$ifNull": [f"$model_cooldowns.{escaped_model_key}", False]}},
                                    # {ts(f"id_3422")}
                                    {"$lte": [f"$model_cooldowns.{escaped_model_key}", current_time]}
                                ]
                            }
                        }
                    },
                    # {ts(f"id_3424")}: {ts('id_3423')}
                    {"$match": {"is_available": True}},
                ])

            # {ts(f"id_3426")}: {ts('id_3425')}
            pipeline.append({"$sample": {"size": 1}})

            # {ts(f"id_3428")}: {ts('id_3427')}
            pipeline.append({
                "$project": {
                    "filename": 1,
                    "credential_data": 1,
                    "_id": 0
                }
            })

            # {ts(f"id_3429")}
            docs = await collection.aggregate(pipeline).to_list(length=1)

            if docs:
                doc = docs[0]
                return doc["filename"], doc.get("credential_data")

            return None

        except Exception as e:
            log.error(f"Error getting next available credential (mode={mode}, model_key={model_key}): {e}")
            return None

    async def get_available_credentials_list(self, mode: str = "geminicli") -> List[str]:
        """
        {ts(f"id_3430")}
        - {ts(f"id_3407")}
        - {ts(f"id_3431")}
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            pipeline = [
                {"$match": {"disabled": False}},
                {"$sort": {"rotation_order": 1}},
                {"$project": {"filename": 1, "_id": 0}}
            ]

            docs = await collection.aggregate(pipeline).to_list(length=None)
            return [doc["filename"] for doc in docs]

        except Exception as e:
            log.error(f"Error getting available credentials list (mode={mode}): {e}")
            return []

    # ============ StorageBackend {ts(f"id_3432")} ============

    async def store_credential(self, filename: str, credential_data: Dict[str, Any], mode: str = "geminicli") -> bool:
        f"""{ts('id_3433')}"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]
            current_ts = time.time()

            # {ts(f"id_463")} upsert + $setOnInsert
            # {ts(f"id_3434")} credential_data {ts('id_15')} updated_at
            # {ts(f"id_3435")}

            # {ts(f"id_3436")}
            result = await collection.update_one(
                {"filename": filename},
                {
                    "$set": {
                        "credential_data": credential_data,
                        "updated_at": current_ts,
                    }
                }
            )

            # {ts(f"id_3437")}
            if result.matched_count == 0:
                # {ts(f"id_3438")} rotation_order
                pipeline = [
                    {"$group": {"_id": None, "max_order": {"$max": "$rotation_order"}}},
                    {"$project": {"_id": 0, "next_order": {"$add": ["$max_order", 1]}}}
                ]

                result_list = await collection.aggregate(pipeline).to_list(length=1)
                next_order = result_list[0]["next_order"] if result_list else 0

                # {ts(f"id_3440")} insert_one{ts('id_3439')}
                try:
                    await collection.insert_one({
                        "filename": filename,
                        "credential_data": credential_data,
                        "disabled": False,
                        "error_codes": [],
                        "last_success": current_ts,
                        "user_email": None,
                        "model_cooldowns": {},
                        "rotation_order": next_order,
                        "call_count": 0,
                        "created_at": current_ts,
                        "updated_at": current_ts,
                    })
                except Exception as insert_error:
                    # {ts(f"id_3441")}
                    if "duplicate key" in str(insert_error).lower():
                        # {ts(f"id_3442")}
                        await collection.update_one(
                            {"filename": filename},
                            {"$set": {"credential_data": credential_data, "updated_at": current_ts}}
                        )
                    else:
                        raise

            log.debug(f"Stored credential: {filename} (mode={mode})")
            return True

        except Exception as e:
            log.error(f"Error storing credential {filename}: {e}")
            return False

    async def get_credential(self, filename: str, mode: str = "geminicli") -> Optional[Dict[str, Any]]:
        f"""{ts('id_3443')}basename{ts('id_3444')}"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # {ts(f"id_3445")}
            doc = await collection.find_one(
                {"filename": filename},
                {"credential_data": 1, "_id": 0}
            )
            if doc:
                return doc.get("credential_data")

            # {ts(f"id_3447")}basename{ts('id_3446')}
            # {ts(f"id_3449")} $regex {ts('id_3448')} $or {ts('id_1889')}
            regex_pattern = re.escape(filename)
            doc = await collection.find_one(
                {"filename": {"$regex": f".*{regex_pattern}$"}},
                {"credential_data": 1, "_id": 0}
            )

            if doc:
                return doc.get("credential_data")

            return None

        except Exception as e:
            log.error(f"Error getting credential {filename}: {e}")
            return None

    async def list_credentials(self, mode: str = "geminicli") -> List[str]:
        f"""{ts('id_3450')}"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # {ts(f"id_3451")}
            pipeline = [
                {"$sort": {"rotation_order": 1}},
                {"$project": {"filename": 1, "_id": 0}}
            ]

            docs = await collection.aggregate(pipeline).to_list(length=None)
            return [doc["filename"] for doc in docs]

        except Exception as e:
            log.error(f"Error listing credentials: {e}")
            return []

    async def delete_credential(self, filename: str, mode: str = "geminicli") -> bool:
        f"""{ts('id_3452')}basename{ts('id_3444')}"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # {ts(f"id_3453")}
            result = await collection.delete_one({"filename": filename})
            deleted_count = result.deleted_count

            # {ts(f"id_3454")}basename{ts('id_3455')}
            if deleted_count == 0:
                regex_pattern = re.escape(filename)
                result = await collection.delete_one({
                    "filename": {"$regex": f".*{regex_pattern}$"}
                })
                deleted_count = result.deleted_count

            if deleted_count > 0:
                log.debug(f"Deleted {deleted_count} credential(s): {filename} (mode={mode})")
                return True
            else:
                log.warning(f"No credential found to delete: {filename} (mode={mode})")
                return False

        except Exception as e:
            log.error(f"Error deleting credential {filename}: {e}")
            return False

    async def get_duplicate_credentials_by_email(self, mode: str = "geminicli") -> Dict[str, Any]:
        """
        {ts(f"id_3456")}
        {ts(f"id_3457")}

        Args:
            mode: {ts(f"id_1808")} ("geminicli" {ts('id_413')} "antigravity")

        Returns:
            {ts(f"id_906")} email_groups{ts('id_3460')}duplicate_count{ts('id_3459')}no_email_count{ts('id_3458')}
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # {ts(f"id_3461")} filename {ts('id_15')} user_email {ts('id_2018')}
            pipeline = [
                {
                    "$project": {
                        "filename": 1,
                        "user_email": 1,
                        "_id": 0
                    }
                },
                {
                    "$sort": {"filename": 1}
                }
            ]

            docs = await collection.aggregate(pipeline).to_list(length=None)

            # {ts(f"id_3462")}
            email_to_files = {}
            no_email_files = []

            for doc in docs:
                filename = doc.get("filename")
                user_email = doc.get("user_email")

                if user_email:
                    if user_email not in email_to_files:
                        email_to_files[user_email] = []
                    email_to_files[user_email].append(filename)
                else:
                    no_email_files.append(filename)

            # {ts(f"id_3463")}
            duplicate_groups = []
            total_duplicate_count = 0

            for email, files in email_to_files.items():
                if len(files) > 1:
                    # {ts(f"id_3464")}
                    duplicate_groups.append({
                        "email": email,
                        "kept_file": files[0],
                        "duplicate_files": files[1:],
                        "duplicate_count": len(files) - 1,
                    })
                    total_duplicate_count += len(files) - 1

            return {
                "email_groups": email_to_files,
                "duplicate_groups": duplicate_groups,
                "duplicate_count": total_duplicate_count,
                "no_email_files": no_email_files,
                "no_email_count": len(no_email_files),
                "unique_email_count": len(email_to_files),
                "total_count": len(docs),
            }

        except Exception as e:
            log.error(f"Error getting duplicate credentials by email: {e}")
            return {
                "email_groups": {},
                "duplicate_groups": [],
                "duplicate_count": 0,
                "no_email_files": [],
                "no_email_count": 0,
                "unique_email_count": 0,
                "total_count": 0,
            }

    async def update_credential_state(
        self, filename: str, state_updates: Dict[str, Any], mode: str = "geminicli"
    ) -> bool:
        f"""{ts('id_3465')}basename{ts('id_3444')}"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # {ts(f"id_3466")}
            valid_updates = {
                k: v for k, v in state_updates.items() if k in self.STATE_FIELDS
            }

            if not valid_updates:
                return True

            valid_updates["updated_at"] = time.time()

            # {ts(f"id_3467")}
            result = await collection.update_one(
                {"filename": filename}, {"$set": valid_updates}
            )
            updated_count = result.modified_count + result.matched_count

            # {ts(f"id_3468")}basename{ts('id_3455')}
            if updated_count == 0:
                regex_pattern = re.escape(filename)
                result = await collection.update_one(
                    {"filename": {"$regex": f".*{regex_pattern}$"}},
                    {"$set": valid_updates}
                )
                updated_count = result.modified_count + result.matched_count

            return updated_count > 0

        except Exception as e:
            log.error(f"Error updating credential state {filename}: {e}")
            return False

    async def get_credential_state(self, filename: str, mode: str = "geminicli") -> Dict[str, Any]:
        f"""{ts('id_3469')}basename{ts('id_3444')}"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]
            current_time = time.time()

            # {ts(f"id_3470")}
            doc = await collection.find_one({"filename": filename})

            if doc:
                model_cooldowns = doc.get("model_cooldowns", {})
                # {ts(f"id_3471")}(dict{ts('id_1454')}){ts('id_3472')}
                if model_cooldowns:
                    model_cooldowns = {
                        k: v for k, v in model_cooldowns.items()
                        if isinstance(v, (int, float)) and v > current_time
                    }

                return {
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", current_time),
                    "user_email": doc.get("user_email"),
                    "model_cooldowns": model_cooldowns,
                }

            # {ts(f"id_3473")}basename{ts('id_3455')}
            regex_pattern = re.escape(filename)
            doc = await collection.find_one({
                "filename": {"$regex": f".*{regex_pattern}$"}
            })

            if doc:
                model_cooldowns = doc.get("model_cooldowns", {})
                # {ts(f"id_3471")}(dict{ts('id_1454')}){ts('id_3472')}
                if model_cooldowns:
                    model_cooldowns = {
                        k: v for k, v in model_cooldowns.items()
                        if isinstance(v, (int, float)) and v > current_time
                    }

                return {
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", current_time),
                    "user_email": doc.get("user_email"),
                    "model_cooldowns": model_cooldowns,
                }

            # {ts(f"id_3474")}
            return {
                "disabled": False,
                "error_codes": [],
                "last_success": current_time,
                "user_email": None,
                "model_cooldowns": {},
            }

        except Exception as e:
            log.error(f"Error getting credential state {filename}: {e}")
            return {}

    async def get_all_credential_states(self, mode: str = "geminicli") -> Dict[str, Dict[str, Any]]:
        f"""{ts('id_3475')}"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # {ts(f"id_3476")}
            cursor = collection.find(
                {},
                projection={
                    "filename": 1,
                    "disabled": 1,
                    "error_codes": 1,
                    "last_success": 1,
                    "user_email": 1,
                    "model_cooldowns": 1,
                    "_id": 0
                }
            )

            states = {}
            current_time = time.time()

            async for doc in cursor:
                filename = doc["filename"]
                model_cooldowns = doc.get("model_cooldowns", {})

                # {ts(f"id_3477")}CD
                if model_cooldowns:
                    model_cooldowns = {
                        k: v for k, v in model_cooldowns.items()
                        if isinstance(v, (int, float)) and v > current_time
                    }

                states[filename] = {
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", time.time()),
                    "user_email": doc.get("user_email"),
                    "model_cooldowns": model_cooldowns,
                }

            return states

        except Exception as e:
            log.error(f"Error getting all credential states: {e}")
            return {}

    async def get_credentials_summary(
        self,
        offset: int = 0,
        limit: Optional[int] = None,
        status_filter: str = "all",
        mode: str = "geminicli",
        error_code_filter: Optional[str] = None,
        cooldown_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        {ts(f"id_3478")}- {ts('id_3479')}

        Args:
            offset: {ts(f"id_34800")}{ts('id_292')}
            limit: {ts(f"id_3481")}None{ts('id_3482')}
            status_filter: {ts(f"id_3483")}all={ts('id_1238')}, enabled={ts('id_724')}, disabled={ts('id_3484')}
            mode: {ts(f"id_1808")} ("geminicli" {ts('id_413')} "antigravity")
            error_code_filter: {ts(f"id_3486")}"400"{ts('id_413')}"403"{ts('id_3485')}
            cooldown_filter: {ts(f"id_3487")}"in_cooldown"={ts('id_3489')}, "no_cooldown"={ts('id_3488')}

        Returns:
            {ts(f"id_906")} items{ts('id_3490')}total{ts('id_3491f')}offset{ts('id_189')}limit {ts('id_2782')}
        """
        self._ensure_initialized()

        try:
            # {ts(f"id_2136")} mode {ts('id_3492')}
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # {ts(f"id_3493")}
            query = {}
            if status_filter == "enabled":
                query["disabled"] = False
            elif status_filter == "disabled":
                query["disabled"] = True

            # {ts(f"id_3495")} - {ts('id_3494')}
            if error_code_filter and str(error_code_filter).strip().lower() != "all":
                filter_value = str(error_code_filter).strip()
                query_values = [filter_value]
                try:
                    query_values.append(int(filter_value))
                except ValueError:
                    pass
                query["error_codes"] = {"$in": query_values}

            # {ts(f"id_3496")}
            global_stats = {"total": 0, "normal": 0, "disabled": 0}
            stats_pipeline = [
                {
                    "$group": {
                        "_id": "$disabled",
                        "count": {"$sum": 1}
                    }
                }
            ]

            stats_result = await collection.aggregate(stats_pipeline).to_list(length=10)
            for item in stats_result:
                count = item["count"]
                global_stats["total"] += count
                if item["_id"]:
                    global_stats["disabled"] = count
                else:
                    global_stats["normal"] = count

            # {ts(f"id_3497")}Python{ts('id_3498')}
            cursor = collection.find(
                query,
                projection={
                    "filename": 1,
                    "disabled": 1,
                    "error_codes": 1,
                    "last_success": 1,
                    "user_email": 1,
                    "rotation_order": 1,
                    "model_cooldowns": 1,
                    "_id": 0
                }
            ).sort("rotation_order", 1)

            all_summaries = []
            current_time = time.time()

            async for doc in cursor:
                model_cooldowns = doc.get("model_cooldowns", {})

                # {ts(f"id_3477")}CD
                active_cooldowns = {}
                if model_cooldowns:
                    active_cooldowns = {
                        k: v for k, v in model_cooldowns.items()
                        if isinstance(v, (int, float)) and v > current_time
                    }

                summary = {
                    "filename": doc["filename"],
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", current_time),
                    "user_email": doc.get("user_email"),
                    "rotation_order": doc.get("rotation_order", 0),
                    "model_cooldowns": active_cooldowns,
                }

                # {ts(f"id_3499")}
                if cooldown_filter == "in_cooldown":
                    # {ts(f"id_3500")}
                    if active_cooldowns:
                        all_summaries.append(summary)
                elif cooldown_filter == "no_cooldown":
                    # {ts(f"id_3501")}
                    if not active_cooldowns:
                        all_summaries.append(summary)
                else:
                    # {ts(f"id_3502")}
                    all_summaries.append(summary)

            # {ts(f"id_3503")}
            total_count = len(all_summaries)
            if limit is not None:
                summaries = all_summaries[offset:offset + limit]
            else:
                summaries = all_summaries[offset:]

            return {
                "items": summaries,
                "total": total_count,
                "offset": offset,
                "limit": limit,
                "stats": global_stats,
            }

        except Exception as e:
            log.error(f"Error getting credentials summary: {e}")
            return {
                "items": [],
                "total": 0,
                "offset": offset,
                "limit": limit,
                "stats": {"total": 0, "normal": 0, "disabled": 0},
            }

    # ============ {ts(f"id_3504")}============

    async def set_config(self, key: str, value: Any) -> bool:
        f"""{ts('id_3505')} + {ts('id_3506')}"""
        self._ensure_initialized()

        try:
            config_collection = self._db["config"]
            await config_collection.update_one(
                {"key": key},
                {"$set": {"value": value, "updated_at": time.time()}},
                upsert=True,
            )

            # {ts(f"id_3507")}
            self._config_cache[key] = value
            return True

        except Exception as e:
            log.error(f"Error setting config {key}: {e}")
            return False

    async def reload_config_cache(self):
        f"""{ts('id_3508')}"""
        self._ensure_initialized()
        self._config_loaded = False
        await self._load_config_cache()
        log.info("Config cache reloaded from database")

    async def get_config(self, key: str, default: Any = None) -> Any:
        f"""{ts('id_3509')}"""
        self._ensure_initialized()
        return self._config_cache.get(key, default)

    async def get_all_config(self) -> Dict[str, Any]:
        f"""{ts('id_3510')}"""
        self._ensure_initialized()
        return self._config_cache.copy()

    async def delete_config(self, key: str) -> bool:
        f"""{ts('id_3511')}"""
        self._ensure_initialized()

        try:
            config_collection = self._db["config"]
            result = await config_collection.delete_one({"key": key})

            # {ts(f"id_3512")}
            self._config_cache.pop(key, None)
            return result.deleted_count > 0

        except Exception as e:
            log.error(f"Error deleting config {key}: {e}")
            return False

    # ============ {ts(f"id_3513")} ============

    async def set_model_cooldown(
        self,
        filename: str,
        model_key: str,
        cooldown_until: Optional[float],
        mode: str = "geminicli"
    ) -> bool:
        """
        {ts(f"id_3514")}

        Args:
            filename: {ts(f"id_3515")}
            model_key: {ts(f"id_3516")}antigravity {ts('id_3411')}gcli {ts('id_3412')} pro/flash{ts('id_292')}
            cooldown_until: {ts(f"id_2991")}None {ts('id_3517')}
            mode: {ts(f"id_1808")} ("geminicli" {ts('id_413')} "antigravity")

        Returns:
            {ts(f"id_2989")}
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # {ts(f"id_3390")}
            escaped_model_key = self._escape_model_key(model_key)

            # {ts(f"id_3518")}
            if cooldown_until is None:
                # {ts(f"id_3519")}
                result = await collection.update_one(
                    {"filename": filename},
                    {
                        "$unset": {f"model_cooldowns.{escaped_model_key}": ""},
                        "$set": {"updated_at": time.time()}
                    }
                )
            else:
                # {ts(f"id_3520")}
                result = await collection.update_one(
                    {"filename": filename},
                    {
                        "$set": {
                            f"model_cooldowns.{escaped_model_key}": cooldown_until,
                            "updated_at": time.time()
                        }
                    }
                )

            if result.matched_count == 0:
                log.warning(f"Credential {filename} not found")
                return False

            log.debug(f"Set model cooldown: {filename}, model_key={model_key}, cooldown_until={cooldown_until}")
            return True

        except Exception as e:
            log.error(f"Error setting model cooldown for {filename}: {e}")
            return False

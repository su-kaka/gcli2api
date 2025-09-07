"""
数据迁移工具，用于将本地文件数据迁移到MongoDB。
"""
import os
import json
import asyncio
from typing import Dict, Any

import aiofiles
import toml

from log import log
from ..src.mongodb_manager import get_mongodb_manager
from ..src.storage_adapter import FileStorageBackend


class MigrationTool:
    """数据迁移工具"""
    
    def __init__(self):
        self._file_backend = None
        self._mongo_manager = None
    
    async def initialize(self):
        """初始化迁移工具"""
        self._file_backend = FileStorageBackend()
        await self._file_backend.initialize()
        
        self._mongo_manager = await get_mongodb_manager()
        
        log.info("Migration tool initialized")
    
    async def migrate_all_data(self) -> Dict[str, Any]:
        """迁移所有数据从本地文件到MongoDB"""
        if not self._file_backend or not self._mongo_manager:
            raise RuntimeError("Migration tool not initialized")
        
        results = {
            "credentials": {"success": 0, "failed": 0, "errors": []},
            "states": {"success": 0, "failed": 0, "errors": []},
            "config": {"success": 0, "failed": 0, "errors": []},
            "usage_stats": {"success": 0, "failed": 0, "errors": []}
        }
        
        log.info("Starting data migration from files to MongoDB")
        
        # 迁移凭证文件
        await self._migrate_credentials(results)
        
        # 迁移状态数据
        await self._migrate_states(results)
        
        # 迁移配置数据
        await self._migrate_config(results)
        
        # 迁移使用统计（集成在状态中）
        await self._migrate_usage_stats(results)
        
        log.info(f"Migration completed: {results}")
        return results
    
    async def _migrate_credentials(self, results: Dict[str, Any]):
        """迁移凭证文件"""
        try:
            credentials = await self._file_backend.list_credentials()
            
            for filename in credentials:
                try:
                    credential_data = await self._file_backend.get_credential(filename)
                    if credential_data:
                        success = await self._mongo_manager.store_credential(filename, credential_data)
                        if success:
                            results["credentials"]["success"] += 1
                            log.debug(f"Migrated credential: {filename}")
                        else:
                            results["credentials"]["failed"] += 1
                            results["credentials"]["errors"].append(f"Failed to store {filename}")
                    else:
                        results["credentials"]["failed"] += 1
                        results["credentials"]["errors"].append(f"Could not read {filename}")
                        
                except Exception as e:
                    results["credentials"]["failed"] += 1
                    results["credentials"]["errors"].append(f"Error migrating {filename}: {e}")
                    log.error(f"Error migrating credential {filename}: {e}")
                    
        except Exception as e:
            log.error(f"Error migrating credentials: {e}")
            results["credentials"]["errors"].append(f"General error: {e}")
    
    async def _migrate_states(self, results: Dict[str, Any]):
        """迁移状态数据"""
        try:
            all_states = await self._file_backend.get_all_credential_states()
            
            for filename, state in all_states.items():
                try:
                    # 分离状态数据和统计数据
                    state_data = {
                        "error_codes": state.get("error_codes", []),
                        "disabled": state.get("disabled", False),
                        "last_success": state.get("last_success"),
                        "user_email": state.get("user_email"),
                    }
                    
                    success = await self._mongo_manager.update_credential_state(filename, state_data)
                    if success:
                        results["states"]["success"] += 1
                        log.debug(f"Migrated state for: {filename}")
                    else:
                        results["states"]["failed"] += 1
                        results["states"]["errors"].append(f"Failed to migrate state for {filename}")
                        
                except Exception as e:
                    results["states"]["failed"] += 1
                    results["states"]["errors"].append(f"Error migrating state for {filename}: {e}")
                    log.error(f"Error migrating state for {filename}: {e}")
                    
        except Exception as e:
            log.error(f"Error migrating states: {e}")
            results["states"]["errors"].append(f"General error: {e}")
    
    async def _migrate_config(self, results: Dict[str, Any]):
        """迁移配置数据"""
        try:
            all_config = await self._file_backend.get_all_config()
            
            for key, value in all_config.items():
                try:
                    success = await self._mongo_manager.set_config(key, value)
                    if success:
                        results["config"]["success"] += 1
                        log.debug(f"Migrated config: {key}")
                    else:
                        results["config"]["failed"] += 1
                        results["config"]["errors"].append(f"Failed to migrate config {key}")
                        
                except Exception as e:
                    results["config"]["failed"] += 1
                    results["config"]["errors"].append(f"Error migrating config {key}: {e}")
                    log.error(f"Error migrating config {key}: {e}")
                    
        except Exception as e:
            log.error(f"Error migrating config: {e}")
            results["config"]["errors"].append(f"General error: {e}")
    
    async def _migrate_usage_stats(self, results: Dict[str, Any]):
        """迁移使用统计数据"""
        try:
            all_stats = await self._file_backend.get_all_usage_stats()
            
            for filename, stats in all_stats.items():
                try:
                    stats_data = {
                        "gemini_2_5_pro_calls": stats.get("gemini_2_5_pro_calls", 0),
                        "total_calls": stats.get("total_calls", 0),
                        "next_reset_time": stats.get("next_reset_time"),
                        "daily_limit_gemini_2_5_pro": stats.get("daily_limit_gemini_2_5_pro", 100),
                        "daily_limit_total": stats.get("daily_limit_total", 1000)
                    }
                    
                    success = await self._mongo_manager.update_usage_stats(filename, stats_data)
                    if success:
                        results["usage_stats"]["success"] += 1
                        log.debug(f"Migrated usage stats for: {filename}")
                    else:
                        results["usage_stats"]["failed"] += 1
                        results["usage_stats"]["errors"].append(f"Failed to migrate stats for {filename}")
                        
                except Exception as e:
                    results["usage_stats"]["failed"] += 1
                    results["usage_stats"]["errors"].append(f"Error migrating stats for {filename}: {e}")
                    log.error(f"Error migrating stats for {filename}: {e}")
                    
        except Exception as e:
            log.error(f"Error migrating usage stats: {e}")
            results["usage_stats"]["errors"].append(f"General error: {e}")
    
    async def export_from_mongodb(self, export_dir: str) -> Dict[str, Any]:
        """从MongoDB导出数据到本地文件（用于备份或迁移回本地）"""
        if not self._mongo_manager:
            raise RuntimeError("Migration tool not initialized")
        
        os.makedirs(export_dir, exist_ok=True)
        
        results = {
            "credentials": {"success": 0, "failed": 0, "errors": []},
            "states": {"success": 0, "failed": 0, "errors": []},
            "config": {"success": 0, "failed": 0, "errors": []},
            "usage_stats": {"success": 0, "failed": 0, "errors": []}
        }
        
        log.info(f"Starting data export from MongoDB to {export_dir}")
        
        # 导出凭证
        await self._export_credentials(export_dir, results)
        
        # 导出状态和统计（合并到一个TOML文件）
        await self._export_states_and_stats(export_dir, results)
        
        # 导出配置
        await self._export_config(export_dir, results)
        
        log.info(f"Export completed: {results}")
        return results
    
    async def _export_credentials(self, export_dir: str, results: Dict[str, Any]):
        """导出凭证文件"""
        try:
            credentials = await self._mongo_manager.list_credentials()
            
            creds_dir = os.path.join(export_dir, "creds")
            os.makedirs(creds_dir, exist_ok=True)
            
            for filename in credentials:
                try:
                    credential_data = await self._mongo_manager.get_credential(filename)
                    if credential_data:
                        file_path = os.path.join(creds_dir, filename)
                        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                            await f.write(json.dumps(credential_data, indent=2, ensure_ascii=False))
                        
                        results["credentials"]["success"] += 1
                        log.debug(f"Exported credential: {filename}")
                    else:
                        results["credentials"]["failed"] += 1
                        results["credentials"]["errors"].append(f"Could not read {filename} from MongoDB")
                        
                except Exception as e:
                    results["credentials"]["failed"] += 1
                    results["credentials"]["errors"].append(f"Error exporting {filename}: {e}")
                    log.error(f"Error exporting credential {filename}: {e}")
                    
        except Exception as e:
            log.error(f"Error exporting credentials: {e}")
            results["credentials"]["errors"].append(f"General error: {e}")
    
    async def _export_states_and_stats(self, export_dir: str, results: Dict[str, Any]):
        """导出状态和统计数据（合并到TOML文件）"""
        try:
            all_states = await self._mongo_manager.get_all_credential_states()
            all_stats = await self._mongo_manager.get_all_usage_stats()
            
            # 合并状态和统计数据
            combined_data = {}
            
            # 添加状态数据
            for filename, state in all_states.items():
                if filename not in combined_data:
                    combined_data[filename] = {}
                combined_data[filename].update(state)
            
            # 添加统计数据
            for filename, stats in all_stats.items():
                if filename not in combined_data:
                    combined_data[filename] = {}
                combined_data[filename].update(stats)
            
            # 保存到TOML文件
            if combined_data:
                creds_dir = os.path.join(export_dir, "creds")
                os.makedirs(creds_dir, exist_ok=True)
                
                state_file = os.path.join(creds_dir, "creds_state.toml")
                async with aiofiles.open(state_file, "w", encoding="utf-8") as f:
                    await f.write(toml.dumps(combined_data))
                
                results["states"]["success"] = len(all_states)
                results["usage_stats"]["success"] = len(all_stats)
                log.debug(f"Exported combined state and stats data to {state_file}")
            
        except Exception as e:
            log.error(f"Error exporting states and stats: {e}")
            results["states"]["errors"].append(f"General error: {e}")
            results["usage_stats"]["errors"].append(f"General error: {e}")
    
    async def _export_config(self, export_dir: str, results: Dict[str, Any]):
        """导出配置数据"""
        try:
            all_config = await self._mongo_manager.get_all_config()
            
            if all_config:
                creds_dir = os.path.join(export_dir, "creds")
                os.makedirs(creds_dir, exist_ok=True)
                
                config_file = os.path.join(creds_dir, "config.toml")
                async with aiofiles.open(config_file, "w", encoding="utf-8") as f:
                    await f.write(toml.dumps(all_config))
                
                results["config"]["success"] = len(all_config)
                log.debug(f"Exported config data to {config_file}")
            
        except Exception as e:
            log.error(f"Error exporting config: {e}")
            results["config"]["errors"].append(f"General error: {e}")
    
    async def verify_migration(self) -> Dict[str, Any]:
        """验证迁移结果"""
        if not self._file_backend or not self._mongo_manager:
            raise RuntimeError("Migration tool not initialized")
        
        verification = {
            "credentials": {"file_count": 0, "mongo_count": 0, "match": False},
            "states": {"file_count": 0, "mongo_count": 0, "match": False},
            "config": {"file_count": 0, "mongo_count": 0, "match": False},
            "usage_stats": {"file_count": 0, "mongo_count": 0, "match": False}
        }
        
        try:
            # 验证凭证
            file_creds = await self._file_backend.list_credentials()
            mongo_creds = await self._mongo_manager.list_credentials()
            verification["credentials"]["file_count"] = len(file_creds)
            verification["credentials"]["mongo_count"] = len(mongo_creds)
            verification["credentials"]["match"] = set(file_creds) == set(mongo_creds)
            
            # 验证状态
            file_states = await self._file_backend.get_all_credential_states()
            mongo_states = await self._mongo_manager.get_all_credential_states()
            verification["states"]["file_count"] = len(file_states)
            verification["states"]["mongo_count"] = len(mongo_states)
            verification["states"]["match"] = set(file_states.keys()) == set(mongo_states.keys())
            
            # 验证配置
            file_config = await self._file_backend.get_all_config()
            mongo_config = await self._mongo_manager.get_all_config()
            verification["config"]["file_count"] = len(file_config)
            verification["config"]["mongo_count"] = len(mongo_config)
            verification["config"]["match"] = set(file_config.keys()) == set(mongo_config.keys())
            
            # 验证使用统计
            file_stats = await self._file_backend.get_all_usage_stats()
            mongo_stats = await self._mongo_manager.get_all_usage_stats()
            verification["usage_stats"]["file_count"] = len(file_stats)
            verification["usage_stats"]["mongo_count"] = len(mongo_stats)
            verification["usage_stats"]["match"] = set(file_stats.keys()) == set(mongo_stats.keys())
            
        except Exception as e:
            log.error(f"Error during verification: {e}")
            verification["error"] = str(e)
        
        return verification


async def run_migration():
    """运行数据迁移（独立脚本使用）"""
    try:
        migration_tool = MigrationTool()
        await migration_tool.initialize()
        
        print("Starting migration from local files to MongoDB...")
        results = await migration_tool.migrate_all_data()
        
        print("\nMigration Results:")
        print(f"Credentials: {results['credentials']['success']} success, {results['credentials']['failed']} failed")
        print(f"States: {results['states']['success']} success, {results['states']['failed']} failed")
        print(f"Config: {results['config']['success']} success, {results['config']['failed']} failed")
        print(f"Usage Stats: {results['usage_stats']['success']} success, {results['usage_stats']['failed']} failed")
        
        if any(results[key]["errors"] for key in results):
            print("\nErrors:")
            for category, data in results.items():
                if data["errors"]:
                    print(f"{category}: {data['errors']}")
        
        print("\nVerifying migration...")
        verification = await migration_tool.verify_migration()
        
        print("\nVerification Results:")
        for category, data in verification.items():
            if isinstance(data, dict) and "match" in data:
                status = "✓ MATCH" if data["match"] else "✗ MISMATCH"
                print(f"{category}: {data['file_count']} files → {data['mongo_count']} MongoDB documents {status}")
        
        print("\nMigration completed!")
        
    except Exception as e:
        print(f"Migration failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(run_migration())
#!/usr/bin/env python3
"""
MongoDB模式设置和管理脚本
"""
import asyncio
import sys

from config import is_mongodb_mode, get_mongodb_uri, get_mongodb_database
from log import log
from tools.migration_tool import MigrationTool
from src.storage_adapter import get_storage_adapter
from mongodb_manager import get_mongodb_manager

async def check_mongodb_connection():
    """检查MongoDB连接"""
    if not is_mongodb_mode():
        log.info("❌ MongoDB模式未启用。请设置MONGODB_URI环境变量。")
        return False
    
    try:
        mongo_manager = await get_mongodb_manager()
        available = await mongo_manager.is_available()
        
        if available:
            db_info = await mongo_manager.get_database_info()
            log.info("✅ MongoDB连接成功！")
            log.info(f"数据库: {db_info.get('database_name', 'unknown')}")
            log.info(f"集合数量: {len(db_info.get('collections', {}))}")
            return True
        else:
            log.info("❌ MongoDB连接失败")
            return False
    except Exception as e:
        log.info(f"❌ MongoDB连接错误: {e}")
        return False


async def show_storage_info():
    """显示当前存储信息"""
    log.info("\n=== 当前存储配置 ===")
    
    if is_mongodb_mode():
        log.info(f"🗄️ 存储模式: MongoDB")
        log.info(f"📍 MongoDB URI: {get_mongodb_uri()}")
        log.info(f"🏗️ 数据库名称: {get_mongodb_database()}")
        
        # 检查连接并显示详情
        if await check_mongodb_connection():
            storage_adapter = await get_storage_adapter()
            backend_info = await storage_adapter.get_backend_info()
            
            collections = backend_info.get('collections', {})
            for collection_name, info in collections.items():
                count = info.get('document_count', 0)
                log.info(f"📋 {collection_name}: {count} 文档")
    else:
        log.info(f"🗄️ 存储模式: 本地文件")
        log.info(f"📁 凭证目录: ./creds/")
        
        # 统计本地文件
        try:
            storage_adapter = await get_storage_adapter()
            credentials = await storage_adapter.list_credentials()
            all_states = await storage_adapter.get_all_credential_states()
            all_config = await storage_adapter.get_all_config()
            
            log.info(f"🔑 凭证文件: {len(credentials)} 个")
            log.info(f"📊 状态记录: {len(all_states)} 个")
            log.info(f"⚙️ 配置项: {len(all_config)} 个")
        except Exception as e:
            log.info(f"❌ 获取文件信息失败: {e}")


async def migrate_to_mongodb():
    """迁移数据到MongoDB"""
    if not is_mongodb_mode():
        log.info("❌ 请先设置MONGODB_URI环境变量启用MongoDB模式")
        return
    
    if not await check_mongodb_connection():
        log.info("❌ MongoDB连接失败，无法进行迁移")
        return
    
    log.info("\n=== 开始数据迁移 ===")
    log.info("正在将本地文件数据迁移到MongoDB...")
    
    try:
        migration_tool = MigrationTool()
        await migration_tool.initialize()
        
        # 执行迁移
        results = await migration_tool.migrate_all_data()
        
        log.info("\n📊 迁移结果:")
        for category, data in results.items():
            success_count = data.get('success', 0)
            failed_count = data.get('failed', 0)
            log.info(f"  {category}: ✅{success_count} ❌{failed_count}")
            
            if data.get('errors'):
                log.info(f"    错误: {data['errors']}")
        
        # 验证迁移
        log.info("\n🔍 验证迁移结果...")
        verification = await migration_tool.verify_migration()
        
        log.info("验证结果:")
        all_match = True
        for category, data in verification.items():
            if isinstance(data, dict) and "match" in data:
                status = "✅" if data["match"] else "❌"
                log.info(f"  {category}: {data['file_count']} → {data['mongo_count']} {status}")
                if not data["match"]:
                    all_match = False
        
        if all_match:
            log.info("\n🎉 迁移成功完成！")
        else:
            log.info("\n⚠️ 迁移可能存在问题，请检查日志")
            
    except Exception as e:
        log.info(f"❌ 迁移失败: {e}")


async def export_from_mongodb():
    """从MongoDB导出数据"""
    if not is_mongodb_mode():
        log.info("❌ 当前不是MongoDB模式")
        return
    
    if not await check_mongodb_connection():
        log.info("❌ MongoDB连接失败")
        return
    
    # 询问导出目录
    export_dir = input("请输入导出目录 (默认: ./mongodb_backup): ").strip()
    if not export_dir:
        export_dir = "./mongodb_backup"
    
    log.info(f"\n=== 导出数据到 {export_dir} ===")
    
    try:
        migration_tool = MigrationTool()
        await migration_tool.initialize()
        
        results = await migration_tool.export_from_mongodb(export_dir)
        
        log.info("\n📊 导出结果:")
        for category, data in results.items():
            success_count = data.get('success', 0)
            failed_count = data.get('failed', 0)
            log.info(f"  {category}: ✅{success_count} ❌{failed_count}")
            
            if data.get('errors'):
                log.info(f"    错误: {data['errors']}")
        
        log.info(f"\n🎉 数据已导出到: {export_dir}")
        
    except Exception as e:
        log.info(f"❌ 导出失败: {e}")


async def interactive_menu():
    """交互式菜单"""
    while True:
        log.info("\n" + "="*50)
        log.info("🍃 gcli2api MongoDB管理工具")
        log.info("="*50)
        
        await show_storage_info()
        
        log.info("\n📋 可用操作:")
        log.info("1. 🔍 检查MongoDB连接")
        log.info("2. 📤 迁移数据到MongoDB") 
        log.info("3. 📥 从MongoDB导出数据")
        log.info("4. ❌ 退出")
        
        choice = input("\n请选择操作 (1-4): ").strip()
        
        if choice == "1":
            await check_mongodb_connection()
        elif choice == "2":
            await migrate_to_mongodb()
        elif choice == "3":
            await export_from_mongodb()
        elif choice == "4":
            log.info("👋 再见！")
            break
        else:
            log.info("❌ 无效选择，请重试")
        
        input("\n按回车键继续...")


def show_usage():
    """显示使用说明"""
    log.info("""
🍃 gcli2api MongoDB管理工具

使用方法:
    python mongodb_setup.py [命令]

可用命令:
    status      - 显示当前存储状态
    check       - 检查MongoDB连接
    migrate     - 迁移数据到MongoDB  
    export      - 从MongoDB导出数据
    interactive - 交互式菜单 (默认)

环境变量:
    MONGODB_URI      - MongoDB连接字符串
    MONGODB_DATABASE - 数据库名称 (默认: gcli2api)

示例:
    # 启用MongoDB模式
    export MONGODB_URI="mongodb://localhost:27017"
    python mongodb_setup.py migrate
    
    # 或使用交互式菜单
    python mongodb_setup.py
""")


async def main():
    """主函数"""
    if len(sys.argv) < 2:
        await interactive_menu()
        return
    
    command = sys.argv[1].lower()
    
    if command == "status":
        await show_storage_info()
    elif command == "check":
        await check_mongodb_connection()
    elif command == "migrate":
        await migrate_to_mongodb()
    elif command == "export":
        await export_from_mongodb()
    elif command == "interactive":
        await interactive_menu()
    elif command in ["-h", "--help", "help"]:
        show_usage()
    else:
        log.info(f"❌ 未知命令: {command}")
        show_usage()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("\n👋 用户中断，再见！")
    except Exception as e:
        log.info(f"❌ 运行错误: {e}")
        sys.exit(1)
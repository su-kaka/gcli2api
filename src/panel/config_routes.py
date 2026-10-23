"""
配置路由模块 - 处理 /config/* 相关的HTTP请求
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

import config
from log import log
from src.models import ConfigSaveRequest
from src.storage.database import get_database
from src.utils import verify_panel_token
from .utils import get_env_locked_keys


# 创建路由器
router = APIRouter(prefix="/config", tags=["config"])


@router.get("/get")
async def get_config(token: str = Depends(verify_panel_token)):
    """获取当前配置"""
    try:


        # 读取当前配置（包括环境变量和TOML文件中的配置）
        current_config = {}

        # 基础配置
        current_config["credentials_dir"] = await config.get_credentials_dir()
        current_config["proxy"] = await config.get_proxy_config() or ""

        # 代理端点配置
        current_config["oauth_proxy_url"] = await config.get_oauth_proxy_url()
        current_config["googleapis_proxy_url"] = await config.get_googleapis_proxy_url()
        current_config["resource_manager_api_url"] = await config.get_resource_manager_api_url()
        current_config["service_usage_api_url"] = await config.get_service_usage_api_url()
        current_config["antigravity_api_url"] = await config.get_antigravity_api_url()

        # 自动封禁配置
        current_config["auto_ban_enabled"] = await config.get_auto_ban_enabled()
        current_config["auto_ban_error_codes"] = await config.get_auto_ban_error_codes()

        # 429重试配置
        current_config["retry_429_max_retries"] = await config.get_retry_429_max_retries()
        current_config["retry_429_enabled"] = await config.get_retry_429_enabled()
        current_config["retry_429_interval"] = await config.get_retry_429_interval()
        # 抗截断配置
        current_config["anti_truncation_max_attempts"] = await config.get_anti_truncation_max_attempts()

        # 兼容性配置

        # 思维链返回配置
        current_config["return_thoughts_to_frontend"] = await config.get_return_thoughts_to_frontend()

        # 敏感词过滤配置
        current_config["sensitive_words_enabled"] = await config.get_sensitive_words_enabled()
        current_config["sensitive_words"] = await config.get_sensitive_words()

        # IP 白名单配置
        current_config["ip_whitelist_enabled"] = await config.get_ip_whitelist_enabled()
        current_config["ip_whitelist"] = await config.get_ip_whitelist()

        # 请求日志配置
        current_config["usage_log_enabled"] = await config.get_usage_log_enabled()

        # 凭证选择模式
        current_config["credential_select_mode"] = await config.get_credential_select_mode()

        # 服务器配置
        current_config["host"] = await config.get_server_host()
        current_config["port"] = await config.get_server_port()
        current_config["api_password"] = await config.get_api_password()
        current_config["panel_password"] = await config.get_panel_password()

        # 从数据库读取配置
        database = await get_database()
        storage_config = await database.get_all_config()

        # 获取环境变量锁定的配置键
        env_locked_keys = get_env_locked_keys()

        # 合并存储系统配置（不覆盖环境变量）
        for key, value in storage_config.items():
            if key not in env_locked_keys:
                current_config[key] = value

        return JSONResponse(content={"config": current_config, "env_locked": list(env_locked_keys)})

    except Exception as e:
        log.error(f"获取配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save")
async def save_config(request: ConfigSaveRequest, token: str = Depends(verify_panel_token)):
    """保存配置"""
    try:

        new_config = request.config

        log.debug(f"收到的配置数据: {list(new_config.keys())}")

        # 验证配置项
        if "retry_429_max_retries" in new_config:
            if (
                not isinstance(new_config["retry_429_max_retries"], int)
                or new_config["retry_429_max_retries"] < 0
            ):
                raise HTTPException(status_code=400, detail="最大429重试次数必须是大于等于0的整数")

        if "retry_429_enabled" in new_config:
            if not isinstance(new_config["retry_429_enabled"], bool):
                raise HTTPException(status_code=400, detail="429重试开关必须是布尔值")

        # 验证新的配置项
        if "retry_429_interval" in new_config:
            try:
                interval = float(new_config["retry_429_interval"])
                if interval < 0 or interval > 3600:
                    raise HTTPException(status_code=400, detail="429重试间隔必须在0-3600秒之间")
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="429重试间隔必须是有效的数字")

        if "anti_truncation_max_attempts" in new_config:
            if (
                not isinstance(new_config["anti_truncation_max_attempts"], int)
                or new_config["anti_truncation_max_attempts"] < 1
                or new_config["anti_truncation_max_attempts"] > 10
            ):
                raise HTTPException(
                    status_code=400, detail="抗截断最大重试次数必须是1-10之间的整数"
                )

        if "return_thoughts_to_frontend" in new_config:
            if not isinstance(new_config["return_thoughts_to_frontend"], bool):
                raise HTTPException(status_code=400, detail="思维链返回开关必须是布尔值")

        # 验证敏感词过滤配置
        if "sensitive_words_enabled" in new_config:
            if not isinstance(new_config["sensitive_words_enabled"], bool):
                raise HTTPException(status_code=400, detail="敏感词过滤开关必须是布尔值")

        if "sensitive_words" in new_config:
            words = new_config["sensitive_words"]
            if not isinstance(words, list):
                raise HTTPException(status_code=400, detail="敏感词列表必须是数组（每行一个词）")
            import re as _re

            cleaned_words = []
            for word in words:
                if not isinstance(word, str):
                    raise HTTPException(status_code=400, detail=f"敏感词必须是字符串: {word!r}")
                word = word.strip()
                if not word:
                    continue
                try:
                    _re.compile(_re.escape(word), _re.IGNORECASE | _re.UNICODE)
                except _re.error:
                    raise HTTPException(status_code=400, detail=f"无效的敏感词: {word!r}")
                cleaned_words.append(word)
            new_config["sensitive_words"] = cleaned_words

        # 验证 IP 白名单配置（每行一条，支持单 IP / CIDR / 起止范围，IPv4 / IPv6）
        if "ip_whitelist_enabled" in new_config:
            if not isinstance(new_config["ip_whitelist_enabled"], bool):
                raise HTTPException(status_code=400, detail="IP 白名单开关必须是布尔值")

        if "ip_whitelist" in new_config:
            entries = new_config["ip_whitelist"]
            if not isinstance(entries, list):
                raise HTTPException(status_code=400, detail="IP 白名单必须是数组（每行一条）")
            cleaned_entries = []
            for entry in entries:
                if not isinstance(entry, str):
                    raise HTTPException(status_code=400, detail=f"IP 白名单条目必须是字符串: {entry!r}")
                entry = entry.strip()
                if not entry:
                    continue
                try:
                    config.parse_ip_whitelist_line(entry)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"无效的 IP 白名单条目: {entry!r}")
                cleaned_entries.append(entry)
            new_config["ip_whitelist"] = cleaned_entries

        # 验证请求日志配置
        if "usage_log_enabled" in new_config:
            if not isinstance(new_config["usage_log_enabled"], bool):
                raise HTTPException(status_code=400, detail="请求日志开关必须是布尔值")

        # 验证凭证选择模式
        if "credential_select_mode" in new_config:
            mode = new_config["credential_select_mode"]
            if mode not in ("random", "cache", "sticky"):
                raise HTTPException(status_code=400, detail="凭证选择模式必须是 random / cache / sticky")

        # 验证服务器配置
        if "host" in new_config:
            if not isinstance(new_config["host"], str) or not new_config["host"].strip():
                raise HTTPException(status_code=400, detail="服务器主机地址不能为空")

        if "port" in new_config:
            if (
                not isinstance(new_config["port"], int)
                or new_config["port"] < 1
                or new_config["port"] > 65535
            ):
                raise HTTPException(status_code=400, detail="端口号必须是1-65535之间的整数")

        if "api_password" in new_config:
            if not isinstance(new_config["api_password"], str):
                raise HTTPException(status_code=400, detail="API访问密码必须是字符串")

        if "panel_password" in new_config:
            if not isinstance(new_config["panel_password"], str):
                raise HTTPException(status_code=400, detail="控制面板密码必须是字符串")

        # 获取环境变量锁定的配置键
        env_locked_keys = get_env_locked_keys()

        # 直接写入数据库
        database = await get_database()
        for key, value in new_config.items():
            if key not in env_locked_keys:
                await database.set_config(key, value)
                if key in ("api_password", "panel_password"):
                    log.debug(f"设置{key}字段为: {value}")

        # 重新加载配置缓存（关键！）
        await config.reload_config()

        # 验证保存后的结果
        test_api_password = await config.get_api_password()
        test_panel_password = await config.get_panel_password()
        log.debug(f"保存后立即读取的API密码: {test_api_password}")
        log.debug(f"保存后立即读取的面板密码: {test_panel_password}")

        # 构建响应消息
        response_data = {
            "message": "配置保存成功",
            "saved_config": {k: v for k, v in new_config.items() if k not in env_locked_keys},
        }

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"保存配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

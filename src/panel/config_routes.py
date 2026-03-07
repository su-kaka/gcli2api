"""
配置路由模块 - 处理 /config/* 相关的HTTP请求
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

import config
from log import log
from src.keeplive import keepalive_service
from src.models import ConfigSaveRequest
from src.storage_adapter import get_storage_adapter
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
        current_config["code_assist_endpoint"] = await config.get_code_assist_endpoint()
        current_config["credentials_dir"] = await config.get_credentials_dir()
        current_config["proxy"] = await config.get_proxy_config() or ""

        # 代理端点配置
        current_config["oauth_proxy_url"] = await config.get_oauth_proxy_url()
        current_config["googleapis_proxy_url"] = await config.get_googleapis_proxy_url()
        current_config[
            "resource_manager_api_url"
        ] = await config.get_resource_manager_api_url()
        current_config[
            "service_usage_api_url"
        ] = await config.get_service_usage_api_url()
        current_config["antigravity_api_url"] = await config.get_antigravity_api_url()

        # 自动封禁配置
        current_config["auto_ban_enabled"] = await config.get_auto_ban_enabled()
        current_config["auto_ban_error_codes"] = await config.get_auto_ban_error_codes()

        # 429重试配置
        current_config[
            "retry_429_max_retries"
        ] = await config.get_retry_429_max_retries()
        current_config["retry_429_enabled"] = await config.get_retry_429_enabled()
        current_config["retry_429_interval"] = await config.get_retry_429_interval()
        # 抗截断配置
        current_config[
            "anti_truncation_max_attempts"
        ] = await config.get_anti_truncation_max_attempts()

        # 兼容性配置
        current_config[
            "compatibility_mode_enabled"
        ] = await config.get_compatibility_mode_enabled()

        # 思维链返回配置
        current_config[
            "return_thoughts_to_frontend"
        ] = await config.get_return_thoughts_to_frontend()

        # Antigravity流式转非流式配置
        current_config[
            "antigravity_stream2nostream"
        ] = await config.get_antigravity_stream2nostream()

        # 灰度发布与回滚门禁配置
        current_config["ff_retry_policy_v2"] = await config.get_ff_retry_policy_v2()
        current_config["ff_http2_pool_tuning"] = await config.get_ff_http2_pool_tuning()
        current_config[
            "ff_converter_fast_path"
        ] = await config.get_ff_converter_fast_path()
        current_config[
            "ff_preview_credential_scheduler_v2"
        ] = await config.get_ff_preview_credential_scheduler_v2()
        current_config[
            "rollout_stage_percent"
        ] = await config.get_rollout_stage_percent()
        current_config[
            "rollback_trigger_latency_p95_ms"
        ] = await config.get_rollback_trigger_latency_p95_ms()
        current_config[
            "rollback_trigger_throughput_drop_pct"
        ] = await config.get_rollback_trigger_throughput_drop_pct()
        current_config[
            "rollback_trigger_quality_drop_pct"
        ] = await config.get_rollback_trigger_quality_drop_pct()

        # 保活配置
        current_config["keepalive_url"] = await config.get_keepalive_url()
        current_config["keepalive_interval"] = await config.get_keepalive_interval()

        # 服务器配置
        current_config["host"] = await config.get_server_host()
        current_config["port"] = await config.get_server_port()
        current_config["api_password"] = await config.get_api_password()
        current_config["panel_password"] = await config.get_panel_password()
        current_config["password"] = await config.get_server_password()

        # 从存储系统读取配置
        storage_adapter = await get_storage_adapter()
        storage_config = await storage_adapter.get_all_config()

        # 获取环境变量锁定的配置键
        env_locked_keys = get_env_locked_keys()

        # 合并存储系统配置（不覆盖环境变量）
        for key, value in storage_config.items():
            if key not in env_locked_keys:
                current_config[key] = value

        return JSONResponse(
            content={"config": current_config, "env_locked": list(env_locked_keys)}
        )

    except Exception as e:
        log.error(f"获取配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save")
async def save_config(
    request: ConfigSaveRequest, token: str = Depends(verify_panel_token)
):
    """保存配置"""
    try:
        new_config = request.config

        log.debug(f"收到的配置数据: {list(new_config.keys())}")
        log.debug(f"收到的password值: {new_config.get('password', 'NOT_FOUND')}")

        # 验证配置项
        if "retry_429_max_retries" in new_config:
            if (
                not isinstance(new_config["retry_429_max_retries"], int)
                or new_config["retry_429_max_retries"] < 0
            ):
                raise HTTPException(
                    status_code=400, detail="最大429重试次数必须是大于等于0的整数"
                )

        if "retry_429_enabled" in new_config:
            if not isinstance(new_config["retry_429_enabled"], bool):
                raise HTTPException(status_code=400, detail="429重试开关必须是布尔值")

        # 验证新的配置项
        if "retry_429_interval" in new_config:
            try:
                interval = float(new_config["retry_429_interval"])
                if interval < 0.01 or interval > 10:
                    raise HTTPException(
                        status_code=400, detail="429重试间隔必须在0.01-10秒之间"
                    )
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=400, detail="429重试间隔必须是有效的数字"
                )

        if "anti_truncation_max_attempts" in new_config:
            if (
                not isinstance(new_config["anti_truncation_max_attempts"], int)
                or new_config["anti_truncation_max_attempts"] < 1
                or new_config["anti_truncation_max_attempts"] > 10
            ):
                raise HTTPException(
                    status_code=400, detail="抗截断最大重试次数必须是1-10之间的整数"
                )

        if "compatibility_mode_enabled" in new_config:
            if not isinstance(new_config["compatibility_mode_enabled"], bool):
                raise HTTPException(
                    status_code=400, detail="兼容性模式开关必须是布尔值"
                )

        if "return_thoughts_to_frontend" in new_config:
            if not isinstance(new_config["return_thoughts_to_frontend"], bool):
                raise HTTPException(
                    status_code=400, detail="思维链返回开关必须是布尔值"
                )

        if "antigravity_stream2nostream" in new_config:
            if not isinstance(new_config["antigravity_stream2nostream"], bool):
                raise HTTPException(
                    status_code=400, detail="Antigravity流式转非流式开关必须是布尔值"
                )

        feature_flag_names = [
            "ff_retry_policy_v2",
            "ff_http2_pool_tuning",
            "ff_converter_fast_path",
            "ff_preview_credential_scheduler_v2",
        ]
        for feature_flag_name in feature_flag_names:
            if feature_flag_name in new_config and not isinstance(
                new_config[feature_flag_name], bool
            ):
                raise HTTPException(
                    status_code=400, detail=f"{feature_flag_name} 必须是布尔值"
                )

        if "rollout_stage_percent" in new_config:
            try:
                rollout_stage = int(new_config["rollout_stage_percent"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="灰度比例必须是有效整数")
            if rollout_stage not in (5, 20, 50, 100):
                raise HTTPException(
                    status_code=400, detail="灰度比例仅支持 5/20/50/100"
                )
            new_config["rollout_stage_percent"] = rollout_stage

        if "rollback_trigger_latency_p95_ms" in new_config:
            try:
                latency_p95_ms = float(new_config["rollback_trigger_latency_p95_ms"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="延迟阈值必须是有效数字")
            if latency_p95_ms < 0 or latency_p95_ms > 60000:
                raise HTTPException(
                    status_code=400, detail="延迟阈值必须在 0-60000 ms 之间"
                )
            new_config["rollback_trigger_latency_p95_ms"] = latency_p95_ms

        if "rollback_trigger_throughput_drop_pct" in new_config:
            try:
                throughput_drop_pct = float(
                    new_config["rollback_trigger_throughput_drop_pct"]
                )
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="吞吐下降阈值必须是有效数字"
                )
            if throughput_drop_pct < 0 or throughput_drop_pct > 100:
                raise HTTPException(
                    status_code=400, detail="吞吐下降阈值必须在 0-100 之间"
                )
            new_config["rollback_trigger_throughput_drop_pct"] = throughput_drop_pct

        if "rollback_trigger_quality_drop_pct" in new_config:
            try:
                quality_drop_pct = float(
                    new_config["rollback_trigger_quality_drop_pct"]
                )
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="质量下降阈值必须是有效数字"
                )
            if quality_drop_pct < 0 or quality_drop_pct > 100:
                raise HTTPException(
                    status_code=400, detail="质量下降阈值必须在 0-100 之间"
                )
            new_config["rollback_trigger_quality_drop_pct"] = quality_drop_pct

        # 验证保活配置
        if "keepalive_url" in new_config:
            if not isinstance(new_config["keepalive_url"], str):
                raise HTTPException(status_code=400, detail="保活URL必须是字符串")

        if "keepalive_interval" in new_config:
            try:
                interval = int(new_config["keepalive_interval"])
                if interval < 5 or interval > 86400:
                    raise HTTPException(
                        status_code=400, detail="保活间隔必须在 5-86400 秒之间"
                    )
                new_config["keepalive_interval"] = interval
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="保活间隔必须是有效整数")
        # 验证服务器配置
        if "host" in new_config:
            if (
                not isinstance(new_config["host"], str)
                or not new_config["host"].strip()
            ):
                raise HTTPException(status_code=400, detail="服务器主机地址不能为空")

        if "port" in new_config:
            if (
                not isinstance(new_config["port"], int)
                or new_config["port"] < 1
                or new_config["port"] > 65535
            ):
                raise HTTPException(
                    status_code=400, detail="端口号必须是1-65535之间的整数"
                )

        if "api_password" in new_config:
            if not isinstance(new_config["api_password"], str):
                raise HTTPException(status_code=400, detail="API访问密码必须是字符串")

        if "panel_password" in new_config:
            if not isinstance(new_config["panel_password"], str):
                raise HTTPException(status_code=400, detail="控制面板密码必须是字符串")

        if "password" in new_config:
            if not isinstance(new_config["password"], str):
                raise HTTPException(status_code=400, detail="访问密码必须是字符串")

        # 获取环境变量锁定的配置键
        env_locked_keys = get_env_locked_keys()

        # 直接使用存储适配器保存配置
        storage_adapter = await get_storage_adapter()
        for key, value in new_config.items():
            if key not in env_locked_keys:
                await storage_adapter.set_config(key, value)
                if key in ("password", "api_password", "panel_password"):
                    log.debug(f"设置{key}字段为: {value}")

        # 重新加载配置缓存（关键！）
        await config.reload_config()

        # 如果保活相关配置发生变化，立即重启保活服务
        keepalive_keys = {"keepalive_url", "keepalive_interval"}
        if keepalive_keys & set(new_config.keys()):
            try:
                await keepalive_service.restart()
            except Exception as e:
                log.warning(f"重启保活服务失败: {e}")

        # 验证保存后的结果
        test_api_password = await config.get_api_password()
        test_panel_password = await config.get_panel_password()
        test_password = await config.get_server_password()
        log.debug(f"保存后立即读取的API密码: {test_api_password}")
        log.debug(f"保存后立即读取的面板密码: {test_panel_password}")
        log.debug(f"保存后立即读取的通用密码: {test_password}")

        # 构建响应消息
        response_data = {
            "message": "配置保存成功",
            "saved_config": {
                k: v for k, v in new_config.items() if k not in env_locked_keys
            },
        }

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"保存配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

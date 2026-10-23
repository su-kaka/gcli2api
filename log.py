"""
日志模块 - 纯控制台输出（无状态函数风格）
- LOG_LEVEL 控制级别（debug/info/warning/error/critical，默认 info）
- ENABLE_LOG=0 彻底静默（不打印任何日志）
- log.debug/info/warning/error/critical(message) 记录日志
- set_log_level 动态调整级别，get_log_level 读取当前级别
"""

import os
import sys
from datetime import datetime
from types import SimpleNamespace

# 日志级别定义
LOG_LEVELS = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}

# 缓存配置（模块加载时读取一次）
_cached_log_level: int = LOG_LEVELS["info"]
_log_enabled: bool = True


def _refresh_config():
    """从环境变量刷新缓存配置"""
    global _cached_log_level, _log_enabled
    level = os.getenv("LOG_LEVEL", "info").lower()
    _cached_log_level = LOG_LEVELS.get(level, LOG_LEVELS["info"])
    _log_enabled = os.getenv("ENABLE_LOG", "1").strip().lower() not in ("0", "false", "no", "off")


_refresh_config()


def _log(level: str, message: str):
    # 最快短路：日志整体已禁用时直接返回，零开销
    if not _log_enabled:
        return

    level_val = LOG_LEVELS.get(level.lower())
    if level_val is None:
        return

    if level_val < _cached_log_level:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{level.upper()}] {message}"

    if level in ("error", "critical"):
        print(entry, file=sys.stderr)
    else:
        print(entry)


def set_log_level(level: str):
    """动态设置日志级别"""
    global _cached_log_level
    level = level.lower()
    if level not in LOG_LEVELS:
        return False
    _cached_log_level = LOG_LEVELS[level]
    return True


def get_log_level() -> str:
    """获取当前日志级别名称"""
    current = _cached_log_level
    for name, value in LOG_LEVELS.items():
        if value == current:
            return name
    return "info"


# 日志接口：log.debug/info/warning/error/critical(message)
log = SimpleNamespace(
    debug=lambda message: _log("debug", message),
    info=lambda message: _log("info", message),
    warning=lambda message: _log("warning", message),
    error=lambda message: _log("error", message),
    critical=lambda message: _log("critical", message),
)


# 导出的公共接口
__all__ = ["log", "set_log_level", "get_log_level", "LOG_LEVELS"]

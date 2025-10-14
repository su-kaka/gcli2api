"""
日志模块 - 使用环境变量配置
"""

import os
import sys
import threading
import logging
from datetime import datetime

try:
    import colorlog
except ImportError:
    colorlog = None

# 日志级别映射
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

# 线程锁，用于文件写入同步
_file_lock = threading.Lock()

# 文件写入状态标志
_file_writing_disabled = False
_disable_reason = None

# 获取根 logger
_logger = logging.getLogger()


def setup_logging():
    """
    配置日志系统。此函数应在加载环境变量后显式调用。
    """
    # 确保只配置一次
    if _logger.handlers:
        return

    _logger.setLevel(logging.DEBUG)  # 设置最低级别，由 handler 控制

    # --- 控制台 Handler ---
    console_handler = None
    if colorlog:
        console_formatter = colorlog.ColoredFormatter(
            "%(log_color)s[%(asctime)s] [%(levelname)s]%(reset)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
        console_handler = colorlog.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
    else:
        console_formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)

    # 设置控制台 handler 的级别
    console_handler.setLevel(_get_current_log_level())
    _logger.addHandler(console_handler)

    # --- 文件 Handler ---
    if os.getenv("LOG_FILE"):
        file_handler = SafeFileHandler()
        _logger.addHandler(file_handler)

    _logger.info(
        f"Logging configured. Level set to '{_get_current_log_level_name().upper()}' from environment."
    )


def _get_current_log_level_name():
    """获取当前日志级别名称"""
    return os.getenv("LOG_LEVEL", "info").lower().strip()


def _get_current_log_level():
    """获取当前日志级别"""
    level_name = _get_current_log_level_name()
    return LOG_LEVELS.get(level_name, logging.INFO)


def _get_log_file_path():
    """获取日志文件路径"""
    return os.getenv("LOG_FILE", "log.txt")


# --- 文件 Handler ---
def _truncate_message(message, max_length=1000, head=200, tail=200):
    """如果消息过长，则截断消息"""
    if len(message) > max_length:
        return f"{message[:head]}...[truncated]...{message[-tail:]}"
    return message


class SafeFileHandler(logging.Handler):
    """线程安全且能处理文件系统只读错误的 FileHandler"""

    def __init__(self):
        super().__init__()
        self.formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        self.setLevel(logging.DEBUG)  # 文件记录所有级别的日志

    def emit(self, record):
        global _file_writing_disabled, _disable_reason
        if _file_writing_disabled:
            return

        try:
            log_file = _get_log_file_path()
            msg = self.format(record)
            # 截断过长的消息
            truncated_msg = _truncate_message(msg)
            with _file_lock:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(truncated_msg + "\n")
                    f.flush()
        except (PermissionError, OSError, IOError) as e:
            _file_writing_disabled = True
            _disable_reason = str(e)
            # 使用 logging 记录禁用信息，避免无限循环
            _logger.warning(
                f"File system appears to be read-only or permission denied. Disabling log file writing: {e}"
            )
            _logger.warning("Log messages will continue to display in console only.")
        except Exception as e:
            _logger.warning(f"Failed to write to log file: {e}")


def _log(level: str, message: str):
    """内部日志函数，现在代理到 logging"""
    level = level.lower()
    log_level = LOG_LEVELS.get(level)
    if log_level is not None:
        _logger.log(log_level, message)
    else:
        _logger.warning(f"Unknown log level '{level}'")


def set_log_level(level: str):
    """动态设置所有处理器的日志级别"""
    level = level.lower()
    log_level = LOG_LEVELS.get(level)

    if log_level is None:
        _logger.warning(
            f"Attempted to set an unknown log level: '{level}'. Valid levels are: {', '.join(LOG_LEVELS.keys())}"
        )
        return False

    # 更新所有处理器的级别
    for handler in _logger.handlers:
        handler.setLevel(log_level)

    _logger.info(f"Log level dynamically set to '{level.upper()}'")
    return True


class Logger:
    """支持 log('info', 'msg') 和 log.info('msg') 两种调用方式"""

    def __call__(self, level: str, message: str):
        """支持 log('info', 'message') 调用方式"""
        _log(level, message)

    def debug(self, message: str, *args, **kwargs):
        """记录调试信息"""
        _logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args, **kwargs):
        """记录一般信息"""
        _logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs):
        """记录警告信息"""
        _logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs):
        """记录错误信息"""
        _logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args, **kwargs):
        """记录严重错误信息"""
        _logger.critical(message, *args, **kwargs)

    def get_current_level(self) -> str:
        """获取当前日志级别名称"""
        return _get_current_log_level_name()

    def get_log_file(self) -> str:
        """获取当前日志文件路径"""
        if _file_writing_disabled:
            return f"File writing disabled: {_disable_reason}"
        return _get_log_file_path()


# 导出全局日志实例
log = Logger()

# 导出的公共接口
__all__ = ["log", "set_log_level", "setup_logging", "LOG_LEVELS"]

# 使用说明:
# 1. 设置日志级别: export LOG_LEVEL=debug (或在.env文件中设置)
# 2. 设置日志文件: export LOG_FILE=log.txt (或在.env文件中设置)

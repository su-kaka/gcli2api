"""
日志模块 - 使用环境变量配置
"""
import os
import sys
import threading
from datetime import datetime

# 日志级别定义
LOG_LEVELS = {
    'debug': 0,
    'info': 1,
    'warning': 2,
    'error': 3,
    'critical': 4
}

# 线程锁，用于文件写入同步
_file_lock = threading.Lock()

# 文件写入状态标志
_file_writing_disabled = False
_disable_reason = None

def _get_current_log_level():
    """获取当前日志级别"""
    level = os.getenv('LOG_LEVEL', 'info').lower()
    return LOG_LEVELS.get(level, LOG_LEVELS['info'])

def _get_log_file_path():
    """获取日志文件路径"""
    return os.getenv('LOG_FILE', 'log.txt')

def _write_to_file(message: str):
    """线程安全地写入日志文件"""
    global _file_writing_disabled, _disable_reason
    
    # 如果文件写入已被禁用，直接返回
    if _file_writing_disabled:
        return
    
    try:
        log_file = _get_log_file_path()
        with _file_lock:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(message + '\n')
                f.flush()  # 强制刷新到磁盘，确保实时写入
    except (PermissionError, OSError, IOError) as e:
        # 检测只读文件系统或权限问题，禁用文件写入
        _file_writing_disabled = True
        _disable_reason = str(e)
        print(f"Warning: File system appears to be read-only or permission denied. Disabling log file writing: {e}", file=sys.stderr)
        print(f"Log messages will continue to display in console only.", file=sys.stderr)
    except Exception as e:
        # 其他异常仍然输出警告但不禁用写入（可能是临时问题）
        print(f"Warning: Failed to write to log file: {e}", file=sys.stderr)

def _log(level: str, message: str):
    """
    内部日志函数
    """
    level = level.lower()
    if level not in LOG_LEVELS:
        print(f"Warning: Unknown log level '{level}'", file=sys.stderr)
        return
    
    # 检查日志级别
    current_level = _get_current_log_level()
    if LOG_LEVELS[level] < current_level:
        return
    
    # 格式化日志消息
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{level.upper()}] {message}"
    
    # 输出到控制台
    if level in ('error', 'critical'):
        print(entry, file=sys.stderr)
    else:
        print(entry)
    
    # 实时写入文件
    _write_to_file(entry)

def set_log_level(level: str):
    """设置日志级别提示"""
    level = level.lower()
    if level not in LOG_LEVELS:
        print(f"Warning: Unknown log level '{level}'. Valid levels: {', '.join(LOG_LEVELS.keys())}")
        return False
    
    print(f"Note: To set log level '{level}', please set LOG_LEVEL environment variable")
    return True

class Logger:
    """支持 log('info', 'msg') 和 log.info('msg') 两种调用方式"""
    
    def __call__(self, level: str, message: str):
        """支持 log('info', 'message') 调用方式"""
        _log(level, message)

    def debug(self, message: str):
        """记录调试信息"""
        _log('debug', message)
    
    def info(self, message: str):
        """记录一般信息"""
        _log('info', message)
    
    def warning(self, message: str):
        """记录警告信息"""
        _log('warning', message)
    
    def error(self, message: str):
        """记录错误信息"""
        _log('error', message)
    
    def critical(self, message: str):
        """记录严重错误信息"""
        _log('critical', message)
    
    def get_current_level(self) -> str:
        """获取当前日志级别名称"""
        current_level = _get_current_log_level()
        for name, value in LOG_LEVELS.items():
            if value == current_level:
                return name
        return 'info'
    
    def get_log_file(self) -> str:
        """获取当前日志文件路径"""
        return _get_log_file_path()
    

# 导出全局日志实例
log = Logger()

# 导出的公共接口
__all__ = ['log', 'set_log_level', 'LOG_LEVELS']

# 使用说明:
# 1. 设置日志级别: export LOG_LEVEL=debug (或在.env文件中设置)
# 2. 设置日志文件: export LOG_FILE=log.txt (或在.env文件中设置)
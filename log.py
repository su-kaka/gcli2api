"""
日志模块 - 使用环境变量配置
"""

import os
import sys
import threading
from datetime import datetime
from queue import Queue, Empty
import atexit

# 日志级别定义
LOG_LEVELS = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}

# 线程锁，用于文件写入同步
_file_lock = threading.Lock()

# 文件写入状态标志
_file_writing_disabled = False
_disable_reason = None

# 全局文件句柄和写入计数器
_log_file_handle = None
_write_counter = 0
_max_writes_before_reopen = 1000  # 每1000次写入后重新打开文件，防止句柄泄漏

# 异步写入相关
_log_queue = Queue(maxsize=500)  # 日志队列，最多缓存500条
_writer_thread = None
_writer_running = False
_shutdown_event = threading.Event()


def _get_current_log_level():
    """获取当前日志级别"""
    level = os.getenv("LOG_LEVEL", "info").lower()
    return LOG_LEVELS.get(level, LOG_LEVELS["info"])


def _get_log_file_path():
    """获取日志文件路径"""
    return os.getenv("LOG_FILE", "log.txt")


def _close_log_file():
    """关闭日志文件句柄"""
    global _log_file_handle

    if _log_file_handle is not None:
        try:
            _log_file_handle.flush()  # 确保数据写入磁盘
            _log_file_handle.close()
        except Exception:
            pass  # 忽略关闭时的异常
        finally:
            _log_file_handle = None


def _open_log_file(mode="a"):
    """打开日志文件句柄"""
    global _log_file_handle, _file_writing_disabled, _disable_reason, _write_counter

    # 先关闭旧的句柄（确保在任何情况下都关闭）
    try:
        _close_log_file()
    except Exception:
        pass
    _write_counter = 0  # 重置计数器

    try:
        log_file = _get_log_file_path()
        _log_file_handle = open(log_file, mode, encoding="utf-8", buffering=1)  # 行缓冲
        return True
    except (PermissionError, OSError, IOError) as e:
        _file_writing_disabled = True
        _disable_reason = str(e)
        print(
            f"Warning: Cannot open log file. Disabling log file writing: {e}",
            file=sys.stderr,
        )
        print("Log messages will continue to display in console only.", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Warning: Failed to open log file: {e}", file=sys.stderr)
        return False


def _clear_log_file():
    """清空日志文件（在启动时调用）"""
    global _file_writing_disabled, _disable_reason

    try:
        log_file = _get_log_file_path()
        with _file_lock:
            # 先关闭现有句柄
            _close_log_file()
            # 使用独立的文件句柄清空，不影响全局句柄
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("")  # 清空文件
                f.flush()  # 确保写入磁盘
            # 清空后打开用于追加写入
            _open_log_file("a")
    except (PermissionError, OSError, IOError) as e:
        # 检测只读文件系统或权限问题，禁用文件写入
        _file_writing_disabled = True
        _disable_reason = str(e)
        print(
            f"Warning: File system appears to be read-only or permission denied. "
            f"Disabling log file writing: {e}",
            file=sys.stderr,
        )
        print("Log messages will continue to display in console only.", file=sys.stderr)
    except Exception as e:
        # 其他异常仍然输出警告但不禁用写入（可能是临时问题）
        print(f"Warning: Failed to clear log file: {e}", file=sys.stderr)


def _write_to_file_sync(message: str):
    """同步写入日志文件（内部使用）"""
    global _file_writing_disabled, _disable_reason, _log_file_handle, _write_counter

    # 如果文件写入已被禁用，直接返回
    if _file_writing_disabled:
        return False

    with _file_lock:
        try:
            # 如果文件句柄不存在或达到重开阈值，重新打开
            if _log_file_handle is None or _write_counter >= _max_writes_before_reopen:
                if not _open_log_file("a"):
                    return False  # 打开失败，直接返回

            # 写入日志
            _log_file_handle.write(message + "\n")
            _write_counter += 1

            # 每10条日志刷新一次，平衡性能和实时性
            if _write_counter % 10 == 0:
                _log_file_handle.flush()

            return True

        except (PermissionError, OSError, IOError) as e:
            # 检测只读文件系统或权限问题，禁用文件写入
            _file_writing_disabled = True
            _disable_reason = str(e)
            _close_log_file()  # 关闭句柄
            print(
                f"Warning: File system appears to be read-only or permission denied. "
                f"Disabling log file writing: {e}",
                file=sys.stderr,
            )
            print("Log messages will continue to display in console only.", file=sys.stderr)
            return False
        except Exception as e:
            # 其他异常尝试重新打开文件句柄（自动恢复机制）
            print(f"Warning: Failed to write to log file: {e}. Attempting to reopen...", file=sys.stderr)
            _close_log_file()
            try:
                _open_log_file("a")
            except Exception:
                pass  # 如果重新打开失败，静默处理
            return False


def _log_writer_worker():
    """后台日志写入线程"""
    global _writer_running

    while _writer_running or not _log_queue.empty():
        try:
            # 从队列获取日志消息，超时0.1秒
            message = _log_queue.get(timeout=0.1)
            _write_to_file_sync(message)
            _log_queue.task_done()
        except Empty:
            # 队列为空，检查是否需要退出
            if _shutdown_event.is_set() and _log_queue.empty():
                break
            continue
        except Exception as e:
            print(f"Error in log writer thread: {e}", file=sys.stderr)

    # 退出前强制刷新
    if _log_file_handle:
        try:
            _log_file_handle.flush()
        except Exception:
            pass


def _start_writer_thread():
    """启动异步写入线程"""
    global _writer_thread, _writer_running

    if _writer_thread is None or not _writer_thread.is_alive():
        _writer_running = True
        _shutdown_event.clear()
        _writer_thread = threading.Thread(target=_log_writer_worker, daemon=True, name="LogWriter")
        _writer_thread.start()


def _stop_writer_thread():
    """停止异步写入线程"""
    global _writer_running

    _writer_running = False
    _shutdown_event.set()

    # 等待队列处理完成
    try:
        _log_queue.join()  # 等待所有任务完成
    except Exception:
        pass

    # 等待线程退出
    if _writer_thread and _writer_thread.is_alive():
        _writer_thread.join(timeout=2.0)

    # 关闭文件句柄
    _close_log_file()


def _write_to_file(message: str):
    """异步写入日志文件（放入队列）"""
    # 如果文件写入已被禁用，直接返回
    if _file_writing_disabled:
        return

    try:
        # 非阻塞放入队列，如果队列满了就丢弃（防止内存溢出）
        _log_queue.put_nowait(message)
    except Exception:
        # 队列满了，直接打印警告（不阻塞主程序）
        pass


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
    if level in ("error", "critical"):
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
        _log("debug", message)

    def info(self, message: str):
        """记录一般信息"""
        _log("info", message)

    def warning(self, message: str):
        """记录警告信息"""
        _log("warning", message)

    def error(self, message: str):
        """记录错误信息"""
        _log("error", message)

    def critical(self, message: str):
        """记录严重错误信息"""
        _log("critical", message)

    def get_current_level(self) -> str:
        """获取当前日志级别名称"""
        current_level = _get_current_log_level()
        for name, value in LOG_LEVELS.items():
            if value == current_level:
                return name
        return "info"

    def get_log_file(self) -> str:
        """获取当前日志文件路径"""
        return _get_log_file_path()

    def close(self):
        """手动关闭日志文件句柄（可选，用于优雅退出）"""
        _stop_writer_thread()

    def get_queue_size(self) -> int:
        """获取当前队列中待写入的日志数量"""
        return _log_queue.qsize()


# 导出全局日志实例
log = Logger()

# 导出的公共接口
__all__ = ["log", "set_log_level", "LOG_LEVELS"]

# 在模块加载时清空日志文件并启动写入线程
_clear_log_file()
_start_writer_thread()

# 注册退出时清理函数
atexit.register(_stop_writer_thread)

# 使用说明:
# 1. 设置日志级别: export LOG_LEVEL=debug (或在.env文件中设置)
# 2. 设置日志文件: export LOG_FILE=log.txt (或在.env文件中设置)
# 3. 文件句柄会自动管理，每1000次写入后自动重新打开以防止泄漏
# 4. 日志采用异步写入，对主程序性能影响极小
# 5. 队列最多缓存500条日志，超出时会丢弃新日志（防止内存溢出）

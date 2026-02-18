"""
日志模块 - 使用环境变量配置
"""

import os
import sys
import threading
from datetime import datetime
from collections import deque
import atexit

# 日志级别定义
LOG_LEVELS = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}

# 文件写入状态标志（仅由 writer 线程修改，无需锁保护）
_file_writing_disabled = False
_disable_reason = None

# 全局文件句柄（仅由 writer 线程访问，无需文件锁）
_log_file_handle = None

# -----------------------------------------------------------------
# 高性能无锁队列：用 deque + Condition 替代 Queue
# deque.append / deque.popleft 在 CPython 中受 GIL 保护，是原子操作，
# 不需要额外的 Lock 做入队保护，只用 Condition 做"有数据"通知。
# -----------------------------------------------------------------
_log_deque: deque = deque()
_deque_condition = threading.Condition(threading.Lock())
_writer_thread = None
_writer_running = False

# -----------------------------------------------------------------
# 缓存日志级别，避免每次都读 os.getenv（高并发热路径）
# -----------------------------------------------------------------
_cached_log_level: int = LOG_LEVELS["info"]
_cached_log_file: str = "log.txt"


def _refresh_config():
    """从环境变量刷新缓存配置（模块加载时及需要时调用）"""
    global _cached_log_level, _cached_log_file
    level = os.getenv("LOG_LEVEL", "info").lower()
    _cached_log_level = LOG_LEVELS.get(level, LOG_LEVELS["info"])
    _cached_log_file = os.getenv("LOG_FILE", "log.txt")


def _get_current_log_level() -> int:
    return _cached_log_level


def _get_log_file_path() -> str:
    return _cached_log_file


# -----------------------------------------------------------------
# 文件句柄管理（仅在 writer 线程内调用，不需要 _file_lock）
# -----------------------------------------------------------------

def _close_log_file():
    global _log_file_handle
    if _log_file_handle is not None:
        try:
            _log_file_handle.flush()
            _log_file_handle.close()
        except Exception:
            pass
        finally:
            _log_file_handle = None


def _open_log_file(mode: str = "a") -> bool:
    global _log_file_handle, _file_writing_disabled, _disable_reason
    _close_log_file()
    try:
        # 使用较大缓冲区（64 KB），由 writer 线程定期 flush，减少系统调用
        _log_file_handle = open(_cached_log_file, mode, encoding="utf-8", buffering=65536)
        return True
    except (PermissionError, OSError, IOError) as e:
        _file_writing_disabled = True
        _disable_reason = str(e)
        print(f"Warning: Cannot open log file, disabling file writing: {e}", file=sys.stderr)
        print("Log messages will continue to display in console only.", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Warning: Failed to open log file: {e}", file=sys.stderr)
        return False


def _clear_log_file():
    """清空日志文件（启动时调用，此时 writer 线程尚未启动，直接操作安全）"""
    global _file_writing_disabled, _disable_reason
    try:
        with open(_cached_log_file, "w", encoding="utf-8") as f:
            pass  # 覆盖清空
        _open_log_file("a")
    except (PermissionError, OSError, IOError) as e:
        _file_writing_disabled = True
        _disable_reason = str(e)
        print(
            f"Warning: File system appears to be read-only or permission denied. "
            f"Disabling log file writing: {e}",
            file=sys.stderr,
        )
        print("Log messages will continue to display in console only.", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to clear log file: {e}", file=sys.stderr)


# -----------------------------------------------------------------
# Writer 线程：批量从 deque 取出并写入，减少系统调用次数
# -----------------------------------------------------------------
_BATCH_SIZE = 200          # 单次最多批量写入条数
_FLUSH_INTERVAL = 0.5      # 秒：无新消息时强制 flush 周期


def _log_writer_worker():
    global _writer_running

    last_flush_time = 0.0

    while True:
        # 等待数据或超时
        with _deque_condition:
            if not _log_deque and _writer_running:
                _deque_condition.wait(timeout=_FLUSH_INTERVAL)

            # 批量取出
            batch = []
            for _ in range(_BATCH_SIZE):
                if _log_deque:
                    batch.append(_log_deque.popleft())
                else:
                    break

        if batch and not _file_writing_disabled:
            # 一次 write 调用搞定整批，最大化减少系统调用
            chunk = "\n".join(batch) + "\n"
            try:
                if _log_file_handle is None:
                    _open_log_file("a")
                if _log_file_handle is not None:
                    _log_file_handle.write(chunk)
            except Exception as e:
                print(f"Warning: Failed to write log batch: {e}", file=sys.stderr)
                _close_log_file()
                try:
                    _open_log_file("a")
                except Exception:
                    pass

        # 定时 flush
        now = _now_ts()
        if now - last_flush_time >= _FLUSH_INTERVAL:
            if _log_file_handle is not None:
                try:
                    _log_file_handle.flush()
                except Exception:
                    pass
            last_flush_time = now

        # 退出条件：已停止 + deque 已清空
        if not _writer_running and not _log_deque:
            break

    # 最终 flush & close
    if _log_file_handle is not None:
        try:
            _log_file_handle.flush()
        except Exception:
            pass
    _close_log_file()


def _now_ts() -> float:
    import time
    return time.monotonic()


def _start_writer_thread():
    global _writer_thread, _writer_running

    if _writer_thread is None or not _writer_thread.is_alive():
        _writer_running = True
        _writer_thread = threading.Thread(target=_log_writer_worker, daemon=True, name="LogWriter")
        _writer_thread.start()


def _stop_writer_thread():
    global _writer_running

    _writer_running = False
    # 唤醒 writer 线程让它能感知退出信号
    with _deque_condition:
        _deque_condition.notify_all()

    if _writer_thread and _writer_thread.is_alive():
        _writer_thread.join(timeout=3.0)


# -----------------------------------------------------------------
# 入队（热路径，极轻量）
# -----------------------------------------------------------------
_MAX_QUEUE_SIZE = 5000  # 防止极端情况内存无限增长


def _write_to_file(message: str):
    if _file_writing_disabled:
        return
    # deque.append 在 CPython 受 GIL 保护，无需额外锁
    if len(_log_deque) >= _MAX_QUEUE_SIZE:
        return  # 过载保护：丢弃而非阻塞
    _log_deque.append(message)
    # 非阻塞通知 writer（acquire 失败直接跳过，不影响主线程）
    if _deque_condition.acquire(blocking=False):
        try:
            _deque_condition.notify()
        finally:
            _deque_condition.release()


# -----------------------------------------------------------------
# 核心日志函数（热路径）
# -----------------------------------------------------------------

def _log(level: str, message: str):
    level = level.lower()
    level_val = LOG_LEVELS.get(level)
    if level_val is None:
        print(f"Warning: Unknown log level '{level}'", file=sys.stderr)
        return

    # 热路径：直接与缓存值比较，无函数调用开销
    if level_val < _cached_log_level:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{level.upper()}] {message}"

    if level in ("error", "critical"):
        print(entry, file=sys.stderr)
    else:
        print(entry)

    _write_to_file(entry)


def set_log_level(level: str):
    """动态设置日志级别（同时更新缓存）"""
    global _cached_log_level
    level = level.lower()
    if level not in LOG_LEVELS:
        print(f"Warning: Unknown log level '{level}'. Valid levels: {', '.join(LOG_LEVELS.keys())}")
        return False
    _cached_log_level = LOG_LEVELS[level]
    return True


class Logger:
    """支持 log('info', 'msg') 和 log.info('msg') 两种调用方式"""

    def __call__(self, level: str, message: str):
        _log(level, message)

    def debug(self, message: str):
        _log("debug", message)

    def info(self, message: str):
        _log("info", message)

    def warning(self, message: str):
        _log("warning", message)

    def error(self, message: str):
        _log("error", message)

    def critical(self, message: str):
        _log("critical", message)

    def get_current_level(self) -> str:
        current_level = _get_current_log_level()
        for name, value in LOG_LEVELS.items():
            if value == current_level:
                return name
        return "info"

    def get_log_file(self) -> str:
        return _get_log_file_path()

    def close(self):
        """手动关闭（优雅退出用）"""
        _stop_writer_thread()

    def get_queue_size(self) -> int:
        return len(_log_deque)


# 导出全局日志实例
log = Logger()

# 导出的公共接口
__all__ = ["log", "set_log_level", "LOG_LEVELS"]

# 模块加载时：读取配置缓存 → 清空日志文件 → 启动 writer 线程
_refresh_config()
_clear_log_file()
_start_writer_thread()

# 注册退出清理
atexit.register(_stop_writer_thread)

# 使用说明:
# 1. 设置日志级别: export LOG_LEVEL=debug  (或在 .env 中设置)
# 2. 设置日志文件: export LOG_FILE=log.txt (或在 .env 中设置)
# 3. 日志级别已缓存，热路径零 os.getenv 调用
# 4. 写入线程批量处理（最多 200 条/次），64 KB 缓冲区，每 0.5 s flush 一次
# 5. 队列上限 5000 条，超出时丢弃新日志（过载保护，不阻塞主线程）
# 6. 动态调整级别：set_log_level('debug') 立即生效

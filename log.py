from src.i18n import ts
"""
{ts("id_1406")} - {ts("id_1405")}
"""

import os
import sys
import threading
from datetime import datetime

# {ts("id_1407")}
LOG_LEVELS = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}

# {ts("id_1408")}
_file_lock = threading.Lock()

# {ts("id_1409")}
_file_writing_disabled = False
_disable_reason = None


def _get_current_log_level():
    f"""{ts("id_1410")}"""
    level = os.getenv("LOG_LEVEL", "info").lower()
    return LOG_LEVELS.get(level, LOG_LEVELS["info"])


def _get_log_file_path():
    f"""{ts("id_1411")}"""
    return os.getenv("LOG_FILE", "log.txt")


def _clear_log_file():
    f"""{ts("id_1412")}"""
    global _file_writing_disabled, _disable_reason

    try:
        log_file = _get_log_file_path()
        with _file_lock:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("")  # {ts("id_1413")}
    except (PermissionError, OSError, IOError) as e:
        # {ts("id_1414")}
        _file_writing_disabled = True
        _disable_reason = str(e)
        print(
            f"Warning: File system appears to be read-only or permission denied. "
            f"Disabling log file writing: {e}",
            file=sys.stderr,
        )
        print("Log messages will continue to display in console only.", file=sys.stderr)
    except Exception as e:
        # {ts("id_1415")}
        print(f"Warning: Failed to clear log file: {e}", file=sys.stderr)


def _write_to_file(message: str):
    f"""{ts("id_1416")}"""
    global _file_writing_disabled, _disable_reason

    # {ts("id_1417")}
    if _file_writing_disabled:
        return

    try:
        log_file = _get_log_file_path()
        with _file_lock:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(message + "\n")
                f.flush()  # {ts("id_1418")}
    except (PermissionError, OSError, IOError) as e:
        # {ts("id_1414")}
        _file_writing_disabled = True
        _disable_reason = str(e)
        print(
            f"Warning: File system appears to be read-only or permission denied. "
            f"Disabling log file writing: {e}",
            file=sys.stderr,
        )
        print("Log messages will continue to display in console only.", file=sys.stderr)
    except Exception as e:
        # {ts("id_1415")}
        print(f"Warning: Failed to write to log file: {e}", file=sys.stderr)


def _log(level: str, message: str):
    """
    {ts("id_1419")}
    """
    level = level.lower()
    if level not in LOG_LEVELS:
        print(f"Warning: Unknown log level '{level}'", file=sys.stderr)
        return

    # {ts("id_1420")}
    current_level = _get_current_log_level()
    if LOG_LEVELS[level] < current_level:
        return

    # {ts("id_1421500")}{ts("id_1422")}
    #if len(message) > 500:
        #message = message[:500] + "..."

    # {ts("id_1423")}
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{level.upper()}] {message}"

    # {ts("id_1424")}
    if level in ("error", "critical"):
        print(entry, file=sys.stderr)
    else:
        print(entry)

    # {ts("id_1425")}
    _write_to_file(entry)


def set_log_level(level: str):
    f"""{ts("id_1426")}"""
    level = level.lower()
    if level not in LOG_LEVELS:
        print(f"Warning: Unknown log level '{level}'. Valid levels: {', '.join(LOG_LEVELS.keys())}")
        return False

    print(f"Note: To set log level '{level}', please set LOG_LEVEL environment variable")
    return True


class Logger:
    f"""{ts("id_56")} log('info', 'msg') {ts("id_15f")} log.info('msg') {ts("id_1427")}"""

    def __call__(self, level: str, message: str):
        f"""{ts("id_56")} log('info', 'message') {ts("id_1428")}"""
        _log(level, message)

    def debug(self, message: str):
        f"""{ts("id_1429")}"""
        _log("debug", message)

    def info(self, message: str):
        f"""{ts("id_1430")}"""
        _log("info", message)

    def warning(self, message: str):
        f"""{ts("id_1431")}"""
        _log("warning", message)

    def error(self, message: str):
        f"""{ts("id_1432")}"""
        _log("error", message)

    def critical(self, message: str):
        f"""{ts("id_1433")}"""
        _log("critical", message)

    def get_current_level(self) -> str:
        f"""{ts("id_1434")}"""
        current_level = _get_current_log_level()
        for name, value in LOG_LEVELS.items():
            if value == current_level:
                return name
        return "info"

    def get_log_file(self) -> str:
        f"""{ts("id_1435")}"""
        return _get_log_file_path()


# {ts("id_1436")}
log = Logger()

# {ts("id_1437")}
__all__ = ["log", "set_log_level", "LOG_LEVELS"]

# {ts("id_1438")}
_clear_log_file()

# {ts("id_1439")}:
# 1. {ts(f"id_1440")}: export LOG_LEVEL=debug ({ts("id_1442")}.env{ts("id_1441")})
# 2. {ts(f"id_1443")}: export LOG_FILE=log.txt ({ts("id_1442")}.env{ts("id_1441")})

"""
Panel 共享工具
"""

import os
from typing import Set

import config


def get_env_locked_keys() -> Set:
    """获取被环境变量锁定的配置键集合"""
    env_locked_keys = set()

    # 使用 config.py 中统一维护的映射表
    for env_key, config_key in config.ENV_MAPPINGS.items():
        if os.getenv(env_key):
            env_locked_keys.add(config_key)

    return env_locked_keys

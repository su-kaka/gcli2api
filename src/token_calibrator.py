from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


def _bucket_from_raw_tokens(raw_tokens: int) -> str:
    """
    将 raw token 数映射到桶，避免“短句”和“超长日志”共用同一倍率导致互相干扰。
    """
    t = max(0, int(raw_tokens or 0))
    if t < 500:
        return "lt500"
    if t < 2000:
        return "lt2k"
    if t < 8000:
        return "lt8k"
    if t < 20000:
        return "lt20k"
    return "ge20k"


@dataclass
class _Entry:
    ratio: float
    updated_at: float


class TokenCalibrator:
    """
    基于“下游真实 promptTokenCount / 本地 raw 估算”的在线校准器（进程内缓存）。

    目标：
    - 不追求 100% 还原下游 tokenizer/模板（不可见），而是用观测到的真实值逐步逼近
    - 让 /count_tokens 和流式 message_start 的预估在长上下文时更稳定

    注意：
    - 缓存是进程内的，多进程/多实例之间不会共享（这没问题）
    - 只存倍率，不存任何文本内容
    """

    def __init__(self) -> None:
        self._store: "OrderedDict[Tuple[str, str], _Entry]" = OrderedDict()

    def _max_entries(self) -> int:
        raw = str(os.getenv("TOKEN_CALIBRATION_MAX_ENTRIES", "")).strip()
        if not raw:
            return 512
        try:
            return max(64, int(raw))
        except Exception:
            return 512

    def _ema_alpha(self, bucket: str) -> float:
        """
        EMA 更新速度：长上下文桶更快收敛，短上下文更保守。
        """
        return {
            "lt500": 0.10,
            "lt2k": 0.15,
            "lt8k": 0.20,
            "lt20k": 0.25,
            "ge20k": 0.30,
        }.get(bucket, 0.20)

    def get_ratio(self, key: str, raw_tokens: int) -> Optional[float]:
        bucket = _bucket_from_raw_tokens(raw_tokens)
        k = (str(key or ""), bucket)
        entry = self._store.get(k)
        if not entry:
            return None
        # 触发 LRU
        self._store.move_to_end(k)
        return float(entry.ratio)

    def update(self, key: str, *, raw_tokens: int, downstream_tokens: int) -> Optional[float]:
        raw = int(raw_tokens or 0)
        down = int(downstream_tokens or 0)
        if raw <= 0 or down <= 0:
            return None

        bucket = _bucket_from_raw_tokens(raw)
        k = (str(key or ""), bucket)

        ratio = down / float(raw)
        # 限幅：避免单次异常导致倍率飞掉
        ratio = max(0.80, min(2.50, ratio))

        now = time.time()
        old = self._store.get(k)
        if old:
            alpha = self._ema_alpha(bucket)
            new_ratio = (1.0 - alpha) * float(old.ratio) + alpha * ratio
        else:
            new_ratio = ratio

        self._store[k] = _Entry(ratio=float(new_ratio), updated_at=now)
        self._store.move_to_end(k)

        # LRU 裁剪
        max_entries = self._max_entries()
        while len(self._store) > max_entries:
            self._store.popitem(last=False)

        return float(new_ratio)


# 进程内单例（足够；不做跨进程共享）
token_calibrator = TokenCalibrator()


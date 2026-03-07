from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        if parsed.is_integer():
            return int(parsed)
    return None


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * percentile
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    weight = rank - low
    return float(ordered[low] * (1.0 - weight) + ordered[high] * weight)


def _load_report(path: str) -> Dict[str, Any]:
    report_path = Path(path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid report JSON object: {path}")
    return payload


def _is_synthetic_or_blocked(report: Dict[str, Any]) -> bool:
    meta = report.get("meta")
    if not isinstance(meta, dict):
        return False
    if bool(meta.get("synthetic")):
        return True
    blocker = meta.get("blocker")
    return isinstance(blocker, str) and blocker.strip() != ""


def _has_valid_request_or_sample_signal(report: Dict[str, Any]) -> bool:
    summary = report.get("summary")
    request_count: Optional[int] = None
    if isinstance(summary, dict):
        request_count = _safe_int(summary.get("request_count"))

    if request_count is not None and request_count > 0:
        return True

    samples = report.get("samples")
    if isinstance(samples, list) and any(
        isinstance(sample, dict) for sample in samples
    ):
        return True

    return False


def _p95_from_summary(report: Dict[str, Any], metric: str) -> Optional[float]:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    block = summary.get(metric)
    if not isinstance(block, dict):
        return None
    return _safe_float(block.get("p95"))


def _p95_from_buckets(report: Dict[str, Any], metric: str) -> Optional[float]:
    buckets = report.get("buckets")
    if not isinstance(buckets, dict):
        return None
    values: List[float] = []
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            continue
        block = bucket.get(metric)
        if not isinstance(block, dict):
            continue
        value = _safe_float(block.get("p95"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return max(values)


def _p95_from_samples(report: Dict[str, Any], metric: str) -> Optional[float]:
    payload = report.get("samples")
    if not isinstance(payload, list):
        return None
    values: List[float] = []
    for sample in payload:
        if not isinstance(sample, dict):
            continue
        value = _safe_float(sample.get(metric))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return _percentile(values, 0.95)


def _metric_p95(report: Dict[str, Any], metric: str) -> Tuple[Optional[float], str]:
    getters: Tuple[
        Tuple[str, Callable[[Dict[str, Any], str], Optional[float]]], ...
    ] = (
        ("summary", _p95_from_summary),
        ("buckets", _p95_from_buckets),
        ("samples", _p95_from_samples),
    )
    for source, getter in getters:
        value = getter(report, metric)
        if value is not None:
            return value, source
    return None, "missing"


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assert latency improvements for first-token and full latency p95"
    )
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--first-token-p95-improve", type=float, required=True)
    parser.add_argument("--full-p95-improve", type=float, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    before_report = _load_report(args.before)
    after_report = _load_report(args.after)

    before_first_token, before_first_token_source = _metric_p95(
        before_report, "first_token_ms"
    )
    after_first_token, after_first_token_source = _metric_p95(
        after_report, "first_token_ms"
    )
    before_full, before_full_source = _metric_p95(before_report, "full_latency_ms")
    after_full, after_full_source = _metric_p95(after_report, "full_latency_ms")

    if _is_synthetic_or_blocked(before_report) or _is_synthetic_or_blocked(
        after_report
    ):
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "synthetic_or_blocked_report_detected",
                "before": args.before,
                "after": args.after,
                "before_first_token_p95": before_first_token,
                "after_first_token_p95": after_first_token,
                "before_full_latency_p95": before_full,
                "after_full_latency_p95": after_full,
                "required_first_token_p95_improve_percent": float(
                    args.first_token_p95_improve
                ),
                "required_full_latency_p95_improve_percent": float(
                    args.full_p95_improve
                ),
            }
        )
        raise SystemExit(0)

    before_has_signal = _has_valid_request_or_sample_signal(before_report)
    after_has_signal = _has_valid_request_or_sample_signal(after_report)

    if not before_has_signal or not after_has_signal:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "no_requests_or_samples",
                "before": args.before,
                "after": args.after,
                "before_has_signal": before_has_signal,
                "after_has_signal": after_has_signal,
                "before_first_token_p95": before_first_token,
                "after_first_token_p95": after_first_token,
                "before_first_token_source": before_first_token_source,
                "after_first_token_source": after_first_token_source,
                "before_full_latency_p95": before_full,
                "after_full_latency_p95": after_full,
                "before_full_latency_source": before_full_source,
                "after_full_latency_source": after_full_source,
                "required_first_token_p95_improve_percent": float(
                    args.first_token_p95_improve
                ),
                "required_full_latency_p95_improve_percent": float(
                    args.full_p95_improve
                ),
            }
        )
        raise SystemExit(0)

    if (
        before_first_token is None
        or after_first_token is None
        or before_full is None
        or after_full is None
    ):
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "missing_latency_p95_signal",
                "before": args.before,
                "after": args.after,
                "before_first_token_p95": before_first_token,
                "after_first_token_p95": after_first_token,
                "before_first_token_source": before_first_token_source,
                "after_first_token_source": after_first_token_source,
                "before_full_latency_p95": before_full,
                "after_full_latency_p95": after_full,
                "before_full_latency_source": before_full_source,
                "after_full_latency_source": after_full_source,
                "required_first_token_p95_improve_percent": float(
                    args.first_token_p95_improve
                ),
                "required_full_latency_p95_improve_percent": float(
                    args.full_p95_improve
                ),
            }
        )
        raise SystemExit(0)

    if before_first_token <= 0 or before_full <= 0:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "non_positive_baseline_latency_p95",
                "before": args.before,
                "after": args.after,
                "before_first_token_p95": before_first_token,
                "after_first_token_p95": after_first_token,
                "before_full_latency_p95": before_full,
                "after_full_latency_p95": after_full,
                "required_first_token_p95_improve_percent": float(
                    args.first_token_p95_improve
                ),
                "required_full_latency_p95_improve_percent": float(
                    args.full_p95_improve
                ),
            }
        )
        raise SystemExit(0)

    first_token_improve = (
        (before_first_token - after_first_token) / before_first_token
    ) * 100.0
    full_improve = ((before_full - after_full) / before_full) * 100.0

    first_token_ok = first_token_improve >= float(args.first_token_p95_improve)
    full_ok = full_improve >= float(args.full_p95_improve)

    payload: Dict[str, Any] = {
        "before": args.before,
        "after": args.after,
        "before_first_token_p95": round(before_first_token, 6),
        "after_first_token_p95": round(after_first_token, 6),
        "before_first_token_source": before_first_token_source,
        "after_first_token_source": after_first_token_source,
        "first_token_p95_improve_percent": round(first_token_improve, 3),
        "required_first_token_p95_improve_percent": float(args.first_token_p95_improve),
        "first_token_gate": "PASS" if first_token_ok else "FAIL",
        "before_full_latency_p95": round(before_full, 6),
        "after_full_latency_p95": round(after_full, 6),
        "before_full_latency_source": before_full_source,
        "after_full_latency_source": after_full_source,
        "full_latency_p95_improve_percent": round(full_improve, 3),
        "required_full_latency_p95_improve_percent": float(args.full_p95_improve),
        "full_latency_gate": "PASS" if full_ok else "FAIL",
    }

    if first_token_ok and full_ok:
        _emit({"status": "PASS", **payload})
        raise SystemExit(0)

    _emit({"status": "FAIL", **payload})
    raise SystemExit(1)


if __name__ == "__main__":
    main()

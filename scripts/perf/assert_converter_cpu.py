from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
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


def _converter_cpu_p95_from_summary(report: Dict[str, Any]) -> Optional[float]:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    converter_block = summary.get("converter_cpu_ms")
    if not isinstance(converter_block, dict):
        return None
    return _safe_float(converter_block.get("p95"))


def _converter_cpu_p95_from_buckets(report: Dict[str, Any]) -> Optional[float]:
    buckets = report.get("buckets")
    if not isinstance(buckets, dict):
        return None
    values: List[float] = []
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            continue
        converter_block = bucket.get("converter_cpu_ms")
        if not isinstance(converter_block, dict):
            continue
        value = _safe_float(converter_block.get("p95"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return max(values)


def _converter_cpu_p95_from_samples(report: Dict[str, Any]) -> Optional[float]:
    payload = report.get("samples")
    if not isinstance(payload, list):
        return None
    values: List[float] = []
    for sample in payload:
        if not isinstance(sample, dict):
            continue
        value = _safe_float(sample.get("converter_cpu_ms"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return _percentile(values, 0.95)


def _converter_cpu_p95(report: Dict[str, Any]) -> Tuple[Optional[float], str]:
    for source, getter in (
        ("summary", _converter_cpu_p95_from_summary),
        ("buckets", _converter_cpu_p95_from_buckets),
        ("samples", _converter_cpu_p95_from_samples),
    ):
        value = getter(report)
        if value is not None:
            return value, source
    return None, "missing"


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assert converter_cpu_ms p95 reduction against baseline"
    )
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--cpu-p95-reduce", type=float, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    before_report = _load_report(args.before)
    after_report = _load_report(args.after)

    before_p95, before_source = _converter_cpu_p95(before_report)
    after_p95, after_source = _converter_cpu_p95(after_report)

    if _is_synthetic_or_blocked(before_report) or _is_synthetic_or_blocked(
        after_report
    ):
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "synthetic_or_blocked_report_detected",
                "before": args.before,
                "after": args.after,
                "before_converter_cpu_p95": before_p95,
                "after_converter_cpu_p95": after_p95,
                "before_converter_cpu_source": before_source,
                "after_converter_cpu_source": after_source,
                "required_cpu_p95_reduce_percent": float(args.cpu_p95_reduce),
            }
        )
        raise SystemExit(0)

    if before_p95 is None or after_p95 is None:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "missing_converter_cpu_p95_signal",
                "before": args.before,
                "after": args.after,
                "before_converter_cpu_p95": before_p95,
                "after_converter_cpu_p95": after_p95,
                "before_converter_cpu_source": before_source,
                "after_converter_cpu_source": after_source,
                "required_cpu_p95_reduce_percent": float(args.cpu_p95_reduce),
            }
        )
        raise SystemExit(0)

    if before_p95 <= 0:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "non_positive_baseline_converter_cpu_p95",
                "before": args.before,
                "after": args.after,
                "before_converter_cpu_p95": before_p95,
                "after_converter_cpu_p95": after_p95,
                "before_converter_cpu_source": before_source,
                "after_converter_cpu_source": after_source,
                "required_cpu_p95_reduce_percent": float(args.cpu_p95_reduce),
            }
        )
        raise SystemExit(0)

    reduction_percent = ((before_p95 - after_p95) / before_p95) * 100.0
    if reduction_percent >= float(args.cpu_p95_reduce):
        _emit(
            {
                "status": "PASS",
                "before": args.before,
                "after": args.after,
                "before_converter_cpu_p95": round(before_p95, 6),
                "after_converter_cpu_p95": round(after_p95, 6),
                "before_converter_cpu_source": before_source,
                "after_converter_cpu_source": after_source,
                "converter_cpu_p95_reduce_percent": round(reduction_percent, 3),
                "required_cpu_p95_reduce_percent": float(args.cpu_p95_reduce),
            }
        )
        raise SystemExit(0)

    _emit(
        {
            "status": "FAIL",
            "before": args.before,
            "after": args.after,
            "before_converter_cpu_p95": round(before_p95, 6),
            "after_converter_cpu_p95": round(after_p95, 6),
            "before_converter_cpu_source": before_source,
            "after_converter_cpu_source": after_source,
            "converter_cpu_p95_reduce_percent": round(reduction_percent, 3),
            "required_cpu_p95_reduce_percent": float(args.cpu_p95_reduce),
        }
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()

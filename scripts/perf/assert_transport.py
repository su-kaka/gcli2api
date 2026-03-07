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


def _ttfb_p95_from_summary(report: Dict[str, Any]) -> Optional[float]:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    ttfb_block = summary.get("ttfb_ms")
    if not isinstance(ttfb_block, dict):
        return None
    return _safe_float(ttfb_block.get("p95"))


def _ttfb_p95_from_buckets(report: Dict[str, Any]) -> Optional[float]:
    buckets = report.get("buckets")
    if not isinstance(buckets, dict):
        return None
    values: List[float] = []
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            continue
        ttfb_block = bucket.get("ttfb_ms")
        if not isinstance(ttfb_block, dict):
            continue
        value = _safe_float(ttfb_block.get("p95"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return max(values)


def _ttfb_p95_from_samples(report: Dict[str, Any]) -> Optional[float]:
    payload = report.get("samples")
    if not isinstance(payload, list):
        return None
    values: List[float] = []
    for sample in payload:
        if not isinstance(sample, dict):
            continue
        value = _safe_float(sample.get("ttfb_ms"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    return _percentile(values, 0.95)


def _ttfb_p95(report: Dict[str, Any]) -> Tuple[Optional[float], str]:
    for source, getter in (
        ("summary", _ttfb_p95_from_summary),
        ("buckets", _ttfb_p95_from_buckets),
        ("samples", _ttfb_p95_from_samples),
    ):
        value = getter(report)
        if value is not None:
            return value, source
    return None, "missing"


def _parse_status_code(value: Any) -> Optional[int]:
    code = _safe_int(value)
    if code is None:
        return None
    if code < 100 or code > 599:
        return None
    return code


def _status_counts_from_summary(report: Dict[str, Any]) -> Optional[Dict[int, int]]:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    buckets = summary.get("status_buckets")
    if not isinstance(buckets, dict):
        return None

    counts: Dict[int, int] = {}
    for raw_status, raw_count in buckets.items():
        status = _parse_status_code(raw_status)
        count = _safe_int(raw_count)
        if status is None or count is None or count <= 0:
            continue
        counts[status] = counts.get(status, 0) + count
    return counts if counts else None


def _status_counts_from_buckets(report: Dict[str, Any]) -> Optional[Dict[int, int]]:
    buckets = report.get("buckets")
    if not isinstance(buckets, dict):
        return None

    counts: Dict[int, int] = {}
    for key, bucket in buckets.items():
        if not isinstance(key, str) or not isinstance(bucket, dict):
            continue
        parts = key.split("|")
        if not parts:
            continue
        status = _parse_status_code(parts[-1])
        count = _safe_int(bucket.get("count"))
        if status is None or count is None or count <= 0:
            continue
        counts[status] = counts.get(status, 0) + count
    return counts if counts else None


def _status_counts_from_samples(report: Dict[str, Any]) -> Optional[Dict[int, int]]:
    payload = report.get("samples")
    if not isinstance(payload, list):
        return None

    counts: Dict[int, int] = {}
    for sample in payload:
        if not isinstance(sample, dict):
            continue
        status = _parse_status_code(sample.get("status"))
        if status is None:
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts if counts else None


def _status_counts(report: Dict[str, Any]) -> Tuple[Optional[Dict[int, int]], str]:
    for source, getter in (
        ("summary", _status_counts_from_summary),
        ("buckets", _status_counts_from_buckets),
        ("samples", _status_counts_from_samples),
    ):
        counts = getter(report)
        if counts is not None:
            return counts, source
    return None, "missing"


def _connection_error_rate(counts: Dict[int, int]) -> Tuple[int, int, float]:
    total = sum(counts.values())
    if total <= 0:
        return 0, 0, 0.0
    errors = sum(count for status, count in counts.items() if 400 <= status <= 599)
    rate = (float(errors) / float(total)) * 100.0
    return total, errors, rate


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assert transport improvements and connection error-rate stability"
    )
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--ttfb-p95-improve", type=float, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    before_report = _load_report(args.before)
    after_report = _load_report(args.after)

    before_ttfb, before_ttfb_source = _ttfb_p95(before_report)
    after_ttfb, after_ttfb_source = _ttfb_p95(after_report)

    if _is_synthetic_or_blocked(before_report) or _is_synthetic_or_blocked(
        after_report
    ):
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "synthetic_or_blocked_report_detected",
                "before": args.before,
                "after": args.after,
                "before_ttfb_p95": before_ttfb,
                "after_ttfb_p95": after_ttfb,
                "required_ttfb_p95_improve_percent": float(args.ttfb_p95_improve),
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
                "before_ttfb_p95": before_ttfb,
                "after_ttfb_p95": after_ttfb,
                "before_ttfb_source": before_ttfb_source,
                "after_ttfb_source": after_ttfb_source,
                "required_ttfb_p95_improve_percent": float(args.ttfb_p95_improve),
            }
        )
        raise SystemExit(0)

    if before_ttfb is None or after_ttfb is None:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "missing_ttfb_p95_signal",
                "before": args.before,
                "after": args.after,
                "before_ttfb_p95": before_ttfb,
                "after_ttfb_p95": after_ttfb,
                "before_ttfb_source": before_ttfb_source,
                "after_ttfb_source": after_ttfb_source,
                "required_ttfb_p95_improve_percent": float(args.ttfb_p95_improve),
            }
        )
        raise SystemExit(0)

    if before_ttfb <= 0:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "non_positive_baseline_ttfb_p95",
                "before": args.before,
                "after": args.after,
                "before_ttfb_p95": before_ttfb,
                "after_ttfb_p95": after_ttfb,
                "required_ttfb_p95_improve_percent": float(args.ttfb_p95_improve),
            }
        )
        raise SystemExit(0)

    ttfb_improve = ((before_ttfb - after_ttfb) / before_ttfb) * 100.0
    ttfb_ok = ttfb_improve >= float(args.ttfb_p95_improve)

    before_status_counts, before_status_source = _status_counts(before_report)
    after_status_counts, after_status_source = _status_counts(after_report)

    connection_gate: Dict[str, Any] = {
        "applied": False,
        "status": "NOT_APPLICABLE",
        "reason": "insufficient_connection_error_signal",
    }

    connection_ok = True

    if before_status_counts is not None and after_status_counts is not None:
        before_total, before_errors, before_rate = _connection_error_rate(
            before_status_counts
        )
        after_total, after_errors, after_rate = _connection_error_rate(
            after_status_counts
        )

        signal_exists = before_errors > 0 or after_errors > 0
        if signal_exists:
            connection_ok = after_rate <= before_rate
            connection_gate = {
                "applied": True,
                "status": "PASS" if connection_ok else "FAIL",
                "before_total": before_total,
                "before_error_count": before_errors,
                "before_error_rate_percent": round(before_rate, 6),
                "before_status_source": before_status_source,
                "after_total": after_total,
                "after_error_count": after_errors,
                "after_error_rate_percent": round(after_rate, 6),
                "after_status_source": after_status_source,
            }
        else:
            connection_gate = {
                "applied": False,
                "status": "NOT_APPLICABLE",
                "reason": "no_4xx_or_5xx_signal",
                "before_total": before_total,
                "before_status_source": before_status_source,
                "after_total": after_total,
                "after_status_source": after_status_source,
            }

    base_payload: Dict[str, Any] = {
        "before": args.before,
        "after": args.after,
        "before_ttfb_p95": round(before_ttfb, 6),
        "after_ttfb_p95": round(after_ttfb, 6),
        "before_ttfb_source": before_ttfb_source,
        "after_ttfb_source": after_ttfb_source,
        "ttfb_p95_improve_percent": round(ttfb_improve, 3),
        "required_ttfb_p95_improve_percent": float(args.ttfb_p95_improve),
        "ttfb_gate": "PASS" if ttfb_ok else "FAIL",
        "connection_error_rate_gate": connection_gate,
    }

    if ttfb_ok and connection_ok:
        _emit({"status": "PASS", **base_payload})
        raise SystemExit(0)

    _emit({"status": "FAIL", **base_payload})
    raise SystemExit(1)


if __name__ == "__main__":
    main()

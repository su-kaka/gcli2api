from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


def _load_report(path: str) -> Dict[str, Any]:
    report_path = Path(path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid report JSON object: {path}")
    return payload


def _is_synthetic(report: Dict[str, Any]) -> bool:
    meta = report.get("meta")
    if not isinstance(meta, dict):
        return False
    synthetic = bool(meta.get("synthetic"))
    blocked = bool(meta.get("blocker"))
    return synthetic or blocked


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


def _retry_p95_from_summary(report: Dict[str, Any]) -> Optional[float]:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    retry_block = summary.get("retry_count")
    if isinstance(retry_block, dict):
        value = _safe_float(retry_block.get("p95"))
        if value is not None:
            return value
    return None


def _retry_p95_from_buckets(report: Dict[str, Any]) -> Optional[float]:
    buckets = report.get("buckets")
    if not isinstance(buckets, dict):
        return None

    values: List[float] = []
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            continue
        retry_block = bucket.get("retry_count")
        if not isinstance(retry_block, dict):
            continue
        value = _safe_float(retry_block.get("p95"))
        if value is not None:
            values.append(value)

    if not values:
        return None
    return max(values)


def _retry_p95_from_samples(report: Dict[str, Any]) -> Optional[float]:
    samples = report.get("samples")
    if not isinstance(samples, list):
        return None

    retry_values: List[float] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        value = _safe_float(sample.get("retry_count"))
        if value is not None:
            retry_values.append(value)

    if not retry_values:
        return None
    return _percentile(retry_values, 0.95)


def _get_retry_p95(report: Dict[str, Any]) -> Optional[float]:
    for getter in (
        _retry_p95_from_summary,
        _retry_p95_from_buckets,
        _retry_p95_from_samples,
    ):
        value = getter(report)
        if value is not None:
            return value
    return None


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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assert preview pool retry reduction against baseline"
    )
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--retry-reduce", type=float, required=True)
    return parser.parse_args()


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    args = _parse_args()

    before_report = _load_report(args.before)
    after_report = _load_report(args.after)

    before_p95 = _get_retry_p95(before_report)
    after_p95 = _get_retry_p95(after_report)

    if _is_synthetic(before_report) or _is_synthetic(after_report):
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "synthetic_or_blocked_report_detected",
                "before": args.before,
                "after": args.after,
                "before_retry_p95": before_p95,
                "after_retry_p95": after_p95,
                "required_retry_reduce_percent": float(args.retry_reduce),
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
                "before_retry_p95": before_p95,
                "after_retry_p95": after_p95,
                "required_retry_reduce_percent": float(args.retry_reduce),
            }
        )
        raise SystemExit(0)

    retry_gate: Dict[str, Any] = {
        "status": "NOT_EVALUATED",
        "required_retry_reduce_percent": float(args.retry_reduce),
        "before_retry_p95": before_p95,
        "after_retry_p95": after_p95,
    }

    retry_blocked_reason: Optional[str] = None
    retry_ok = False

    if before_p95 is None or after_p95 is None:
        retry_gate.update(
            {
                "status": "BLOCKED",
                "reason": "missing_retry_count_p95_signal",
            }
        )
        retry_blocked_reason = "missing_retry_count_p95_signal"
    elif before_p95 <= 0:
        retry_gate.update(
            {
                "status": "BLOCKED",
                "reason": "non_positive_baseline_retry_p95",
            }
        )
        retry_blocked_reason = "non_positive_baseline_retry_p95"
    else:
        reduction_percent = ((before_p95 - after_p95) / before_p95) * 100.0
        retry_ok = reduction_percent >= float(args.retry_reduce)
        retry_gate.update(
            {
                "status": "PASS" if retry_ok else "FAIL",
                "retry_reduce_percent": round(reduction_percent, 3),
                "before_retry_p95": round(before_p95, 6),
                "after_retry_p95": round(after_p95, 6),
            }
        )

    before_status_counts, before_status_source = _status_counts(before_report)
    after_status_counts, after_status_source = _status_counts(after_report)

    no_credential_500_gate: Dict[str, Any] = {
        "status": "NOT_APPLICABLE",
        "reason": "missing_status_signal",
        "signal": "status_code_500_proxy",
        "before_status_source": before_status_source,
        "after_status_source": after_status_source,
    }
    no_credential_500_ok = False
    no_credential_500_blocked_reason: Optional[str] = "missing_status_signal"

    if before_status_counts is not None and after_status_counts is not None:
        before_total = sum(before_status_counts.values())
        after_total = sum(after_status_counts.values())
        before_500 = int(before_status_counts.get(500, 0))
        after_500 = int(after_status_counts.get(500, 0))

        if before_total > 0 and after_total > 0:
            before_500_rate = (float(before_500) / float(before_total)) * 100.0
            after_500_rate = (float(after_500) / float(after_total)) * 100.0

            no_credential_500_gate.update(
                {
                    "before_total": before_total,
                    "before_500_count": before_500,
                    "before_500_rate_percent": round(before_500_rate, 6),
                    "after_total": after_total,
                    "after_500_count": after_500,
                    "after_500_rate_percent": round(after_500_rate, 6),
                }
            )

            if before_500 <= 0 and after_500 <= 0:
                no_credential_500_gate.update(
                    {
                        "status": "NOT_APPLICABLE",
                        "reason": "no_status_500_signal_for_no_credential_proxy",
                    }
                )
                no_credential_500_blocked_reason = (
                    "no_status_500_signal_for_no_credential_proxy"
                )
            else:
                no_credential_500_ok = after_500_rate < before_500_rate
                no_credential_500_gate.update(
                    {
                        "status": "PASS" if no_credential_500_ok else "FAIL",
                        "reason": "status_code_500_proxy",
                    }
                )
                no_credential_500_blocked_reason = None

    base_payload = {
        "before": args.before,
        "after": args.after,
        "retry_gate": retry_gate,
        "no_credential_500_rate_gate": no_credential_500_gate,
    }

    if retry_blocked_reason or no_credential_500_blocked_reason:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": ";".join(
                    [
                        reason
                        for reason in (
                            retry_blocked_reason,
                            no_credential_500_blocked_reason,
                        )
                        if reason
                    ]
                ),
                **base_payload,
            }
        )
        raise SystemExit(0)

    if retry_ok and no_credential_500_ok:
        _emit({"status": "PASS", **base_payload})
        raise SystemExit(0)

    _emit({"status": "FAIL", **base_payload})
    raise SystemExit(1)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional


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


def _is_synthetic_or_blocked(report: Dict[str, Any]) -> bool:
    meta = report.get("meta")
    if not isinstance(meta, dict):
        return False
    if bool(meta.get("synthetic")):
        return True
    blocker = meta.get("blocker")
    return isinstance(blocker, str) and blocker.strip() != ""


def _samples(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = report.get("samples")
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _per_retry_sleep_values(samples: List[Dict[str, Any]]) -> List[float]:
    values: List[float] = []
    for sample in samples:
        retry_count = _safe_int(sample.get("retry_count"))
        retry_sleep_ms = _safe_float(sample.get("retry_sleep_ms"))
        if retry_count is None or retry_sleep_ms is None:
            continue
        if retry_count <= 0 or retry_sleep_ms <= 0:
            continue
        values.append(retry_sleep_ms / float(retry_count))
    return values


def _derive_nominal_retry_sleep_ms(
    before_samples: List[Dict[str, Any]], after_samples: List[Dict[str, Any]]
) -> Optional[float]:
    before_values = _per_retry_sleep_values(before_samples)
    if len(before_values) >= 3:
        return float(median(before_values))
    merged = before_values + _per_retry_sleep_values(after_samples)
    if len(merged) < 3:
        return None
    return float(median(merged))


def _find_double_sleep_anomalies(
    samples: List[Dict[str, Any]], nominal_sleep_ms: float
) -> List[Dict[str, Any]]:
    anomalies: List[Dict[str, Any]] = []
    for idx, sample in enumerate(samples):
        retry_count = _safe_int(sample.get("retry_count"))
        retry_sleep_ms = _safe_float(sample.get("retry_sleep_ms"))

        if retry_count is None and retry_sleep_ms is None:
            continue

        if retry_count is None:
            retry_count = 0
        if retry_sleep_ms is None:
            retry_sleep_ms = 0.0

        if retry_count <= 0:
            if retry_sleep_ms > 0:
                anomalies.append(
                    {
                        "sample_index": idx,
                        "reason": "retry_sleep_without_retry_count",
                        "retry_count": retry_count,
                        "retry_sleep_ms": round(retry_sleep_ms, 6),
                    }
                )
            continue

        expected_sleep_ms = nominal_sleep_ms * float(retry_count)
        if expected_sleep_ms <= 0:
            continue

        ratio = retry_sleep_ms / expected_sleep_ms
        if ratio >= 1.8:
            anomalies.append(
                {
                    "sample_index": idx,
                    "reason": "duplicate_wait_ratio_exceeded",
                    "retry_count": retry_count,
                    "retry_sleep_ms": round(retry_sleep_ms, 6),
                    "expected_sleep_ms": round(expected_sleep_ms, 6),
                    "ratio": round(ratio, 6),
                }
            )

    return anomalies


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assert retry behavior by checking duplicate-wait anomalies"
    )
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--max-double-sleep-count", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    before_report = _load_report(args.before)
    after_report = _load_report(args.after)

    if _is_synthetic_or_blocked(before_report) or _is_synthetic_or_blocked(
        after_report
    ):
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "synthetic_or_blocked_report_detected",
                "before": args.before,
                "after": args.after,
                "max_double_sleep_count": int(args.max_double_sleep_count),
            }
        )
        raise SystemExit(0)

    before_samples = _samples(before_report)
    after_samples = _samples(after_report)
    nominal_sleep_ms = _derive_nominal_retry_sleep_ms(before_samples, after_samples)

    if nominal_sleep_ms is None:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "insufficient_retry_sleep_signal",
                "before": args.before,
                "after": args.after,
                "max_double_sleep_count": int(args.max_double_sleep_count),
            }
        )
        raise SystemExit(0)

    anomalies = _find_double_sleep_anomalies(after_samples, nominal_sleep_ms)
    observed = len(anomalies)
    threshold = int(args.max_double_sleep_count)

    base_payload: Dict[str, Any] = {
        "before": args.before,
        "after": args.after,
        "nominal_retry_sleep_ms": round(nominal_sleep_ms, 6),
        "observed_double_sleep_count": observed,
        "max_double_sleep_count": threshold,
        "anomaly_examples": anomalies[:10],
    }

    if observed <= threshold:
        _emit({"status": "PASS", **base_payload})
        raise SystemExit(0)

    _emit({"status": "FAIL", **base_payload})
    raise SystemExit(1)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


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


def _summary_metric(report: Dict[str, Any], field: str) -> Optional[float]:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    return _safe_float(summary.get(field))


def _duration_seconds(report: Dict[str, Any]) -> Optional[float]:
    meta = report.get("meta")
    if not isinstance(meta, dict):
        return None
    duration = _safe_float(meta.get("duration"))
    if duration is None or duration <= 0:
        return None
    return duration


def _samples_count(report: Dict[str, Any]) -> Optional[int]:
    payload = report.get("samples")
    if not isinstance(payload, list):
        return None
    return len([sample for sample in payload if isinstance(sample, dict)])


def _summary_request_count(report: Dict[str, Any]) -> Optional[int]:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return None
    request_count = _safe_int(summary.get("request_count"))
    if request_count is None or request_count < 0:
        return None
    return request_count


def _total_tokens_from_samples(report: Dict[str, Any]) -> Optional[int]:
    payload = report.get("samples")
    if not isinstance(payload, list):
        return None
    total = 0
    seen = False
    for sample in payload:
        if not isinstance(sample, dict):
            continue
        token_value = _safe_int(sample.get("total_tokens"))
        if token_value is None or token_value < 0:
            continue
        total += token_value
        seen = True
    if not seen:
        return None
    return total


def _reqps(report: Dict[str, Any]) -> Tuple[Optional[float], str]:
    summary_value = _summary_metric(report, "reqps")
    if summary_value is not None:
        return summary_value, "summary"

    duration = _duration_seconds(report)
    request_count = _summary_request_count(report)
    if duration is not None and request_count is not None:
        return float(request_count) / duration, "summary+meta"

    sample_count = _samples_count(report)
    if duration is not None and sample_count is not None:
        return float(sample_count) / duration, "samples+meta"

    return None, "missing"


def _tokensps(report: Dict[str, Any]) -> Tuple[Optional[float], str]:
    summary_value = _summary_metric(report, "tokensps")
    if summary_value is not None:
        return summary_value, "summary"

    duration = _duration_seconds(report)
    total_tokens = _total_tokens_from_samples(report)
    if duration is not None and total_tokens is not None:
        return float(total_tokens) / duration, "samples+meta"

    return None, "missing"


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assert throughput deltas for reqps and tokensps against baseline"
    )
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--min-delta", type=float, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    before_report = _load_report(args.before)
    after_report = _load_report(args.after)

    before_reqps, before_reqps_source = _reqps(before_report)
    after_reqps, after_reqps_source = _reqps(after_report)
    before_tokensps, before_tokensps_source = _tokensps(before_report)
    after_tokensps, after_tokensps_source = _tokensps(after_report)

    if _is_synthetic_or_blocked(before_report) or _is_synthetic_or_blocked(
        after_report
    ):
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "synthetic_or_blocked_report_detected",
                "before": args.before,
                "after": args.after,
                "before_reqps": before_reqps,
                "after_reqps": after_reqps,
                "before_reqps_source": before_reqps_source,
                "after_reqps_source": after_reqps_source,
                "before_tokensps": before_tokensps,
                "after_tokensps": after_tokensps,
                "before_tokensps_source": before_tokensps_source,
                "after_tokensps_source": after_tokensps_source,
                "required_min_delta_percent": float(args.min_delta),
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
                "before_reqps": before_reqps,
                "after_reqps": after_reqps,
                "before_reqps_source": before_reqps_source,
                "after_reqps_source": after_reqps_source,
                "before_tokensps": before_tokensps,
                "after_tokensps": after_tokensps,
                "before_tokensps_source": before_tokensps_source,
                "after_tokensps_source": after_tokensps_source,
                "required_min_delta_percent": float(args.min_delta),
            }
        )
        raise SystemExit(0)

    if (
        before_reqps is None
        or after_reqps is None
        or before_tokensps is None
        or after_tokensps is None
    ):
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "missing_throughput_signal",
                "before": args.before,
                "after": args.after,
                "before_reqps": before_reqps,
                "after_reqps": after_reqps,
                "before_reqps_source": before_reqps_source,
                "after_reqps_source": after_reqps_source,
                "before_tokensps": before_tokensps,
                "after_tokensps": after_tokensps,
                "before_tokensps_source": before_tokensps_source,
                "after_tokensps_source": after_tokensps_source,
                "required_min_delta_percent": float(args.min_delta),
            }
        )
        raise SystemExit(0)

    if before_reqps <= 0 or before_tokensps <= 0:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "non_positive_baseline_throughput",
                "before": args.before,
                "after": args.after,
                "before_reqps": before_reqps,
                "after_reqps": after_reqps,
                "before_reqps_source": before_reqps_source,
                "after_reqps_source": after_reqps_source,
                "before_tokensps": before_tokensps,
                "after_tokensps": after_tokensps,
                "before_tokensps_source": before_tokensps_source,
                "after_tokensps_source": after_tokensps_source,
                "required_min_delta_percent": float(args.min_delta),
            }
        )
        raise SystemExit(0)

    reqps_delta = ((after_reqps - before_reqps) / before_reqps) * 100.0
    tokensps_delta = ((after_tokensps - before_tokensps) / before_tokensps) * 100.0

    reqps_ok = reqps_delta >= float(args.min_delta)
    tokensps_ok = tokensps_delta >= float(args.min_delta)

    payload: Dict[str, Any] = {
        "before": args.before,
        "after": args.after,
        "before_reqps": round(before_reqps, 6),
        "after_reqps": round(after_reqps, 6),
        "before_reqps_source": before_reqps_source,
        "after_reqps_source": after_reqps_source,
        "reqps_delta_percent": round(reqps_delta, 3),
        "reqps_gate": "PASS" if reqps_ok else "FAIL",
        "before_tokensps": round(before_tokensps, 6),
        "after_tokensps": round(after_tokensps, 6),
        "before_tokensps_source": before_tokensps_source,
        "after_tokensps_source": after_tokensps_source,
        "tokensps_delta_percent": round(tokensps_delta, 3),
        "tokensps_gate": "PASS" if tokensps_ok else "FAIL",
        "required_min_delta_percent": float(args.min_delta),
    }

    if reqps_ok and tokensps_ok:
        _emit({"status": "PASS", **payload})
        raise SystemExit(0)

    _emit({"status": "FAIL", **payload})
    raise SystemExit(1)


if __name__ == "__main__":
    main()

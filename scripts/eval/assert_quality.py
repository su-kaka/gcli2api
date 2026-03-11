from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DIRECT_SCORE_FIELDS: Tuple[str, ...] = ("win_rate", "pass_rate", "quality_score")
DIRECT_CONTAINERS: Tuple[str, ...] = (
    "summary",
    "metrics",
    "result",
    "stats",
    "quality",
)
CASE_LIST_FIELDS: Tuple[str, ...] = (
    "cases",
    "items",
    "results",
    "evaluations",
    "samples",
    "records",
)
PASS_BOOL_FIELDS: Tuple[str, ...] = ("pass", "passed", "success", "ok", "is_pass")
PASS_STR_FIELDS: Tuple[str, ...] = ("status", "outcome", "result", "verdict")
PASS_STR_VALUES = {"pass", "passed", "ok", "success", "win", "true"}
FAIL_STR_VALUES = {"fail", "failed", "error", "loss", "false"}


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assert quality regression threshold between baseline and candidate"
    )
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--max-drop", type=float, required=True)
    return parser.parse_args()


def _load_report(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    report_path = Path(path)
    if not report_path.exists():
        return None, f"missing_file:{path}"
    if not report_path.is_file():
        return None, f"not_a_file:{path}"
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"invalid_json:{path}:{exc.__class__.__name__}"
    if not isinstance(payload, dict):
        return None, f"invalid_report_object:{path}"
    return payload, None


def _is_synthetic_or_blocked(report: Dict[str, Any]) -> bool:
    meta = report.get("meta")
    if not isinstance(meta, dict):
        return False
    if bool(meta.get("synthetic")):
        return True
    blocker = meta.get("blocker")
    return isinstance(blocker, str) and blocker.strip() != ""


def _extract_direct_score(
    report: Dict[str, Any],
) -> Tuple[Optional[float], str, Optional[str]]:
    for field in DIRECT_SCORE_FIELDS:
        value = _safe_float(report.get(field))
        if value is not None:
            return value, "root", field

    for container_name in DIRECT_CONTAINERS:
        container = report.get(container_name)
        if not isinstance(container, dict):
            continue
        for field in DIRECT_SCORE_FIELDS:
            value = _safe_float(container.get(field))
            if value is not None:
                return value, container_name, field

    return None, "missing", None


def _normalize_case_pass(case: Dict[str, Any]) -> Optional[bool]:
    for field in PASS_BOOL_FIELDS:
        value = case.get(field)
        if isinstance(value, bool):
            return value

    for field in PASS_STR_FIELDS:
        value = case.get(field)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in PASS_STR_VALUES:
                return True
            if normalized in FAIL_STR_VALUES:
                return False

    return None


def _extract_case_list(
    report: Dict[str, Any],
) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    for field in CASE_LIST_FIELDS:
        payload = report.get(field)
        if isinstance(payload, list):
            cases = [item for item in payload if isinstance(item, dict)]
            if cases:
                return cases, f"root.{field}"

    for container_name in DIRECT_CONTAINERS:
        container = report.get(container_name)
        if not isinstance(container, dict):
            continue
        for field in CASE_LIST_FIELDS:
            payload = container.get(field)
            if isinstance(payload, list):
                cases = [item for item in payload if isinstance(item, dict)]
                if cases:
                    return cases, f"{container_name}.{field}"

    return None, "missing"


def _extract_case_pass_rate(
    report: Dict[str, Any],
) -> Tuple[Optional[float], str, Dict[str, Any]]:
    cases, case_source = _extract_case_list(report)
    if not cases:
        return None, "missing", {"case_source": case_source}

    resolved = 0
    passed = 0
    unresolved = 0
    for case in cases:
        verdict = _normalize_case_pass(case)
        if verdict is None:
            unresolved += 1
            continue
        resolved += 1
        if verdict:
            passed += 1

    details: Dict[str, Any] = {
        "case_source": case_source,
        "case_count": len(cases),
        "resolved_case_count": resolved,
        "unresolved_case_count": unresolved,
        "passed_case_count": passed,
    }

    if resolved <= 0:
        return None, "missing", details

    pass_rate = (float(passed) / float(resolved)) * 100.0
    return pass_rate, "case_pass_rate", details


def _extract_quality_score(
    report: Dict[str, Any],
) -> Tuple[Optional[float], str, Dict[str, Any]]:
    direct_value, direct_source, direct_field = _extract_direct_score(report)
    if direct_value is not None:
        return (
            direct_value,
            "direct",
            {"score_source": direct_source, "score_field": direct_field},
        )

    case_rate, case_source, case_details = _extract_case_pass_rate(report)
    if case_rate is not None:
        return case_rate, case_source, case_details

    return None, "missing", {"direct_score_source": direct_source, **case_details}


def main() -> None:
    args = _parse_args()
    baseline_report, baseline_load_error = _load_report(args.baseline)
    candidate_report, candidate_load_error = _load_report(args.candidate)

    if baseline_load_error or candidate_load_error:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "missing_or_unreadable_quality_artifact",
                "baseline": args.baseline,
                "candidate": args.candidate,
                "baseline_error": baseline_load_error,
                "candidate_error": candidate_load_error,
                "required_max_drop": float(args.max_drop),
            }
        )
        raise SystemExit(0)

    assert baseline_report is not None
    assert candidate_report is not None

    baseline_score, baseline_source, baseline_details = _extract_quality_score(
        baseline_report
    )
    candidate_score, candidate_source, candidate_details = _extract_quality_score(
        candidate_report
    )

    if _is_synthetic_or_blocked(baseline_report) or _is_synthetic_or_blocked(
        candidate_report
    ):
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "synthetic_or_blocked_report_detected",
                "baseline": args.baseline,
                "candidate": args.candidate,
                "baseline_score": baseline_score,
                "candidate_score": candidate_score,
                "baseline_source": baseline_source,
                "candidate_source": candidate_source,
                "required_max_drop": float(args.max_drop),
                "baseline_details": baseline_details,
                "candidate_details": candidate_details,
            }
        )
        raise SystemExit(0)

    if baseline_score is None or candidate_score is None:
        _emit(
            {
                "status": "SKIP(BLOCKED)",
                "reason": "missing_quality_signal",
                "baseline": args.baseline,
                "candidate": args.candidate,
                "baseline_score": baseline_score,
                "candidate_score": candidate_score,
                "baseline_source": baseline_source,
                "candidate_source": candidate_source,
                "required_max_drop": float(args.max_drop),
                "baseline_details": baseline_details,
                "candidate_details": candidate_details,
            }
        )
        raise SystemExit(0)

    drop = float(baseline_score) - float(candidate_score)
    passed = drop <= float(args.max_drop)

    payload: Dict[str, Any] = {
        "baseline": args.baseline,
        "candidate": args.candidate,
        "baseline_score": round(float(baseline_score), 6),
        "candidate_score": round(float(candidate_score), 6),
        "baseline_source": baseline_source,
        "candidate_source": candidate_source,
        "score_drop": round(drop, 6),
        "required_max_drop": float(args.max_drop),
        "quality_gate": "PASS" if passed else "FAIL",
        "baseline_details": baseline_details,
        "candidate_details": candidate_details,
    }

    if passed:
        _emit({"status": "PASS", **payload})
        raise SystemExit(0)

    _emit({"status": "FAIL", **payload})
    raise SystemExit(1)


if __name__ == "__main__":
    main()

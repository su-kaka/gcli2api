from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    get_rollback_trigger_latency_p95_ms,
    get_rollback_trigger_quality_drop_pct,
    get_rollback_trigger_throughput_drop_pct,
    get_rollout_stage_percent,
    reload_config,
)
from scripts.eval.assert_quality import (  # noqa: E402
    _extract_quality_score,
    _is_synthetic_or_blocked as _quality_is_synthetic_or_blocked,
    _load_report as _quality_load_report,
)
from scripts.perf.assert_latency import (  # noqa: E402
    _has_valid_request_or_sample_signal as _latency_has_valid_request_or_sample_signal,
    _is_synthetic_or_blocked as _latency_is_synthetic_or_blocked,
    _load_report as _load_perf_report_or_raise,
    _metric_p95,
)
from scripts.perf.assert_throughput import (  # noqa: E402
    _has_valid_request_or_sample_signal as _throughput_has_valid_request_or_sample_signal,
    _is_synthetic_or_blocked as _throughput_is_synthetic_or_blocked,
    _reqps,
    _tokensps,
)
from src.storage_adapter import get_storage_adapter  # noqa: E402

STAGE_LADDER: Tuple[int, ...] = (5, 20, 50, 100)
LATENCY_POLICY_MODE_ABSOLUTE_P95_CAP = "absolute_p95_cap"
LATENCY_POLICY_MODE_RELATIVE_FULL_P95_IMPROVE = "relative_full_p95_improve"
LATENCY_POLICY_MODES: Tuple[str, ...] = (
    LATENCY_POLICY_MODE_ABSOLUTE_P95_CAP,
    LATENCY_POLICY_MODE_RELATIVE_FULL_P95_IMPROVE,
)


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be >= 0")
    return parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rollout/rollback decision guard based on perf + quality artifacts"
    )
    parser.add_argument("--before-perf", required=True)
    parser.add_argument("--after-perf", required=True)
    parser.add_argument("--baseline-quality", required=True)
    parser.add_argument("--candidate-quality", required=True)
    parser.add_argument("--rollout-stage-percent", type=int, choices=STAGE_LADDER)
    parser.add_argument("--rollback-trigger-latency-p95-ms", type=_non_negative_float)
    parser.add_argument(
        "--latency-policy-mode",
        choices=LATENCY_POLICY_MODES,
        help="Latency rollback policy mode",
    )
    parser.add_argument(
        "--rollback-trigger-latency-p95-improve-pct", type=_non_negative_float
    )
    parser.add_argument(
        "--rollback-trigger-throughput-drop-pct", type=_non_negative_float
    )
    parser.add_argument("--rollback-trigger-quality-drop-pct", type=_non_negative_float)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist target rollout stage + stage-mapped feature flags",
    )
    return parser.parse_args()


def get_next_stage_percent(current_stage_percent: int) -> int:
    if current_stage_percent not in STAGE_LADDER:
        raise ValueError(f"unsupported stage percent: {current_stage_percent}")

    stage_index = STAGE_LADDER.index(current_stage_percent)
    if stage_index >= len(STAGE_LADDER) - 1:
        return STAGE_LADDER[-1]
    return STAGE_LADDER[stage_index + 1]


def get_previous_stage_percent(current_stage_percent: int) -> int:
    if current_stage_percent not in STAGE_LADDER:
        raise ValueError(f"unsupported stage percent: {current_stage_percent}")

    stage_index = STAGE_LADDER.index(current_stage_percent)
    if stage_index <= 0:
        return STAGE_LADDER[0]
    return STAGE_LADDER[stage_index - 1]


def stage_percent_to_feature_flags(stage_percent: int) -> Dict[str, bool]:
    if stage_percent not in STAGE_LADDER:
        raise ValueError(f"unsupported stage percent: {stage_percent}")

    return {
        "ff_retry_policy_v2": stage_percent >= 5,
        "ff_http2_pool_tuning": stage_percent >= 20,
        "ff_converter_fast_path": stage_percent >= 50,
        "ff_preview_credential_scheduler_v2": stage_percent >= 100,
    }


def _load_perf_report(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        payload = _load_perf_report_or_raise(path)
    except FileNotFoundError:
        return None, f"missing_file:{path}"
    except Exception as exc:
        return (
            None,
            f"invalid_or_unreadable_perf_artifact:{path}:{exc.__class__.__name__}",
        )
    return payload, None


def _latency_gate(
    *,
    before_report: Dict[str, Any],
    after_report: Dict[str, Any],
    latency_threshold_ms: float,
    latency_policy_mode: str,
    latency_improve_pct_threshold: float,
) -> Dict[str, Any]:
    threshold_latency_p95_ms = float(latency_threshold_ms)
    required_full_latency_p95_improve_percent = float(latency_improve_pct_threshold)

    if latency_policy_mode == LATENCY_POLICY_MODE_RELATIVE_FULL_P95_IMPROVE:
        if _latency_is_synthetic_or_blocked(
            before_report
        ) or _latency_is_synthetic_or_blocked(after_report):
            return {
                "status": "BLOCKED",
                "reason": "synthetic_or_blocked_report_detected",
                "latency_policy_mode": latency_policy_mode,
                "threshold_latency_p95_ms": threshold_latency_p95_ms,
                "required_full_latency_p95_improve_percent": required_full_latency_p95_improve_percent,
            }

        before_has_signal = _latency_has_valid_request_or_sample_signal(before_report)
        after_has_signal = _latency_has_valid_request_or_sample_signal(after_report)
        if not before_has_signal or not after_has_signal:
            return {
                "status": "BLOCKED",
                "reason": "no_requests_or_samples",
                "before_has_signal": before_has_signal,
                "after_has_signal": after_has_signal,
                "latency_policy_mode": latency_policy_mode,
                "threshold_latency_p95_ms": threshold_latency_p95_ms,
                "required_full_latency_p95_improve_percent": required_full_latency_p95_improve_percent,
            }

        before_full_latency_p95, before_full_latency_source = _metric_p95(
            before_report, "full_latency_ms"
        )
        after_full_latency_p95, after_full_latency_source = _metric_p95(
            after_report, "full_latency_ms"
        )
        if before_full_latency_p95 is None or after_full_latency_p95 is None:
            return {
                "status": "BLOCKED",
                "reason": "missing_latency_p95_signal",
                "before_full_latency_source": before_full_latency_source,
                "after_full_latency_source": after_full_latency_source,
                "latency_policy_mode": latency_policy_mode,
                "threshold_latency_p95_ms": threshold_latency_p95_ms,
                "required_full_latency_p95_improve_percent": required_full_latency_p95_improve_percent,
            }

        before_full_latency_p95_float = float(before_full_latency_p95)
        after_full_latency_p95_float = float(after_full_latency_p95)
        if before_full_latency_p95_float <= 0:
            return {
                "status": "BLOCKED",
                "reason": "non_positive_baseline_latency_p95",
                "before_full_latency_p95_ms": round(before_full_latency_p95_float, 6),
                "after_full_latency_p95_ms": round(after_full_latency_p95_float, 6),
                "latency_policy_mode": latency_policy_mode,
                "threshold_latency_p95_ms": threshold_latency_p95_ms,
                "required_full_latency_p95_improve_percent": required_full_latency_p95_improve_percent,
            }

        full_latency_p95_improve_percent = (
            (before_full_latency_p95_float - after_full_latency_p95_float)
            / before_full_latency_p95_float
        ) * 100.0
        passed = (
            full_latency_p95_improve_percent
            >= required_full_latency_p95_improve_percent
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "reason": "full_latency_p95_improve_within_threshold"
            if passed
            else "full_latency_p95_improve_below_threshold",
            "before_full_latency_p95_ms": round(before_full_latency_p95_float, 6),
            "after_full_latency_p95_ms": round(after_full_latency_p95_float, 6),
            "before_full_latency_source": before_full_latency_source,
            "after_full_latency_source": after_full_latency_source,
            "full_latency_p95_improve_percent": round(
                full_latency_p95_improve_percent, 6
            ),
            "latency_policy_mode": latency_policy_mode,
            "threshold_latency_p95_ms": threshold_latency_p95_ms,
            "required_full_latency_p95_improve_percent": required_full_latency_p95_improve_percent,
        }

    if _latency_is_synthetic_or_blocked(after_report):
        return {
            "status": "BLOCKED",
            "reason": "synthetic_or_blocked_report_detected",
            "latency_policy_mode": latency_policy_mode,
            "threshold_latency_p95_ms": threshold_latency_p95_ms,
            "required_full_latency_p95_improve_percent": required_full_latency_p95_improve_percent,
        }

    if not _latency_has_valid_request_or_sample_signal(after_report):
        return {
            "status": "BLOCKED",
            "reason": "no_requests_or_samples",
            "latency_policy_mode": latency_policy_mode,
            "threshold_latency_p95_ms": threshold_latency_p95_ms,
            "required_full_latency_p95_improve_percent": required_full_latency_p95_improve_percent,
        }

    after_full_latency_p95, after_full_latency_source = _metric_p95(
        after_report, "full_latency_ms"
    )
    if after_full_latency_p95 is None:
        return {
            "status": "BLOCKED",
            "reason": "missing_latency_p95_signal",
            "after_full_latency_source": after_full_latency_source,
            "latency_policy_mode": latency_policy_mode,
            "threshold_latency_p95_ms": threshold_latency_p95_ms,
            "required_full_latency_p95_improve_percent": required_full_latency_p95_improve_percent,
        }

    passed = float(after_full_latency_p95) <= threshold_latency_p95_ms
    return {
        "status": "PASS" if passed else "FAIL",
        "reason": "latency_p95_within_threshold"
        if passed
        else "latency_p95_exceeds_threshold",
        "after_full_latency_p95_ms": round(float(after_full_latency_p95), 6),
        "after_full_latency_source": after_full_latency_source,
        "latency_policy_mode": latency_policy_mode,
        "threshold_latency_p95_ms": threshold_latency_p95_ms,
        "required_full_latency_p95_improve_percent": required_full_latency_p95_improve_percent,
    }


def _throughput_gate(
    before_report: Dict[str, Any],
    after_report: Dict[str, Any],
    throughput_drop_pct_threshold: float,
) -> Dict[str, Any]:
    if _throughput_is_synthetic_or_blocked(
        before_report
    ) or _throughput_is_synthetic_or_blocked(after_report):
        return {
            "status": "BLOCKED",
            "reason": "synthetic_or_blocked_report_detected",
        }

    before_has_signal = _throughput_has_valid_request_or_sample_signal(before_report)
    after_has_signal = _throughput_has_valid_request_or_sample_signal(after_report)
    if not before_has_signal or not after_has_signal:
        return {
            "status": "BLOCKED",
            "reason": "no_requests_or_samples",
            "before_has_signal": before_has_signal,
            "after_has_signal": after_has_signal,
        }

    before_reqps, before_reqps_source = _reqps(before_report)
    after_reqps, after_reqps_source = _reqps(after_report)
    before_tokensps, before_tokensps_source = _tokensps(before_report)
    after_tokensps, after_tokensps_source = _tokensps(after_report)

    if (
        before_reqps is None
        or after_reqps is None
        or before_tokensps is None
        or after_tokensps is None
    ):
        return {
            "status": "BLOCKED",
            "reason": "missing_throughput_signal",
            "before_reqps": before_reqps,
            "after_reqps": after_reqps,
            "before_reqps_source": before_reqps_source,
            "after_reqps_source": after_reqps_source,
            "before_tokensps": before_tokensps,
            "after_tokensps": after_tokensps,
            "before_tokensps_source": before_tokensps_source,
            "after_tokensps_source": after_tokensps_source,
        }

    if float(before_reqps) <= 0 or float(before_tokensps) <= 0:
        return {
            "status": "BLOCKED",
            "reason": "non_positive_baseline_throughput",
            "before_reqps": round(float(before_reqps), 6),
            "before_tokensps": round(float(before_tokensps), 6),
        }

    reqps_drop_pct = (
        (float(before_reqps) - float(after_reqps)) / float(before_reqps)
    ) * 100.0
    tokensps_drop_pct = (
        (float(before_tokensps) - float(after_tokensps)) / float(before_tokensps)
    ) * 100.0
    threshold = float(throughput_drop_pct_threshold)

    reqps_passed = reqps_drop_pct <= threshold
    tokensps_passed = tokensps_drop_pct <= threshold
    passed = reqps_passed and tokensps_passed

    failed_metrics = []
    if not reqps_passed:
        failed_metrics.append("reqps")
    if not tokensps_passed:
        failed_metrics.append("tokensps")

    return {
        "status": "PASS" if passed else "FAIL",
        "reason": "throughput_drop_within_threshold"
        if passed
        else "throughput_drop_exceeds_threshold",
        "before_reqps": round(float(before_reqps), 6),
        "after_reqps": round(float(after_reqps), 6),
        "before_reqps_source": before_reqps_source,
        "after_reqps_source": after_reqps_source,
        "reqps_drop_pct": round(reqps_drop_pct, 6),
        "before_tokensps": round(float(before_tokensps), 6),
        "after_tokensps": round(float(after_tokensps), 6),
        "before_tokensps_source": before_tokensps_source,
        "after_tokensps_source": after_tokensps_source,
        "tokensps_drop_pct": round(tokensps_drop_pct, 6),
        "throughput_drop_pct_threshold": threshold,
        "failed_metrics": failed_metrics,
    }


def _quality_gate(
    baseline_quality_path: str,
    candidate_quality_path: str,
    quality_drop_pct_threshold: float,
) -> Dict[str, Any]:
    baseline_report, baseline_error = _quality_load_report(baseline_quality_path)
    candidate_report, candidate_error = _quality_load_report(candidate_quality_path)
    if baseline_error or candidate_error:
        return {
            "status": "BLOCKED",
            "reason": "missing_or_unreadable_quality_artifact",
            "baseline_error": baseline_error,
            "candidate_error": candidate_error,
        }

    assert baseline_report is not None
    assert candidate_report is not None

    if _quality_is_synthetic_or_blocked(
        baseline_report
    ) or _quality_is_synthetic_or_blocked(candidate_report):
        return {
            "status": "BLOCKED",
            "reason": "synthetic_or_blocked_report_detected",
        }

    baseline_score, baseline_source, baseline_details = _extract_quality_score(
        baseline_report
    )
    candidate_score, candidate_source, candidate_details = _extract_quality_score(
        candidate_report
    )

    if baseline_score is None or candidate_score is None:
        return {
            "status": "BLOCKED",
            "reason": "missing_quality_signal",
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "baseline_source": baseline_source,
            "candidate_source": candidate_source,
            "baseline_details": baseline_details,
            "candidate_details": candidate_details,
        }

    baseline_score_float = float(baseline_score)
    candidate_score_float = float(candidate_score)
    score_drop = baseline_score_float - candidate_score_float
    allowed_drop = max(baseline_score_float, 0.0) * (
        float(quality_drop_pct_threshold) / 100.0
    )
    passed = score_drop <= allowed_drop

    return {
        "status": "PASS" if passed else "FAIL",
        "reason": "quality_drop_within_threshold"
        if passed
        else "quality_drop_exceeds_threshold",
        "baseline_score": round(baseline_score_float, 6),
        "candidate_score": round(candidate_score_float, 6),
        "score_drop": round(score_drop, 6),
        "quality_drop_pct_threshold": float(quality_drop_pct_threshold),
        "quality_drop_max_allowed": round(allowed_drop, 6),
        "baseline_source": baseline_source,
        "candidate_source": candidate_source,
        "baseline_details": baseline_details,
        "candidate_details": candidate_details,
    }


async def _resolve_effective_thresholds(
    rollout_stage_percent: Optional[int],
    rollback_trigger_latency_p95_ms: Optional[float],
    latency_policy_mode: Optional[str],
    rollback_trigger_latency_p95_improve_pct: Optional[float],
    rollback_trigger_throughput_drop_pct: Optional[float],
    rollback_trigger_quality_drop_pct: Optional[float],
) -> Dict[str, Any]:
    current_stage_percent = (
        int(rollout_stage_percent)
        if rollout_stage_percent is not None
        else int(await get_rollout_stage_percent())
    )
    latency_threshold_ms = (
        float(rollback_trigger_latency_p95_ms)
        if rollback_trigger_latency_p95_ms is not None
        else float(await get_rollback_trigger_latency_p95_ms())
    )
    resolved_latency_policy_mode = (
        str(latency_policy_mode)
        if latency_policy_mode is not None
        else LATENCY_POLICY_MODE_ABSOLUTE_P95_CAP
    )
    if resolved_latency_policy_mode not in LATENCY_POLICY_MODES:
        raise ValueError(
            f"unsupported latency policy mode: {resolved_latency_policy_mode}"
        )
    latency_improve_pct_threshold = (
        float(rollback_trigger_latency_p95_improve_pct)
        if rollback_trigger_latency_p95_improve_pct is not None
        else 0.0
    )
    throughput_drop_pct_threshold = (
        float(rollback_trigger_throughput_drop_pct)
        if rollback_trigger_throughput_drop_pct is not None
        else float(await get_rollback_trigger_throughput_drop_pct())
    )
    quality_drop_pct_threshold = (
        float(rollback_trigger_quality_drop_pct)
        if rollback_trigger_quality_drop_pct is not None
        else float(await get_rollback_trigger_quality_drop_pct())
    )

    return {
        "current_stage_percent": current_stage_percent,
        "latency_threshold_ms": latency_threshold_ms,
        "latency_policy_mode": resolved_latency_policy_mode,
        "latency_improve_pct_threshold": latency_improve_pct_threshold,
        "throughput_drop_pct_threshold": throughput_drop_pct_threshold,
        "quality_drop_pct_threshold": quality_drop_pct_threshold,
    }


async def _persist_rollout_stage_with_feature_flags(
    target_stage_percent: int,
) -> Dict[str, Any]:
    persisted_config = {
        "rollout_stage_percent": int(target_stage_percent),
        **stage_percent_to_feature_flags(target_stage_percent),
    }

    storage_adapter = await get_storage_adapter()
    for key, value in persisted_config.items():
        await storage_adapter.set_config(key, value)
    await reload_config()
    return persisted_config


async def evaluate_rollout_decision(
    *,
    before_perf_path: str,
    after_perf_path: str,
    baseline_quality_path: str,
    candidate_quality_path: str,
    rollout_stage_percent: Optional[int] = None,
    rollback_trigger_latency_p95_ms: Optional[float] = None,
    latency_policy_mode: Optional[str] = None,
    rollback_trigger_latency_p95_improve_pct: Optional[float] = None,
    rollback_trigger_throughput_drop_pct: Optional[float] = None,
    rollback_trigger_quality_drop_pct: Optional[float] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    thresholds = await _resolve_effective_thresholds(
        rollout_stage_percent=rollout_stage_percent,
        rollback_trigger_latency_p95_ms=rollback_trigger_latency_p95_ms,
        latency_policy_mode=latency_policy_mode,
        rollback_trigger_latency_p95_improve_pct=rollback_trigger_latency_p95_improve_pct,
        rollback_trigger_throughput_drop_pct=rollback_trigger_throughput_drop_pct,
        rollback_trigger_quality_drop_pct=rollback_trigger_quality_drop_pct,
    )

    current_stage_percent = int(thresholds["current_stage_percent"])
    latency_threshold_ms = float(thresholds["latency_threshold_ms"])
    resolved_latency_policy_mode = str(thresholds["latency_policy_mode"])
    latency_improve_pct_threshold = float(thresholds["latency_improve_pct_threshold"])
    throughput_drop_pct_threshold = float(thresholds["throughput_drop_pct_threshold"])
    quality_drop_pct_threshold = float(thresholds["quality_drop_pct_threshold"])

    next_stage_percent = get_next_stage_percent(current_stage_percent)
    rollback_stage_percent = get_previous_stage_percent(current_stage_percent)

    before_perf_report, before_perf_error = _load_perf_report(before_perf_path)
    after_perf_report, after_perf_error = _load_perf_report(after_perf_path)

    if before_perf_error or after_perf_error:
        latency_gate = {
            "status": "BLOCKED",
            "reason": "missing_or_unreadable_perf_artifact",
            "before_perf_error": before_perf_error,
            "after_perf_error": after_perf_error,
            "latency_policy_mode": resolved_latency_policy_mode,
            "threshold_latency_p95_ms": latency_threshold_ms,
            "required_full_latency_p95_improve_percent": latency_improve_pct_threshold,
        }
        throughput_gate = {
            "status": "BLOCKED",
            "reason": "missing_or_unreadable_perf_artifact",
            "before_perf_error": before_perf_error,
            "after_perf_error": after_perf_error,
        }
    else:
        assert before_perf_report is not None
        assert after_perf_report is not None
        latency_gate = _latency_gate(
            before_report=before_perf_report,
            after_report=after_perf_report,
            latency_threshold_ms=latency_threshold_ms,
            latency_policy_mode=resolved_latency_policy_mode,
            latency_improve_pct_threshold=latency_improve_pct_threshold,
        )
        throughput_gate = _throughput_gate(
            before_report=before_perf_report,
            after_report=after_perf_report,
            throughput_drop_pct_threshold=throughput_drop_pct_threshold,
        )

    quality_gate = _quality_gate(
        baseline_quality_path=baseline_quality_path,
        candidate_quality_path=candidate_quality_path,
        quality_drop_pct_threshold=quality_drop_pct_threshold,
    )

    gates = {
        "latency": latency_gate,
        "throughput": throughput_gate,
        "quality": quality_gate,
    }
    blocked_gates = [
        name for name, gate in gates.items() if gate.get("status") == "BLOCKED"
    ]
    failed_gates = [
        name for name, gate in gates.items() if gate.get("status") == "FAIL"
    ]
    blocked_reasons = [
        f"{name}:{gate.get('reason', 'unknown')}"
        for name, gate in gates.items()
        if gate.get("status") == "BLOCKED"
    ]

    if blocked_gates:
        decision = "HOLD_BLOCKED"
        target_stage_percent = current_stage_percent
    elif failed_gates:
        decision = "ROLLBACK"
        target_stage_percent = rollback_stage_percent
    else:
        decision = "PROMOTE"
        target_stage_percent = next_stage_percent

    apply_requested = bool(apply)
    dry_run = not apply_requested
    applied = False
    apply_error = None
    apply_skipped_reason = None
    persisted_config: Dict[str, Any] = {}

    if apply_requested:
        if decision == "HOLD_BLOCKED":
            apply_skipped_reason = "decision_hold_blocked"
        else:
            try:
                persisted_config = await _persist_rollout_stage_with_feature_flags(
                    target_stage_percent
                )
                applied = True
            except Exception as exc:
                apply_error = f"{exc.__class__.__name__}:{exc}"

    return {
        "decision": decision,
        "current_stage_percent": current_stage_percent,
        "next_stage_percent": next_stage_percent,
        "rollback_stage_percent": rollback_stage_percent,
        "target_stage_percent": target_stage_percent,
        "feature_flags_for_target_stage": stage_percent_to_feature_flags(
            target_stage_percent
        ),
        "thresholds": {
            "latency_policy_mode": resolved_latency_policy_mode,
            "rollback_trigger_latency_p95_ms": latency_threshold_ms,
            "rollback_trigger_latency_p95_improve_pct": latency_improve_pct_threshold,
            "rollback_trigger_throughput_drop_pct": throughput_drop_pct_threshold,
            "rollback_trigger_quality_drop_pct": quality_drop_pct_threshold,
        },
        "blocked_gates": blocked_gates,
        "blocked_reason": ";".join(blocked_reasons) if blocked_reasons else None,
        "failed_gates": failed_gates,
        "gates": gates,
        "before_perf": before_perf_path,
        "after_perf": after_perf_path,
        "baseline_quality": baseline_quality_path,
        "candidate_quality": candidate_quality_path,
        "apply_requested": apply_requested,
        "dry_run": dry_run,
        "applied": applied,
        "apply_skipped_reason": apply_skipped_reason,
        "apply_error": apply_error,
        "persisted_config": persisted_config,
    }


def main() -> None:
    args = _parse_args()
    result = asyncio.run(
        evaluate_rollout_decision(
            before_perf_path=args.before_perf,
            after_perf_path=args.after_perf,
            baseline_quality_path=args.baseline_quality,
            candidate_quality_path=args.candidate_quality,
            rollout_stage_percent=args.rollout_stage_percent,
            rollback_trigger_latency_p95_ms=args.rollback_trigger_latency_p95_ms,
            latency_policy_mode=args.latency_policy_mode,
            rollback_trigger_latency_p95_improve_pct=args.rollback_trigger_latency_p95_improve_pct,
            rollback_trigger_throughput_drop_pct=args.rollback_trigger_throughput_drop_pct,
            rollback_trigger_quality_drop_pct=args.rollback_trigger_quality_drop_pct,
            apply=args.apply,
        )
    )
    _emit(result)


if __name__ == "__main__":
    main()

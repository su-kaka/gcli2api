import asyncio
import json
from pathlib import Path

from scripts.perf import rollout_guard


def _write_json(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _perf_report(*, full_latency_p95: float, reqps: float, tokensps: float) -> dict:
    return {
        "meta": {"duration": 30},
        "summary": {
            "request_count": 12,
            "full_latency_ms": {"p95": full_latency_p95},
            "reqps": reqps,
            "tokensps": tokensps,
        },
        "samples": [{"full_latency_ms": full_latency_p95, "total_tokens": 100}],
    }


def _quality_report(*, quality_score: float) -> dict:
    return {
        "summary": {
            "quality_score": quality_score,
        }
    }


def test_rollout_guard_all_pass_promotes_next_stage(tmp_path: Path):
    before_perf = _write_json(
        tmp_path / "before_perf.json",
        _perf_report(full_latency_p95=1800, reqps=10.0, tokensps=100.0),
    )
    after_perf = _write_json(
        tmp_path / "after_perf.json",
        _perf_report(full_latency_p95=1400, reqps=9.0, tokensps=92.0),
    )
    baseline_quality = _write_json(
        tmp_path / "baseline_quality.json", _quality_report(quality_score=90.0)
    )
    candidate_quality = _write_json(
        tmp_path / "candidate_quality.json", _quality_report(quality_score=85.0)
    )

    result = asyncio.run(
        rollout_guard.evaluate_rollout_decision(
            before_perf_path=before_perf,
            after_perf_path=after_perf,
            baseline_quality_path=baseline_quality,
            candidate_quality_path=candidate_quality,
            rollout_stage_percent=20,
            rollback_trigger_latency_p95_ms=2000,
            rollback_trigger_throughput_drop_pct=20,
            rollback_trigger_quality_drop_pct=10,
            apply=False,
        )
    )

    assert result["decision"] == "PROMOTE"
    assert result["current_stage_percent"] == 20
    assert result["next_stage_percent"] == 50
    assert result["target_stage_percent"] == 50
    assert result["failed_gates"] == []
    assert result["blocked_gates"] == []
    assert (
        result["thresholds"]["latency_policy_mode"]
        == rollout_guard.LATENCY_POLICY_MODE_ABSOLUTE_P95_CAP
    )
    assert result["thresholds"]["rollback_trigger_latency_p95_improve_pct"] == 0.0
    assert (
        result["gates"]["latency"]["latency_policy_mode"]
        == rollout_guard.LATENCY_POLICY_MODE_ABSOLUTE_P95_CAP
    )
    assert result["dry_run"] is True
    assert result["applied"] is False


def test_rollout_guard_gate_fail_rolls_back(tmp_path: Path):
    before_perf = _write_json(
        tmp_path / "before_perf.json",
        _perf_report(full_latency_p95=1700, reqps=10.0, tokensps=100.0),
    )
    after_perf = _write_json(
        tmp_path / "after_perf.json",
        _perf_report(full_latency_p95=2800, reqps=9.5, tokensps=99.0),
    )
    baseline_quality = _write_json(
        tmp_path / "baseline_quality.json", _quality_report(quality_score=90.0)
    )
    candidate_quality = _write_json(
        tmp_path / "candidate_quality.json", _quality_report(quality_score=89.0)
    )

    result = asyncio.run(
        rollout_guard.evaluate_rollout_decision(
            before_perf_path=before_perf,
            after_perf_path=after_perf,
            baseline_quality_path=baseline_quality,
            candidate_quality_path=candidate_quality,
            rollout_stage_percent=50,
            rollback_trigger_latency_p95_ms=2500,
            rollback_trigger_throughput_drop_pct=20,
            rollback_trigger_quality_drop_pct=10,
            apply=False,
        )
    )

    assert result["decision"] == "ROLLBACK"
    assert result["current_stage_percent"] == 50
    assert result["rollback_stage_percent"] == 20
    assert result["target_stage_percent"] == 20
    assert "latency" in result["failed_gates"]
    assert result["blocked_gates"] == []
    assert (
        result["gates"]["latency"]["latency_policy_mode"]
        == rollout_guard.LATENCY_POLICY_MODE_ABSOLUTE_P95_CAP
    )


def test_rollout_guard_blocked_signal_holds_even_with_apply_requested(tmp_path: Path):
    before_payload = _perf_report(full_latency_p95=1700, reqps=10.0, tokensps=100.0)
    after_payload = _perf_report(full_latency_p95=1400, reqps=9.5, tokensps=95.0)
    after_payload["meta"]["blocker"] = "artifact_incomplete"

    before_perf = _write_json(tmp_path / "before_perf.json", before_payload)
    after_perf = _write_json(tmp_path / "after_perf.json", after_payload)
    baseline_quality = _write_json(
        tmp_path / "baseline_quality.json", _quality_report(quality_score=90.0)
    )
    candidate_quality = _write_json(
        tmp_path / "candidate_quality.json", _quality_report(quality_score=89.0)
    )

    result = asyncio.run(
        rollout_guard.evaluate_rollout_decision(
            before_perf_path=before_perf,
            after_perf_path=after_perf,
            baseline_quality_path=baseline_quality,
            candidate_quality_path=candidate_quality,
            rollout_stage_percent=20,
            rollback_trigger_latency_p95_ms=2500,
            rollback_trigger_throughput_drop_pct=20,
            rollback_trigger_quality_drop_pct=10,
            apply=True,
        )
    )

    assert result["decision"] == "HOLD_BLOCKED"
    assert result["target_stage_percent"] == 20
    assert "latency" in result["blocked_gates"]
    assert "throughput" in result["blocked_gates"]
    assert result["applied"] is False
    assert result["apply_skipped_reason"] == "decision_hold_blocked"


def test_rollout_guard_relative_latency_mode_promotes_on_required_improvement(
    tmp_path: Path,
):
    before_perf = _write_json(
        tmp_path / "before_perf.json",
        _perf_report(full_latency_p95=5000, reqps=10.0, tokensps=100.0),
    )
    after_perf = _write_json(
        tmp_path / "after_perf.json",
        _perf_report(full_latency_p95=3500, reqps=9.5, tokensps=95.0),
    )
    baseline_quality = _write_json(
        tmp_path / "baseline_quality.json", _quality_report(quality_score=90.0)
    )
    candidate_quality = _write_json(
        tmp_path / "candidate_quality.json", _quality_report(quality_score=89.0)
    )

    result = asyncio.run(
        rollout_guard.evaluate_rollout_decision(
            before_perf_path=before_perf,
            after_perf_path=after_perf,
            baseline_quality_path=baseline_quality,
            candidate_quality_path=candidate_quality,
            rollout_stage_percent=20,
            rollback_trigger_latency_p95_ms=2500,
            latency_policy_mode=rollout_guard.LATENCY_POLICY_MODE_RELATIVE_FULL_P95_IMPROVE,
            rollback_trigger_latency_p95_improve_pct=20,
            rollback_trigger_throughput_drop_pct=20,
            rollback_trigger_quality_drop_pct=10,
            apply=False,
        )
    )

    assert result["decision"] == "PROMOTE"
    assert result["failed_gates"] == []
    assert result["blocked_gates"] == []
    assert (
        result["thresholds"]["latency_policy_mode"]
        == rollout_guard.LATENCY_POLICY_MODE_RELATIVE_FULL_P95_IMPROVE
    )
    assert result["thresholds"]["rollback_trigger_latency_p95_improve_pct"] == 20.0
    assert (
        result["gates"]["latency"]["reason"]
        == "full_latency_p95_improve_within_threshold"
    )
    assert result["gates"]["latency"]["full_latency_p95_improve_percent"] == 30.0


def test_rollout_guard_relative_latency_mode_fails_when_improvement_is_too_small(
    tmp_path: Path,
):
    before_perf = _write_json(
        tmp_path / "before_perf.json",
        _perf_report(full_latency_p95=5000, reqps=10.0, tokensps=100.0),
    )
    after_perf = _write_json(
        tmp_path / "after_perf.json",
        _perf_report(full_latency_p95=4800, reqps=9.9, tokensps=99.0),
    )
    baseline_quality = _write_json(
        tmp_path / "baseline_quality.json", _quality_report(quality_score=90.0)
    )
    candidate_quality = _write_json(
        tmp_path / "candidate_quality.json", _quality_report(quality_score=89.0)
    )

    result = asyncio.run(
        rollout_guard.evaluate_rollout_decision(
            before_perf_path=before_perf,
            after_perf_path=after_perf,
            baseline_quality_path=baseline_quality,
            candidate_quality_path=candidate_quality,
            rollout_stage_percent=50,
            rollback_trigger_latency_p95_ms=2500,
            latency_policy_mode=rollout_guard.LATENCY_POLICY_MODE_RELATIVE_FULL_P95_IMPROVE,
            rollback_trigger_latency_p95_improve_pct=10,
            rollback_trigger_throughput_drop_pct=20,
            rollback_trigger_quality_drop_pct=10,
            apply=False,
        )
    )

    assert result["decision"] == "ROLLBACK"
    assert result["target_stage_percent"] == 20
    assert "latency" in result["failed_gates"]
    assert result["gates"]["latency"]["status"] == "FAIL"
    assert (
        result["gates"]["latency"]["reason"]
        == "full_latency_p95_improve_below_threshold"
    )


def test_stage_ladder_bounds_floor_and_ceiling():
    assert rollout_guard.get_previous_stage_percent(5) == 5
    assert rollout_guard.get_next_stage_percent(100) == 100


def test_stage_percent_to_feature_flags_mapping():
    assert rollout_guard.stage_percent_to_feature_flags(5) == {
        "ff_retry_policy_v2": True,
        "ff_http2_pool_tuning": False,
        "ff_converter_fast_path": False,
        "ff_preview_credential_scheduler_v2": False,
    }
    assert rollout_guard.stage_percent_to_feature_flags(20) == {
        "ff_retry_policy_v2": True,
        "ff_http2_pool_tuning": True,
        "ff_converter_fast_path": False,
        "ff_preview_credential_scheduler_v2": False,
    }
    assert rollout_guard.stage_percent_to_feature_flags(50) == {
        "ff_retry_policy_v2": True,
        "ff_http2_pool_tuning": True,
        "ff_converter_fast_path": True,
        "ff_preview_credential_scheduler_v2": False,
    }
    assert rollout_guard.stage_percent_to_feature_flags(100) == {
        "ff_retry_policy_v2": True,
        "ff_http2_pool_tuning": True,
        "ff_converter_fast_path": True,
        "ff_preview_credential_scheduler_v2": True,
    }


def test_apply_mode_persists_stage_and_feature_flags(monkeypatch, tmp_path: Path):
    before_perf = _write_json(
        tmp_path / "before_perf.json",
        _perf_report(full_latency_p95=1800, reqps=10.0, tokensps=100.0),
    )
    after_perf = _write_json(
        tmp_path / "after_perf.json",
        _perf_report(full_latency_p95=1400, reqps=9.0, tokensps=95.0),
    )
    baseline_quality = _write_json(
        tmp_path / "baseline_quality.json", _quality_report(quality_score=90.0)
    )
    candidate_quality = _write_json(
        tmp_path / "candidate_quality.json", _quality_report(quality_score=89.0)
    )

    class _FakeStorageAdapter:
        def __init__(self):
            self.calls = []

        async def set_config(self, key, value):
            self.calls.append((key, value))
            return True

    fake_storage = _FakeStorageAdapter()

    async def _fake_get_storage_adapter():
        return fake_storage

    reload_calls = {"count": 0}

    async def _fake_reload_config():
        reload_calls["count"] += 1

    monkeypatch.setattr(rollout_guard, "get_storage_adapter", _fake_get_storage_adapter)
    monkeypatch.setattr(rollout_guard, "reload_config", _fake_reload_config)

    result = asyncio.run(
        rollout_guard.evaluate_rollout_decision(
            before_perf_path=before_perf,
            after_perf_path=after_perf,
            baseline_quality_path=baseline_quality,
            candidate_quality_path=candidate_quality,
            rollout_stage_percent=5,
            rollback_trigger_latency_p95_ms=2000,
            rollback_trigger_throughput_drop_pct=20,
            rollback_trigger_quality_drop_pct=10,
            apply=True,
        )
    )

    assert result["decision"] == "PROMOTE"
    assert result["target_stage_percent"] == 20
    assert result["applied"] is True
    assert reload_calls["count"] == 1
    assert dict(fake_storage.calls) == {
        "rollout_stage_percent": 20,
        "ff_retry_policy_v2": True,
        "ff_http2_pool_tuning": True,
        "ff_converter_fast_path": False,
        "ff_preview_credential_scheduler_v2": False,
    }

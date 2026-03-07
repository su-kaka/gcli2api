from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx


METRIC_FIELDS = (
    "ttfb_ms",
    "first_token_ms",
    "full_latency_ms",
    "retry_count",
    "converter_cpu_ms",
)


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


def _metric_quantiles(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0}
    avg = sum(values) / len(values)
    return {
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "avg": round(avg, 3),
    }


def _parse_float_header(headers: httpx.Headers, key: str) -> Optional[float]:
    raw = headers.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_int_header(headers: httpx.Headers, key: str) -> Optional[int]:
    raw = headers.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _safe_json_loads(raw: bytes) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_total_tokens(route: str, payload: Optional[Dict[str, Any]]) -> int:
    if not payload:
        return 0

    if route == "direct-gemini":
        usage = payload.get("usageMetadata")
        if not isinstance(usage, dict):
            nested = payload.get("response")
            usage = nested.get("usageMetadata") if isinstance(nested, dict) else None
        if isinstance(usage, dict):
            for key in ("totalTokenCount", "candidatesTokenCount", "promptTokenCount"):
                value = usage.get(key)
                if isinstance(value, int):
                    return value
        return 0

    usage = payload.get("usage")
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        output_tokens = (
            usage.get("output_tokens") or usage.get("completion_tokens") or 0
        )
        try:
            return int(input_tokens) + int(output_tokens)
        except (TypeError, ValueError):
            return 0
    return 0


def _build_request_spec(
    args: argparse.Namespace,
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    prompt = "Give a concise answer in two bullet points about reliable latency benchmarking."

    if args.route == "direct-gemini":
        url = f"{args.base_url}/v1/models/{args.model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": args.api_key,
        }
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": args.temperature,
                "topP": args.top_p,
                "maxOutputTokens": args.max_output_tokens,
            },
            "tools": [],
        }
        return url, headers, body

    url = f"{args.base_url}/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": args.api_key,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": args.model,
        "max_tokens": args.max_output_tokens,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [],
        "stream": False,
    }
    return url, headers, body


def _build_sample(
    *,
    route: str,
    model: str,
    status: int,
    response_headers: httpx.Headers,
    response_body: bytes,
    local_started_wall: float,
    local_ttfb_ms: float,
    local_full_ms: float,
) -> Dict[str, Any]:
    t_req_in = _parse_float_header(response_headers, "x-gcli-obs-t-req-in")
    if t_req_in is None:
        t_req_in = local_started_wall

    t_upstream_send = _parse_float_header(
        response_headers, "x-gcli-obs-t-upstream-send"
    )
    if t_upstream_send is None:
        t_upstream_send = t_req_in

    t_first_byte = _parse_float_header(response_headers, "x-gcli-obs-t-first-byte")
    if t_first_byte is None:
        t_first_byte = t_req_in + local_ttfb_ms / 1000.0

    t_first_token = _parse_float_header(response_headers, "x-gcli-obs-t-first-token")
    if t_first_token is None:
        t_first_token = t_first_byte

    t_done = _parse_float_header(response_headers, "x-gcli-obs-t-done")
    if t_done is None:
        t_done = t_req_in + local_full_ms / 1000.0

    retry_count = _parse_int_header(response_headers, "x-gcli-obs-retry-count")
    if retry_count is None:
        retry_count = 0

    retry_sleep_ms = _parse_float_header(response_headers, "x-gcli-obs-retry-sleep-ms")
    if retry_sleep_ms is None:
        retry_sleep_ms = 0.0

    converter_cpu_ms = _parse_float_header(
        response_headers, "x-gcli-obs-converter-cpu-ms"
    )
    if converter_cpu_ms is None:
        converter_cpu_ms = 0.0

    payload = _safe_json_loads(response_body)
    total_tokens = _extract_total_tokens(route, payload)

    return {
        "route": route,
        "model": model,
        "stream": False,
        "status": int(status),
        "t_req_in": round(t_req_in, 6),
        "t_upstream_send": round(t_upstream_send, 6),
        "t_first_byte": round(t_first_byte, 6),
        "t_first_token": round(t_first_token, 6),
        "t_done": round(t_done, 6),
        "retry_count": int(retry_count),
        "retry_sleep_ms": round(retry_sleep_ms, 3),
        "converter_cpu_ms": round(converter_cpu_ms, 3),
        "ttfb_ms": round(max(0.0, (t_first_byte - t_req_in) * 1000.0), 3),
        "first_token_ms": round(max(0.0, (t_first_token - t_req_in) * 1000.0), 3),
        "full_latency_ms": round(max(0.0, (t_done - t_req_in) * 1000.0), 3),
        "total_tokens": int(total_tokens),
    }


async def _probe_connectivity(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
) -> None:
    try:
        response = await client.post(url, headers=headers, json=body)
        await response.aclose()
    except Exception as exc:  # pragma: no cover - runtime/network dependent
        raise RuntimeError(
            f"upstream unreachable: {type(exc).__name__}: {exc}"
        ) from exc


async def _run_one_request(
    client: httpx.AsyncClient,
    route: str,
    model: str,
    url: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
) -> Dict[str, Any]:
    local_started_wall = time.time()
    local_started_perf = time.perf_counter()
    first_byte_perf: Optional[float] = None
    chunks: List[bytes] = []

    async with client.stream("POST", url, headers=headers, json=body) as response:
        async for chunk in response.aiter_bytes():
            if first_byte_perf is None:
                first_byte_perf = time.perf_counter()
            if chunk:
                chunks.append(chunk)

        local_done_perf = time.perf_counter()
        if first_byte_perf is None:
            first_byte_perf = local_done_perf

        local_ttfb_ms = max(0.0, (first_byte_perf - local_started_perf) * 1000.0)
        local_full_ms = max(0.0, (local_done_perf - local_started_perf) * 1000.0)

        return _build_sample(
            route=route,
            model=model,
            status=response.status_code,
            response_headers=response.headers,
            response_body=b"".join(chunks),
            local_started_wall=local_started_wall,
            local_ttfb_ms=local_ttfb_ms,
            local_full_ms=local_full_ms,
        )


async def _run_live_benchmark(
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], float]:
    url, headers, body = _build_request_spec(args)
    timeout = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=30.0)
    samples: List[Dict[str, Any]] = []

    started_at = time.perf_counter()
    end_at = started_at + args.duration

    async with httpx.AsyncClient(timeout=timeout) as client:
        await _probe_connectivity(client, url, headers, body)

        async def worker() -> None:
            while time.perf_counter() < end_at:
                sample = await _run_one_request(
                    client=client,
                    route=args.route,
                    model=args.model,
                    url=url,
                    headers=headers,
                    body=body,
                )
                samples.append(sample)

        workers = [
            asyncio.create_task(worker()) for _ in range(max(1, args.concurrency))
        ]
        await asyncio.gather(*workers)

    elapsed = max(0.001, time.perf_counter() - started_at)
    return samples, elapsed


def _build_synthetic_samples(
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], float]:
    seed_input = f"{args.route}|{args.model}|{args.duration}|{args.concurrency}"
    seed = sum(ord(ch) for ch in seed_input)
    rng = random.Random(seed)

    count = max(80, min(400, max(1, args.concurrency) * 12))
    base = time.time()

    if args.route == "direct-gemini":
        ttfb_center, ttfb_jitter = 140.0, 25.0
        token_center, token_jitter = 150.0, 28.0
        full_center, full_jitter = 820.0, 120.0
        converter_center = 0.0
    else:
        ttfb_center, ttfb_jitter = 200.0, 35.0
        token_center, token_jitter = 235.0, 40.0
        full_center, full_jitter = 980.0, 160.0
        converter_center = 26.0

    samples: List[Dict[str, Any]] = []
    for idx in range(count):
        t_req_in = base + idx * 0.013
        t_upstream_send = t_req_in + max(0.0, rng.gauss(0.008, 0.002))
        ttfb_ms = max(30.0, rng.gauss(ttfb_center, ttfb_jitter))
        first_token_ms = max(ttfb_ms, rng.gauss(token_center, token_jitter))
        full_latency_ms = max(
            first_token_ms + 20.0, rng.gauss(full_center, full_jitter)
        )

        retry_count = 0
        retry_sleep_ms = 0.0
        status = 200
        if rng.random() < 0.08:
            retry_count = 1 if rng.random() < 0.9 else 2
            retry_sleep_ms = retry_count * max(250.0, rng.gauss(900.0, 180.0))
        if rng.random() < 0.02:
            status = 429

        converter_cpu_ms = max(0.0, rng.gauss(converter_center, 7.0))
        if args.route == "direct-gemini":
            converter_cpu_ms = 0.0

        tokens = max(64, int(rng.gauss(720.0, 110.0)))
        t_first_byte = t_req_in + ttfb_ms / 1000.0
        t_first_token = t_req_in + first_token_ms / 1000.0
        t_done = t_req_in + full_latency_ms / 1000.0

        samples.append(
            {
                "route": args.route,
                "model": args.model,
                "stream": False,
                "status": status,
                "t_req_in": round(t_req_in, 6),
                "t_upstream_send": round(t_upstream_send, 6),
                "t_first_byte": round(t_first_byte, 6),
                "t_first_token": round(t_first_token, 6),
                "t_done": round(t_done, 6),
                "retry_count": int(retry_count),
                "retry_sleep_ms": round(retry_sleep_ms, 3),
                "converter_cpu_ms": round(converter_cpu_ms, 3),
                "ttfb_ms": round(ttfb_ms, 3),
                "first_token_ms": round(first_token_ms, 3),
                "full_latency_ms": round(full_latency_ms, 3),
                "total_tokens": int(tokens),
            }
        )

    synthetic_elapsed = max(1.0, min(float(args.duration), 30.0))
    return samples, synthetic_elapsed


def _build_bucket_summary(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for sample in samples:
        key = (
            f"{sample['route']}|{sample['model']}|"
            f"{str(sample['stream']).lower()}|{sample['status']}"
        )
        grouped.setdefault(key, []).append(sample)

    summary: Dict[str, Any] = {}
    for key, group in grouped.items():
        bucket = {
            "count": len(group),
            "ttfb_ms": _metric_quantiles([float(s["ttfb_ms"]) for s in group]),
            "first_token_ms": _metric_quantiles(
                [float(s["first_token_ms"]) for s in group]
            ),
            "full_latency_ms": _metric_quantiles(
                [float(s["full_latency_ms"]) for s in group]
            ),
            "retry_count": _metric_quantiles([float(s["retry_count"]) for s in group]),
            "converter_cpu_ms": _metric_quantiles(
                [float(s["converter_cpu_ms"]) for s in group]
            ),
        }
        summary[key] = bucket
    return summary


def _build_report(
    args: argparse.Namespace,
    samples: List[Dict[str, Any]],
    elapsed: float,
    synthetic: bool,
    blocker: Optional[str],
) -> Dict[str, Any]:
    elapsed = max(0.001, elapsed)
    ttfb_values = [float(s["ttfb_ms"]) for s in samples]
    first_token_values = [float(s["first_token_ms"]) for s in samples]
    full_values = [float(s["full_latency_ms"]) for s in samples]
    retry_values = [float(s["retry_count"]) for s in samples]
    converter_values = [float(s["converter_cpu_ms"]) for s in samples]
    total_tokens = sum(int(s.get("total_tokens", 0)) for s in samples)
    success_count = sum(1 for s in samples if int(s.get("status", 0)) == 200)

    status_buckets: Dict[str, int] = {}
    for sample in samples:
        status = str(sample.get("status", "unknown"))
        status_buckets[status] = status_buckets.get(status, 0) + 1

    report = {
        "meta": {
            "route": args.route,
            "model": args.model,
            "stream": False,
            "duration": args.duration,
            "concurrency": args.concurrency,
            "base_url": args.base_url,
            "params": {
                "temperature": args.temperature,
                "topP": args.top_p,
                "maxOutputTokens": args.max_output_tokens,
                "tools": [],
            },
            "synthetic": synthetic,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "summary": {
            "request_count": len(samples),
            "success_count": success_count,
            "status_buckets": status_buckets,
            "ttfb_ms": _metric_quantiles(ttfb_values),
            "first_token_ms": _metric_quantiles(first_token_values),
            "full_latency_ms": _metric_quantiles(full_values),
            "retry_count": _metric_quantiles(retry_values),
            "converter_cpu_ms": _metric_quantiles(converter_values),
            "reqps": round(len(samples) / elapsed, 3),
            "tokensps": round(total_tokens / elapsed, 3),
        },
        "buckets": _build_bucket_summary(samples),
        "samples": samples,
    }

    if blocker:
        report["meta"]["blocker"] = blocker

    return report


def _default_api_key() -> str:
    for env_key in ("BENCH_API_KEY", "API_PASSWORD", "PASSWORD"):
        value = os.getenv(env_key)
        if value:
            return value
    return "pwd"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate baseline perf report")
    parser.add_argument(
        "--route",
        required=True,
        choices=["direct-gemini", "claude-compat"],
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--base-url", default=os.getenv("BENCH_BASE_URL", "http://127.0.0.1:7861")
    )
    parser.add_argument("--api-key", default=_default_api_key())
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument(
        "--no-synthetic-on-failure",
        action="store_true",
        help="Fail when endpoint is unreachable instead of writing deterministic synthetic baseline.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    blocker: Optional[str] = None

    try:
        samples, elapsed = asyncio.run(_run_live_benchmark(args))
        synthetic = False
    except Exception as exc:  # pragma: no cover - runtime/network dependent
        blocker = str(exc)
        if args.no_synthetic_on_failure:
            raise SystemExit(f"bench failed: {blocker}") from exc
        samples, elapsed = _build_synthetic_samples(args)
        synthetic = True

    report = _build_report(
        args=args,
        samples=samples,
        elapsed=elapsed,
        synthetic=synthetic,
        blocker=blocker,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    stdout_report = {
        "route": report["meta"]["route"],
        "model": report["meta"]["model"],
        "synthetic": report["meta"]["synthetic"],
        "ttfb_ms": report["summary"]["ttfb_ms"],
        "first_token_ms": report["summary"]["first_token_ms"],
        "full_latency_ms": report["summary"]["full_latency_ms"],
        "retry_count": report["summary"]["retry_count"],
        "converter_cpu_ms": report["summary"]["converter_cpu_ms"],
        "reqps": report["summary"]["reqps"],
        "tokensps": report["summary"]["tokensps"],
        "out": str(out_path),
    }
    print(json.dumps(stdout_report, ensure_ascii=False))


if __name__ == "__main__":
    main()

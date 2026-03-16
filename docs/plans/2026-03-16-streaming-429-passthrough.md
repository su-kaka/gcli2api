# Streaming 429 Passthrough Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make all six streaming routers return the upstream HTTP 429 directly instead of `200 + SSE error` when the first upstream result is a rate-limit failure.

**Architecture:** Add a small router-level streaming helper that can prefetch the first async item and decide whether to return a plain `Response` or a `StreamingResponse`. Update normal, fake, and anti-truncation streaming paths so they surface a first-item `Response(status=429)` before the framework commits `HTTP 200`.

**Tech Stack:** FastAPI, pytest, async generators, existing GeminiCLI/Antigravity router modules.

---

### Task 1: Write the failing router regression tests

**Files:**
- Create: `tests/test_streaming_error_passthrough.py`

**Step 1: Write the failing test**

Add parametrized tests that hit all six routers in three streaming modes:
- normal streaming
- `假流式/`
- `流式抗截断/`

Each test should simulate an upstream `429` and assert the final HTTP status is `429`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_streaming_error_passthrough.py -v`

Expected: one or more failures showing the current behavior still returns `200`.

### Task 2: Add the streaming first-item passthrough helper

**Files:**
- Create: `src/router/stream_passthrough.py`

**Step 1: Write minimal helper**

Implement:
- a helper to prepend a prefetched async item back into an async iterator
- a helper that reads the first async item and returns either:
  - the original `Response`, or
  - a `StreamingResponse` that starts with the prefetched first item

**Step 2: Run the targeted tests**

Run: `pytest tests/test_streaming_error_passthrough.py -v`

Expected: still failing, because routers are not wired yet.

### Task 3: Wire the helper into GeminiCLI routers

**Files:**
- Modify: `src/router/geminicli/openai.py`
- Modify: `src/router/geminicli/gemini.py`
- Modify: `src/router/geminicli/anthropic.py`

**Step 1: Update normal streaming**

Make normal streaming generators surface a first yielded `Response` object before SSE conversion.

**Step 2: Update fake streaming**

Move fake-stream upstream request resolution ahead of SSE emission so a `429` can be returned as a plain HTTP response.

**Step 3: Update anti-truncation**

Preflight the first upstream attempt before creating the anti-truncation `StreamingResponse`.

**Step 4: Run tests**

Run: `pytest tests/test_streaming_error_passthrough.py -v`

Expected: Antigravity cases still failing, GeminiCLI cases passing.

### Task 4: Wire the helper into Antigravity routers

**Files:**
- Modify: `src/router/antigravity/openai.py`
- Modify: `src/router/antigravity/gemini.py`
- Modify: `src/router/antigravity/anthropic.py`

**Step 1: Apply the same routing changes**

Mirror the GeminiCLI behavior so all Antigravity routers use the same passthrough semantics.

**Step 2: Run tests**

Run: `pytest tests/test_streaming_error_passthrough.py -v`

Expected: all tests pass.

### Task 5: Verify the full fix

**Files:**
- No file changes

**Step 1: Run focused verification**

Run: `pytest tests/test_streaming_error_passthrough.py -v`

Expected: all tests pass.

**Step 2: Commit**

```bash
git add docs/plans/2026-03-16-streaming-429-passthrough.md tests/test_streaming_error_passthrough.py src/router/stream_passthrough.py src/router/geminicli/openai.py src/router/geminicli/gemini.py src/router/geminicli/anthropic.py src/router/antigravity/openai.py src/router/antigravity/gemini.py src/router/antigravity/anthropic.py
git commit -m "fix: passthrough upstream 429 for streaming routes"
```

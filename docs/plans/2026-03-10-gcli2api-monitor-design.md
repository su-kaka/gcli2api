# GCLI2API Multi-Target Monitor Design

## Summary

Build an independently deployable monitoring service for one or more remote `gcli2api` instances. The monitor runs on a separate server, authenticates to each target with the panel password over HTTP, polls the panel JSON APIs, stores snapshots in SQLite, and serves a detailed dashboard for current status, model cooldowns, recovery windows, and historical events.

## Goals

- Monitor multiple `gcli2api` projects from one service.
- Log in automatically with panel password and re-login on `401`.
- Poll credential status every 60 seconds.
- Poll Antigravity quota every 5 minutes.
- Show a detailed dashboard with current counts, cooldown recovery windows, per-project health, and per-credential details.
- Store raw snapshots for 7 days and aggregated data for 30 days.

## Non-Goals

- Browser automation or DOM scraping of the upstream panel.
- Multi-node deployment of the monitor itself.
- HTTPS termination inside the app.
- Real-time push from `gcli2api`; polling is sufficient for v1.

## Why JSON APIs Instead Of HTML Parsing

The upstream panel already reads from JSON endpoints rather than deriving status from rendered DOM. The monitor should consume the same sources:

- `POST /auth/login`
- `GET /creds/status?mode=geminicli`
- `GET /creds/status?mode=antigravity`
- `GET /creds/quota/{filename}?mode=antigravity`

This avoids selector drift, removes browser dependencies, and gives direct access to `model_cooldowns`, `disabled`, `error_codes`, and `last_success`.

## Deployment Model

The monitor is a separate service that can run on any server that can reach the target `gcli2api` URLs.

Required per target:

- `name`
- `base_url`
- `panel_password`
- `enabled`

Global defaults:

- status poll interval: 60 seconds
- quota poll interval: 5 minutes

## High-Level Architecture

The service is a single FastAPI application with four internal parts:

1. `collector`
   Periodic async jobs poll remote `gcli2api` targets.

2. `storage`
   SQLite in WAL mode stores targets, snapshots, cooldown rows, events, and aggregates.

3. `monitor API`
   Local JSON API for target management and dashboard reads.

4. `dashboard UI`
   HTML and JavaScript views that render current and historical monitoring data.

## Data Flow

1. Collector loads enabled targets from SQLite.
2. For each target, collector obtains or reuses bearer token.
3. Collector fetches both credential status endpoints.
4. Collector normalizes records into:
   - credential snapshot rows
   - credential model cooldown rows
   - target summary snapshot rows
5. Every 5 minutes, collector fetches Antigravity quota for each visible Antigravity credential.
6. Collector stores event rows for login failures, request failures, offline targets, and credential state transitions.
7. Dashboard API reads current snapshots and aggregate windows from SQLite.

## Authentication Strategy

Each target has an independent auth state:

- keep token in memory for the running process
- attempt normal request with cached token
- on `401`, re-login once and retry the failed request once
- if login still fails, mark target as degraded and emit event

This keeps login traffic low and isolates failures between targets.

## Normalized Data Model

### Target

- `id`
- `name`
- `base_url`
- `panel_password`
- `enabled`
- `status_poll_seconds`
- `quota_poll_seconds`
- `created_at`
- `updated_at`

### Target Summary Snapshot

- `target_id`
- `captured_at`
- `online`
- `credential_total`
- `credential_disabled_total`
- `credential_in_cooldown_total`
- `model_in_cooldown_total`
- `last_error`

### Credential Snapshot

- `target_id`
- `captured_at`
- `mode`
- `filename`
- `user_email`
- `disabled`
- `error_codes_json`
- `last_success`
- `preview`
- `earliest_recovery_at`
- `has_active_cooldown`

### Credential Model Cooldown

- `target_id`
- `captured_at`
- `mode`
- `filename`
- `raw_model_name`
- `display_model_name`
- `cooldown_until`
- `remaining_seconds`
- `is_active`

### Quota Snapshot

- `target_id`
- `captured_at`
- `filename`
- `models_json`
- `success`
- `error`

### Event

- `target_id`
- `captured_at`
- `level`
- `event_type`
- `message`
- `details_json`

### Aggregate Bucket

Daily or hourly aggregate rows for dashboard trend queries with 30-day retention.

## Cooldown Display Rules

The UI should match the operator mental model rather than expose raw API names first.

Examples:

- `gemini-2.5-pro` -> `2.5-pro`
- `gemini-3.1-pro-preview` -> `3.1-pro-preview`

For each credential:

- show all active cooldown models
- show formatted remaining duration such as `58m22s` or `1h6m17s`
- show the earliest recovery time for that credential

The raw model name remains available in detail views and API responses.

## Dashboard Views

### 1. Global Overview

Cards:

- total targets
- online targets
- offline targets
- total credentials
- credentials currently in cooldown
- active cooldown model count
- earliest recovery
- latest recovery

### 2. Recovery Buckets

Primary view uses non-overlapping buckets:

- `0-1h`
- `1-2h`
- `2-3h`
- `3-5h`
- `>5h`

Optional toggle for cumulative view:

- `<=1h`
- `<=2h`
- `<=3h`
- `<=5h`
- `>5h`

### 3. Target List

One row per monitored project:

- name
- base URL
- online status
- last successful poll
- total credentials
- credentials in cooldown
- active cooldown models
- recent failures

### 4. Credential Table

Columns:

- target name
- mode
- filename
- email
- enabled or disabled state
- error codes
- preview state
- cooldown badges
- earliest recovery
- last success

### 5. Events

Recent stream of:

- login failures
- repeated request failures
- target offline transitions
- credential disabled transitions
- new error code detection

### 6. Target Management

CRUD UI for:

- add target
- edit target
- enable or disable target
- trigger manual poll

## Aggregation Rules

Derived metrics must be computed from active cooldown rows, not from badge text.

Definitions:

- credential in cooldown: a credential with at least one active cooldown row
- model in cooldown: one active cooldown row
- earliest recovery: minimum `cooldown_until` among active cooldown rows for a given credential

Recovery buckets use `remaining_seconds` at query time or at capture time depending on the report:

- current dashboard: compute from most recent snapshot
- historical trend: use captured aggregate rows

## Scheduling

Use two loops:

- status loop every 60 seconds
- quota loop every 5 minutes

Each target runs independently inside a bounded async concurrency model so one slow or dead target does not block others.

## Error Handling

- network failure: mark target poll failure and create warning event
- login failure: create error event and back off until next scheduled poll
- partial fetch failure: keep last known good current snapshot and record degraded event
- quota failure: do not fail the main poll, only record quota warning

## Retention

- raw snapshots: 7 days
- aggregate rows: 30 days
- retention cleanup runs daily

## Security Notes

The user explicitly accepted HTTP for upstream target communication. The monitor should still:

- keep panel passwords stored in SQLite and hidden in UI after save
- avoid logging passwords or bearer tokens
- avoid returning secrets from dashboard APIs

## Implementation Direction

The monitor should be built as a separate entrypoint in this repository so it can be deployed independently without changing the existing `gcli2api` runtime. Reuse existing project conventions:

- FastAPI for HTTP service
- plain HTML and JavaScript assets for dashboard
- `aiosqlite` for storage
- Hypercorn for serving

## Open Items Resolved

- deployment: separate service
- transport to upstream: HTTP
- polling: 60s status, 5min quota
- database: SQLite
- raw retention: 7 days
- aggregate retention: 30 days
- monitoring scope: multiple `gcli2api` targets

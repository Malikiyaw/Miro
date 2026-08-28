# Miro Core Architecture (V2 Plan Implementation)

Implementation of the "Miro V2 — Architecture Implementation Plan" following its
non-negotiable rules: no duplicate systems, full backwards compatibility,
existing systems stay primary, AI never bypasses permissions, all actions auditable.

## What exists now (`core/` package)

| Plan phase | Component | File |
|---|---|---|
| 4. Event Architecture | Internal event bus (`message.created`, `member.joined`, `action.executed`, ...) with isolated handlers | `core/event_bus.py` |
| 8. Unified Audit Logs | `AuditEvent` model (timestamp, actor_id, action, target, source, metadata), JSONL persistence in `data/audit/`, ring buffer for recent queries | `core/audit.py` |
| 9. Rate Limiting | Multi-tier token buckets (user / guild / command / ai / global_emergency) + emergency trip switch | `core/rate_limiter.py` |
| 2. Permission Engine | Central validation wrapping existing admin gate & Discord hierarchy | `core/permissions/engine.py`, `policies.py`, `roles.py`, `context.py` |
| 10. Miro Health | Subsystem watchdogs (gateway, scheduler, database, ai_client, event_bus) with ONLINE/DEGRADED/OFFLINE states and recovery callbacks; exposed on `/status` | `core/health.py`, `render_entrypoint.py` |
| 6. Job Queue | TaskScheduler upgrade: managed jobs with PENDING/RUNNING/SUCCESS/FAILED/RETRYING/CANCELLED states, exponential-backoff retries, timeouts (`schedule_job()`) — legacy `schedule_task()` untouched | `task_scheduler.py` |
| 3. Unified Analytics | Single shared event stream per guild fed by the bus, periodic flush into existing guild data (`analytics_stream` key) | `core/analytics_stream.py` |

## Integration points (non-destructive)

- `bot.py` constructs the core layer and publishes lifecycle events; every
  feature system keeps working unchanged.
- `actions.py::dispatch()` now enforces rate limits, writes unified audit
  records, and fans executed actions onto the event bus **on top of** the
  existing admin permission gate.
- `/status` (Render health endpoint) includes subsystem health and limiter state.
- Heavy work (analytics flush, health checks) runs off the gateway event loop.

## Deferred stages (per plan ordering)

- **Stage 3 — Intelligence Layer**: controlled AI Agent consolidation facade;
  AI moderation as secondary AutoMod classification layer (LOW/MEDIUM/HIGH).
- **Stage 5 — Setup Automation**: `/autosetup` preflight checks, smart
  reuse detection, resume & preview, rollback UX.
- Confirmation flow for dangerous actions flagged by `policies.SENSITIVE_ACTIONS`
  (they are currently audited loudly rather than blocked).

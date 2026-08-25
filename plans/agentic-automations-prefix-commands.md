# MIRO — Agentic Automations & Prefix Commands Implementation Plan

## Goal
The agent must be able to CREATE working automations and prefix commands —
not just store JSON that nothing reads. Every creation must produce a LIVE,
executing feature.

## Current state (audited)
| Piece | Status |
|---|---|
| `action_create_prefix_command` (+4 aliases) | Persists to `custom_commands` ✅ BUT **nothing ever executes them** — no runtime handler exists |
| `action_schedule_ai_action` | Calls `self.bot.scheduler.add_ai_task(...)` — **bot.scheduler doesn't exist**, TaskScheduler has no add_ai_task → always fails |
| `action_create_automation` (scheduled_task) | Same broken scheduler call |
| `action_create_automation` (auto_responder) | Writes key `auto_responder_<name>` — **wrong key/shape**; real AutoResponderSystem reads `auto_responder_config.responders[]` → created responders never fire |
| `action_create_automation` (reminder) | Writes `reminder_<name>` key — real ReminderSystem reads `scheduled_reminders[]` → never fires |

## Phase 1 — Scheduled automations actually run (`actions.py`)
Replace both `bot.scheduler.add_ai_task` calls:
```python
from core.agent_runtime import ...  # not needed
croniter = ...  # already a dependency
from task_scheduler import task_scheduler
next_run = croniter(cron, datetime.now()).get_next(float)

def _run_and_reschedule():
    # execute the wrapped action, then requeue at next cron time
    ...
task_scheduler.schedule_job(_run_and_reschedule, delay=(next_run-now))
```
- Register every automation in guild data `automations` registry
  `{name: {type, cron/action, params, created_by, created_at}}`
- New `action_delete_automation(name)` removes it + cancels the job
- Receipt returns `{automation_id, next_run}` so the agent can tell the user

## Phase 2 — Auto-responders route through the real system
Replace the wrong-key write with:
```python
ar = getattr(self.bot, "auto_responder")
ar.add_responder(guild_id, {"name": name, "keywords": triggers,
                            "response": response, "enabled": True})
```
(matches AutoResponderSystem's real API/schema — created responders FIRE.)

## Phase 3 — Reminders route into the real queue
Write proper `scheduled_reminders` entries (`reminder_time`, channel,
content) consumed by the existing ReminderSystem loop, or delegate to
ReminderSystem directly if the method exists.

## Phase 4 — Custom prefix commands EXECUTE (`bot.py`)
In `on_message`, fast-path BEFORE the AI pipeline:
```
if msg starts with configured prefix ("!") and rest in custom_commands:
    respond with stored template ({user} {server} {channel} substituted)
    skip AI entirely
```
Created commands become live immediately — zero restart needed.

## Phase 5 — Agent knows the tools
- `REQUIRED_PARAMS`: create_prefix_command(name, code),
  create_automation(type + trigger/schedule), schedule_ai_action(cron, ...)
- Playbook additions in AGENT_SYSTEM_PROMPT:
  - custom command: create_prefix_command {name, code}
  - automation: create_automation {type:auto_responder|scheduled_task|reminder,...}
  - deletion: delete_prefix_command / delete_automation

## Phase 6 — Proof tests (offline stubs)
1. E2E: agent creates prefix command → simulated "!hello" message → stored
   response sent, AI skipped
2. E2E: agent creates scheduled automation → TaskScheduler job registered
   with correct next-run
3. E2E: agent creates auto-responder → check_message fires response
4. Regression: native e2e + v7 critical still green

## Ship
Full py_compile → commit → `git push origin HEAD:main`.

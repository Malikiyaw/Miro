# MIRO — Universal Tool Guarantees Master Plan
## "Every tool executes like cleanup_duplicate_channels does"

## Current state (measured)
| Layer | Covered | Missing |
|---|---|---|
| ALLOWED_ACTIONS | 123 actions | — |
| ACTION_META (object/operation/danger/permission/verify) | 2 | **121** |
| TOOL_SPECS (native schema + description + params) | 20 | **103** |
| validate_params (pre-dispatch schema check) | 4 special-cases | generic system |
| Verifier branches | channels + delete_role + create | role ops, member ops, invites, events, configs |
| ensure_metadata (rule-based filler) | written, compiling | NOT WIRED into ActionHandler |

## Phase 1 — Wire metadata fill (5 min)
`actions.py::ActionHandler.__init__` after `self._artifacts = []`:
```python
        from core.action_meta import ensure_metadata
        ensure_metadata(self.ALLOWED_ACTIONS)
```
Result: all 123 actions get object_type / operation / danger / permission /
batch / confirm / verify — driving semantic gating, timeouts and confirmations.

## Phase 2 — Tool Registry = single source of truth (`agent/tool_registry.py`)
Add `ensure_full_catalog()`:
- For every ALLOWED_ACTION without a TOOL_SPECS entry, generate one from
  `_infer_action_meta(name)` + parameter heuristics:
  - object=channel → channel_id/name; create → name required
  - object=role → role_id/name; member ops → user_id (+role_id where named)
  - message ops → channel_name + content
  - *_system/setup_* → enabled:boolean
- Call it at module import so `provider_tool_schemas()` advertises ALL tools.
Result: model sees 123 typed tools instead of 20.

## Phase 3 — Parameter schemas + validation (`agent/tools.py`)
Single REQUIRED_PARAMS source used by BOTH validate_params() AND
native_tools schema generation (no drift):
```python
REQUIRED_PARAMS = {
    "create_channel": [("name","channel name")],
    "create_text_channel": [("name",...)], "create_voice_channel": [...],
    "delete_channel": [("channel_id",...)],
    "create_role": [("name",...)], "delete_role": [("role_id",...)],
    "assign_role": [("user_id",...),("role_id",...)],
    "remove_role": [("user_id",...),("role_id",...)],
    "ban_user"/"kick_user"/"warn_user": [("user_id",...)],
    "send_message": [("channel_name",...),("content",...)],
    "cleanup_duplicate_channels": [],   # name optional (server-wide mode)
    ...one row per mutating tool...
}
```
validate_params returns repair instructions listing missing fields.

## Phase 4 — Timeout tiers (`agent/policies.py`)
```
query tools            15s
send/reaction/pin      30s
create_*               60s
edit_/move_/lock_      60s
setup_/configure_     120s
bulk_delete_messages  120s
cleanup/bulk channels 300s
default                60s (raised from 30)
```

## Phase 5 — Verification matrix (`agent/verifier.py`)
| family | verify method |
|---|---|
| channel delete/bulk/cleanup | get_channel None / group rescan empty (done) |
| channel create/category | fetch_channel exists + name matches (done) |
| rename/edit_channel | fetch name == requested |
| lock/unlock/slowmode/topic | overwrites/attr matches |
| role create/delete | role exists / gone (done) |
| assign/remove_role | member.roles contains/lacks |
| kick/softban | get_member None |
| ban | guild.bans() contains user |
| timeout | member.is_timed_out() |
| nickname | member.display_name matches |
| message send | channel last-message author==bot (best-effort) |
| setup_*/configure_* | config key present + enabled |
Fallback: meta["verify"]=="none" → trust handler success.

## Phase 6 — Playbooks for top intents (`agent/planner.py`)
Add after duplicate-cleanup playbook:
- warn/kick/ban flow: query_member_details → tool call → final
- announcement flow: send_embed/create → final
- ticket/setup flows: setup_* tool → config verify → final

## Phase 7 — Tests
New `tests/test_universal_tools.py`:
1. metadata completeness: zero actions missing ACTION_META entries
2. registry completeness: zero actions missing TOOL_SPECS
3. per-family e2e (channel create+verify, role assign+verify, warn, config)
4. required-param rejection lists exact missing fields
5. rerun agent_native_e2e / v7_critical / recovery suites

## Ship
Full-repo py_compile → commit → `git push origin HEAD:main`.

"""Regression test for: create_automation + event_trigger used to silently
register a no-op automation (trigger fires, no side effect) and the progress
board showed '✅ create_automation'. The validator must now require a
non-empty 'actions' list for every side-effecting event_trigger, and the
action-handler must defensively enforce the same rule.
"""
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.tools import validate_params
from agent.observer import Observer
from agent.state import Observation, ErrorType, Receipt


# ----- 1. validator -----
def case(name, params, expect_ok, expect_substr=None):
    ok, msg = validate_params("create_automation", params)
    if expect_ok:
        assert ok, f"{name}: expected OK, got: {msg!r}"
    else:
        assert not ok, f"{name}: expected REJECT, got OK"
        if expect_substr:
            assert expect_substr in msg, f"{name}: error missing {expect_substr!r} in {msg!r}"
    print(f"  {'PASS' if (ok == expect_ok and (not expect_substr or expect_substr in msg)) else 'FAIL'} :: {name}")


print("\n--- Each side-effecting event_trigger must declare actions ---")
for ev in ("message_contains", "reaction_added", "member_joined", "member_left", "voice_joined"):
    # message_contains has its own earlier keywords requirement; supply them
    # so we can isolate the actions check.
    base = {"name": "x", "type": "event_trigger", "event": ev}
    if ev == "message_contains":
        base["keywords"] = ["hi"]
    case(f"{ev} without actions -> REJECT",
         dict(base),
         expect_ok=False, expect_substr="actions")
    case(f"{ev} with empty actions list -> REJECT",
         dict(base, actions=[]),
         expect_ok=False, expect_substr="actions")
    case(f"{ev} with actions=[send_message] -> OK",
         dict(base, actions=[{"name": "send_message", "parameters": {"content": "x"}}]),
         expect_ok=True)
    case(f"{ev} with actions=[add_reaction] (the user's bug) -> OK",
         dict(base, actions=[{"name": "add_reaction", "parameters": {"emoji": "😭"}}]),
         expect_ok=True)
    case(f"{ev} with actions under trigger -> OK",
         dict(base, trigger={"actions": [{"name": "send_message", "parameters": {"content": "x"}}]}),
         expect_ok=True)

print("\n--- Non-side-effecting event_trigger still doesn't require actions ---")
# (No 'event' that means "no side effect" — every event is side-effecting.
#  But 'type' = scheduled_task / auto_responder / reminder / trigger_role
#  should NOT be touched by the new rule.)
case("scheduled_task, no actions -> OK (not event_trigger)",
     {"name": "x", "type": "scheduled_task", "cron": "* * * * *"},
     expect_ok=True)
case("auto_responder, no actions -> OK",
     {"name": "x", "type": "auto_responder", "keywords": ["hi"]},
     expect_ok=True)
case("reminder, no actions -> OK",
     {"name": "x", "type": "reminder", "duration": 60, "response": "hi"},
     expect_ok=True)

print("\n--- 'type' aliases still work ---")
case("type='event' alias requires actions for message_contains",
     {"name": "x", "type": "event", "event": "message_contains", "keywords": ["hi"]},
     expect_ok=False, expect_substr="actions")

# ----- 2. observer honesty -----
print("\n--- Observer.record for create_automation ---")
obs = Observer()
def make_obs(success, verified, params):
    receipt = Receipt(action="create_automation", success=success, verified=verified,
                      error_type=ErrorType.NONE if success else ErrorType.UNKNOWN,
                      parameters=params)
    return Observation(tool="create_automation", params=params, success=success,
                       verified=verified, detail="ok", receipt=receipt)

obs.record(make_obs(True, True, {"type": "event_trigger", "event": "message_contains",
                                  "actions": [{"name": "add_reaction", "parameters": {"emoji": "😭"}}]}))
assert "✅ `create_automation`" in obs.history[-1], f"expected ✅, got: {obs.history[-1]!r}"
assert "actions=[add_reaction]" in obs.history[-1], f"expected actions summary, got: {obs.history[-1]!r}"
print(f"  PASS :: create_automation event_trigger with actions -> {obs.history[-1]!r}")

obs2 = Observer()
obs2.record(make_obs(True, True, {"type": "event_trigger", "event": "message_contains", "actions": []}))
assert "⚠️" in obs2.history[-1] and "✅" not in obs2.history[-1], f"expected ⚠️, got: {obs2.history[-1]!r}"
assert "no actions" in obs2.history[-1], f"expected warning, got: {obs2.history[-1]!r}"
print(f"  PASS :: create_automation event_trigger with empty actions -> {obs2.history[-1]!r}")

obs3 = Observer()
obs3.record(make_obs(True, True, {"type": "event_trigger", "event": "message_contains"}))
assert "⚠️" in obs3.history[-1], f"expected ⚠️, got: {obs3.history[-1]!r}"
print(f"  PASS :: create_automation event_trigger with no actions key -> {obs3.history[-1]!r}")

obs4 = Observer()
obs4.record(make_obs(True, True, {"type": "scheduled_task", "cron": "* * * * *"}))
assert "✅" in obs4.history[-1], f"expected ✅ (non-event_trigger type), got: {obs4.history[-1]!r}"
print(f"  PASS :: scheduled_task (not event_trigger) stays ✅ -> {obs4.history[-1]!r}")

obs5 = Observer()
obs5.record(make_obs(False, False, {"actions": []}))
assert "❌" in obs5.history[-1], f"expected ❌, got: {obs5.history[-1]!r}"
print(f"  PASS :: create_automation failure stays ❌ -> {obs5.history[-1]!r}")

print("\nALL create_automation ACTIONS-REQUIRED CASES PASSED")

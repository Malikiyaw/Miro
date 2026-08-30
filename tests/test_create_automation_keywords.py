"""Regression test for: create_automation + event_trigger + message_contains
without keywords used to silently fail with an UNKNOWN error in the user-facing
final response. The validator must now reject it with an actionable message
BEFORE dispatch.

Also covers the legitimate cases (keywords as list, comma-string, under
trigger.keywords, under filters.keywords) so the fix doesn't over-reject.
"""
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.tools import validate_params


def case(name, params, expect_ok, expect_substr=None):
    ok, msg = validate_params("create_automation", params)
    if expect_ok:
        assert ok, f"{name}: expected OK, got: {msg!r}"
    else:
        assert not ok, f"{name}: expected REJECT, got OK"
        if expect_substr:
            assert expect_substr in msg, f"{name}: error missing {expect_substr!r} in {msg!r}"
    print(f"  {'PASS' if (ok == expect_ok and (not expect_substr or expect_substr in msg)) else 'FAIL'} :: {name}")


# Reusable action step so the event_trigger cases below can satisfy both
# the keywords check (for message_contains) and the actions check.
_ACTIONS = [{"name": "send_message", "parameters": {"content": "x"}}]


print("\n--- The original bug (keywords missing for message_contains) ---")
case("event_trigger + message_contains, no keywords (the user's error)",
     {"name": "x", "type": "event_trigger", "event": "message_contains",
      "actions": _ACTIONS},
     expect_ok=False, expect_substr="keywords")

print("\n--- The new bug: actions missing for any side-effecting event_trigger ---")
case("message_contains with keywords but no actions -> REJECT",
     {"name": "x", "type": "event_trigger", "event": "message_contains",
      "keywords": ["hi"]}, expect_ok=False, expect_substr="actions")
case("member_joined with no actions -> REJECT",
     {"name": "x", "type": "event_trigger", "event": "member_joined"},
     expect_ok=False, expect_substr="actions")

print("\n--- Acceptable: keywords AND actions provided ---")
case("keywords as list + actions",
     {"name": "x", "type": "event_trigger", "event": "message_contains",
      "keywords": ["hello", "ping"], "actions": _ACTIONS}, expect_ok=True)
case("keywords as comma-string + actions",
     {"name": "x", "type": "event_trigger", "event": "message_contains",
      "keywords": "hello, ping", "actions": _ACTIONS}, expect_ok=True)
case("keywords under trigger + actions",
     {"name": "x", "type": "event_trigger", "event": "message_contains",
      "trigger": {"keywords": ["hi"]}, "actions": _ACTIONS}, expect_ok=True)
case("keywords under filters + actions",
     {"name": "x", "type": "event_trigger", "event": "message_contains",
      "filters": {"keywords": ["hi"]}, "actions": _ACTIONS}, expect_ok=True)
case("keywords as whitespace-only string -> empty -> REJECT",
     {"name": "x", "type": "event_trigger", "event": "message_contains",
      "keywords": "   ,  ,  ", "actions": _ACTIONS},
     expect_ok=False, expect_substr="keywords")
case("keywords as empty list -> REJECT",
     {"name": "x", "type": "event_trigger", "event": "message_contains",
      "keywords": [], "actions": _ACTIONS},
     expect_ok=False, expect_substr="keywords")

print("\n--- Other event_trigger events don't need keywords (but do need actions) ---")
case("member_joined with actions only (no keywords) -> OK",
     {"name": "x", "type": "event_trigger", "event": "member_joined",
      "actions": _ACTIONS}, expect_ok=True)
case("reaction_added with actions only -> OK",
     {"name": "x", "type": "event_trigger", "event": "reaction_added",
      "actions": _ACTIONS}, expect_ok=True)
case("voice_joined with actions only -> OK",
     {"name": "x", "type": "event_trigger", "event": "voice_joined",
      "actions": _ACTIONS}, expect_ok=True)

print("\n--- Other automation types ---")
case("auto_responder without keywords -> REJECT",
     {"name": "x", "type": "auto_responder"},
     expect_ok=False, expect_substr="keywords")
case("auto_responder with keywords -> OK",
     {"name": "x", "type": "auto_responder", "keywords": ["hi"]}, expect_ok=True)
case("trigger_role without keywords -> REJECT",
     {"name": "x", "type": "trigger_role", "role_id": 1},
     expect_ok=False, expect_substr="keywords")
case("reminder doesn't need keywords -> OK",
     {"name": "x", "type": "reminder", "duration": 60, "response": "hi"}, expect_ok=True)
case("scheduled_task doesn't need keywords -> OK",
     {"name": "x", "type": "scheduled_task", "cron": "* * * * *"}, expect_ok=True)

print("\n--- Name is required ---")
case("no name -> REJECT",
     {"type": "scheduled_task", "cron": "* * * * *"},
     expect_ok=False, expect_substr="name")
case("name = '   ' -> REJECT",
     {"type": "scheduled_task", "cron": "* * * * *", "name": "   "},
     expect_ok=False, expect_substr="name")

print("\n--- Type aliases ---")
case("type='event' alias still requires keywords for message_contains",
     {"name": "x", "type": "event", "event": "message_contains",
      "actions": _ACTIONS},
     expect_ok=False, expect_substr="keywords")
case("type='event' alias still requires actions",
     {"name": "x", "type": "event", "event": "member_joined"},
     expect_ok=False, expect_substr="actions")
case("type='responder' alias still requires keywords",
     {"name": "x", "type": "responder"},
     expect_ok=False, expect_substr="keywords")

print("\nALL REGRESSION CASES PASSED")

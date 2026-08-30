"""Regression test for: add_reaction used to silently fail with
"ActionHandler returned no message" when called with bad parameters
(missing emoji, missing message_id, or wrong keys like 'properties').

Two layers are now enforced:
1. The agent validator (agent/tools.py::validate_params) rejects bad
   add_reaction calls with an actionable error BEFORE dispatch.
2. The action handler (actions.py::action_add_reaction) returns
   informative errors instead of (False, None).
"""
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.tools import validate_params


def case(name, params, expect_ok, expect_substr=None):
    ok, msg = validate_params("add_reaction", params)
    if expect_ok:
        assert ok, f"{name}: expected OK, got: {msg!r}"
    else:
        assert not ok, f"{name}: expected REJECT, got OK"
        if expect_substr:
            assert expect_substr in msg, f"{name}: error missing {expect_substr!r} in {msg!r}"
    print(f"  {'PASS' if (ok == expect_ok and (not expect_substr or expect_substr in msg)) else 'FAIL'} :: {name}")


print("\n--- The user's actual bug (no emoji, no message_id) ---")
case("user's bad call: {type, properties} -> REJECT with helpful keys listing",
     {"type": "message_contains", "properties": "😭"},
     expect_ok=False, expect_substr="emoji")
assert "properties" in (validate_params("add_reaction", {"type": "message_contains", "properties": "😭"})[1] or ""), \
    "validator should list the keys the model actually sent"

print("\n--- Missing emoji ---")
case("no emoji at all -> REJECT",
     {"message_id": "123"},
     expect_ok=False, expect_substr="emoji")
case("emoji empty string -> REJECT",
     {"emoji": "", "message_id": "123"},
     expect_ok=False, expect_substr="emoji")
case("emoji whitespace -> REJECT",
     {"emoji": "   ", "message_id": "123"},
     expect_ok=False, expect_substr="emoji")
case("emoji non-string (e.g. dict) -> REJECT",
     {"emoji": {"face": "😭"}, "message_id": "123"},
     expect_ok=False, expect_substr="emoji")

print("\n--- Missing message_id (no channel fallback) ---")
case("emoji present but no message_id, no channel -> REJECT",
     {"emoji": "😭"},
     expect_ok=False, expect_substr="message_id")
case("emoji present but message_id empty string -> REJECT",
     {"emoji": "😭", "message_id": "   "},
     expect_ok=False, expect_substr="message_id")

print("\n--- Acceptable: emoji + message_id ---")
case("emoji + message_id (str) -> OK",
     {"emoji": "😭", "message_id": "123456789012345678"},
     expect_ok=True)
case("emoji + message_id (int) -> OK",
     {"emoji": "😭", "message_id": 123456789012345678},
     expect_ok=True)
case("emoji + channel_id only -> OK (action handler will need to find latest message)",
     {"emoji": "😭", "channel_id": 5},
     expect_ok=True)
case("emoji + channel_name only -> OK",
     {"emoji": "😭", "channel_name": "general"},
     expect_ok=True)
case("emoji + message_id + custom_emoji (':name:') -> OK",
     {"emoji": ":crying_face:", "message_id": "123456789012345678"},
     expect_ok=True)

print("\n--- Action handler returns informative errors (not None) ---")
# Build a minimal bot and exercise the action handler directly with the
# user's exact bad params, to verify the (False, None) silent-fail is gone.
import asyncio
import tempfile
import types
from data_manager import dm
dm.__init__(data_dir=tempfile.mkdtemp(), use_sqlite=True)


class _Bot:
    def __init__(self):
        self.user = types.SimpleNamespace(id=9999, name="Miro")


class _Guild:
    def __init__(self):
        self.id = 123
        self.name = "TestGuild"
        self.me = types.SimpleNamespace(id=9999, name="Miro")
        self.channels = []
        self.text_channels = []

        async def fetch_channel(cid):
            return types.SimpleNamespace(id=cid, name="general", send=_s, fetch_message=_fm)
        self.fetch_channel = fetch_channel

        def get_channel(cid):
            return types.SimpleNamespace(id=cid, name="general", send=_s, fetch_message=_fm)
        self.get_channel = get_channel


async def _s(*a, **k): return None
async def _fm(mid): raise Exception("not found")


class _Ixn:
    def __init__(self, g):
        self.guild = g
        self.channel = types.SimpleNamespace(id=5, name="general", send=_s, fetch_message=_fm)


bot = _Bot()
g = _Guild()
from actions import ActionHandler
ah = ActionHandler(bot)


async def go():
    # 1. user's exact bad call
    ok, info = await ah.action_add_reaction(_Ixn(g), {"type": "message_contains", "properties": "😭"})
    assert not ok, f"expected reject, got ok={ok} info={info!r}"
    assert isinstance(info, dict) and info.get("error"), f"expected error dict, got: {info!r}"
    assert "emoji" in info["error"], f"expected 'emoji' in error, got: {info['error']!r}"
    print(f"  PASS :: action handler returns informative error for the user's exact bad call: {info['error']!r}")

    # 2. emoji but no message_id
    ok, info = await ah.action_add_reaction(_Ixn(g), {"emoji": "😭"})
    assert not ok
    assert isinstance(info, dict) and info.get("error")
    assert "message_id" in info["error"], f"expected 'message_id' in error, got: {info['error']!r}"
    print(f"  PASS :: action handler returns 'message_id' error: {info['error']!r}")

    # 3. message_id but no such message (fetch_message raises)
    ok, info = await ah.action_add_reaction(_Ixn(g), {"emoji": "😭", "message_id": 999, "channel_id": 5})
    assert not ok
    assert isinstance(info, dict) and info.get("error")
    assert "could not fetch" in info["error"].lower() or "not found" in info["error"].lower(), \
        f"expected fetch error, got: {info['error']!r}"
    print(f"  PASS :: action handler returns fetch error (not silent None): {info['error']!r}")

asyncio.run(go())
print("\nALL add_reaction VALIDATION CASES PASSED")

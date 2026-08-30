"""Regression test: the V9 add_reaction screenshot bug.

User: just types "😆" → agent should react to the most recent message in
the current channel, NOT fail with "requires 'message_id'".

The fix has two layers:
  1. agent.runtime.AgentRuntime._enrich_defaults: when the LLM emits an
     add_reaction tool call without message_id, the runtime auto-fills
     the channel from the triggering message.
  2. actions.ActionHandler.action_add_reaction: when no message_id is
     passed at all, fetch the most recent message in the resolved channel
     and react to it.
  3. agent.tools.validate_params: the validator now allows channel_id /
     channel_name as a fallback (so the runtime's enrichment satisfies it).
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest.mock as mock

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="actor_ctx_"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_minimal_runtime(message=None, channel=None):
    """Build a real AgentRuntime with just enough wiring to exercise
    _enrich_defaults without spinning up a Discord bot.
    """
    from agent.runtime import AgentRuntime
    # We bypass __init__ to avoid the heavy bot/guild wiring.
    rt = AgentRuntime.__new__(AgentRuntime)
    rt.bot = None
    rt.guild = None
    rt.user = None
    rt._trigger_message = message
    rt._trigger_channel = channel
    rt.max_steps = 1
    rt.allow_dangerous = False
    rt.confirmed = False
    rt.on_progress = None
    rt._signatures = []
    rt._nudges = 0
    rt._max_plan_attempts = 3
    rt._original_request = ""
    rt._actor_context_cache = None
    return rt


def test_enrich_add_reaction_fills_message_id_from_trigger():
    # Triggering message has id=999.
    msg = types.SimpleNamespace(id=999, channel=types.SimpleNamespace(id=7, name="general"))
    rt = _build_minimal_runtime(message=msg, channel=msg.channel)
    out = rt._enrich_defaults("add_reaction", {"emoji": "😆"}, interaction=None)
    assert out["message_id"] == 999, out
    assert out["_auto_resolved_message_id"] is True


def test_enrich_add_reaction_keeps_explicit_message_id():
    msg = types.SimpleNamespace(id=999, channel=types.SimpleNamespace(id=7))
    rt = _build_minimal_runtime(message=msg, channel=msg.channel)
    out = rt._enrich_defaults("add_reaction", {"emoji": "😆", "message_id": "111"}, interaction=None)
    assert out["message_id"] == "111", out
    assert "_auto_resolved_message_id" not in out


def test_enrich_add_reaction_falls_back_to_channel_when_no_trigger_message():
    # No triggering message — runtime can't fill message_id, but it should
    # still fill channel_id/channel_name from the trigger channel.
    ch = types.SimpleNamespace(id=42, name="general")
    rt = _build_minimal_runtime(message=None, channel=ch)
    out = rt._enrich_defaults("add_reaction", {"emoji": "😆"}, interaction=None)
    assert out["channel_id"] == 42
    assert out["channel_name"] == "general"
    assert "message_id" not in out  # No trigger to draw from.


def test_enrich_passes_through_non_add_reaction():
    rt = _build_minimal_runtime()
    out = rt._enrich_defaults("send_message", {"content": "hi"}, interaction=None)
    assert out == {"content": "hi"}


def test_validator_passes_when_channel_name_provided():
    from agent.tools import validate_params
    ok, msg = validate_params("add_reaction", {"emoji": "😆", "channel_name": "general"})
    assert ok, msg
    ok, msg = validate_params("add_reaction", {"emoji": "😆", "channel_id": 5})
    assert ok, msg


def test_validator_rejects_when_no_target_at_all():
    from agent.tools import validate_params
    ok, msg = validate_params("add_reaction", {"emoji": "😆"})
    assert not ok
    assert "channel" in msg or "message_id" in msg


def test_validator_accepts_blank_message_id_when_channel_present():
    from agent.tools import validate_params
    ok, msg = validate_params("add_reaction", {"emoji": "😆", "message_id": "   ", "channel_name": "general"})
    assert ok, msg


def test_actor_context_includes_channel_and_message_ids():
    from agent.context import build_actor_context
    ch = types.SimpleNamespace(id=42, name="general")
    msg = types.SimpleNamespace(id=999, author=types.SimpleNamespace(id=1, name="Alice"))
    user = types.SimpleNamespace(id=2, name="Bob")
    guild = types.SimpleNamespace(id=10, name="TestGuild")
    out = build_actor_context(guild=guild, user=user, channel=ch, message=msg)
    assert "user: id=2" in out
    assert "guild: id=10" in out
    assert "channel: id=42" in out and "name=general" in out
    assert "message: id=999" in out
    assert "author_id=1" in out


def test_actor_context_omits_missing_pieces():
    from agent.context import build_actor_context
    out = build_actor_context()
    assert out == ""
    out = build_actor_context(user=types.SimpleNamespace(id=1, name="X"))
    assert "user: id=1" in out and "guild:" not in out


def test_action_add_reaction_picks_most_recent_message():
    """The action handler must fetch the most recent message when no
    message_id is provided and the runtime hands it a channel.
    """
    import asyncio
    from actions import ActionHandler

    class FakeMsg:
        def __init__(self, mid):
            self.id = mid
        async def add_reaction(self, emoji):
            self.last_emoji = emoji

    class FakeChannel:
        def __init__(self, name, recent_id):
            self.name = name
            self.id = 5
            self._recent = recent_id
        def history(self, limit=1):
            # discord.py history() is an async iterator directly, not a coroutine.
            class _Iter:
                def __init__(self, ch): self.ch = ch; self.yielded = False
                def __aiter__(self): return self
                async def __anext__(self):
                    if self.yielded: raise StopAsyncIteration
                    self.yielded = True
                    return FakeMsg(self.ch._recent)
            return _Iter(self)
        async def fetch_message(self, mid):
            raise Exception("not used in this test")

    ah = ActionHandler.__new__(ActionHandler)
    ah.bot = types.SimpleNamespace(user=types.SimpleNamespace(id=9999, name="Miro"))
    ch = FakeChannel("general", recent_id=4242)
    interaction = types.SimpleNamespace(
        guild=types.SimpleNamespace(id=1, name="g", me=ah.bot.user, channels=[ch]),
        channel=ch,
    )
    ok, info = asyncio.run(ah.action_add_reaction(interaction, {"emoji": "😆", "channel_name": "general"}))
    assert ok, info
    assert info["message_id"] == 4242, info
    assert info["emoji"] == "😆"


def test_action_add_reaction_still_rejects_missing_emoji():
    import asyncio
    from actions import ActionHandler
    ah = ActionHandler.__new__(ActionHandler)
    ah.bot = types.SimpleNamespace(user=types.SimpleNamespace(id=9999))
    interaction = types.SimpleNamespace(
        guild=types.SimpleNamespace(id=1, channels=[]),
        channel=types.SimpleNamespace(name="general"),
    )
    ok, info = asyncio.run(ah.action_add_reaction(interaction, {"message_id": "1"}))
    assert not ok
    assert "emoji" in info["error"].lower()


if __name__ == "__main__":
    test_enrich_add_reaction_fills_message_id_from_trigger()
    test_enrich_add_reaction_keeps_explicit_message_id()
    test_enrich_add_reaction_falls_back_to_channel_when_no_trigger_message()
    test_enrich_passes_through_non_add_reaction()
    test_validator_passes_when_channel_name_provided()
    test_validator_rejects_when_no_target_at_all()
    test_validator_accepts_blank_message_id_when_channel_present()
    test_actor_context_includes_channel_and_message_ids()
    test_actor_context_omits_missing_pieces()
    test_action_add_reaction_picks_most_recent_message()
    test_action_add_reaction_still_rejects_missing_emoji()
    print("ALL add_reaction ENRICHMENT CASES PASSED")

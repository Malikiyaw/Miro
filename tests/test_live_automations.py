"""Proof tests: agent-created automations & prefix commands are LIVE.

Offline (stubbed Discord) end-to-end coverage for:
1. Prefix command: create -> simulated "!hello" message -> response rendered+sent.
2. Scheduled automation: create -> TaskScheduler job registered with future next_run -> delete cancels.
3. Auto-responder: create -> real AutoResponderSystem.check_message fires.
4. Reminder: create -> scheduled_reminders entry + scheduler task registered.
5. Tool catalog exposes the new tools; semantic gate permits automation wording.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tests._stub_discord  # noqa: F401  (must precede any real discord import)

from types import SimpleNamespace

import data_manager as dm_mod
from data_manager import dm


GID = 424242
CHID = 222


class FakeChannel:
    def __init__(self):
        self.id = CHID
        self.name = "general"
        self.sent = []

    async def send(self, *args, **kwargs):
        text = kwargs.get("content") or (args[0] if args else "")
        self.sent.append(text)
        return SimpleNamespace(id=987654)


class FakeGuild:
    id = GID
    name = "TestGuild"
    text_channels = []
    members = []
    system_channel_id = None
    rules_channel_id = None


CHANNEL = FakeChannel()
GUILD = FakeGuild()


class FakeAdmin:
    id = 111
    name = "tester"
    display_name = "Tester"
    mention = "<@111>"
    bot = False
    roles = []
    guild_permissions = SimpleNamespace(administrator=True)


USER = FakeAdmin()


class FakeInteraction:
    """Minimal interaction accepted by ActionHandler.dispatch."""

    def __init__(self):
        self.guild = GUILD
        self.user = USER
        self.channel = CHANNEL
        self.response = self
        self.followup = self

    async def send(self, *a, **k):
        pass

    async def send_message(self, *a, **k):
        pass

    async def edit_message(self, *a, **k):
        pass

    async def defer(self, *a, **k):
        pass


def isolate_data(monkeypatch, tmp_path):
    """Point the dm singleton at a per-test data/ directory."""
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join(str(tmp_path), "data"), exist_ok=True)
    dm_mod.dm._cache = {}
    dm_mod.dm.db_path = os.path.join("data", "conversation_history.db")


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def make_bot():
    from actions import ActionHandler
    from modules.auto_responder import AutoResponderSystem
    from modules.reminders import ReminderSystem

    class FakeBot:
        pass

    bot = FakeBot()
    bot.user = SimpleNamespace(id=1, name="Miro")
    bot.get_guild = lambda gid: GUILD if gid == GID else None
    bot.get_channel = lambda cid: CHANNEL if cid == CHID else None
    bot.auto_responder = AutoResponderSystem(bot)
    bot.reminders = ReminderSystem(bot)
    bot.action_handler = ActionHandler(bot)
    return bot


def test_prefix_command_create_then_live_execution(tmp_path, monkeypatch):
    isolate_data(monkeypatch, tmp_path)
    bot = make_bot()
    inter = FakeInteraction()
    ah = bot.action_handler

    ok, info = run(ah.action_create_prefix_command(
        inter, {"name": "hello", "code": "Hi {user} in {server}!"}))
    assert ok, info
    assert info["cmd_name"] == "hello"

    stored_raw = dm.get_guild_data(GID, "custom_commands", {}).get("hello")
    assert stored_raw, "prefix command must be persisted"
    stored = json.loads(stored_raw)
    assert stored == {"command_type": "simple", "content": "Hi {user} in {server}!"}

    autos = dm.get_guild_data(GID, "automations", {})
    assert "hello" in autos and autos["hello"]["type"] == "prefix_command"

    # Simulated "!hello" message: fast-path matches, executes STORED code, AI skipped
    cmds = dm.get_guild_data(GID, "custom_commands", {})
    assert "hello" in cmds
    msg = SimpleNamespace(content="!hello", author=USER, guild=GUILD,
                          channel=CHANNEL, mentions=[])
    result = run(ah.execute_custom_command(msg, cmds["hello"], "hello"))
    assert result is True
    assert CHANNEL.sent and CHANNEL.sent[-1] == "Hi Tester in TestGuild!"

    ok2, info2 = run(ah.action_delete_prefix_command(inter, {"cmd_name": "hello"}))
    assert ok2, info2
    assert "hello" not in dm.get_guild_data(GID, "custom_commands", {})


def test_scheduled_automation_registers_and_cancels(tmp_path, monkeypatch):
    from task_scheduler import task_scheduler

    isolate_data(monkeypatch, tmp_path)
    bot = make_bot()
    inter = FakeInteraction()
    ah = bot.action_handler

    ok, info = run(ah.action_create_automation(inter, {
        "type": "scheduled_task",
        "name": "daily-tip",
        "cron": "0 12 * * *",
        "action_type": "send_message",
        "response": "Daily tip!",
        "channel_id": CHID,
    }))
    assert ok, info
    assert info["type"] == "scheduled_task"
    assert isinstance(info["next_run"], float) and info["next_run"] > time.time()
    assert info["task_id"] is not None

    entry = dm.get_guild_data(GID, "automations", {}).get("daily-tip")
    assert entry and entry["handler"] == "send_message"
    assert entry["params"].get("channel_id") == CHID

    scheduled = task_scheduler.get_scheduled_tasks()
    assert any(t[2] == "_run_and_reschedule" and t[1] == info["task_id"]
               for t in scheduled), scheduled

    # legacy schedule_ai_action tool now works too
    ok3, info3 = run(ah.action_schedule_ai_action(inter, {
        "name": "noon-ping", "cron": "0 12 * * *",
        "action_type": "send_message", "channel_id": CHID}))
    assert ok3, info3
    assert "noon-ping" in dm.get_guild_data(GID, "automations", {})

    # delete cancels the queued job and clears the registry
    ok2, info2 = run(ah.action_delete_automation(inter, {"name": "daily-tip"}))
    assert ok2, info2
    assert "daily-tip" not in dm.get_guild_data(GID, "automations", {})
    assert task_scheduler.cancel_task(info["task_id"]) is False  # already cancelled

    run(ah.action_delete_automation(inter, {"name": "noon-ping"}))
    assert "noon-ping" not in dm.get_guild_data(GID, "automations", {})

    # invalid cron fails closed with a readable error
    bad_ok, bad_info = run(ah.action_schedule_ai_action(
        inter, {"name": "bad", "cron": "not-a-cron", "action_type": "send_message"}))
    assert bad_ok is False and "Invalid cron" in str(bad_info)


def test_auto_responder_routes_through_real_system(tmp_path, monkeypatch):
    isolate_data(monkeypatch, tmp_path)
    bot = make_bot()
    inter = FakeInteraction()
    ah = bot.action_handler

    ok, info = run(ah.action_create_automation(inter, {
        "type": "auto_responder",
        "name": "greet",
        "keywords": ["ping"],
        "match_type": "contains",
        "response": "pong {user}",
    }))
    assert ok, info
    responders = dm.get_guild_data(GID, "auto_responders", [])
    assert any(r.get("trigger") == "ping" and r.get("enabled") for r in responders), \
        "responder must live in the REAL auto_responders store"

    class Msg:
        content = "PING now please"

    Msg.author = SimpleNamespace(id=555, bot=False, display_name="Alice",
                                 mention="<@555>", roles=[])
    Msg.guild = GUILD
    Msg.channel = CHANNEL
    Msg.mentions = []

    hit = bot.auto_responder.check_message(Msg())
    assert hit is not None and hit["response"] == "pong {user}"

    rendered = ah._render_template(hit["response"], Msg())
    assert rendered == "pong Alice"

    ok2, info2 = run(ah.action_delete_automation(inter, {"name": "greet"}))
    assert ok2, info2
    responders = dm.get_guild_data(GID, "auto_responders", [])
    assert not [r for r in responders if r.get("_automation_name") == "greet"]


def test_reminder_enters_real_queue(tmp_path, monkeypatch):
    from task_scheduler import task_scheduler

    isolate_data(monkeypatch, tmp_path)
    bot = make_bot()
    inter = FakeInteraction()
    ah = bot.action_handler

    ok, info = run(ah.action_create_automation(inter, {
        "type": "reminder", "name": "tea", "duration": 120,
        "response": "Tea time!", "channel_id": CHID}))
    assert ok, info
    assert info["reminder_time"] > time.time()

    rems = dm.get_guild_data(GID, "scheduled_reminders", [])
    assert any(r["message"] == "Tea time!" and r["channel_id"] == CHID for r in rems)
    scheduled = task_scheduler.get_scheduled_tasks()
    assert any(t[2] == "send_reminder" for t in scheduled)

    ok2, info2 = run(ah.action_delete_automation(inter, {"name": "tea"}))
    assert ok2, info2
    rems = dm.get_guild_data(GID, "scheduled_reminders", [])
    assert not [r for r in rems if r.get("_automation_name") == "tea"]


def test_tool_catalog_and_semantic_gate_allow_automations():
    from agent.tool_registry import TOOL_SPECS
    from core.action_meta import validate_action

    for tool in ("create_prefix_command", "delete_prefix_command",
                 "create_automation", "delete_automation",
                 "list_automations", "schedule_ai_action"):
        assert tool in TOOL_SPECS, f"{tool} missing from TOOL_SPECS"
        assert TOOL_SPECS[tool].get("parameters") is not None

    ok, reason, _sug = validate_action(
        "create an automation that sends a message daily", "create_automation")
    assert ok, reason
    ok2, reason2, _ = validate_action(
        "delete my reminder automation", "delete_automation")
    assert ok2, reason2
    # destructive object mismatch still blocked (regression guard)
    ok3, _r, _s = validate_action("delete duplicate channels", "bulk_delete_messages")
    assert ok3 is False

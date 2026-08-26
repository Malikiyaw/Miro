"""Automation scale layer: bulk ops, lifecycle, intervals, quotas, run tracking.

Offline (stubbed Discord) coverage for the 1000x automation upgrade:
1. interval_to_cron conversions (minutes/hours/daily/weekly)
2. bulk_create_automations (multiple in one call) + quota enforcement
3. pause/resume lifecycle (paused skips restore & firing)
4. run_automation_now records run stats
5. auto-pause after repeated failures
6. bulk pause/delete by names and by type
7. enhanced prefix commands (aliases, cooldown, permission) + bulk create
8. new tools present in the V9 registry with array-item schemas
"""
import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tests._stub_discord  # noqa: F401

import pytest

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
        self.sent.append(kwargs.get("content") or (args[0] if args else ""))
        return SimpleNamespace(id=987654)


class FakeGuild:
    id = GID
    name = "TestGuild"
    text_channels = []
    channels = []
    members = []


CHANNEL = FakeChannel()
GUILD = FakeGuild()
USER = SimpleNamespace(id=111, name="tester", bot=False, roles=[],
                       guild_permissions=SimpleNamespace(administrator=True),
                       mention="<@111>")


class FakeInteraction:
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
    monkeypatch.chdir(tmp_path)
    os.makedirs(os.path.join(str(tmp_path), "data"), exist_ok=True)
    dm_mod.dm._cache = {}
    dm_mod.dm.db_path = os.path.join("data", "conversation_history.db")


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
    handler = ActionHandler(bot)
    bot.action_handler = handler
    return bot, handler


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --------------------------------------------------------------------------- #

def test_interval_to_cron(monkeypatch, tmp_path):
    isolate_data(monkeypatch, tmp_path)
    from modules.automation_manager import interval_to_cron
    assert interval_to_cron({"every_minutes": 15}) == "*/15 * * * *"
    assert interval_to_cron({"every_hours": 2}) == "0 */2 * * *"
    assert interval_to_cron({"daily_at": "09:30"}) == "30 9 * * *"
    assert interval_to_cron({"weekly_on": "mon", "at": "08:00"}) == "0 8 * * 1"
    assert interval_to_cron({"cron": "0 12 * * *"}) is None  # not an interval
    assert interval_to_cron("not-a-dict") is None


def test_bulk_create_and_quota(monkeypatch, tmp_path):
    isolate_data(monkeypatch, tmp_path)
    bot, handler = make_bot()
    it = FakeInteraction()

    items = [{"name": f"bulk_{i}", "type": "scheduled_task",
              "cron": "0 12 * * *", "action": {"name": "send_message"},
              "response": f"hello {i}", "channel_id": str(CHID)} for i in range(25)]
    ok, info = run(handler.dispatch(it, "bulk_create_automations", {"automations": items}))
    assert ok, info
    assert info["created"] == 25
    autos = dm.get_guild_data(GID, "automations", {})
    assert sum(1 for k, v in autos.items() if v.get("type") == "scheduled_task") == 25

    # Over bulk limit
    too_many = [{"name": f"over_{i}", "type": "scheduled_task",
                 "cron": "0 12 * * *", "action": {"name": "send_message"},
                 "response": "x", "channel_id": str(CHID)} for i in range(26)]
    ok2, info2 = run(handler.dispatch(it, "bulk_create_automations",
                                      {"automations": too_many}))
    assert not ok2 and "Max 25" in info2["error"]


def test_pause_resume_lifecycle(monkeypatch, tmp_path):
    isolate_data(monkeypatch, tmp_path)
    bot, handler = make_bot()
    it = FakeInteraction()

    ok, _ = run(handler.dispatch(it, "create_automation", {
        "name": "lifecycle_test", "type": "scheduled_task", "cron": "0 12 * * *",
        "action": {"name": "send_message"}, "response": "hi", "channel_id": str(CHID)}))
    assert ok

    ok, info = run(handler.dispatch(it, "pause_automation", {"name": "lifecycle_test"}))
    assert ok and info["paused"] is True
    entry = dm.get_guild_data(GID, "automations", {})["lifecycle_test"]
    assert entry["paused"] is True

    # Paused automations are skipped on restore (bot.py logic)
    assert entry.get("paused") is True  # bot._restore_automations checks this flag

    ok, info = run(handler.dispatch(it, "resume_automation", {"name": "lifecycle_test"}))
    assert ok and info["paused"] is False
    entry = dm.get_guild_data(GID, "automations", {})["lifecycle_test"]
    assert entry["paused"] is False and entry.get("fail_count", 0) == 0


def test_run_now_records_stats(monkeypatch, tmp_path):
    isolate_data(monkeypatch, tmp_path)
    bot, handler = make_bot()
    it = FakeInteraction()

    run(handler.dispatch(it, "create_automation", {
        "name": "stats_run", "type": "scheduled_task", "cron": "0 12 * * *",
        "action": {"name": "send_message"}, "response": "daily!",
        "channel_id": str(CHID)}))
    ok, info = run(handler.dispatch(it, "run_automation_now", {"name": "stats_run"}))
    assert ok, info
    entry = dm.get_guild_data(GID, "automations", {})["stats_run"]
    assert entry["run_count"] >= 1 and entry.get("fail_count", 0) == 0
    assert entry.get("last_run") is not None
    assert len(CHANNEL.sent) >= 1


def test_auto_pause_after_failures(monkeypatch, tmp_path):
    isolate_data(monkeypatch, tmp_path)
    from modules.automation_manager import record_run, AUTO_PAUSE_AFTER_FAILURES
    dm.update_guild_data(GID, "automations", {"flaky": {"type": "scheduled_task", "cron": "* * * * *"}})
    for _ in range(AUTO_PAUSE_AFTER_FAILURES):
        entry = record_run(GID, "flaky", False, "boom")
    assert entry["paused"] is True
    assert "auto-paused" in entry.get("paused_reason", "")


def test_bulk_pause_delete_by_type(monkeypatch, tmp_path):
    isolate_data(monkeypatch, tmp_path)
    bot, handler = make_bot()
    it = FakeInteraction()

    for i in range(3):
        run(handler.dispatch(it, "create_automation", {
            "name": f"resp_{i}", "type": "auto_responder",
            "trigger": {"keywords": [f"kw{i}"]}, "response": f"auto {i}"}))
    ok, info = run(handler.dispatch(it, "bulk_pause_automations",
                                    {"all": True, "type": "auto_responder"}))
    assert ok and info["paused"] == 3
    autos = dm.get_guild_data(GID, "automations", {})
    assert all(autos[f"resp_{i}"]["paused"] for i in range(3))

    ok, info = run(handler.dispatch(it, "bulk_delete_automations",
                                    {"all": True, "type": "auto_responder"}))
    assert ok and info["deleted"] == 3
    autos = dm.get_guild_data(GID, "automations", {})
    assert not any(v.get("type") == "auto_responder" for v in autos.values())


def test_enhanced_prefix_commands(monkeypatch, tmp_path):
    isolate_data(monkeypatch, tmp_path)
    bot, handler = make_bot()
    it = FakeInteraction()

    ok, info = run(handler.dispatch(it, "create_prefix_command", {
        "name": "rules", "code": "Read the rules!",
        "aliases": ["rulez", "rulz"], "cooldown_seconds": 10,
        "required_permission": "everyone", "description": "Show the rules"}))
    assert ok, info
    cmds = dm.get_guild_data(GID, "custom_commands", {})
    assert "rules" in cmds and "rulez" in cmds and "rulz" in cmds
    data = json.loads(cmds["rules"])
    assert data["cooldown"] == 10
    assert data["description"] == "Show the rules"

    # Bulk create
    ok, info = run(handler.dispatch(it, "bulk_create_prefix_commands", {"commands": [
        {"name": "faq", "code": "Read the FAQ"},
        {"name": "links", "code": "Docs: example.com", "cooldown_seconds": 5},
        {"name": "socials", "code": "Follow us!", "aliases": ["social"]},
    ]}))
    assert ok and info["created"] == 3
    cmds = dm.get_guild_data(GID, "custom_commands", {})
    assert "faq" in cmds and "social" in cmds


def test_command_permission_gate(monkeypatch, tmp_path):
    isolate_data(monkeypatch, tmp_path)
    from modules.automation_manager import check_command_permission
    msg = SimpleNamespace(author=SimpleNamespace(
        guild_permissions=SimpleNamespace(administrator=False, manage_messages=False,
                                          manage_guild=False)))
    assert check_command_permission(msg, {}) is True
    assert check_command_permission(msg, {"permission": "admin"}) is False
    assert check_command_permission(msg, {"permission": "mod"}) is False
    mod_msg = SimpleNamespace(author=SimpleNamespace(
        guild_permissions=SimpleNamespace(administrator=False, manage_messages=True,
                                          manage_guild=False)))
    assert check_command_permission(mod_msg, {"permission": "mod"}) is True


def test_v9_registry_exposes_scale_tools():
    from agent.tool_registry import TOOL_SPECS
    from agent.native_tools import provider_tool_schemas
    for name in ("update_automation", "pause_automation", "resume_automation",
                 "run_automation_now", "bulk_create_automations",
                 "bulk_pause_automations", "bulk_delete_automations",
                 "bulk_create_prefix_commands"):
        assert name in TOOL_SPECS, name
    schemas = {s["function"]["name"]: s for s in provider_tool_schemas()}
    items = schemas["bulk_create_automations"]["function"]["parameters"]["properties"]["automations"]
    assert items["items"]["type"] == "object"
    aliases = schemas["create_prefix_command"]["function"]["parameters"]["properties"]["aliases"]
    assert aliases["items"]["type"] == "string"


def test_verifier_automation_checks(monkeypatch, tmp_path):
    isolate_data(monkeypatch, tmp_path)
    from agent.verifier import Verifier
    dm.update_guild_data(GID, "automations", {
        "live_one": {"type": "scheduled_task"},
        "paused_one": {"type": "scheduled_task", "paused": True},
    })
    dm.update_guild_data(GID, "custom_commands", {"mycmd": "{}"})
    v = Verifier(bot=None)
    guild = SimpleNamespace(id=GID)

    assert run(v.verify(guild, "create_automation", {"name": "live_one"})) is True
    assert run(v.verify(guild, "pause_automation", {"name": "paused_one"})) is True
    assert run(v.verify(guild, "pause_automation", {"name": "live_one"})) is False
    assert run(v.verify(guild, "bulk_delete_automations", {"names": ["ghost"]})) is True
    assert run(v.verify(guild, "create_prefix_command", {"name": "mycmd"})) is True


def test_create_automation_without_name_is_verifiable(monkeypatch, tmp_path):
    """Regression: when the model omits `name`, the action auto-generates it and
    returns a human-readable `message`; the executor propagates the resolved
    name back into params so Verifier can find and verify the new automation
    (previously ended as 'Unverified: 1')."""
    isolate_data(monkeypatch, tmp_path)
    bot, handler = make_bot()
    it = FakeInteraction()

    ok, info = run(handler.dispatch(it, "create_automation", {
        "type": "auto_responder", "keywords": ["hi"],
        "response": "Is that goat reyrey?", "channel_name": "general"}))
    assert ok is True, info
    assert info.get("message"), "action must return a readable message"
    assert info.get("automation_id"), "action must return the resolved automation name"

    # Simulate the executor propagation that makes verification succeed.
    from agent.verifier import Verifier
    params = {"type": "auto_responder", "name": info["automation_id"]}
    assert run(Verifier(bot).verify(GUILD, "create_automation", params)) is True
    assert run(v.verify(guild, "delete_prefix_command", {"name": "ghost"})) is True

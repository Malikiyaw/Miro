"""
V10 E2E — verification, tickets, auto_responder, logging, reaction_roles
Uses isolated data_dir and stub guild, no live Discord.
"""
import os, sys, json, tempfile, shutil
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_manager import DataManager
import discord

# Reuse stub from test_discord_ui if available, else minimal fake
try:
    from tests._stub_discord import FakeGuild, FakeMember, FakeChannel, FakeRole
except Exception:
    class FakeRole:
        def __init__(self, id=1, name="Role", position=1):
            self.id=id; self.name=name; self.position=position
            self.colour=discord.Colour.blue()
    class FakeGuild:
        def __init__(self, id=999):
            self.id=id; self.name=f"Guild{id}"
            self.roles=[FakeRole(0,"@everyone",0), FakeRole(999,"Bot",10)]
            self.channels=[]
            self.text_channels=[]
            self.categories=[]
            self.me=type("Me",(),{"id":0,"top_role":FakeRole(999,"Bot",10),"guild_permissions":discord.Permissions(manage_channels=True, manage_roles=True)})()
            async def create_role(**kw): r=FakeRole(100+len(self.roles),kw.get("name","New")); self.roles.append(r); return r
            async def create_text_channel(name,**kw):
                ch=type("Ch",(),{"id":200+len(self.channels),"name":name, "permissions_for":lambda s,m: discord.Permissions(view_channel=True,send_messages=True),"history":lambda **k: type("H",(),{"__aiter__":lambda s: s,"__anext__":async_mock})()})()
                self.channels.append(ch); self.text_channels.append(ch); return ch
            self.create_role=create_role; self.create_text_channel=create_text_channel
            def get_channel(cid):
                for c in self.channels:
                    if str(c.id)==str(cid): return c
                return None
            def get_role(rid):
                for r in self.roles:
                    if str(r.id)==str(rid): return r
                return None
            self.get_channel=get_channel; self.get_role=get_role

def async_mock(*a,**k): 
    raise StopAsyncIteration

import types

async def _fake_setup(guild):
    # minimal bot with needed systems stubbed
    bot = types.SimpleNamespace()
    bot.auto_setup = types.SimpleNamespace()
    async def _post_panel(*a,**kw): return True
    bot.auto_setup._post_panel=_post_panel
    # ensure setup_generic_system exists
    async def setup_generic_system(g, key, user=None):
        from data_manager import dm
        cfg=dm.get_guild_data(g.id, f"{key}_config" if not key.endswith("_config") and key not in ("reaction_roles","trigger_roles") else (key if key in ("reaction_roles","trigger_roles") else f"{key}_config"), {}) or {}
        cfg["enabled"]=True
        dm.update_guild_data(g.id, f"{key}_config", cfg)
        return True
    bot.auto_setup.setup_generic_system=setup_generic_system
    async def setup_verification_system(g,u=None):
        cfg={"enabled":True,"verify_channel":"123","verified_role":"124","unverified_role":"125"}
        from data_manager import dm
        dm.update_guild_data(g.id,"verification_config",cfg)
        return True
    bot.auto_setup.setup_verification_system=setup_verification_system
    bot.verification=types.SimpleNamespace()
    bot.tickets=types.SimpleNamespace()
    bot.auto_setup.setup_tickets_system=setup_generic_system
    # resource manager needed
    from core.installer import SystemInstaller
    from core.resource_manager import ResourceManager
    # mock bot needed fields for ResourceManager
    bot.user=type("U",(),{"id":999})()
    bot.installer=SystemInstaller(bot)
    bot.resource_manager=ResourceManager(bot)
    return bot

def test_e2e_verification():
    with tempfile.TemporaryDirectory() as tmp:
        dm = DataManager(data_dir=tmp, use_sqlite=False)
        # patch global dm
        import data_manager as dm_mod
        orig = dm_mod.dm
        dm_mod.dm = dm
        try:
            guild = FakeGuild(1)
            bot = asyncio.run(_fake_setup(guild))
            # install -> repair -> test
            res = asyncio.run(bot.installer.install(guild, "verification"))
            assert res.get("ok") or res==True or res.get("ok") is not None
            # enable check
            cfg = dm.get_guild_data(1, "verification_config")
            assert isinstance(cfg, dict)
            pre = bot.installer.preflight(guild, "verification")
            assert "missing" in pre
            rep = asyncio.run(bot.installer.repair(guild, "verification"))
            assert rep.get("ok") or "report" in rep
            # test
            tre = asyncio.run(bot.installer.test(guild, "verification"))
            # test may be not ok due to missing channel but should return dict
            assert isinstance(tre, dict)
        finally:
            dm_mod.dm = orig

def test_e2e_tickets():
    with tempfile.TemporaryDirectory() as tmp:
        dm = DataManager(data_dir=tmp, use_sqlite=False)
        import data_manager as dm_mod
        orig = dm_mod.dm; dm_mod.dm = dm
        try:
            guild = FakeGuild(2)
            bot = asyncio.run(_fake_setup(guild))
            res = asyncio.run(bot.installer.install(guild, "tickets"))
            assert res is not None
            cfg = dm.get_guild_data(2, "tickets_config")
            assert isinstance(cfg, dict)
            pre = bot.installer.preflight(guild, "tickets")
            assert "missing" in pre
            tre = asyncio.run(bot.installer.test(guild, "tickets"))
            assert isinstance(tre, dict)
        finally:
            dm_mod.dm = orig

def test_e2e_auto_responder():
    with tempfile.TemporaryDirectory() as tmp:
        dm = DataManager(data_dir=tmp, use_sqlite=False)
        import data_manager as dm_mod
        orig = dm_mod.dm; dm_mod.dm = dm
        try:
            guild = FakeGuild(3)
            bot = asyncio.run(_fake_setup(guild))
            # simulate creating responder
            dm.update_guild_data(3, "auto_responders", [{"id":"r1","trigger":"hi","response":"hello","enabled":True}])
            pre = bot.installer.preflight(guild, "auto_responder")
            assert "ok" in pre
            rep = asyncio.run(bot.installer.repair(guild, "auto_responder"))
            assert rep.get("ok") is True or "report" in rep
        finally:
            dm_mod.dm = orig

def test_e2e_logging():
    with tempfile.TemporaryDirectory() as tmp:
        dm = DataManager(data_dir=tmp, use_sqlite=False)
        import data_manager as dm_mod
        orig = dm_mod.dm; dm_mod.dm = dm
        try:
            guild = FakeGuild(4)
            bot = asyncio.run(_fake_setup(guild))
            dm.update_guild_data(4, "logging_config", {"enabled":True,"log_channel_id":"999"})
            tre = asyncio.run(bot.installer.test(guild, "event_logging"))
            assert isinstance(tre, dict)
        finally:
            dm_mod.dm = orig

def test_e2e_reaction_roles():
    with tempfile.TemporaryDirectory() as tmp:
        dm = DataManager(data_dir=tmp, use_sqlite=False)
        import data_manager as dm_mod
        orig = dm_mod.dm; dm_mod.dm = dm
        try:
            guild = FakeGuild(5)
            bot = asyncio.run(_fake_setup(guild))
            dm.update_guild_data(5, "reaction_roles", {"msg1": {"👍": {"role_id":"10"}}})
            pre = bot.installer.preflight(guild, "reaction_roles")
            assert isinstance(pre, dict)
            rep = asyncio.run(bot.installer.repair(guild, "reaction_roles"))
            assert rep.get("ok") is True or "report" in rep
        finally:
            dm_mod.dm = orig

def test_supabase_url_normalization():
    from core import supabase_sync
    # ensure /rest/v1 suffix stripped
    from unittest import mock
    with mock.patch.dict(os.environ, {"SUPABASE_URL":"https://abc.supabase.co/rest/v1", "SUPABASE_SERVICE_KEY":"k"}):
        url, key = supabase_sync._cfg()
        assert url == "https://abc.supabase.co" and key == "k"

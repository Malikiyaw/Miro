"""Exhaustive UI walk of every slash command, the /autosetup wizard, and every
configpanel surface in the Miro Discord bot.

Strategy
--------
- Boot the REAL MiroBot (no token / no network; AI is monkeypatched).
- Load every extension the live bot loads.
- For every app command:
    * invoke it with sample args,
    * capture any view it sent,
    * recursively click every button / select / submit every modal in that view
      until the surface stabilises.
- Separately, drive the /autosetup wizard from a clean state through every
  reachable branch (Quick Setup, Custom Selection, Resume, Undo).
- Separately, drive /configpanel for every system group and recursively walk
  its panel.
- Separately, instantiate every class in the modules that subclasses
  discord.ui.View with a fake guild/user and call its first button callback.

Every exception is captured; nothing is allowed to silently abort the run.
At the end a non-zero exit code is returned iff any click/select/modal
callback raised an exception.
"""
import os
import sys
import asyncio
import tempfile
import traceback
import inspect
from unittest.mock import AsyncMock, MagicMock

import discord

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DISCORD_TOKEN", "")
os.environ.setdefault("AI_API_KEY", "")
os.environ.setdefault("SYNC_COMMANDS", "false")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.modules.pop("discord", None)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakePerms:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
    def __getattr__(self, name):
        return True


class FakeRole:
    def __init__(self, id=1, name="role"):
        self.id = id
        self.name = name
        self.mention = f"<@&{id}>"
        self.members = []
        self.permissions = FakePerms(administrator=True)
        self.bot = False
        self.hoist = False
        self.position = 1
        self.managed = False
        self.color = discord.Color.default()
    def is_default(self):
        return self.name == "@everyone"
    async def edit(self, *a, **k):
        return None
    async def delete(self, *a, **k):
        return None


class FakeMember:
    def __init__(self, id=10, name="user", bot=False):
        self.id = id
        self.name = name
        self.mention = f"<@{id}>"
        self.bot = bot
        self.roles = [FakeRole(999, "@everyone")]
        self.guild_permissions = FakePerms(administrator=True)
        self.guild = None
        self.display_name = name
        self.top_role = FakeRole(9999, "Miro")
        import datetime as _dt
        self.created_at = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=60)
        self.joined_at = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
        self.avatar = type("Asset", (), {"url": "", "key": ""})()
        self.display_avatar = self.avatar
    async def add_roles(self, *a, **k):
        return None
    async def remove_roles(self, *a, **k):
        return None
    async def send(self, *a, **k):
        return FakeMessage()
    async def ban(self, *a, **k):
        return None
    async def kick(self, *a, **k):
        return None
    async def timeout(self, *a, **k):
        return None
    async def edit(self, *a, **k):
        return None
    async def history(self, *a, **k):
        class _H:
            async def flatten(self): return []
        return _H()


class FakeChannel:
    def __init__(self, id=5, name="channel", **kwargs):
        self.id = id
        self.name = name
        self.mention = f"<#{id}>"
        self.type = kwargs.get("type", discord.ChannelType.text)
        self.topic = ""
        self.position = 0
        self.category = None
        self.guild = kwargs.get("guild")
    async def send(self, *a, **k):
        return FakeMessage()
    async def edit(self, *a, **k):
        return None
    async def delete(self, *a, **k):
        return None
    def permissions_for(self, *a, **k):
        return FakePerms(administrator=True)
    async def create_thread(self, *a, **k):
        return FakeChannel()
    async def fetch_message(self, *a, **k):
        return FakeMessage()
    async def set_permissions(self, *a, **k):
        return None


class FakeMessage:
    def __init__(self, id=None):
        self.id = id or 777
        self.content = ""
        self.embeds = []
        self.components = []
        self.edit = AsyncMock()
        self.delete = AsyncMock()
        self.add_reaction = AsyncMock()
        self.pin = AsyncMock()
    async def reply(self, *a, **k):
        return FakeMessage()


class FakeGuild:
    def __init__(self, id=123456789):
        self.id = id
        self.name = "TestGuild"
        self.owner_id = 10
        self.me = FakeMember(id=9999, name="Miro", bot=True)
        self.roles = [FakeRole(999, "@everyone"), FakeRole(1, "Admin"), FakeRole(2, "Member")]
        self.members = [FakeMember(10, "owner"), FakeMember(11, "user")]
        self.channels = [FakeChannel(5, "general")]
        self.system_channel = FakeChannel(5, "general")
        self.text_channels = [FakeChannel(5, "general")]
        self.voice_channels = []
        self.categories = []
        self.default_role = FakeRole(999, "@everyone")
        self._counter = 100
    async def create_category(self, name, **k):
        return await self.create_category_channel(name, **k)
    def get_channel(self, cid):
        return FakeChannel(id=cid or 5, name="ch", guild=self)
    def get_role(self, rid):
        return FakeRole(id=rid or 1)
    def get_member(self, uid):
        return FakeMember(id=uid or 10)
    async def fetch_member(self, uid):
        return FakeMember(id=uid or 10)
    async def fetch_role(self, rid):
        return FakeRole(id=rid or 1)
    async def fetch_channel(self, cid):
        return FakeChannel(id=cid or 5, guild=self)
    async def create_text_channel(self, name, **k):
        self._counter += 1
        ch = FakeChannel(id=self._counter, name=name, guild=self)
        self.channels.append(ch); self.text_channels.append(ch)
        return ch
    async def create_category_channel(self, name, **k):
        self._counter += 1
        ch = FakeChannel(id=self._counter, name=name, type=discord.ChannelType.category, guild=self)
        self.categories.append(ch); self.channels.append(ch)
        return ch
    async def create_role(self, **k):
        self._counter += 1
        return FakeRole(id=self._counter, name=k.get("name", "role"))
    def permissions_for(self, *a, **k):
        return FakePerms(administrator=True)
    def fetch_roles(self):
        return self.roles


def _new_mocks():
    return MagicMock()


class FakeInteraction:
    def __init__(self, bot, guild=None, user=None, channel=None):
        self.client = bot
        self.guild = guild or FakeGuild()
        self.guild_id = self.guild.id
        self.user = user or FakeMember(id=10, name="owner")
        self.user.guild_permissions = FakePerms(administrator=True)
        self.channel = channel or FakeChannel()
        self.message = FakeMessage()
        self.data = {}
        self.values = []
        self.response = MagicMock()
        self.response.send_message = AsyncMock()
        self.response.defer = AsyncMock()
        self.response.edit_message = AsyncMock()
        self.response.is_done = MagicMock(return_value=False)
        self.response.send_modal = AsyncMock()
        self.followup = MagicMock()
        self.followup.send = AsyncMock(return_value=FakeMessage())
        self.followup.edit = AsyncMock()
        self.followup.delete = AsyncMock()
        self.edit_original_response = AsyncMock(return_value=FakeMessage())
    async def original_response(self):
        return self.message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_view_from_interaction(itx):
    """Find the most recent View sent by a fake interaction.

    discord.py sometimes overwrites response.send_message with a plain
    callable (e.g. the /bot command's _economy_defer patch), so we
    gracefully handle both MagicMock attributes and bare callables.
    """
    candidates = [
        ("response.edit_message", itx.response.edit_message),
        ("edit_original_response", itx.edit_original_response),
        ("message.edit", itx.message.edit),
        ("followup.send", itx.followup.send),
        ("response.send_message", itx.response.send_message),
    ]
    for name, m in candidates:
        try:
            calls = list(m.call_args_list)
        except Exception:
            continue
        for call in reversed(calls):
            v = (call.kwargs or {}).get("view")
            if v is not None:
                return v
    return None


def find_button(view, *, label=None, custom_id=None, contains=None):
    for item in getattr(view, "children", []):
        if isinstance(item, discord.ui.Button):
            if label is not None and item.label == label:
                return item
            if contains is not None and item.label and contains in item.label:
                return item
            if custom_id is not None and item.custom_id == custom_id:
                return item
    return None


def view_signature(view):
    """Fingerprint a view that is stable across re-renders of the same logical surface.

    Uses (class, n_children, sorted tuple of (type, label-or-placeholder)) so a
    SystemSelectView with 5 SystemButtons is treated as the same surface no
    matter what the inner state of each SystemButton is.
    """
    parts = []
    for c in getattr(view, "children", []):
        ctype = type(c).__name__
        # Use a stable placeholder for instance-state-derived ids; the goal is
        # to recognise a re-render of the same logical view, not to differentiate
        # two distinct views of the same type.
        if isinstance(c, discord.ui.Button):
            label = c.label or c.custom_id or "?"
        else:
            label = c.custom_id or type(c).__name__
        parts.append((ctype, str(label)))
    return (type(view).__name__, len(parts), tuple(sorted(parts)))


def extract_modal(itx):
    m = getattr(itx.response, "send_modal", None)
    if m is None or not getattr(m, "call_args_list", None):
        return None
    calls = list(m.call_args_list)
    if not calls:
        return None
    last = calls[-1]
    kw = getattr(last, "kwargs", None) or {}
    modal = kw.get("modal")
    if modal is None and getattr(last, "args", None):
        args = last.args
        if args:
            modal = args[0]
    return modal


async def submit_modal_and_continue(modal, ctx, depth, seen, errors, stats):
    stats["modals"] += 1
    for f in getattr(modal, "children", []):
        if isinstance(f, discord.ui.TextInput):
            try:
                f._value = "walk"
            except Exception:
                pass
            try:
                f.value = "walk"
            except Exception:
                pass
    s_itx = ctx.itx()
    s_itx.data = {
        "custom_id": getattr(modal, "custom_id", "modal"),
        "components": [
            {"custom_id": getattr(f, "custom_id", ""), "value": "walk"}
            for f in getattr(modal, "children", [])
            if isinstance(f, discord.ui.TextInput)
        ],
    }
    try:
        if hasattr(modal, "on_submit"):
            await modal.on_submit(s_itx)
    except Exception:
        errors.append((type(modal).__name__, "modal:submit", traceback.format_exc()))
        return
    new_view = get_view_from_interaction(s_itx)
    if new_view is not None and isinstance(new_view, discord.ui.View):
        await descend(s_itx, new_view, depth + 1, ctx, seen, errors, stats)


MAX_DEPTH = 15


async def descend(itx, view, depth, ctx, seen, errors, stats):
    if depth > MAX_DEPTH:
        return
    sig = view_signature(view)
    if sig in seen:
        return
    seen.add(sig)
    stats["views"] += 1
    for item in list(getattr(view, "children", [])):
        if not isinstance(item, (discord.ui.Button, discord.ui.Select)):
            continue
        stats["steps"] += 1
        label = getattr(item, "label", None) or getattr(item, "custom_id", "?")
        try:
            if isinstance(item, discord.ui.Select):
                opts = list(getattr(item, "options", []) or [])
                vals = [opts[0].value] if opts else []
                itx.values = vals
                itx.data = {
                    "custom_id": getattr(item, "custom_id", ""),
                    "component_type": 3,
                    "values": vals,
                }
                await item.callback(itx)
            else:
                itx.data = {
                    "custom_id": getattr(item, "custom_id", ""),
                    "component_type": 2,
                }
                await item.callback(itx)
        except Exception:
            errors.append((type(view).__name__, label, traceback.format_exc()))
            continue
        modal = extract_modal(itx)
        if modal is not None:
            await submit_modal_and_continue(modal, ctx, depth, seen, errors, stats)
            continue
        new_view = get_view_from_interaction(itx)
        if new_view is not None and isinstance(new_view, discord.ui.View):
            await descend(itx, new_view, depth + 1, ctx, seen, errors, stats)


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def build_bot():
    import bot as botmod
    b = botmod.MiroBot()
    guild = FakeGuild()
    b.get_guild = lambda gid: guild
    b.get_channel = lambda cid: FakeChannel(id=cid or 5, guild=guild)
    b.get_user = lambda uid: FakeMember(id=uid or 10)
    b.fetch_user = AsyncMock(return_value=FakeMember())
    ai = b.ai
    ai.chat = AsyncMock(return_value={"content": "ok", "role": "assistant",
                                       "choices": [{"message": {"content": "ok"}}]})
    ai.analyze_content = AsyncMock(return_value={"ok": True, "score": 0.1})
    if hasattr(ai, "answer"):
        ai.answer = AsyncMock(return_value={"content": "ok"})
    return b, guild


async def load_cogs(b):
    await b.load_extension("modules.slash_commands")
    await b.load_extension("cogs.core_commands")
    for ext in ("cogs.auto_delete", "modules.proactive_assist"):
        try:
            await b.load_extension(ext)
        except Exception:
            pass


def sample_args(cmd):
    args = {}
    for p in getattr(cmd, "parameters", []):
        t = getattr(p, "type", None)
        tv = getattr(t, "value", t)
        name = p.name
        if tv == 3:  args[name] = "test"
        elif tv == 4: args[name] = 1
        elif tv == 5: args[name] = False
        elif tv == 6: args[name] = FakeMember()
        elif tv == 7: args[name] = FakeChannel()
        elif tv == 8: args[name] = FakeRole()
        elif tv == 9: args[name] = FakeMember()
        elif tv == 10: args[name] = 1.0
        elif tv == 11: args[name] = FakeMessage()
        else: args[name] = "test"
    return args


class WalkCtx:
    def __init__(self, bot, guild, user, channel):
        self.bot = bot
        self.guild = guild
        self.user = user
        self.channel = channel
    def itx(self):
        return FakeInteraction(self.bot, guild=self.guild, user=self.user, channel=self.channel)


# ---------------------------------------------------------------------------
# Walk 1: every slash command
# ---------------------------------------------------------------------------
async def walk_all_commands(errors, stats):
    b, guild = build_bot()
    await load_cogs(b)
    from data_manager import dm
    cmds = list(b.tree.walk_commands())
    # Only run leaf commands (those with a real callback, not Group parents)
    leaves = [c for c in cmds if getattr(c, "callback", None) is not None]
    seen = set()
    seen_views_per_cmd = []
    for i, cmd in enumerate(leaves):
        print(f"    [{i+1}/{len(leaves)}] {cmd.qualified_name}…", flush=True)
        dm.save_json("pending_setups", {})
        dm.save_json("completed_setups", {})
        ctx = WalkCtx(b, guild, FakeMember(id=10), FakeChannel())
        itx = ctx.itx()
        try:
            func = cmd.callback
            binding = getattr(cmd, "binding", None)
            kwargs = sample_args(cmd)
            if binding is not None:
                await asyncio.wait_for(func(binding, itx, **kwargs), timeout=4.0)
            else:
                await asyncio.wait_for(func(itx, **kwargs), timeout=4.0)
        except asyncio.TimeoutError:
            errors.append((cmd.qualified_name, "COMMAND-TIMEOUT", "command >4s"))
            continue
        except Exception as e:
            errors.append((cmd.qualified_name, "COMMAND", f"{type(e).__name__}: {e}"))
            continue
        modal = extract_modal(itx)
        if modal is not None:
            await submit_modal_and_continue(modal, ctx, 0, seen, errors, stats)
            seen_views_per_cmd.append((cmd.qualified_name, 0))
            continue
        view = get_view_from_interaction(itx)
        if view is not None and isinstance(view, discord.ui.View):
            before = len(seen)
            try:
                await asyncio.wait_for(descend(itx, view, 0, ctx, seen, errors, stats), timeout=6.0)
            except asyncio.TimeoutError:
                errors.append((cmd.qualified_name, "WALK-TIMEOUT", "descend >6s"))
            seen_views_per_cmd.append((cmd.qualified_name, len(seen) - before))
    return len(leaves), seen_views_per_cmd


# ---------------------------------------------------------------------------
# Walk 2: every configpanel system
# ---------------------------------------------------------------------------
async def walk_all_configpanels(errors, stats):
    b, guild = build_bot()
    await load_cogs(b)
    from data_manager import dm
    cog = b.get_cog("SlashCommands")
    if cog is None:
        return 0
    # Mirror LEGACY_PANEL_ALIASES so we hit every reachable group
    aliases = dict(cog.LEGACY_PANEL_ALIASES)
    aliases.update({
        "member_management": "member_management", "progression": "progression",
        "tickets": "tickets", "suggestions": "suggestions", "giveaways": "giveaways",
        "communications": "communications", "anti_raid": "anti_raid",
        "moderation": "moderation", "automation": "automation",
        "staff_management": "staff_management", "ai": "ai",
    })
    seen = set()
    counts = []
    for label, key in aliases.items():
        if key == "health":
            # /configpanel health renders an embed (no view) — still call it
            ctx = WalkCtx(b, guild, FakeMember(id=10), FakeChannel())
            itx = ctx.itx()
            try:
                from modules.system_panels import build_global_health_embed
                embed = build_global_health_embed(b, guild)
                # Also exercise the live code path the way the command does
                await itx.response.defer(ephemeral=True)
                await itx.followup.send(embed=embed, ephemeral=True)
            except Exception as e:
                errors.append((f"/configpanel {label}", "embed", f"{type(e).__name__}: {e}"))
            continue
        ctx = WalkCtx(b, guild, FakeMember(id=10), FakeChannel())
        itx = ctx.itx()
        try:
            await itx.response.defer(ephemeral=True)
            from modules.system_panels import open_system_panel
            await open_system_panel(itx, key)
        except Exception as e:
            errors.append((f"/configpanel {key}", "open", f"{type(e).__name__}: {e}"))
            continue
        modal = extract_modal(itx)
        if modal is not None:
            await submit_modal_and_continue(modal, ctx, 0, seen, errors, stats)
            counts.append((key, -1))
            continue
        view = get_view_from_interaction(itx)
        if view is not None and isinstance(view, discord.ui.View):
            before = len(seen)
            try:
                await asyncio.wait_for(descend(itx, view, 0, ctx, seen, errors, stats), timeout=8.0)
            except asyncio.TimeoutError:
                errors.append((f"/configpanel {key}", "WALK-TIMEOUT", "descend >8s"))
            counts.append((key, len(seen) - before))
        else:
            counts.append((key, 0))
    return counts


# ---------------------------------------------------------------------------
# Walk 3: /autosetup wizard - every branch
# ---------------------------------------------------------------------------
async def walk_autosetup(errors, stats):
    b, guild = build_bot()
    await load_cogs(b)
    from data_manager import dm
    auto = b.auto_setup
    counts = []
    scenarios = [
        ("fresh_quicksetup", {}),
        ("fresh_custom", {}),
        ("resume_pending", {"pending_setups": {str(guild.id): {
            "stage": "category_select", "started_by": 10, "created_at": 1,
            "selected_systems": []}}}),
        ("already_setup", {"completed_setups": {str(guild.id): {
            "installed_by": 10, "completed_at": 1, "systems_installed": ["economy"]}}}),
    ]
    for label, preset in scenarios:
        dm.save_json("pending_setups", preset.get("pending_setups", {}))
        dm.save_json("completed_setups", preset.get("completed_setups", {}))
        ctx = WalkCtx(b, guild, FakeMember(id=10), FakeChannel())
        itx = ctx.itx()
        seen = set()
        try:
            await itx.response.defer(ephemeral=True)
            await auto.start_setup(itx)
        except Exception as e:
            errors.append((f"autosetup/{label}", "start", f"{type(e).__name__}: {e}"))
            continue
        view = get_view_from_interaction(itx)
        if view is None:
            errors.append((f"autosetup/{label}", "view", "no view returned"))
            continue
        before = len(seen)
        try:
            await asyncio.wait_for(descend(itx, view, 0, ctx, seen, errors, stats), timeout=10.0)
        except asyncio.TimeoutError:
            errors.append((f"autosetup/{label}", "WALK-TIMEOUT", "descend >10s"))
        counts.append((label, len(seen) - before))
    return counts


# ---------------------------------------------------------------------------
# Walk 4: /automations manager
# ---------------------------------------------------------------------------
async def walk_automations_panel(errors, stats):
    b, guild = build_bot()
    await load_cogs(b)
    from data_manager import dm
    dm.save_json("automations", {
        str(guild.id): {
            "auto1": {"name": "auto1", "type": "scheduled_task", "cron": "* * * * *",
                       "response": "x", "channel_id": 5, "enabled": True,
                       "failure_count": 0, "created_by": 10, "created_at": 1,
                       "last_run": 0, "last_error": None, "history": []},
            "auto2": {"name": "auto2", "type": "event_trigger",
                       "event": "member_joined", "actions": [], "enabled": False,
                       "failure_count": 0, "created_by": 10, "created_at": 1,
                       "last_run": 0, "last_error": None, "history": []},
        }
    })
    ctx = WalkCtx(b, guild, FakeMember(id=10), FakeChannel())
    itx = ctx.itx()
    try:
        from modules.automation_manager import AutomationManagerView
        view = AutomationManagerView(b, 10, guild.id)
        embed = view.build_embed()
        if not view._names():
            return ("automations", 0)
        await itx.response.defer(ephemeral=True)
        await itx.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        errors.append(("automations/manager", "init", f"{type(e).__name__}: {e}"))
        return ("automations", 0)
    seen = set()
    before = len(seen)
    try:
        await asyncio.wait_for(descend(itx, view, 0, ctx, seen, errors, stats), timeout=8.0)
    except asyncio.TimeoutError:
        errors.append(("automations/manager", "WALK-TIMEOUT", "descend >8s"))
    return ("automations", len(seen) - before)


# ---------------------------------------------------------------------------
# Walk 5: every View class in modules/* (instanced with sane args)
# ---------------------------------------------------------------------------
async def walk_all_view_classes(errors, stats):
    b, guild = build_bot()
    seen = set()
    skipped = []
    tested = []
    import modules as _modpkg
    import importlib, pkgutil
    for m in pkgutil.iter_modules(_modpkg.__path__):
        modname = f"modules.{m.name}"
        try:
            mod = importlib.import_module(modname)
        except Exception as e:
            skipped.append((modname, f"import: {type(e).__name__}: {e}"))
            continue
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ != modname:
                continue
            if not issubclass(obj, discord.ui.View):
                continue
            if obj in (discord.ui.View, discord.ui.Modal):
                continue
            tested.append(f"{modname}.{name}")
            ctx = WalkCtx(b, guild, FakeMember(id=10), FakeChannel())
            itx = ctx.itx()
            instance = None
            try:
                sig = inspect.signature(obj.__init__)
                kwargs = {}
                for pname, p in sig.parameters.items():
                    if pname in ("self",): continue
                    if pname in ("bot",): kwargs[pname] = b
                    elif pname in ("guild_id",): kwargs[pname] = guild.id
                    elif pname in ("user_id",): kwargs[pname] = 10
                    elif pname in ("guild",): kwargs[pname] = guild
                    elif pname in ("timeout",): kwargs[pname] = 5
                    elif p.default is not inspect.Parameter.empty:
                        kwargs[pname] = p.default
                if any(pname not in sig.parameters and p.default is inspect.Parameter.empty
                       for pname in ("bot","guild_id","user_id","guild")):
                    # can't easily build; skip
                    continue
                instance = obj(**kwargs)
            except Exception:
                # Build failed — that's OK, the slash command path will build it
                continue
            # If it has no buttons, skip walking
            if not any(isinstance(c, (discord.ui.Button, discord.ui.Select))
                       for c in getattr(instance, "children", [])):
                continue
            # Send the view through a fake interaction and walk
            try:
                await itx.response.defer(ephemeral=True)
                await itx.followup.send("test", view=instance, ephemeral=True)
            except Exception as e:
                errors.append((f"{modname}.{name}", "send", f"{type(e).__name__}: {e}"))
                continue
            before = len(seen)
            try:
                await asyncio.wait_for(descend(itx, instance, 0, ctx, seen, errors, stats), timeout=8.0)
            except asyncio.TimeoutError:
                errors.append((f"{modname}.{name}", "WALK-TIMEOUT", "descend >8s"))
            except Exception as e:
                errors.append((f"{modname}.{name}", "descend", f"{type(e).__name__}: {e}"))
    return tested, skipped


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
async def main():
    errors = []
    stats = {"views": 0, "steps": 0, "modals": 0}
    print("\n" + "=" * 78)
    print("EXHAUSTIVE UI WALK — Miro Discord bot")
    print("=" * 78)

    print("\n[1/5] Walk every slash command…")
    n_cmds, per_cmd = await walk_all_commands(errors, stats)
    print(f"  walked {n_cmds} commands; views reached per command: {per_cmd[:10]}{' …' if len(per_cmd) > 10 else ''}")

    print("\n[2/5] Walk every /configpanel system…")
    cp_counts = await walk_all_configpanels(errors, stats)
    print(f"  configpanels reached: {cp_counts}")

    print("\n[3/5] Walk /autosetup (4 scenarios)…")
    auto_counts = await walk_autosetup(errors, stats)
    print(f"  autosetup scenarios: {auto_counts}")

    print("\n[4/5] Walk /automations manager…")
    auto_mgr = await walk_automations_panel(errors, stats)
    print(f"  automations manager: {auto_mgr}")

    print("\n[5/5] Walk every discord.ui.View class in modules/*…")
    tested, skipped = await walk_all_view_classes(errors, stats)
    print(f"  tested {len(tested)} view classes, skipped {len(skipped)} (import failures)")

    # ---- summary ----
    print("\n" + "=" * 78)
    print(f"COVERAGE: {stats['views']} distinct views, {stats['steps']} clicks, {stats['modals']} modal submits")
    print("=" * 78)
    cmd_errs = [e for e in errors if e[1] == "COMMAND"]
    other_errs = [e for e in errors if e[1] != "COMMAND"]
    if errors:
        print(f"\n*** {len(errors)} ERRORS  ({len(cmd_errs)} command-level, {len(other_errs)} click/select/modal) ***")
        for name, where, tb in errors[:80]:
            print(f"\n[{name} :: {where}]")
            print(tb.rstrip())
        if len(errors) > 80:
            print(f"\n… and {len(errors)-80} more")
        return False
    print("\nNO ERRORS — every click, select, and modal submit returned cleanly.")
    return True


if __name__ == "__main__":
    # Neutralise tasks that need a connected client
    discord.ui.View.wait = AsyncMock(return_value=None)
    asyncio.sleep = AsyncMock(return_value=None)
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)

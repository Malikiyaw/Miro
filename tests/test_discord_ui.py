"""End-to-end smoke test of Miro's Discord UI surface (slash commands + buttons).

Strategy
--------
We boot the REAL MiroBot (no Discord token needed - commands/buttons only need
an ``interaction`` handle), monkeypatch the AI client so nothing hits the
network, and drive the actual command callbacks + discord.ui.Button callbacks
with faithful fake ``Interaction``/guild/channel/member objects.

For buttons we capture the ``View`` that each ``send_message``/``edit_message``
call received and invoke the matching button's ``callback`` - exactly the code
that runs when a human clicks it.
"""
import os
import sys
import asyncio
import tempfile
import traceback
from unittest.mock import AsyncMock, MagicMock

import discord

# Ensure the project root (where `bot.py` lives) is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- prevent the bot from trying to sync / start background loops ----
os.environ.setdefault("DISCORD_TOKEN", "")
os.environ.setdefault("AI_API_KEY", "")
os.environ.setdefault("SYNC_COMMANDS", "false")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

# Make sure the stub discord module (used by other tests) is NOT in sys.modules
sys.modules.pop("discord", None)


# ===========================================================================
# Fake Discord objects
# ===========================================================================
class FakePerms:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
    def __getattr__(self, name):
        return True  # default: allowed


class FakeRole:
    def __init__(self, id=1, name="role", members=None, bot=False):
        self.id = id
        self.name = name
        self.mention = f"<@&{id}>"
        self.members = members or []
        self.permissions = FakePerms(administrator=True)
        self.bot = bot
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
        self.avatar = type("Asset", (), {"url": "", "key": ""})()
        self.display_avatar = self.avatar
        import datetime as _dt
        self.created_at = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=60)
        self.joined_at = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
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


class FakeChannel:
    def __init__(self, id=5, name="channel", **kwargs):
        self.id = id
        self.name = name
        self.mention = f"<#{id}>"
        self.type = kwargs.get("type", discord.ChannelType.text)
        self.topic = ""
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
        self.edit = AsyncMock()
        self.delete = AsyncMock()
        self.add_reaction = AsyncMock()
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
        return FakeChannel(id=cid or 5, name="ch")
    def get_role(self, rid):
        return FakeRole(id=rid or 1)
    def get_member(self, uid):
        return FakeMember(id=uid or 10)
    async def fetch_member(self, uid):
        return FakeMember(id=uid or 10)
    async def fetch_role(self, rid):
        return FakeRole(id=rid or 1)
    async def fetch_channel(self, cid):
        return FakeChannel(id=cid or 5)
    async def create_text_channel(self, name, **k):
        self._counter += 1
        return FakeChannel(id=self._counter, name=name)
    async def create_category_channel(self, name, **k):
        self._counter += 1
        return FakeChannel(id=self._counter, name=name, type=discord.ChannelType.category)
    async def create_role(self, **k):
        self._counter += 1
        return FakeRole(id=self._counter, name=k.get("name", "role"))
    def permissions_for(self, *a, **k):
        return FakePerms(administrator=True)
    def fetch_roles(self):
        return self.roles


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


def get_view_from_interaction(itx):
    # Prefer the most recent interaction that actually set a view. Navigation
    # typically edits the message; new panels are sent via followup/send.
    for call in list(itx.response.edit_message.call_args_list)[::-1]:
        v = (call.kwargs or {}).get("view")
        if v:
            return v
    for call in list(itx.edit_original_response.call_args_list)[::-1]:
        v = (call.kwargs or {}).get("view")
        if v:
            return v
    for call in list(itx.message.edit.call_args_list)[::-1]:
        v = (call.kwargs or {}).get("view")
        if v:
            return v
    for call in list(itx.followup.send.call_args_list)[::-1]:
        v = (call.kwargs or {}).get("view")
        if v:
            return v
    for call in list(itx.response.send_message.call_args_list)[::-1]:
        v = (call.kwargs or {}).get("view")
        if v:
            return v
    return None


def find_button(view, label=None, custom_id=None, contains=None):
    for item in getattr(view, "children", []):
        if isinstance(item, discord.ui.Button):
            if label is not None and item.label == label:
                return item
            if contains is not None and item.label and contains in item.label:
                return item
            if custom_id is not None and item.custom_id == custom_id:
                return item
    return None


async def click(itx, view, label=None, custom_id=None, contains=None):
    btn = find_button(view, label, custom_id, contains)
    if btn is None:
        labels = [getattr(c, "label", None) for c in getattr(view, "children", [])]
        raise AssertionError(f"button {label or custom_id or contains} not found; have {labels}")
    await btn.callback(itx)
    return get_view_from_interaction(itx)


# ===========================================================================
# Exhaustive walker — every button/select/modal transitively reachable
# ===========================================================================
MAX_WALK_DEPTH = 18
MAX_WALK_STEPS = 9000


class WalkCtx:
    def __init__(self, b, guild, user, channel):
        self.b = b
        self.guild = guild
        self.user = user
        self.channel = channel
    def itx(self):
        return FakeInteraction(self.b, guild=self.guild, user=self.user, channel=self.channel)


def _extract_modal(itx):
    try:
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
    except Exception:
        return None


def _view_sig(view):
    parts = []
    for c in getattr(view, "children", []):
        cid = getattr(c, "custom_id", None) or getattr(c, "label", None) or type(c).__name__
        parts.append((type(c).__name__, str(cid)))
    return (type(view).__name__, frozenset(parts))


async def _submit_modal_and_walk(modal, ctx, depth, seen, errors, stats):
    stats["modals"] = stats.get("modals", 0) + 1
    # fill each TextInput so on_submit sees a value
    for f in getattr(modal, "children", []):
        if isinstance(f, discord.ui.TextInput):
            try:
                f._value = "walk_test_value"  # discord.py stores via _value / value
            except Exception:
                pass
            try:
                f.value = "walk_test_value"
            except Exception:
                pass
    s_itx = ctx.itx()
    s_itx.data = {
        "custom_id": getattr(modal, "custom_id", "modal"),
        "components": [
            {"custom_id": getattr(f, "custom_id", ""), "value": "walk_test_value"}
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
        await _descend(s_itx, new_view, depth + 1, ctx, seen, errors, stats)


async def _descend(itx, view, depth, ctx, seen, errors, stats):
    if depth > MAX_WALK_DEPTH or stats.get("steps", 0) > MAX_WALK_STEPS:
        return
    sig = _view_sig(view)
    if sig in seen:
        return
    seen.add(sig)
    stats["views"] = stats.get("views", 0) + 1
    # snapshot children so mutation during iteration is safe
    for item in list(getattr(view, "children", [])):
        if not isinstance(item, (discord.ui.Button, discord.ui.Select)):
            continue
        stats["steps"] = stats.get("steps", 0) + 1
        label = getattr(item, "label", None) or getattr(item, "custom_id", "?")
        try:
            if isinstance(item, discord.ui.Select):
                opts = list(getattr(item, "options", []) or [])
                vals = [opts[0].value] if opts else []
                itx.values = vals
                itx.data = {"custom_id": getattr(item, "custom_id", ""), "component_type": 3, "values": vals}
                await item.callback(itx)
            else:
                itx.data = {"custom_id": getattr(item, "custom_id", ""), "component_type": 2}
                await item.callback(itx)
        except Exception:
            errors.append((type(view).__name__, label, traceback.format_exc()))
            continue
        modal = _extract_modal(itx)
        if modal is not None:
            await _submit_modal_and_walk(modal, ctx, depth, seen, errors, stats)
            continue
        new_view = get_view_from_interaction(itx)
        if new_view is not None and isinstance(new_view, discord.ui.View):
            await _descend(itx, new_view, depth + 1, ctx, seen, errors, stats)


async def atest_full_ui_walk():
    """Walk every reachable view: each slash command -> buttons -> sub-buttons -> modals.
    Records every exception; printing a coverage summary at the end.
    """
    # Prevent any `await view.wait()` inside button flows (e.g. ConfirmView)
    # from stalling the walker — fake interactions have no real user to click.
    _orig_view_wait = getattr(discord.ui.View, "wait", None)
    try:
        discord.ui.View.wait = AsyncMock(return_value=None)  # type: ignore
    except Exception:
        pass
    _orig_sleep = asyncio.sleep
    try:
        asyncio.sleep = AsyncMock(return_value=None)  # type: ignore  # walk installs instantly
    except Exception:
        pass
    b, guild = build_bot()
    await load_cogs(b)
    from data_manager import dm
    dm.save_json("pending_setups", {})
    dm.save_json("completed_setups", {})
    cmds = list(b.tree.walk_commands())
    assert cmds, "no app commands loaded"
    seen: set = set()
    errors: list = []
    stats: dict = {"views": 0, "steps": 0, "modals": 0}
    smoke_errors = []
    for cmd in cmds:
        func = getattr(cmd, "callback", None)
        if func is None:
            continue  # group parent (e.g. /config) — subcommands are separate walks
        binding = getattr(cmd, "binding", None)
        # clean dm state per command so /autosetup reliably starts at the wizard root
        dm.save_json("pending_setups", {})
        dm.save_json("completed_setups", {})
        itx = FakeInteraction(b, guild=guild)
        kwargs = sample_args(cmd)
        try:
            if binding is not None:
                await func(binding, itx, **kwargs)
            else:
                await func(itx, **kwargs)
        except Exception:
            # command itself failed — recorded, not walked
            smoke_errors.append((cmd.qualified_name, traceback.format_exc()))
            errors.append((cmd.qualified_name, "COMMAND", traceback.format_exc()))
            continue
        modal = _extract_modal(itx)
        if modal is not None:
            ctx = WalkCtx(b, guild, itx.user, itx.channel)
            await _submit_modal_and_walk(modal, ctx, 0, seen, errors, stats)
            continue
        view = get_view_from_interaction(itx)
        if view is not None and isinstance(view, discord.ui.View):
            ctx = WalkCtx(b, guild, itx.user, itx.channel)
            await _descend(itx, view, 0, ctx, seen, errors, stats)
    print(f"\n=== FULL UI WALK: {len(cmds)} commands, {stats['views']} distinct views, {stats['steps']} clicks, {stats['modals']} modal submits ===")
    if seen:
        # short sample of reached views
        sample = list(seen)[:12]
        print("  reached views: " + ", ".join(s[0] for s in sample))
        if len(seen) > 12:
            print(f"  (+{len(seen)-12} others)")
    if smoke_errors:
        print(f"\n--- COMMAND ERRS ({len(smoke_errors)}) ---")
        for q, tb in smoke_errors:
            print(f"[{q}]")
            print(tb.rstrip())
            print("---")
    if errors:
        # de-dup command errors from smoke_errors
        walk_only = [e for e in errors if e[1] != "COMMAND"]
        print(f"\n*** UI WALK ERRORS ({len(errors)} total; {len(walk_only)} from buttons/selects/modals) ***")
        for view_name, label, tb in errors[:30]:
            print(f"[{view_name} :: {label}]")
            print(tb.rstrip())
            print("---")
        if len(errors) > 30:
            print(f"... plus {len(errors)-30} more errors omitted ...")
    # restore patches
    try:
        if _orig_view_wait is not None:
            discord.ui.View.wait = _orig_view_wait  # type: ignore
    except Exception:
        pass
    try:
        asyncio.sleep = _orig_sleep  # type: ignore
    except Exception:
        pass
    return seen, stats, errors


# ===========================================================================
# Boot helpers
# ===========================================================================
def build_bot():
    import bot as botmod
    b = botmod.MiroBot()
    # make the bot "see" our fake guild everywhere subsystems look it up
    guild = FakeGuild()
    b.get_guild = lambda gid: guild
    b.get_channel = lambda cid: FakeChannel(id=cid or 5)
    b.get_user = lambda uid: FakeMember(id=uid or 10)
    b.fetch_user = AsyncMock(return_value=FakeMember())
    # neutralise AI network calls
    ai = b.ai
    ai.chat = AsyncMock(return_value={"content": "ok", "role": "assistant", "choices": [{"message": {"content": "ok"}}]})
    ai.analyze_content = AsyncMock(return_value={"ok": True, "score": 0.1})
    if hasattr(ai, "answer"):
        ai.answer = AsyncMock(return_value={"content": "ok"})
    return b, guild


async def load_cogs(b):
    await b.load_extension("modules.slash_commands")
    await b.load_extension("cogs.core_commands")
    # Load every extension the live bot loads so walk_commands is complete.
    # Extra cogs must not crash the run — best-effort try/except.
    for ext in ("cogs.auto_delete", "modules.proactive_assist"):
        try:
            await b.load_extension(ext)
        except Exception:
            pass


def sample_args(cmd):
    args = {}
    for p in getattr(cmd, "parameters", []):
        t = getattr(p, "type", None)
        tv = getattr(t, "value", t)  # AppCommandOptionType is a plain Enum
        name = p.name
        if tv == 3:  # string
            args[name] = "test"
        elif tv == 4:  # integer
            args[name] = 1
        elif tv == 5:  # boolean
            args[name] = False
        elif tv == 6:  # user/member
            args[name] = FakeMember()
        elif tv == 7:  # channel
            args[name] = FakeChannel()
        elif tv == 8:  # role
            args[name] = FakeRole()
        elif tv == 9:  # mentionable
            args[name] = FakeMember()
        elif tv == 10:  # number
            args[name] = 1.0
        elif tv == 11:  # attachment
            args[name] = FakeMessage()
        else:
            args[name] = "test"
    return args


# ===========================================================================
# Tests
# ===========================================================================
def test_boot_and_load():
    b, _ = build_bot()
    assert b is not None


async def atest_all_slash_commands_smoke():
    b, guild = build_bot()
    await load_cogs(b)
    cmds = list(b.tree.walk_commands())
    assert cmds, "no app commands loaded"
    results = []
    for cmd in cmds:
        itx = FakeInteraction(b, guild=guild)
        try:
            binding = getattr(cmd, "binding", None)
            func = getattr(cmd, "callback", None)
            if func is None:
                results.append((cmd.qualified_name, "SKIP(no-callback)", ""))
                continue
            kwargs = sample_args(cmd)
            if binding is not None:
                await func(binding, itx, **kwargs)
            else:
                await func(itx, **kwargs)
            results.append((cmd.qualified_name, "PASS", ""))
        except Exception as e:
            results.append((cmd.qualified_name, "FAIL", f"{type(e).__name__}: {e}"))
    failed = [r for r in results if r[1] != "PASS"]
    print(f"\n=== SLASH COMMAND SMOKE: {len(cmds)} commands, {len(failed)} failures ===")
    for r in results:
        if r[1] != "PASS":
            print(f"  [{r[1]}] {r[0]} :: {r[2]}")
    # We don't hard-fail the suite on fake-limitation errors, but report.
    return results


async def atest_autosetup_flow():
    b, guild = build_bot()
    await load_cogs(b)
    from data_manager import dm
    dm.save_json("pending_setups", {})
    dm.save_json("completed_setups", {})
    auto = b.auto_setup
    itx = FakeInteraction(b, guild=guild)

    # 1) start -> SetupStartView
    await auto.start_setup(itx)
    view = get_view_from_interaction(itx)
    assert view is not None, "start_setup did not send a view"
    print(f"\n[autosetup] after start_setup view={type(view).__name__}")

    # 2) Quick Setup (recommended bundle)
    view2 = await click(itx, view, contains="Quick Setup")
    print(f"[autosetup] after Quick Setup view={type(view2).__name__ if view2 else None}")
    # The final report should be a SetupDoneView; if None it may have ended with followup
    assert view2 is not None, "Quick Setup did not produce a follow-up view"

    # 3) Undo path: re-run start (completed) -> AlreadySetupView -> Undo
    # Reset manifests and completed to exercise that branch lightly
    from data_manager import dm
    dm.save_json("completed_setups", {str(guild.id): {"installed_by": 10, "completed_at": 1, "systems_installed": ["economy"]}})
    itx2 = FakeInteraction(b, guild=guild)
    await auto.start_setup(itx2)
    v2 = get_view_from_interaction(itx2)
    assert v2 is not None
    # click Undo Entire Setup (danger confirm) - it opens a ConfirmView; click Confirm
    undo_btn = find_button(v2, label="↩️ Undo Entire Setup")
    if undo_btn:
        await undo_btn.callback(itx2)
        confirm_view = get_view_from_interaction(itx2)
        if confirm_view is not None:
            cbtn = find_button(confirm_view, label="Confirm")
            if cbtn:
                await cbtn.callback(itx2)
    print("[autosetup] undo path exercised")


async def atest_autosetup_custom_selection():
    b, guild = build_bot()
    await load_cogs(b)
    from data_manager import dm
    dm.save_json("pending_setups", {})
    dm.save_json("completed_setups", {})
    auto = b.auto_setup
    itx = FakeInteraction(b, guild=guild)
    await auto.start_setup(itx)
    view = get_view_from_interaction(itx)
    # click "Custom Selection…"
    view = await click(itx, view, contains="Custom Selection")
    assert view is not None, "Custom Selection did not open category view"
    print(f"\n[autosetup-custom] category view={type(view).__name__}")
    # click first category button (CategoryButton)
    cat_btn = next((c for c in view.children if isinstance(c, discord.ui.Button)), None)
    assert cat_btn is not None
    await cat_btn.callback(itx)
    sys_view = get_view_from_interaction(itx)
    assert sys_view is not None, "category click did not open system view"
    print(f"[automation-custom] system view={type(sys_view).__name__}")
    # click Install Selected (no systems selected -> should just run with empty or prompt)
    install_btn = find_button(sys_view, label="✅ Install Selected")
    if install_btn:
        await install_btn.callback(itx)
    print("[autosetup-custom] install-selected exercised")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        test_boot_and_load()
        res = loop.run_until_complete(atest_all_slash_commands_smoke())
        loop.run_until_complete(atest_autosetup_flow())
        loop.run_until_complete(atest_autosetup_custom_selection())
        try:
            seen, stats, errors = loop.run_until_complete(asyncio.wait_for(atest_full_ui_walk(), timeout=70))
        except asyncio.TimeoutError:
            print("\n*** FULL UI WALK TIMED OUT after 70s (partial coverage) — dumping progress ***")
            traceback.print_exc()
            sys.exit(1)
        print("\nALL UI SMOKE TESTS COMPLETED")
        if errors:
            # Real bugs surfaced by the exhaustive walk — fail visibly
            print(f"\n*** FULL-WALK FOUND {len(errors)} REAL UI BUGS (see above) ***")
            sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)

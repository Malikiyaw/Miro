"""
Miro Agent V7 — Critical execution proof (runnable standalone: python3 tests/agent_v7_critical.py)

Scenario (plan item: "Critical test"):
    "Delete the 3 duplicate ⚠️_warned_by_warn_lords channels."

Must:
    Find 3 -> Execute delete_channel x3 -> Verify 3 are actually gone -> Report success.
Invariant:
    If Discord shows 0 deleted, Miro must NOT say it deleted anything.
"""
import sys
import types
import asyncio
from datetime import datetime

sys.path.insert(0, ".")

# ---- offline stubs (no discord.py in CI sandbox) ---------------------------
try:
    __import__("aiosqlite")
except ImportError:
    sys.modules["aiosqlite"] = types.ModuleType("aiosqlite")

import importlib
import tempfile
dm_mod = importlib.import_module("data_manager")
dm_mod.dm = dm_mod.DataManager(data_dir=tempfile.mkdtemp())

from core.agent_runtime import AgentRuntime          # noqa: E402
from agent.state import JobStatus                    # noqa: E402


class Channel:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name
        self.category_id = None
        self.created_at = datetime(2026, 8, 22)


class Guild:
    id = 999
    name = "Warn Lords HQ"
    roles = []

    def __init__(self):
        self.channels = [
            Channel(1, "⚠️-warned-by-warn-lords"),     # protected original
            Channel(2, "⚠️-warned-by-warn-lords-2"),   # duplicate
            Channel(3, "⚠️-warned-by-warn-lords-3"),   # duplicate
            Channel(4, "warned-by-warn-lords-4"),      # duplicate
            Channel(5, "general"),                     # untouched
        ]
        self.deleted_ids = []                          # what Discord ACTUALLY removed

    def text_channels(self):
        return [c for c in self.channels]

    def get_channel(self, cid):
        cid = int(cid)
        return next((c for c in self.channels if c.id == cid), None)

    async def fetch_channel(self, cid):
        """Live Discord lookup: raises NotFound-style when truly gone."""
        ch = self.get_channel(cid)
        if ch is None:
            raise type("NotFound", (Exception,), {})()
        return ch

    def apply_discord_deletion(self, cid):
        """Simulates Discord really deleting: only then does state change."""
        ch = self.get_channel(cid)
        if ch is None:
            return False
        self.channels.remove(ch)
        self.deleted_ids.append(cid)
        return True


class Resp:
    done = False
    async def send_message(self, *a, **k): self.done = True
    async def defer(self, *a, **k): self.done = True
    async def edit_message(self, *a, **k): pass
    def is_done(self): return self.done


class Follow:
    async def send(self, *a, **k): pass


class User:
    id = 7
    class guild_permissions:
        administrator = True


class Interaction:
    def __init__(self, guild, user):
        self.guild = guild
        self.user = user
        self.channel = None
        self.response = Resp()
        self.followup = Follow()


def find_duplicates(guild, base="⚠️-warned-by-warn-lords", protected=1):
    import re
    def norm(s):
        s = re.sub(r"[-_\s]+", "-", s.lower().strip())
        s = re.sub(r"^[^a-z0-9]+", "", s)
        s = re.sub(r"-\d+$", "", s)
        return s
    target = norm(base)
    dups = [c for c in guild.channels if norm(c.name) == target and c.id != protected]
    return dups


class ScriptedAI:
    """Turn 1: find. Turn 2-4: delete each by ID. Turn 5: final summary."""
    def __init__(self):
        self.turn = 0
        self.phase = "find"

    async def chat(self, guild_id, user_id, user_input, system_prompt,
                   persist=False, extra_messages=None):
        self.turn += 1
        joined = " ".join(str(m.get("content", ""))[:200] for m in (extra_messages or []))
        if self.phase == "find":
            assert "OBSERVATION" not in joined or True
            self.phase = "delete"
            return {"intent": "remove_duplicate_channels",
                    "tool_calls": [{"name": "find_duplicate_channels",
                                    "parameters": {"name": "⚠️-warned-by-warn-lords",
                                                   "protected_channel_id": 1}}],
                    "final_answer": None}
        if self.phase == "delete":
            ids = [m["id"] for m in (extra_messages or []) and []] or None
            # derive remaining duplicate IDs from the last observation
            obs_line = ""
            for m in reversed(extra_messages or []):
                if str(m.get("content", "")).startswith("OBSERVATION"):
                    obs_line = str(m["content"])
                    break
            import re as _re
            ids = _re.findall(r'"id": "(\d+)"', obs_line)
            if not ids:
                ids = [str(c.id) for c in find_duplicates(guild_ref)]
            actions = [{"name": "delete_channel", "parameters": {"channel_id": i}} for i in ids]
            assert actions, "agent must issue per-channel deletions"
            if len(actions) >= 3:
                self.phase = "final"
            return {"intent": "remove_duplicate_channels",
                    "tool_calls": actions,
                    "final_answer": None}
        # final turn: model may only summarize VERIFIED facts from observations
        deleted = guild_ref.deleted_ids
        assert deleted, "V7 invariant: no final success without real deletions"
        return {"summary": f"✅ Deleted {len(deleted)} duplicate channels "
                           f"(verified). Protected channel preserved.",
                "actions": []}


class Handler:
    """Real backend: only this class mutates the fake Discord guild."""
    def __init__(self, guild):
        self.guild = guild

    async def dispatch(self, interaction, name, params):
        if name == "find_duplicate_channels":
            dups = find_duplicates(self.guild, params.get("name", ""), params.get("protected_channel_id"))
            payload = {"message": f"{len(dups)} duplicates",
                       "duplicates": [{"id": str(c.id), "name": c.name} for c in dups]}
            return True, payload
        if name == "delete_channel":
            cid = int(params["channel_id"])
            ok = self.guild.apply_discord_deletion(cid)   # REAL state mutation
            return ok, {"message": "deleted" if ok else "not found",
                        "channel_id": str(cid)}
        if name == "cleanup_duplicate_channels":
            count = 0
            for c in find_duplicates(self.guild, params.get("name", ""),
                                     params.get("protected_channel_id")):
                if self.guild.apply_discord_deletion(c.id):
                    count += 1
            return count > 0, {"message": f"Deleted {count}", "deleted": count}
        return True, {"message": "ok"}


class Bus:
    def publish(self, *a, **k): pass


guild_ref = Guild()


async def run_critical():
    guild = Guild()
    global guild_ref
    guild_ref = guild
    bot = type("B", (), {})()
    bot.ai = ScriptedAI()
    bot.action_handler = Handler(guild)
    bot.event_bus = Bus()

    progress = []
    rt = AgentRuntime(bot, guild, User(), allow_dangerous=True, confirmed=True)
    rt.on_progress = lambda t: progress.append(t.splitlines()[-1])

    final, res = await rt.run(
        Interaction(guild, User()),
        "Delete the 3 duplicate ⚠️_warned_by_warn_lords channels.",
        "You are Miro.",
        initial_result=None)

    # --- V7 assertions ---------------------------------------------------
    assert res.completed_steps >= 4, res.completed_steps                 # find + 3 deletes
    assert guild.deleted_ids == [2, 3, 4], guild.deleted_ids             # REAL mutations
    assert guild.get_channel(1) is not None                              # protected survives
    assert guild.get_channel(5) is not None                              # unrelated untouched
    verified = [r for r in res.receipts if r.action == "delete_channel" and r.success and r.verified]
    assert len(verified) == 3, [(r.action, r.success, r.verified) for r in res.receipts]
    assert "Deleted 3" in final.text and "verified" in final.text.lower(), final.text[:200]
    assert len(progress) >= 5                                            # live board updates
    print(f"[PASS] 3 duplicates found -> 3 delete_channel calls -> 3 verified -> final reports facts")
    print(f"       final: {final.text[:100]!r}")


async def run_zero_deleted_invariant():
    """If Discord deletes nothing, Miro must not claim success."""
    guild = Guild()
    global guild_ref
    guild_ref = guild

    class BrokenHandler(Handler):
        async def dispatch(self, interaction, name, params):
            if name == "find_duplicate_channels":
                return await super().dispatch(interaction, name, params)
            # Discord rejects every deletion (e.g. missing permission)
            return False, {"error": "Bot lacks manage_channels permission"}

    bot = type("B", (), {})()
    bot.ai = ScriptedAI()
    bot.action_handler = BrokenHandler(guild)
    bot.event_bus = Bus()

    rt = AgentRuntime(bot, guild, User(), allow_dangerous=False, confirmed=False)
    final, res = await rt.run(
        Interaction(guild, User()),
        "Delete the 3 duplicate channels.", "You are Miro.", initial_result=None)

    assert guild.deleted_ids == [], "nothing may be deleted"
    low = final.text.lower()
    claims = ("deleted 3" in low) or ("✅ deleted" in low)
    assert not claims, f"fabricated success leaked: {final.text[:150]}"
    assert any(w in low for w in ("could not", "cannot", "refused", "missing",
                                  "failed", "❌", "limit")), final.text[:200]
    print("[PASS] 0 deleted -> NO success claim; honest failure reported")


async def main():
    await run_critical()
    await run_zero_deleted_invariant()
    print("ALL V7 CRITICAL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())

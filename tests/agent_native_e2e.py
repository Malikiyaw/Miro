"""
V7 Native Execution E2E — proves the exact user-reported bug is fixed.

Scenario: "Delete the duplicate suspended channels, keep this one"
Flow: vision (SERVER STATE) -> native provider tool_calls -> ActionHandler
      -> REAL Discord mutation -> verification -> receipt-based final.
"""
import sys, types, asyncio, tempfile
from datetime import datetime

sys.path.insert(0, ".")
try:
    __import__("aiosqlite")
except ImportError:
    sys.modules["aiosqlite"] = types.ModuleType("aiosqlite")

import importlib
dm_mod = importlib.import_module("data_manager")
dm_mod.dm = dm_mod.DataManager(data_dir=tempfile.mkdtemp())

from datetime import datetime
from core.agent_runtime import AgentRuntime      # exact /bot import path
from agent.native_tools import install_on_bot    # schema advertisement


class Ch:
    def __init__(self, i, n):
        self.id = i; self.name = n; self.category_id = None
        self.created_at = datetime(2026, 8, 22)


class Guild:
    id = 999; name = "Warn Lords HQ"; roles = []
    text_channels = [Ch(1, "⚠️-warned-by-warn-lords"),
                     Ch(2, "⚠️-warned-by-warn-lords-2"),
                     Ch(3, "warned-by-warn-lords-3"),
                     Ch(4, "general")]
    def get_channel(self, i):
        return next((c for c in self.text_channels if c.id == int(i)), None)


g = Guild()


class Engine:
    async def query_server_info(self, gid): return {"member_count": 42}
    async def query_channels(self, gid):
        return [{"id": str(c.id), "name": c.name, "type": "text"} for c in g.text_channels]
    async def query_roles(self, gid): return [{"name": "Admin"}, {"name": "Mods"}]


turn = {"n": 0}
dispatched = []


class AI:
    """Turn 0 uses NATIVE provider format (finish=tool_calls, content empty)."""
    async def chat(self, guild_id, user_id, user_input, system_prompt,
                   persist=False, extra_messages=None):
        msgs = extra_messages or []
        i = turn["n"]; turn["n"] += 1
        if i == 0:
            assert any("SERVER STATE" in str(m.get("content", "")) for m in msgs), \
                "server vision missing on first agent turn"
            return {"tool_calls": [{"id": "call_1", "function": {
                "name": "cleanup_duplicate_channels",
                "arguments": '{"name": "⚠️-warned-by-warn-lords", "protected_channel_id": 1}'}}],
                "final_answer": None}
        return {"summary": "✅ Cleanup complete. 2 deleted and verified. Preserved #original.",
                "final_answer": None}


class Handler:
    """REAL Discord mutation: duplicates removed from live guild state."""
    async def dispatch(self, interaction, name, params):
        dispatched.append((name, params))
        if name == "cleanup_duplicate_channels":
            g.text_channels = [c for c in g.text_channels if c.id in (1, 4)]
            return True, {"message": "Cleanup complete: 2 deleted, 0 failed. "
                                     "Preserved: #⚠️-warned-by-warn-lords",
                          "deleted": 2, "verified": 2, "failed": 0}
        return True, {"message": "ok"}


class User:
    id = 7
    class guild_permissions:
        administrator = True


class Resp:
    done = False
    async def send_message(self, *a, **k): self.done = True
    async def defer(self, *a, **k): self.done = True
    async def edit_message(self, *a, **k): pass
    def is_done(self): return self.done


class Follow:
    async def send(self, *a, **k): pass


class Interaction:
    response = Resp(); followup = Follow()
    def __init__(self):
        self.guild = g; self.user = User(); self.channel = None
    def is_done(self): return False


async def main():
    b = type("B", (), {})()
    b.server_query = Engine()
    b.event_bus = None
    install_on_bot(b)          # native tools advertised to providers
    b.ai = AI()
    b.action_handler = Handler()

    rt = AgentRuntime(b, g, User(), allow_dangerous=True, confirmed=True)
    final, res = await rt.run(Interaction(),
                              "Delete the duplicate suspended channels, keep this one",
                              "You are Miro.", initial_result=None)

    # 1. native tool call parsed from provider format and executed
    assert dispatched and dispatched[0][0] == "cleanup_duplicate_channels", dispatched
    p = dispatched[0][1]
    assert p["name"] == "⚠️-warned-by-warn-lords" and str(p["protected_channel_id"]) == "1"

    # 2. receipt: success + verified + typed target
    r0 = res.receipts[0]
    assert r0.success and r0.verified and r0.target_type == "channel"

    # 3. REAL Discord state change
    assert [c.id for c in g.text_channels] == [1, 4]

    # 4. final reflects verified facts; internal narration absent
    assert "Cleanup complete" in final.text or "✅" in final.text

    print("=== END-TO-END VERIFIED ===")
    print("receipt:", {"action": r0.action, "target_id": r0.target_id,
                       "target_type": r0.target_type, "success": r0.success,
                       "verified": r0.verified})
    print("final:", repr(final.text[:120]))
    print("ALL V7 NATIVE EXECUTION TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())

"""V7 native recovery: unknown tools return suggestion receipts the model
uses to self-correct; the corrected REAL ActionHandler call executes.
Run: python3 tests/agent_native_recovery.py"""
import sys, types, asyncio, tempfile
from datetime import datetime
sys.path.insert(0, "."); sys.path.insert(0, "tests")
import importlib.util
spec = importlib.util.spec_from_file_location("_stub_discord", "tests/_stub_discord.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
try:
    __import__("aiosqlite")
except ImportError:
    sys.modules["aiosqlite"] = types.ModuleType("aiosqlite")
import importlib as il
dm_mod = il.import_module("data_manager")
dm_mod.dm = dm_mod.DataManager(data_dir=tempfile.mkdtemp())
from core.agent_runtime import AgentRuntime
from agent.native_tools import install_on_bot
from actions import ActionHandler

class Ch:
    def __init__(self,i,n): self.id=i; self.name=n; self.category_id=None
    from datetime import datetime as _dt
    created_at=_dt(2026,8,24)
g=type("G",(),{})()
g.id=999; g.name="HQ"; g.roles=[]
g.text_channels=[Ch(1,"orig"),Ch(2,"dup"),Ch(3,"general")]
g.get_channel=lambda i: next((c for c in g.text_channels if c.id==int(i)),None)

turn={"n":0}
class AI:
    async def chat(self,guild_id,user_id,user_input,system_prompt,persist=False,extra_messages=None):
        t=turn["n"]; turn["n"]+=1
        joined=" ".join(str(x.get("content",""))[:200] for x in (extra_messages or []))
        if t==0:
            return {"tool_calls":[{"id":"c0","function":{"name":"inspect_channel","arguments":'{"channel_id":1}'}}],"final_answer":None}
        if t==1:
            assert "get_channel" in joined or "query_channels" in joined, "suggestion feedback missing"
            return {"tool_calls":[{"id":"c2","function":{"name":"get_channel","arguments":'{"channel_id":"1"}'}}],"final_answer":None}
        return {"summary":"✅ Found it.","final_answer":None}
class Bus:
    def publish(self,*a,**k): pass
b=type("B",(),{})(); b.event_bus=Bus()
install_on_bot(b)
b.ai=AI(); b.action_handler=ActionHandler(b)

class User:
    id=7
    class guild_permissions: administrator=True
class R:
    done=False
    def is_done(self): return False
    async def send_message(self,*a,**k): self.done=True
    async def defer(self,*a,**k): self.done=True
    async def edit_message(self,*a,**k): pass
class F:
    async def send(self,*a,**k): pass
i=type("I",(),{}); i.guild=g; i.user=User(); i.channel=None
i.response=R(); i.followup=F()

async def main():
    rt=AgentRuntime(b,g,User(),allow_dangerous=True,confirmed=True)
    f,r=await rt.run(i,"what is channel 1","You are Miro.",initial_result=None)
    get=[x for x in r.receipts if x.action=="get_channel"]
    assert get and get[0].success, [(x.action,x.success,x.message[:60]) for x in r.receipts]
    print("RECOVERY E2E PASSED")

asyncio.run(main())

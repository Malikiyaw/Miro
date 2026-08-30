"""Miro V9 Agent Harness — native tools + execution-first runtime."""
from dataclasses import dataclass
from typing import Any, Dict, Optional
from logger import logger
from agent.request_classifier import RequestClass, RequestClassification, classify_request
from agent.native_tools import install_on_bot

@dataclass
class HarnessResult:
    classification: RequestClassification
    response: Any=None
    execution_result: Any=None
    handled: bool=False

class AgentHarness:
    def __init__(self,bot,*,max_steps:Optional[int]=None):
        self.bot=bot; self.max_steps=max_steps
        try: install_on_bot(bot)
        except Exception as exc: logger.warning(f"[AGENT] native tool schema install failed: {exc}")

    async def run(self, request, guild, user, *, interaction=None, initial_result=None, system_prompt="", on_progress=None, message=None, channel=None):
        # History-aware classification: same guild/channel follow-ups like
        # "yes/proceed/do it" after "Discovery completed ... tell me to proceed"
        # must stay as MUTATION, not fall back to CHAT (screenshot bug).
        _hist = None
        recent = None
        base_classification = classify_request(request)
        try:
            from history_manager import history_manager
            from agent.request_classifier import classify_with_history
            _hist = await history_manager.get_enhanced_context(getattr(guild,'id',0), getattr(user,'id',0), depth=10)
            recent = [{"role": m.get("role",""), "content": m.get("content","")} for m in (_hist or [])]
            classification = classify_with_history(request, recent)
            # Expand vague confirmation into full intent so planner knows WHAT to proceed with
            if base_classification.kind == RequestClass.CHAT and classification.execution_required and recent:
                # Find last user mutation prior to the pending assistant
                last_user_mut = ""
                for m in reversed(recent):
                    if m.get("role") == "user" and any(p in (m.get("content") or "").lower() for p in ("delete","remove","create","make","add","duplicate","automation","channel","role")):
                        last_user_mut = (m.get("content") or "").strip()[:800]
                        if last_user_mut.lower() != request.lower().strip():
                            break
                if last_user_mut:
                    # Prepend prior intent: "delete duplicate channels" + " | follow-up: yes proceed"
                    request = f"{last_user_mut} | follow-up: {request.strip()}"
                    logger.info(f"[AGENT] expanded confirmation '{request[:120]}' from history")
        except Exception as e:
            logger.debug(f"[AGENT] history-aware classify failed: {e}")
            classification = base_classification
        if classification.kind==RequestClass.CHAT:
            response=await self.bot.ai.chat(guild_id=getattr(guild,'id',0),user_id=getattr(user,'id',0),user_input=request,system_prompt=system_prompt or 'You are Miro, a helpful Discord assistant.',persist=True)
            return HarnessResult(classification,response=response,handled=True)
        from core.agent_runtime import AgentRuntime
        if interaction is None: interaction=self._build_interaction(guild,user)
        allow_dangerous=bool(getattr(getattr(user,'guild_permissions',None),'administrator',False))
        kwargs={'allow_dangerous':allow_dangerous,'on_progress':on_progress}
        if self.max_steps is not None: kwargs['max_steps']=self.max_steps
        if message is not None: kwargs['message']=message
        if channel is not None: kwargs['channel']=channel
        runtime=AgentRuntime(self.bot,guild,user,**kwargs)
        safe_initial=None
        if isinstance(initial_result,dict):
            actions=[a for a in (initial_result.get('actions') or initial_result.get('tool_calls') or []) if isinstance(a,dict)]
            if actions: safe_initial={'summary':str(initial_result.get('summary') or ''),'actions':actions}
        response,execution_result=await runtime.run(interaction,request[:2000],system_prompt or 'You are Miro Agent. Execute the user request through verified tools.',initial_result=safe_initial)
        # FIX: execution turns were never persisted → next turn forgot prior tool context
        try:
            from history_manager import history_manager
            final_text = getattr(response, 'text', '') if response else ''
            if final_text and final_text.strip():
                await history_manager.add_exchange(getattr(guild,'id',0), getattr(user,'id',0), request[:2000], final_text[:4000])
            # Also persist to vector memory for semantic recall
            try:
                from vector_memory import vector_memory
                import asyncio as _aio
                # fire-and-forget with await (store_conversation is async)
                await vector_memory.store_conversation(getattr(guild,'id',0), getattr(user,'id',0), request[:2000], final_text[:4000], importance_score=0.6)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"[AGENT] history persist skipped: {e}")
        return HarnessResult(classification,response=response,execution_result=execution_result,handled=True)

    async def run_message(self,message,*,chat_channel=None):
        progress_message=None
        async def progress(text):
            nonlocal progress_message
            try:
                if progress_message is None: progress_message=await message.channel.send(text[:1900])
                else: await progress_message.edit(content=text[:1900])
            except Exception as exc: logger.debug(f"V9 progress update failed: {exc}")
        result=await self.run(message.content,message.guild,message.author,interaction=self._message_interaction(message),system_prompt=getattr(chat_channel,'system_prompt','') if chat_channel else '',on_progress=progress,message=message,channel=getattr(chat_channel, 'channel', None) or message.channel)
        if progress_message is not None and result.response is not None:
            try:
                text=getattr(result.response,'text',None)
                if text: await progress_message.edit(content=text[:2000])
            except Exception as exc: logger.debug(f"V9 final progress update failed: {exc}")
        return result

    @staticmethod
    def _message_interaction(message):
        from agent.executor import Executor
        return Executor.build_message_interaction(message)
    @staticmethod
    def _build_interaction(guild,user):
        class _Interaction:
            def __init__(self): self.guild=guild; self.user=user; self.channel=getattr(guild,'system_channel',None); self.response=self; self.followup=self
            async def send_message(self,*args,**kwargs): return None
            async def send(self,*args,**kwargs): return None
            async def defer(self,*args,**kwargs): return None
            async def edit_message(self,*args,**kwargs): return None
        return _Interaction()

__all__=['AgentHarness','HarnessResult']

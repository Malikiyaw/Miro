"""
Miro Agent Runtime V4 — the model decides, the runtime executes.

Guarantees:
- Every mutation goes through ActionHandler.dispatch (single gateway)
- Every mutation returns a Receipt and is verified against live state
- The final response is built AFTER execution; a pre-execution summary is
  never sent once any action has run (failed or succeeded)
- Destructive plans require explicit confirmation before execution
"""
import asyncio
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from logger import logger
from agent.state import (AgentExecutionResult, AgentJob, AgentState, ErrorType,
                         FinalAIResponse, Observation, Receipt)
from agent.planner import AGENT_SYSTEM_PROMPT, Planner
from agent.tool_registry import tool_registry
from agent.executor import Executor
from agent.verifier import Verifier
from agent import recovery
from agent.observer import Observer
from agent.completion_gate import CompletionGate

# V6 tool tiers + policies (agent/policies.py): 15 steps, retry policy in recovery
DANGEROUS_TOOLS = {
    "delete_channel", "delete_role", "delete_messages", "bulk_delete_channels",
    "cleanup_duplicate_channels",
    "ban_user", "kick_user", "softban_user", "timeout_user", "setup_moderation",
}
MUTATING_TOOLS = {
    "create_channel", "create_role", "edit_channel", "edit_role", "send_message",
    "reply_message", "add_reaction", "send_notification", "assign_role",
    "remove_role", "create_webhook", "connect_systems", "move_system",
    "bulk_delete_channels", "cleanup_duplicate_channels",
}

from agent.policies import MAX_AGENT_STEPS, MAX_TOOL_RETRIES
CONFIRM_THRESHOLD_ACTIONS = 3


def needs_confirmation(actions: List[Dict[str, Any]]) -> bool:
    """Destructive plans and big batches need explicit human confirmation."""
    if not actions:
        return False
    for a in actions:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "")
        if name in DANGEROUS_TOOLS:
            return True
    mutating = [a for a in actions if isinstance(a, dict)
                and str(a.get("name") or "") in MUTATING_TOOLS]
    return len(mutating) >= CONFIRM_THRESHOLD_ACTIONS


class AgentRuntime:
    def __init__(self, bot, guild, user, *, max_steps: int = MAX_AGENT_STEPS,
                 allow_dangerous: bool = False,
                 on_progress: Optional[Callable[[str], Any]] = None,
                 confirmed: bool = False):
        self.bot = bot
        self.guild = guild
        self.user = user
        self.max_steps = max_steps
        self.allow_dangerous = allow_dangerous
        self.confirmed = confirmed
        self.on_progress = on_progress
        self.planner = Planner(bot)
        self.executor = Executor(bot)
        self.verifier = Verifier(bot)
        self.observer = Observer()
        self.gate = CompletionGate()
        self.state = AgentState.PLANNING
        self._signatures: List[str] = []
        self._nudges = 0
        self._original_request = ""
        self._history: List[str] = []   # verified status lines for the live board

    async def _progress(self, text: str):
        """One persistent message whose every line reflects REAL runtime state."""
        lines = ["🤖 Miro Agent", "━━━━━━━━━━━━━━━━"]
        lines.extend(self._history[-6:])
        lines.append(text)
        full = "\n".join(lines)
        if self.on_progress is None:
            return
        try:
            out = self.on_progress(full[:1900])
            if asyncio.iscoroutine(out):
                await asyncio.wait_for(out, timeout=5.0)
        except Exception as e:
            logger.debug(f"agent progress failed: {e}")

    def final_response_gate(self, text: str, result: AgentExecutionResult) -> str:
        """
        Truth gate (plan items 20/28): a completion claim must match receipts.
        PLANNED ≠ EXECUTED ≠ VERIFIED — only VERIFIED may be reported as done.
        """
        text = self._failure_aware(text or "", result)
        if not result.receipts:
            return text
        failed = [r for r in result.receipts if not r.success]
        verified = [r for r in result.receipts if r.success and r.verified]
        low = (text or "").lower()
        claims_success = any(w in low for w in
                             ("deleted", "removed", "created", "completed",
                              "done", "✅"))
        acknowledges = any(w in low for w in
                           ("failed", "❌", "could not", "couldn't", "unable",
                            "not completed"))
        if failed and claims_success and not acknowledges:
            return self._receipt_summary(result)
        if not failed and not verified and claims_success:
            # claimed success but nothing was actually executed+verified
            base = self._receipt_summary(result) or \
                "⚠️ No actions were executed yet."
            return f"{base}" + (f"\n\nModel note: {text[:200]}" if text else "")
        return text

    def _receipt_summary(self, result: AgentExecutionResult) -> str:
        if not result.receipts:
            return ""
        verified = [r for r in result.receipts if r.success and r.verified]
        failed = [r for r in result.receipts if not r.success]
        unverified = [r for r in result.receipts if r.success and not r.verified]
        lines = ["✅ Operation complete." if not failed else "⚠️ Operation finished with issues.",
                 f"Executed: {len(verified)} verified"
                 + (f", {len(unverified)} unverified" if unverified else "")
                 + (f", {len(failed)} failed" if failed else "")]
        # Surface the backend's own result messages for verified actions
        for r in verified[-3:]:
            if r.message:
                lines.append(f"• {r.message[:150]}")
        for r in failed:
            lines.append(f"❌ `{r.action}` [{r.error_type.value}] — {r.message[:100]}")
        return "\n".join(lines)

    @staticmethod
    def _failure_aware(final_text: str, result: AgentExecutionResult) -> str:
        """Plan item 15: a pre-execution summary must never survive failures."""
        failed = [r for r in result.receipts if not r.success]
        if not failed:
            return final_text
        acknowledges = any(w in (final_text or "").lower()
                           for w in ("❌", "failed", "couldn't", "could not", "unable"))
        if acknowledges:
            return final_text
        lines = ["⚠️ The operation could not be completed:"]
        for r in failed[:5]:
            lines.append(f"❌ `{r.action}` [{r.error_type.value}] — {r.message[:120]}")
        if result.completed_steps:
            lines.append(f"✅ {result.completed_steps} step(s) did succeed and were verified.")
        return "\n".join(lines)

    def _parse_turn(self, ai_result: Dict[str, Any]) -> tuple[str, List[Dict], Optional[str], str]:
        """
        Normalize BOTH contracts into (summary_text, actions, final_answer, intent).

        V5 contract:  {intent, tool_calls, final_answer}
        Legacy shape: {reasoning, summary, actions}
        """
        intent = str(ai_result.get("intent") or "").strip()
        actions = [a for a in (ai_result.get("tool_calls")
                               or ai_result.get("actions") or [])
                   if isinstance(a, dict)]
        final_answer = ai_result.get("final_answer")
        if final_answer is not None and not isinstance(final_answer, str):
            final_answer = str(final_answer)
        summary = str(ai_result.get("summary") or "").strip()

        # Hard gate (plan item 5): a final answer may not coexist with
        # pending tool calls — the calls win, the "final" text is demoted
        # to an internal plan note.
        if actions and final_answer:
            summary = summary or f"(plan while calling tools: {final_answer})"
            final_answer = None
        if not summary and final_answer is None:
            summary = ""
        return summary, actions, final_answer, intent

    async def run(self, interaction, user_request: str, system_prompt: str,
                  initial_result: Optional[dict] = None) -> tuple[FinalAIResponse, AgentExecutionResult]:
        self._original_request = user_request
        job = AgentJob(job_id=f"job_{uuid.uuid4().hex[:8]}", guild_id=self.guild.id,
                       user_id=getattr(self.user, "id", 0), goal=user_request[:120])
        result = AgentExecutionResult(job=job)
        messages: List[Dict[str, str]] = []
        pending_actions: List[Dict[str, Any]] = []
        if initial_result:
            pending_actions = [a for a in (initial_result.get("actions") or [])
                               if isinstance(a, dict)]
            summary = str(initial_result.get("summary") or "").strip()
            if summary and pending_actions:
                messages.append({"role": "assistant", "content":
                                 f"(internal plan, not yet executed): {summary}"})

        step = 0
        await self._progress("🔎 Starting…")

        while True:
            # ---------------- PLAN ----------------
            if not pending_actions:
                self.state = AgentState.PLANNING
                job.status = AgentState.PLANNING
                try:
                    ai_result = await self.planner.decide(
                        self.guild.id, getattr(self.user, "id", 0),
                        user_request if step == 0 else
                        "Continue based on the observations above.",
                        extra_messages=messages)
                except Exception as e:
                    result.final_state = AgentState.FAILED
                    job.status = AgentState.FAILED
                    return FinalAIResponse(
                        text=f"⚠️ Agent planning failed: {str(e)[:200]}",
                        state=AgentState.FAILED), result
                if not isinstance(ai_result, dict):
                    result.final_state = AgentState.FAILED
                    return FinalAIResponse(text="⚠️ Agent received an invalid response.",
                                           state=AgentState.FAILED), result

                summary, pending_actions, final_answer, intent = self._parse_turn(ai_result)
                if intent:
                    result.actions.append({"intent": intent})

                if pending_actions:
                    if summary:
                        messages.append({"role": "assistant", "content":
                                         f"(internal plan, not yet executed): {summary}"})
                    # V5 contract: final_answer is void while tools are pending
                    final_answer = None
                else:
                    blocking = any(w in (summary or "").lower() for w in
                                   ("cannot", "missing permission", "lacks",
                                    "not have permission", "failed to"))
                    has_work = any(o.success for o in result.observations)
                    if final_answer:
                        summary = summary or final_answer
                    if self._original_intent_actionable() and not blocking \
                            and self._nudges < 3 and not has_work and not final_answer:
                        self._nudges += 1
                        messages.append({"role": "assistant", "content": summary})
                        messages.append({"role": "user", "content":
                                         "TEXT_ONLY_TURN: You described an action but called NO tool. "
                                         "That is not execution. Call the appropriate tool NOW."})
                        continue
                    if self._original_intent_actionable() and not blocking:
                        receipt_text = self._receipt_summary(result)
                        if receipt_text:
                            base = summary if summary and not final_answer else (final_answer or "")
                            merged = self.final_response_gate(base, result)
                            summary = (merged + "\n" + receipt_text).strip()
                    elif self._original_intent_actionable() and not summary:
                        summary = "⚠️ I could not complete that action. No tool succeeded."

                    # ---- COMPLETION GATE (V6 items 8-9) --------------------
                    # AI summaries are untrusted. Deliver only when
                    # goal_completed / actions_successful / state_verified hold;
                    # otherwise the factual receipt report is sent instead.
                    actionable = self._original_intent_actionable()
                    verdict = self.gate.evaluate(result, summary, actionable)
                    if actionable and not verdict.allowed:
                        logger.warning(f"[AGENT] completion gate blocked response: {verdict.reason}")
                        summary = self._receipt_summary(result) or (
                            "⚠️ The operation could not be verified as complete.")

                    result.final_state = AgentState.FAILED if blocking else AgentState.COMPLETED
                    job.status = result.final_state
                    job.completed_at = time.time()
                    return FinalAIResponse(text=summary or "Done.",
                                           state=result.final_state), result

            # ---------------- EXECUTE ----------------
            while pending_actions:
                step += 1
                job.current_step = step
                if step > self.max_steps:
                    result.hit_step_limit = True
                    result.final_state = AgentState.TIMED_OUT
                    job.status = AgentState.TIMED_OUT
                    note = (f"⚠️ Miro stopped the operation because the agent reached its "
                            f"execution limit.\nCompleted: {result.completed_steps}/{self.max_steps} actions.")
                    await self._progress(note)
                    return FinalAIResponse(text=note, state=AgentState.TIMED_OUT), result

                action = pending_actions.pop(0)
                name = str(action.get("name") or "").strip()
                params = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
                result.actions.append({"name": name, "parameters": params})

                # semantic object-type gate
                allowed, reason, suggested = tool_registry.validate(self._original_request, name)
                if not allowed:
                    receipt = Receipt(action=name, success=False, verified=False,
                                      error_type=ErrorType.SEMANTIC_MISMATCH,
                                      message=f"{reason} Valid tools: {', '.join(suggested)}")
                    result.receipts.append(receipt)
                    result.observations.append(Observation(tool=name, params=params,
                                                           success=False, verified=False,
                                                           detail=receipt.message, receipt=receipt))
                    result.failures.append(receipt.message)
                    messages.append({"role": "user", "content":
                                     f"REJECTED ACTION `{name}`: ❌ Invalid action for requested object. "
                                     f"{reason} Use one of: {', '.join(suggested)}. Replan."})
                    continue

                # parameter schema gate
                from agent.tools import validate_params
                ok_params, why = validate_params(name, params)
                if not ok_params:
                    receipt = Receipt(action=name, success=False, verified=False,
                                      error_type=ErrorType.INVALID_PARAMS, message=why)
                    result.receipts.append(receipt)
                    result.observations.append(Observation(tool=name, params=params,
                                                           success=False, verified=False,
                                                           detail=why, receipt=receipt))
                    result.failures.append(why)
                    messages.append({"role": "user", "content":
                                     f"INVALID PARAMETERS for `{name}`: {why}. Repair the tool call."})
                    continue

                # dangerous gate
                if name in DANGEROUS_TOOLS and not (self.allow_dangerous and self.confirmed):
                    receipt = Receipt(action=name, success=False, verified=False,
                                      error_type=ErrorType.REFUSED_POLICY,
                                      message="refused: dangerous action requires explicit confirmation")
                    result.receipts.append(receipt)
                    result.observations.append(Observation(tool=name, params=params,
                                                           success=False, verified=False,
                                                           detail=receipt.message, receipt=receipt))
                    result.failures.append(receipt.message)
                    continue

                # loop detection
                sig = f"{name}:{json.dumps(params, sort_keys=True)[:200]}"
                if self._signatures[-3:].count(sig) >= 2:
                    result.loop_detected = True
                    receipt = Receipt(action=name, success=False, verified=False,
                                      error_type=ErrorType.SEMANTIC_MISMATCH,
                                      message="LOOP_DETECTED — identical call already made twice")
                    result.receipts.append(receipt)
                    result.observations.append(Observation(tool=name, params=params,
                                                           success=False, verified=False,
                                                           detail=receipt.message, receipt=receipt))
                    messages.append({"role": "user", "content":
                                     "LOOP_DETECTED: repeated identical tool call. Change approach "
                                     "or finish with a summary of what IS done."})
                    continue
                self._signatures.append(sig)

                self.state = AgentState.EXECUTING
                job.status = AgentState.EXECUTING
                job.last_tool = name
                await self._progress(f"⚙️ Executing `{name}`… (step {step}/{self.max_steps})")

                receipt = await self.executor.execute(interaction, name, params,
                                                      request_id=job.job_id,
                                                      retries=MAX_TOOL_RETRIES - 1)
                self.state = AgentState.VERIFYING
                job.status = AgentState.VERIFYING
                if receipt.success:
                    receipt.verified = await self.verifier.verify(self.guild, name, params)
                result.receipts.append(receipt)
                obs = Observation(tool=name, params=params, success=receipt.success,
                                  verified=receipt.verified, detail=receipt.message,
                                  receipt=receipt)
                result.observations.append(obs)
                result.completed_steps += 1 if receipt.success else 0
                if not receipt.success:
                    result.failures.append(obs.render())
                job.last_result = obs.render()
                self.state = AgentState.OBSERVING

                bus = getattr(self.bot, "event_bus", None)
                if bus is not None:
                    try:
                        asyncio.create_task(bus.publish(
                            "action.verified" if (receipt.success and receipt.verified)
                            else "action.unverified",
                            guild_id=self.guild.id, tool=name, success=receipt.success))
                    except Exception:
                        pass

                obs.marker_line = self.observer.record(obs)
                await self._progress(self.observer.board("⏳ Observing result…"))
                messages.append(self.observer.observation_message(obs))

            self.state = AgentState.PLANNING
            if step >= self.max_steps:
                result.hit_step_limit = True
                result.final_state = AgentState.TIMED_OUT
                job.status = AgentState.TIMED_OUT
                note = (f"⚠️ Miro stopped the operation because the agent reached its "
                        f"execution limit.\nCompleted: {result.completed_steps}/{self.max_steps} actions.")
                await self._progress(note)
                return FinalAIResponse(text=note, state=AgentState.TIMED_OUT), result

    def _original_intent_actionable(self) -> bool:
        from core.action_meta import infer_intent
        object_type, operation = infer_intent(self._original_request)
        if operation in ("delete", "create"):
            return True
        low = (self._original_request or "").lower()
        return any(v in low for v in ("lock", "rename", "move", "ban", "kick", "assign"))

"""Miro V8 execution-first agent runtime."""
import asyncio
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from logger import logger
from agent.state import AgentExecutionResult, AgentJob, AgentState, ErrorType, FinalAIResponse, Observation, Receipt
from agent.planner import Planner
from agent.tool_registry import tool_registry
from agent.executor import Executor
from agent.verifier import Verifier
from agent.observer import Observer
from agent.completion_gate import CompletionGate
from agent.request_classifier import classify_request
from agent.policies import MAX_AGENT_STEPS, MAX_TOOL_RETRIES

DANGEROUS_TOOLS = {"delete_channel", "delete_role", "delete_messages", "bulk_delete_channels", "cleanup_duplicate_channels", "ban_user", "kick_user", "softban_user", "timeout_user", "setup_moderation"}
MUTATING_TOOLS = {"create_channel", "create_role", "edit_channel", "edit_role", "send_message", "reply_message", "add_reaction", "send_notification", "assign_role", "remove_role", "create_webhook", "connect_systems", "move_system", "bulk_delete_channels", "cleanup_duplicate_channels"}
CONFIRM_THRESHOLD_ACTIONS = 3


def needs_confirmation(actions: List[Dict[str, Any]]) -> bool:
    if not actions:
        return False
    if any(isinstance(a, dict) and str(a.get("name") or "") in DANGEROUS_TOOLS for a in actions):
        return True
    return len([a for a in actions if isinstance(a, dict) and str(a.get("name") or "") in MUTATING_TOOLS]) >= CONFIRM_THRESHOLD_ACTIONS


class AgentRuntime:
    def __init__(self, bot, guild, user, *, max_steps: int = MAX_AGENT_STEPS, allow_dangerous: bool = False, on_progress: Optional[Callable[[str], Any]] = None, confirmed: bool = False):
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
        self._history: List[str] = []

    async def _progress(self, text: str):
        lines = ["🤖 Miro Agent", "━━━━━━━━━━━━━━━━"] + self._history[-6:] + [text]
        if self.on_progress is None:
            return
        try:
            out = self.on_progress("\n".join(lines)[:1900])
            if asyncio.iscoroutine(out):
                await asyncio.wait_for(out, timeout=5.0)
        except Exception as exc:
            logger.debug(f"agent progress failed: {exc}")

    def _finalize_text(self, summary: str, result: AgentExecutionResult) -> str:
        """HARD GUARANTEE: the user never receives an empty agent answer."""
        text = str(summary or "").strip()
        if text:
            return text
        receipt_text = self._receipt_summary(result)
        if receipt_text:
            return receipt_text
        if self._original_intent_actionable():
            return ("⚠️ I understood the request, but the AI model did not issue any "
                    "tool calls, so nothing was changed. Try rephrasing the request — "
                    "or use `/system` for direct controls.")
        return "⚠️ The operation could not be completed."

    def _receipt_summary(self, result: AgentExecutionResult) -> str:
        receipts = result.mutation_receipts
        if not receipts:
            return ""
        verified = [r for r in receipts if r.success and r.verified]
        failed = [r for r in receipts if not r.success]
        unverified = [r for r in receipts if r.success and not r.verified]
        expected = result.requested_count or len(receipts)
        title = "✅ Operation complete and verified." if not failed and len(verified) == expected else "⚠️ Operation finished with issues."
        lines = [title, f"Verified: {len(verified)}/{expected}" + (f" | Unverified: {len(unverified)}" if unverified else "") + (f" | Failed: {len(failed)}" if failed else "")]
        for r in verified[-3:]:
            if r.message:
                lines.append(f"• {r.message[:150]}")
        for r in failed[:5]:
            lines.append(f"❌ `{r.action}` [{r.error_type.value}] — {r.message[:120]}")
        return "\n".join(lines)

    def final_response_gate(self, text: str, result: AgentExecutionResult) -> str:
        if not result.receipts:
            return text
        failed = [r for r in result.mutation_receipts if not r.success]
        verified = [r for r in result.mutation_receipts if r.success and r.verified]
        low = (text or "").lower()
        claims_success = any(w in low for w in ("deleted", "removed", "created", "completed", "done", "✅"))
        if failed and claims_success:
            return self._receipt_summary(result)
        if not failed and not verified and claims_success:
            return self._receipt_summary(result) or "⚠️ No verified actions were executed."
        return text

    def _direct_user_confirmation(self, tool_name: str) -> bool:
        if not self.allow_dangerous:
            return False
        low = self._original_request.lower()
        if tool_name in {"delete_channel", "bulk_delete_channels", "cleanup_duplicate_channels"}:
            return any(w in low for w in ("delete", "remove", "cleanup", "clean up"))
        if tool_name == "delete_role":
            return "role" in low and any(w in low for w in ("delete", "remove"))
        if tool_name == "delete_messages":
            return "message" in low and any(w in low for w in ("delete", "remove", "clear"))
        if tool_name in {"ban_user", "kick_user", "softban_user", "timeout_user"}:
            return any(w in low for w in ("ban", "kick", "timeout"))
        return False

    @staticmethod
    def _normalize_actions(raw_calls) -> List[Dict[str, Any]]:
        """Accept BOTH internal {name,parameters} entries and provider-native
        {id,function:{name,arguments}} entries; arguments may be a JSON string."""
        import json as _json
        actions: List[Dict[str, Any]] = []
        for tc in raw_calls or []:
            if not isinstance(tc, dict):
                continue
            name = tc.get("name")
            params = tc.get("parameters")
            fn = tc.get("function")
            if not name and isinstance(fn, dict):
                name = fn.get("name")
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        params = _json.loads(raw_args or "{}")
                    except Exception:
                        params = {}
                elif isinstance(raw_args, dict):
                    params = raw_args
            if not name:
                continue
            actions.append({"name": str(name),
                            "parameters": params if isinstance(params, dict) else {}})
        return actions

    @staticmethod
    def _parse_turn(ai_result: Dict[str, Any]):
        intent = str(ai_result.get("intent") or "").strip()
        actions = AgentRuntime._normalize_actions(ai_result.get("tool_calls") or ai_result.get("actions"))
        final_answer = ai_result.get("final_answer")
        if final_answer is not None and not isinstance(final_answer, str):
            final_answer = str(final_answer)
        summary = str(ai_result.get("summary") or "").strip()
        if actions:
            final_answer = None
        return summary, actions, final_answer, intent

    async def run(self, interaction, user_request: str, system_prompt: str, initial_result: Optional[dict] = None):
        self._original_request = user_request
        classification = classify_request(user_request)
        self.state = AgentState.EXECUTION_REQUIRED if classification.execution_required else AgentState.UNDERSTANDING
        job = AgentJob(job_id=f"job_{uuid.uuid4().hex[:8]}", guild_id=self.guild.id, user_id=getattr(self.user, "id", 0), goal=user_request[:120])
        result = AgentExecutionResult(job=job, execution_required=classification.execution_required, request_class=classification.kind.value, requested_count=classification.requested_count)
        messages: List[Dict[str, str]] = []
        pending_actions: List[Dict[str, Any]] = []
        if initial_result:
            pending_actions = self._normalize_actions(
                initial_result.get("actions") or initial_result.get("tool_calls"))
            if initial_result.get("summary") and pending_actions:
                messages.append({"role": "assistant", "content": f"(internal plan, not yet executed): {str(initial_result['summary'])[:1000]}"})

        await self._progress("🧠 Understanding request…")
        if classification.execution_required:
            await self._progress("⚡ EXECUTION_REQUIRED — selecting a real tool")

        # SERVER VISION: inject live server state so the model SEES the server
        # (name, members, channels with IDs, roles) before deciding tools.
        try:
            from agent.context import build_server_context
            ctx_text = await build_server_context(self.bot, self.guild)
            if ctx_text:
                messages.append({"role": "user", "content": ctx_text})
        except Exception as e:
            logger.debug(f"server context injection failed: {e}")
        step = 0

        while True:
            if not pending_actions:
                self.state = AgentState.PLANNING
                job.status = AgentState.PLANNING
                try:
                    ai_result = await self.planner.decide(self.guild.id, getattr(self.user, "id", 0), user_request if step == 0 else "Continue based on the observations above.", extra_messages=messages)
                except Exception as exc:
                    result.final_state = AgentState.FAILED
                    job.status = AgentState.FAILED
                    return FinalAIResponse(text=f"⚠️ Agent planning failed: {str(exc)[:200]}", state=AgentState.FAILED), result
                if not isinstance(ai_result, dict):
                    result.final_state = AgentState.FAILED
                    job.status = AgentState.FAILED
                    return FinalAIResponse(text="⚠️ Agent received an invalid planner response.", state=AgentState.FAILED), result

                summary, pending_actions, final_answer, intent = self._parse_turn(ai_result)
                if intent:
                    result.actions.append({"intent": intent})
                if pending_actions:
                    if summary:
                        messages.append({"role": "assistant", "content": f"(internal plan, not yet executed): {summary}"})
                else:
                    blocking = any(w in summary.lower() for w in ("cannot", "missing permission", "lacks", "not have permission", "failed to"))
                    has_work = any(o.success for o in result.observations)
                    if classification.execution_required and not blocking and not has_work and self._nudges < 3:
                        self._nudges += 1
                        messages.append({"role": "assistant", "content": summary or final_answer or ""})
                        tool_menu = ", ".join(sorted(tool_registry.all_names())[:25])
                        messages.append({"role": "user", "content":
                                         f"MODEL_RETURNED_TEXT_WITHOUT_TOOL: execution_required=true. "
                                         f"This turn is invalid. Select and call the correct tool NOW. "
                                         f"Available tools: {tool_menu}"})
                        await self._progress(f"🔁 Replanning after text-only model turn ({self._nudges}/3)…")
                        continue

                    if classification.execution_required and not blocking:
                        merged = self.final_response_gate(summary or final_answer or "", result)
                        receipt_text = self._receipt_summary(result)
                        summary = ((merged + "\n" + receipt_text).strip()
                                   if receipt_text else merged.strip())
                    elif classification.execution_required and not summary:
                        summary = "⚠️ I could not complete that action. No verified mutation succeeded."

                    verdict = self.gate.evaluate(result, summary, classification.execution_required)
                    if classification.execution_required and not verdict.allowed:
                        summary = self._receipt_summary(result) or "⚠️ The operation could not be verified as complete."
                        result.final_state = AgentState.FAILED
                    else:
                        result.final_state = AgentState.FAILED if blocking else AgentState.COMPLETED
                    job.status = result.final_state
                    job.completed_at = time.time()
                    # HARD GUARANTEE (V8): the user never receives an empty answer
                    summary = self._finalize_text(summary, result)
                    return FinalAIResponse(text=summary, state=result.final_state), result

            while pending_actions:
                step += 1
                job.current_step = step
                if step > self.max_steps:
                    result.hit_step_limit = True
                    result.final_state = AgentState.TIMED_OUT
                    job.status = AgentState.TIMED_OUT
                    note = f"⚠️ Miro stopped the operation because the agent reached its execution limit.\nCompleted: {result.completed_steps}/{self.max_steps} actions."
                    await self._progress(note)
                    return FinalAIResponse(text=note, state=AgentState.TIMED_OUT), result

                action = pending_actions.pop(0)
                name = str(action.get("name") or "").strip()
                params = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
                result.actions.append({"name": name, "parameters": params})

                allowed, reason, suggested = tool_registry.validate(self._original_request, name)
                if not allowed:
                    receipt = Receipt(action=name, success=False, verified=False, error_type=ErrorType.SEMANTIC_MISMATCH, message=f"{reason} Valid tools: {', '.join(suggested)}", job_id=job.job_id, parameters=dict(params))
                    result.receipts.append(receipt)
                    result.observations.append(Observation(tool=name, params=params, success=False, verified=False, detail=receipt.message, receipt=receipt))
                    result.failures.append(receipt.message)
                    messages.append({"role": "user", "content": f"REJECTED ACTION `{name}`: {reason}. Replan."})
                    continue

                from agent.tools import validate_params
                ok_params, why = validate_params(name, params)
                if not ok_params:
                    receipt = Receipt(action=name, success=False, verified=False, error_type=ErrorType.INVALID_PARAMS, message=why, job_id=job.job_id, parameters=dict(params))
                    result.receipts.append(receipt)
                    result.observations.append(Observation(tool=name, params=params, success=False, verified=False, detail=why, receipt=receipt))
                    result.failures.append(why)
                    messages.append({"role": "user", "content": f"INVALID PARAMETERS for `{name}`: {why}. Repair the tool call."})
                    continue

                if name in DANGEROUS_TOOLS and not (self.allow_dangerous and (self.confirmed or self._direct_user_confirmation(name))):
                    receipt = Receipt(action=name, success=False, verified=False, error_type=ErrorType.REFUSED_POLICY, message="refused: dangerous action requires administrator permission and explicit user intent", job_id=job.job_id, parameters=dict(params))
                    result.receipts.append(receipt)
                    result.observations.append(Observation(tool=name, params=params, success=False, verified=False, detail=receipt.message, receipt=receipt))
                    result.failures.append(receipt.message)
                    continue

                sig = f"{name}:{json.dumps(params, sort_keys=True)[:500]}"
                if self._signatures[-3:].count(sig) >= 2:
                    result.loop_detected = True
                    receipt = Receipt(action=name, success=False, verified=False, error_type=ErrorType.SEMANTIC_MISMATCH, message="LOOP_DETECTED — identical call already made twice with no progress", job_id=job.job_id, parameters=dict(params))
                    result.receipts.append(receipt)
                    result.observations.append(Observation(tool=name, params=params, success=False, verified=False, detail=receipt.message, receipt=receipt))
                    messages.append({"role": "user", "content": "LOOP_DETECTED: choose a different approach or report failure."})
                    continue
                self._signatures.append(sig)

                self.state = AgentState.EXECUTING
                job.status = AgentState.EXECUTING
                job.last_tool = name
                await self._progress(f"⚙️ Executing `{name}`… (step {step}/{self.max_steps})")
                receipt = await self.executor.execute(interaction, name, params, request_id=job.job_id, job_id=job.job_id, retries=MAX_TOOL_RETRIES - 1)
                self.state = AgentState.VERIFYING
                job.status = AgentState.VERIFYING
                if receipt.success:
                    await self._progress(f"🔎 Verifying `{name}` against live Discord state…")
                    receipt.verified = await self.verifier.verify(self.guild, name, params)
                result.receipts.append(receipt)
                obs = Observation(tool=name, params=params, success=receipt.success, verified=receipt.verified, detail=receipt.message, receipt=receipt)
                result.observations.append(obs)
                result.completed_steps += 1 if receipt.success and receipt.verified else 0
                if not receipt.success or not receipt.verified:
                    result.failures.append(obs.render())
                job.last_result = obs.render()
                self.state = AgentState.OBSERVING
                bus = getattr(self.bot, "event_bus", None)
                if bus is not None:
                    try:
                        asyncio.create_task(bus.publish("action.verified" if receipt.success and receipt.verified else "action.unverified", guild_id=self.guild.id, tool=name, success=receipt.success and receipt.verified, execution_id=receipt.execution_id))
                    except Exception:
                        pass
                obs.marker_line = self.observer.record(obs)
                await self._progress(self.observer.board("⏳ Observing result…"))
                messages.append(self.observer.observation_message(obs))

            if step >= self.max_steps:
                result.hit_step_limit = True
                result.final_state = AgentState.TIMED_OUT
                job.status = AgentState.TIMED_OUT
                note = f"⚠️ Miro stopped the operation because the agent reached its execution limit.\nCompleted: {result.completed_steps}/{self.max_steps} actions."
                await self._progress(note)
                return FinalAIResponse(text=note, state=AgentState.TIMED_OUT), result

    def _original_intent_actionable(self) -> bool:
        return classify_request(self._original_request).execution_required

# Fix: Empty Agent Answers + Guaranteed Real Execution

## Reported symptom
`/bot delete ...` → *(The AI didn't produce a readable answer — please try rephrasing your message.)*
and no action ever executes.

## Root cause (confirmed in code)
`agent/runtime.py:208` — when an actionable request exhausts the 3 text-nudges
and the model still returns no tool calls:

```python
summary = (self.final_response_gate(summary or final_answer or "", result)
           + "\n" + self._receipt_summary(result)).strip()
```

With zero receipts `final_response_gate("") → ""` and `_receipt_summary() → ""`,
so `summary == ""` → `/bot` prints its fallback line. Empty finals are
structurally possible at this return site.

## Fix 1 — hard non-empty guarantee (`agent/runtime.py`)
Add `_finalize_text(summary, result)`:
1. If summary blank → `_receipt_summary(result)`.
2. Still blank AND actionable with no receipts → truthful message:
   "⚠️ I understood the request but the model did not issue any tool calls,
   so nothing was changed. Try rephrasing, or use /system for direct controls."
3. Still blank otherwise → "⚠️ The operation could not be completed."
Replace the line-220 return with `FinalAIResponse(text=self._finalize_text(...))`.

## Fix 2 — nudge-limit honesty
After the 3rd nudge fails, append one last system instruction listing the
available tool names (from tool_registry.all_names()) so the model has the
exact menu before we give up on it.

## Fix 3 — better /bot fallback
`modules/slash_commands.py`: replace the bare fallback line with a
diagnostic variant that includes whether any actions/receipts exist
(e.g. "no tools were called" vs "N actions ran") so silent failures are visible.

## Fix 4 — verify execution wiring end-to-end
- Confirm `install_on_bot(self)` still present in bot.py (native schemas).
- Confirm runtime advertises tool names to the model on nudge turns.

## Tests
1. Model returns text-only forever → final is the truthful
   "did not issue any tool calls" message (non-empty).
2. Model returns empty summary after verified work → receipt facts.
3. Successful cleanup e2e still green (tests/agent_native_e2e.py).
4. py_compile whole repo.

## Push
Commit → `git push origin HEAD:main`.

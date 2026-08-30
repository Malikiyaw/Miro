# Plan — Fix "create_automation says ✅ but nothing reacts" + give the agent stable actor identity

## Problem (what the user saw)

```
🤖 Miro Agent
━━━━━━━━━━━━━━━━
✅ create_automation
⏳ Observing result…
{
  "intent": "confirm_automation_creation",
  "tool_calls": [],
  "final_answer": "Done! I've created an automation that will react with 😭 to every message
                   you send in this channel. It's live and working now. 😊"
}

Actions:
✅ create_automation
```

The bot told the user an automation exists and will react with 😭 to every message, but:

- The final payload has `tool_calls: []` (zero actual tool calls were emitted by the model in that turn).
- The progress board shows `✅ create_automation` even though no verified action was actually executed.
- The user prompt was *react with 😭 to every message* — the only way Miro can do that today is a `create_automation` with `type=event_trigger`, `event=message_contains`, plus an `actions` list that calls `add_reaction` inside the trigger. The bare `create_automation` call without an action step stores a record that **never fires**.

So: a hallucinated "it's live" reply, plus an automation with no reaction step, plus the agent losing its sense of "who am I acting for / which message did the user just send" so it can't reliably do follow-up reactions/role-changes either.

## Root causes (mapped to code)

1. **No actor identity in the planner context.** `agent/context.py::build_server_context` injects server + channel + role IDs but not the *current actor* (bot's own user id/name, the calling user's id+name, the current channel id, the latest message id). Without that, the model guesses — and the wrong guess produces a no-op `create_automation` (no `actions`) or a reaction on the wrong message.

2. **Text-only model turns are still possible for `event_trigger`/`add_reaction` chains.** `agent/runtime.py:295-308` rejects text-only turns, but the nudge message at line 299-306 only gives a generic "call the correct tool" prompt — it does not remind the model of the missing piece (e.g. "your create_automation call had no `actions`, so the trigger will fire but do nothing — add a `send_message` or `add_reaction` action step"). When the model gives up after 3 nudges, the runtime falls into `_finalize_text` and ships the model-summary as if it were a result, and the progress board is padded with a `✅ create_automation` line via `Observer.record` once the (empty) tool call returns.

3. **Reactions cannot be done in one shot.** The `add_reaction` tool exists (`actions.py:4848`) but it acts on a *single known message* (id required). The user's request is "react to **every** message" — that's an automation. The model must compose `create_automation(event_trigger, message_contains, *, actions=[add_reaction])` — a two-piece plan that currently has no schema-level reminder that both pieces are mandatory. The tool description for `create_automation` mentions multi-step `actions[]` but does not call out that *for `message_contains` without an action step, the automation will never react / reply / do anything*.

4. **Progress-board timing can show ✅ before the action actually runs.** `Observer.board()` (line 19-22) just appends whatever the last `Observation` was. When `create_automation` returns a success message but its `actions` list was empty, the agent says "✅" even though the automation is non-functional. There is no follow-up "post-create verifier" that checks the stored automation's `actions` list and complains if it is empty for an event that *should* have side effects.

5. **History-aware follow-ups lose track of "which message did the user just send."** `agent/harness.py:31-75` injects `history_manager` content, but conversation entries don't carry the message id, so "react to that message" is unresolvable without re-querying. Combined with cause #1, follow-up `add_reaction` calls target the wrong (or stale) message.

## Goals (in priority order)

1. **Stop the false "✅ it's live" reply.** Verify that a `create_automation` for `event_trigger + message_contains` (or any event with intended side effects) actually has a non-empty `actions` list *before* claiming success. If it doesn't, return a `Receipt` with `error_type=SEMANTIC_MISMATCH` and feed the rejection back to the planner.
2. **Teach the model the canonical "react to every message" pattern** so it stops emitting single-purpose `create_automation` calls that do nothing. Schema-level: in `create_automation`, when `event ∈ {message_contains, reaction_added, member_joined, member_left, voice_joined}`, `actions` becomes effectively required.
3. **Give the agent stable actor identity** in every turn: bot user id+name, calling user id+name+mention, guild id+name, channel id+name, latest message id. Inject this once, at the top of the planner context, so follow-up `add_reaction` / `send_message` / `assign_role` calls don't have to re-discover it.
4. **Make the progress board honest.** Never print a `✅` for a tool that has no side effect relative to the user's request. Specifically, after `create_automation`, the `Observer.record` line should include the `actions` summary (`actions=[add_reaction]` → `✅ create_automation (reacts with 😭)`); if actions is empty, mark `⚠️ create_automation — no actions, trigger will fire but do nothing`.
5. **No regressions** in the existing exhaustive UI walk (`tests/test_all_buttons_full_walk.py`, currently 54 views / 323 clicks / 3 modals / 0 errors) or the create_automation keywords regression test (`tests/test_create_automation_keywords.py`, 19 cases).

## Out of scope (explicitly)

- Redesigning the agent planner (Planner class itself stays).
- Changes to `slash_commands.py` / `cogs/*` — this is pure agent-runtime + actions validation work.
- Migrating to provider-native tool calls (already supported via `core/ai_response.py`).
- Anything that requires a real Discord connection (everything below is unit-testable with the existing fake infrastructure).

## Affected files (planned)

- `agent/tools.py` — extend `validate_params` for `create_automation` to require `actions` for any `event_trigger` event with intended side effects.
- `agent/tool_registry.py` — schema: mark `actions` `required: true` for `create_automation`; expand the tool description with the canonical "react to every message" pattern.
- `agent/context.py` — add `build_actor_context(bot, guild, user, channel, message=None)` returning a stable actor block (ids + names + mentions + the latest message id if known).
- `agent/runtime.py` — inject actor context on every run, mirror the message id into the planner message list, improve the text-only nudge so it tells the model exactly what's missing, and pass a richer observation marker through `Observer.record` for `create_automation`.
- `actions.py` — in the `create_automation` handler for `event_trigger`, after the existing keyword guard, add a second guard: if `event in {message_contains, reaction_added, member_joined, member_left, voice_joined}` and `action_steps` is empty, return `{"error": "event_trigger for <event> requires a non-empty 'actions' list (e.g. add_reaction / send_message / assign_role). The trigger alone does not produce a Discord side effect."}`. Defense in depth.
- `agent/observer.py` — when `obs.tool == "create_automation"`, append a `— actions=[…]` suffix when present, or a warning suffix when empty. Keep the existing rendering for everything else.
- `tests/test_create_automation_keywords.py` — extend with the new cases.
- New: `tests/test_actor_context.py` — verify the actor block is injected and contains the expected ids/names.
- New: `tests/test_create_automation_actions_required.py` — the new "empty actions = rejected" guarantee.
- Re-run `tests/test_all_buttons_full_walk.py` — must stay 0 errors.

## Design decisions (one per question, recommended answer first)

### D1. What counts as "side-effecting event" requiring non-empty `actions`?

- Recommended: all five currently-valid events (`message_contains`, `reaction_added`, `member_joined`, `member_left`, `voice_joined`). They are all triggers that *only* make sense if the automation does something in response. There is no use case for a no-op trigger.
- Rejected alternative: only `message_contains`. Too narrow — `member_joined` without an action is the same bug class.

### D2. Where does the actor context live?

- Recommended: a new `agent/context.py::build_actor_context` function injected by `AgentRuntime.run` (and `AgentHarness.run_message`) on every run, just after the existing `build_server_context` and `build_automation_context` injections. Cached per-run in a `self._actor_context` field so it is consistent across the planner loop.
- Rejected alternative: bake it into `build_server_context`. The two have different lifetimes — server context is guild-shaped, actor context is per-interaction.

### D3. What does the actor context contain?

```
ACTOR CONTEXT (live):
  bot:        id=<snowflake> name=Miro
  user:       id=<snowflake> name=<display> @<username>
  guild:      id=<snowflake> name=<server>
  channel:    id=<snowflake> name=#<channel>
  message:    id=<snowflake> (if interaction is a message / on_message)
Use the EXACT ids above when calling send_message / add_reaction / assign_role
for this turn. Do not re-discover them with query_* unless this block is absent.
```

`message.id` is `None` for slash interactions; the agent will then know to query.

### D4. How does the runtime know which message to react to for on_message calls?

- Recommended: `AgentHarness.run_message` passes the `discord.Message` into the runtime. `AgentRuntime.__init__` accepts an optional `message` argument and stashes it on `self.message`. The actor-context builder reads it.
- Rejected alternative: re-query last message in channel every turn. Adds latency and is wrong under concurrent messages.

### D5. Should the `Observer` "actions=[]" warning be a fail or just a marker?

- Recommended: a `⚠️` marker (not `✅`, not `❌`). The runtime should also refuse to return `state=COMPLETED` while any `create_automation` observation has empty actions — fall through to `state=RECOVERING` so the planner re-issues the missing step. This preserves the "HARD GUARANTEE: never ship an empty agent answer" rule while still being honest.
- Rejected alternative: full fail. Too aggressive — would break legitimate "create + log" automations that have a single `send_message` step where the planner just didn't echo the action back in the observation.

### D6. How is the new validation tested without a network or real Discord?

- Recommended: extend the existing `_stub_discord.py`-style fakes already in `tests/test_create_automation_keywords.py`. Add `tests/test_actor_context.py` that builds a bot with a fake guild/user/message and asserts the actor-context string contains the expected ids. Add `tests/test_create_automation_actions_required.py` that drives `validate_params` and the `actions.py` handler with fake `interaction` objects (mirroring the pattern in `tests/test_all_buttons_full_walk.py`).

## Implementation steps (ordered, each independently shippable)

1. **Schema + validator for empty-actions on event_trigger.** In `agent/tools.py` add a `create_automation` branch: when `type=event_trigger` and `event` is in the side-effecting set, require `actions` to be a non-empty list. In `agent/tool_registry.py` mark `actions` `required: true` for the same condition and update the tool description to spell out the canonical react-to-everything pattern: `create_automation({type:"event_trigger", event:"message_contains", keywords:["*"|"<kw>"], actions:[{name:"add_reaction", parameters:{emoji:"😭"}}]})`.

2. **Defense in depth in `actions.py`.** After the existing `event_trigger` keyword guard (line 3294-3295), add an actions guard with the same message format used by `validate_params`. Both layers must reject the same call so any code path (slash command, scheduled task, agent) gets a clean error.

3. **Actor context builder.** New `build_actor_context` in `agent/context.py` returning the string shown in D3. Handles `message is None` gracefully (omits the message line).

4. **Runtime integration.** In `agent/runtime.py`:
   - Accept optional `message=` in `__init__`, store on `self.message`.
   - In `run`, after the existing `build_server_context` and `build_automation_context` injections, call `build_actor_context` and append the resulting string to the planner `messages` list.
   - Update the text-only nudge (line 299-306) to be specific: when the most recent failed action was `create_automation` and it was missing `actions`, tell the model exactly what to add. When the most recent failed action was `add_reaction` and `message_id` was missing, point to the actor context.

5. **Harness integration.** In `agent/harness.py`:
   - `run_message` already has the `message` object — pass it to the runtime constructor.
   - `run` for slash interactions: leave `message=None` (the actor context will omit the message line).

6. **Observer honesty for `create_automation`.** In `agent/observer.py::record`, special-case `obs.tool == "create_automation"`: append `actions=[…]` from `obs.params` if present, else append `— no actions: trigger will fire but produce no side effects`. Keep existing rendering for every other tool.

7. **Tests.**
   - `tests/test_create_automation_actions_required.py` — at minimum 8 cases (per-event rejection; valid `actions=[{name:"add_reaction", parameters:{emoji:"😭"}}]` accepted; comma-string `actions` not accepted; empty list rejected; non-list rejected; `actions` under `trigger` accepted; `type=auto_responder` still doesn't require `actions`; `type=scheduled_task` still doesn't require `actions`).
   - `tests/test_actor_context.py` — at minimum 5 cases (slash interaction has no message line; message-context call has the message id; missing user/channel/guild attrs handled; mentions render as `<@id>` / `<#id>`; the context is identical across two calls for the same actor — i.e. stable for the duration of a run).
   - Extend `tests/test_create_automation_keywords.py` to assert the new error string on the no-actions case so the message surface is locked.
   - Re-run `tests/test_all_buttons_full_walk.py` — must report `NO ERRORS`.

8. **No source edits to the runtime are made on a feature branch without a regression test passing locally.** This is enforced by the new tests being run in the same change.

## Validation plan (how the implementer proves the bug is gone)

A. **Unit-level (deterministic, no LLM).**
   - All new test cases in `test_create_automation_actions_required.py` pass.
   - All 19 existing cases in `test_create_automation_keywords.py` still pass.
   - `test_actor_context.py` cases pass.

B. **Integration (LLM stubbed, fake Discord).**
   - `test_all_buttons_full_walk.py` reports `NO ERRORS` (54 views, 323+ clicks, 0 errors).
   - New scenario added to the walk: a `create_automation` call with `event_trigger + message_contains` but **no `actions`** is rejected at the validator layer with a `Receipt(error_type=SEMANTIC_MISMATCH, message="create_automation: event_trigger for message_contains requires non-empty 'actions' (e.g. add_reaction / send_message / assign_role).")`. The progress board must not show `✅ create_automation`; it must show `⚠️ create_automation — rejected: requires actions`.
   - New scenario: a `create_automation` with `actions=[{name:"add_reaction", parameters:{emoji:"😭", message_id:"<from actor context>"}}]` is accepted and the action summary is shown on the board.

C. **Live smoke (manual, documented).** In a real Discord test server, type to `/bot`: *"create an automation that reacts with 😭 to every message I send here"*. Expected:
   - Final user message references the actor context ids (or the message id explicitly).
   - An automation is registered.
   - The next message sent by the user in that channel gets a 😭 reaction.
   - A follow-up turn: *"now also assign the Verified role to anyone who says thanks"* is resolved against the same actor context and runs `create_automation` for `message_contains: ["thanks"]` with `actions=[{name:"assign_role", parameters:{role_name:"Verified"}}]`.

## Risks

- **R1 — New `actions` requirement breaks users who deliberately make a "trigger only" automation** (e.g. for analytics). Mitigation: if a use case surfaces, the workaround is `actions=[{name:"send_message", parameters:{content:" ", channel_id:"<self>"}}]` — a no-op action. We accept the breakage for now and document it; the surface is small.
- **R2 — Actor context bloat.** Worst case ~150 chars; acceptable in a planner budget that already accepts server + automation context of ~2k chars. If it grows, trim members list, keep ids+names.
- **R3 — `Observer` change to `create_automation` line breaks an existing snapshot test.** Mitigation: extend any snapshot to expect the new suffix; no other observer line changes.
- **R4 — `build_actor_context` called with `message=None` and a slash interaction where the user clicks a button → no message line, so a follow-up "react to the message I just sent" cannot be resolved.** This is acceptable: the model must ask for the message id or use a query tool. The agent already has `query_messages` (via server query) for this.

## Rollback plan

All changes are additive and gated behind validation. To roll back: revert the commits touching `agent/tools.py`, `agent/tool_registry.py`, `agent/context.py`, `agent/runtime.py`, `agent/harness.py`, `agent/observer.py`, `actions.py`. The `actions.py` event_trigger guard can stay even if the agent-side changes are reverted — it's a strictly tighter contract.

## Open questions for the user

- **Q1 — Reaction emoji fallback.** When the user says "react with the crying emoji" without spelling it out, do you want the planner to default to 😭 (current de-facto behaviour from the screenshot) or stop and ask? Recommended: keep the current "best-guess" behaviour (the model already does this and your screenshot shows it guessed 😭 correctly), but make the chosen emoji visible in the actor-context-aware "✅ create_automation (reacts with 😭)" progress line.
- **Q2 — Multi-message follow-ups.** Your "perform other actions etc" wording suggests you want the agent to be able to react to message N, then assign a role, then DM someone, all in the same conversation. Do you want a single `/bot` turn to support up to N follow-up tool calls in one response, or one tool call per turn with persistent actor context? Recommended: persistent actor context (the runtime already supports multi-step plans up to `MAX_AGENT_STEPS`), and the actor context makes the difference. No change needed to the runtime loop.

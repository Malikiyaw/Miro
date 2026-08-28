# Miro V8 — Real Execution-First Agent Harness

## Runtime contract

Miro treats the runtime as the agent and the LLM as the planning component.

```text
USER REQUEST
  -> deterministic classification
  -> UNDERSTANDING / EXECUTION_REQUIRED
  -> PLAN
  -> TOOL SELECTION
  -> PERMISSION ENGINE
  -> ActionHandler.dispatch()
  -> REAL DISCORD RESULT
  -> LIVE VERIFICATION
  -> OBSERVATION
  -> REPLAN / CONTINUE
  -> COMPLETION GATE
  -> FINAL RESPONSE
```

## Execution-required invariant

Mutation requests are classified before a user-facing model response is accepted.
A mutation cannot complete with prose alone. A planner turn such as `I'll delete
those channels` with zero tool calls is rejected and replanned.

## Single mutation gateway

All agent mutations flow through:

`AgentHarness -> AgentRuntime -> Executor -> ActionHandler.dispatch()`

The legacy AI-chat `_execute_actions` path is disabled by the V8 bridge.

## Verification

Mutation success is not verification. The verifier uses live Discord fetches for
supported mutations such as channel deletion/creation, role deletion/creation,
role assignment/removal, kicks, and bans. Unknown mutations fail closed.

## Completion math

For a numeric request, the completion gate compares requested target units against
verified target units. Batch channel receipts count one verified unit per target.

Example: requested `3`, verified `3` => complete. Requested `3`, verified `2` =>
not complete.

## Receipts

Every execution creates an `ExecutionReceipt` containing execution/job IDs, tool,
parameters, timing, success, verification, target information, and error type.

## Recovery and loops

The executor retries transient failures. Permission failures remain failures.
Repeated identical tool calls with no progress trigger `LOOP_DETECTED` and force a
new plan rather than infinite retries.

## Critical regression coverage

`tests/test_agent_v8_harness.py` covers:

- mutation classification
- execution-required enforcement
- text-only mutation rejection
- three-target completion
- partial failure
- recovery using the latest receipt

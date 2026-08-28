"""V8 execution-first regression tests.

These tests stay provider/Discord independent so CI can prove the core contract
without needing a live bot connection.
"""
from agent.completion_gate import CompletionGate
from agent.request_classifier import RequestClass, classify_request
from agent.state import AgentExecutionResult, ErrorType, Receipt


def receipt(action, *, success=True, verified=True, parameters=None):
    return Receipt(
        action=action,
        success=success,
        verified=verified,
        error_type=ErrorType.NONE if success else ErrorType.UNKNOWN,
        message="ok" if success else "failed",
        parameters=parameters or {},
    )


def test_delete_three_is_execution_required_and_multi_step():
    result = classify_request("Delete the 3 duplicate warned channels")
    assert result.kind == RequestClass.MULTI_STEP_MUTATION
    assert result.execution_required is True
    assert result.requested_count == 3


def test_create_channel_is_mutation():
    result = classify_request("Create a staff channel")
    assert result.kind == RequestClass.MUTATION
    assert result.execution_required is True


def test_find_duplicates_is_read_only():
    result = classify_request("Find the duplicate channels")
    assert result.kind == RequestClass.READ_ONLY
    assert result.execution_required is False


def test_text_only_mutation_has_no_completion_receipt():
    result = AgentExecutionResult(execution_required=True, request_class="MUTATION")
    verdict = CompletionGate().evaluate(result, "I'll create the channel.", actionable=True)
    assert verdict.allowed is False
    assert verdict.state_verified is False


def test_three_verified_mutations_complete():
    result = AgentExecutionResult(
        execution_required=True,
        request_class="MULTI_STEP_MUTATION",
        requested_count=3,
        receipts=[
            receipt("delete_channel", parameters={"channel_id": "1"}),
            receipt("delete_channel", parameters={"channel_id": "2"}),
            receipt("delete_channel", parameters={"channel_id": "3"}),
        ],
    )
    verdict = CompletionGate().evaluate(result, "Deleted and verified all 3 channels.", actionable=True)
    assert verdict.allowed is True
    assert verdict.state_verified is True


def test_two_of_three_verified_is_partial_failure():
    result = AgentExecutionResult(
        execution_required=True,
        request_class="MULTI_STEP_MUTATION",
        requested_count=3,
        receipts=[
            receipt("delete_channel", parameters={"channel_id": "1"}),
            receipt("delete_channel", parameters={"channel_id": "2"}),
            receipt("delete_channel", success=False, verified=False, parameters={"channel_id": "3"}),
        ],
    )
    verdict = CompletionGate().evaluate(result, "2 of 3 channels were verified.", actionable=True)
    assert verdict.allowed is False
    assert verdict.state_verified is False


def test_recovered_attempt_uses_latest_receipt():
    params = {"channel_id": "1"}
    result = AgentExecutionResult(
        execution_required=True,
        request_class="MUTATION",
        receipts=[
            receipt("delete_channel", success=False, verified=False, parameters=params),
            receipt("delete_channel", success=True, verified=True, parameters=params),
        ],
    )
    verdict = CompletionGate().evaluate(result, "Channel deleted and verified.", actionable=True)
    assert verdict.allowed is True


def test_create_automation_is_a_real_mutation_not_discovery():
    """Regression: automation-creation tools must count as executions so the
    completion gate does not misreport them as a 'Discovery completed — no
    mutation executed yet' pause (screenshot bug)."""
    from agent.runtime import MUTATING_TOOLS, DANGEROUS_TOOLS

    for tool in ("create_automation", "bulk_create_automations",
                 "create_prefix_command", "delete_automation"):
        assert tool in MUTATING_TOOLS, f"{tool} must be a mutation tool"

    result = AgentExecutionResult(
        execution_required=True,
        request_class="MUTATION",
        receipts=[receipt("create_automation", parameters={"name": "daily-tip"})],
    )
    failed = [r for r in result.receipts if not r.success]
    only_queries = bool(result.receipts) and not failed and all(
        str(r.action) not in DANGEROUS_TOOLS and str(r.action) not in MUTATING_TOOLS
        for r in result.receipts)
    assert only_queries is False


def test_normalize_actions_reads_canonical_arguments():
    """Regression: tool calls arrive as {id, name, arguments} (a JSON string of
    params). If arguments are ignored the agent runs every tool with empty
    params — automations get created with the wrong name/trigger and never fire.
    """
    from agent.runtime import AgentRuntime
    raw = [{
        "id": "call_1",
        "name": "create_automation",
        "arguments": '{"type": "auto_responder", "name": "greet_reyrey", "keywords": ["hi"], "response": "That goat reyrey"}',
    }]
    actions = AgentRuntime._normalize_actions(raw)
    assert len(actions) == 1
    assert actions[0]["name"] == "create_automation"
    params = actions[0]["parameters"]
    assert params.get("name") == "greet_reyrey"
    assert params.get("keywords") == ["hi"]
    assert params.get("response") == "That goat reyrey"
    assert params.get("type") == "auto_responder"

    # Legacy {function:{name, arguments}} shape must still work.
    legacy = [{
        "id": "call_2",
        "function": {"name": "create_channel", "arguments": '{"name": "general"}'},
    }]
    legacy_actions = AgentRuntime._normalize_actions(legacy)
    assert legacy_actions[0]["name"] == "create_channel"
    assert legacy_actions[0]["parameters"].get("name") == "general"

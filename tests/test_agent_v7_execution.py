"""V7 regression tests for the agent completion contract."""
from agent.completion_gate import CompletionGate
from agent.state import AgentExecutionResult, ErrorType, Receipt


def receipt(action, success=True, verified=True):
    return Receipt(action=action, success=success, verified=verified,
                   error_type=ErrorType.NONE if success else ErrorType.UNKNOWN,
                   message="ok" if success else "failed")


def test_actionable_request_without_tool_receipt_cannot_complete():
    verdict = CompletionGate().evaluate(
        AgentExecutionResult(), "I'll delete the channels.", actionable=True
    )
    assert verdict.allowed is False
    assert verdict.state_verified is False


def test_partial_multi_step_execution_cannot_complete():
    result = AgentExecutionResult(receipts=[
        receipt("delete_channel", True, True),
        receipt("delete_channel", True, False),
        receipt("delete_channel", False, False),
    ])
    verdict = CompletionGate().evaluate(result, "Deleted 3 channels.", actionable=True)
    assert verdict.allowed is False
    assert verdict.state_verified is False


def test_all_mutations_verified_can_complete():
    result = AgentExecutionResult(receipts=[
        receipt("delete_channel", True, True),
        receipt("delete_channel", True, True),
        receipt("delete_channel", True, True),
    ])
    verdict = CompletionGate().evaluate(result, "Deleted 3 channels.", actionable=True)
    assert verdict.allowed is True
    assert verdict.state_verified is True

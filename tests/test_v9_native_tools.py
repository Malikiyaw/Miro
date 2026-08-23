import pytest
from core.ai_response import normalize_provider_response, ResponseKind, AIErrorType


def test_mistral_tool_call_with_empty_content_is_not_blank():
    response=normalize_provider_response({
        "choices":[{"message":{"role":"assistant","content":"","tool_calls":[{"id":"call_1","type":"function","function":{"name":"delete_channel","arguments":"{\"channel_id\":123}"}}]},"finish_reason":"tool_calls"}]
    },provider="mistral",model="mistral-small-latest")
    assert response.kind == ResponseKind.TOOL_CALL_RESPONSE
    assert response.status == AIErrorType.OK
    assert response.ok is True
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0]["name"] == "delete_channel"
    assert response.tool_calls[0]["arguments"] == {"channel_id":123}


def test_true_blank_response_is_retryable_classification():
    response=normalize_provider_response({"choices":[{"message":{"content":""},"finish_reason":"stop"}]},provider="mistral")
    assert response.kind == ResponseKind.EMPTY_RESPONSE
    assert response.status == AIErrorType.EMPTY_RESPONSE
    assert response.ok is False


def test_multiple_native_tool_calls_are_preserved():
    response=normalize_provider_response({"choices":[{"message":{"content":None,"tool_calls":[
        {"id":"a","function":{"name":"delete_channel","arguments":{"channel_id":1}}},
        {"id":"b","function":{"name":"delete_channel","arguments":{"channel_id":2}}},
        {"id":"c","function":{"name":"delete_channel","arguments":{"channel_id":3}}}
    ]},"finish_reason":"tool_calls"}]},provider="mistral")
    assert response.kind == ResponseKind.TOOL_CALL_RESPONSE
    assert len(response.tool_calls) == 3
    assert [x["arguments"]["channel_id"] for x in response.tool_calls] == [1,2,3]

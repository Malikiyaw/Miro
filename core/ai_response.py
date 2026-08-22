"""
Canonical AI response pipeline.

Every provider response — whatever its shape — is normalized into one
AIResponse object before Miro touches it. Failures are classified into a
strict error taxonomy so users and admins see EXACTLY what happened instead
of generic "blank answer" messages.
"""
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AIErrorType(str, Enum):
    OK = "OK"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    TOOL_CALL_FAILURE = "TOOL_CALL_FAILURE"
    ACTION_FAILURE = "ACTION_FAILURE"


RETRYABLE = {AIErrorType.RATE_LIMIT, AIErrorType.TIMEOUT,
             AIErrorType.NETWORK_ERROR, AIErrorType.PROVIDER_ERROR}


class ResponseKind(str, Enum):
    """What KIND of turn this is — drives the agent state machine.

    A TOOL_CALL_RESPONSE is a legitimate intermediate turn: it must never
    reach the blank-answer handler or the final-answer watchdog.
    """
    TEXT_RESPONSE = "TEXT_RESPONSE"          # plain answer, ready for Discord
    TOOL_CALL_RESPONSE = "TOOL_CALL_RESPONSE"  # model wants tools executed
    FINAL_RESPONSE = "FINAL_RESPONSE"        # agent's closing answer
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    ERROR = "ERROR"


def new_request_id() -> str:
    return f"ai_{uuid.uuid4().hex[:8]}"


@dataclass
class AIResponse:
    """One canonical shape for every provider's answer."""
    text: str = ""
    provider: str = ""
    model: str = ""
    request_id: str = field(default_factory=new_request_id)
    status: AIErrorType = AIErrorType.OK
    kind: ResponseKind = ResponseKind.TEXT_RESPONSE
    finish_reason: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    has_tool_calls: bool = False
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    raw_shape: str = ""          # which branch of the normalizer matched
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        """A tool-call turn IS okay without text; text turns need content."""
        if self.status != AIErrorType.OK:
            return False
        if self.kind == ResponseKind.TOOL_CALL_RESPONSE:
            return True
        return bool(self.text.strip())

    def describe(self) -> str:
        """Human-readable one-liner for logs and /config test."""
        base = f"{self.provider}/{self.model or '?'} [{self.status.value}/{self.kind.value}]"
        if self.usage:
            base += f" tokens={self.usage.get('total_tokens', '?')}"
        if self.latency_ms:
            base += f" {self.latency_ms:.0f}ms"
        return base


def _text_from_parts(parts: Any) -> str:
    """Anthropic-style content blocks / OpenAI content-part lists -> text."""
    if isinstance(parts, str):
        return parts
    if not isinstance(parts, list):
        return ""
    chunks = []
    for part in parts:
        if isinstance(part, str):
            chunks.append(part)
        elif isinstance(part, dict):
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
            elif isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks)


def _usage_from(data: Dict[str, Any]) -> Dict[str, int]:
    usage = data.get("usage") or {}
    out = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                "input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            out[key] = int(value)
    return out


def normalize_provider_response(res_data: Any, provider: str = "", model: str = "",
                                request_id: str = None, latency_ms: float = 0.0) -> AIResponse:
    """
    Normalize every known provider shape into AIResponse.
    Primary path per OpenRouter/OpenAI docs: choices[0].message.content.
    """
    rid = request_id or new_request_id()
    resp = AIResponse(provider=provider, model=model, request_id=rid, latency_ms=latency_ms)

    if not isinstance(res_data, dict):
        resp.status = AIErrorType.INVALID_RESPONSE
        resp.kind = ResponseKind.ERROR
        resp.raw_shape = "non-dict"
        return resp

    resp.usage = _usage_from(res_data)

    # --- OpenAI-compatible chat completions (OpenRouter, OpenAI, Groq, ...) ---
    choices = res_data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        finish = choice.get("finish_reason") or res_data.get("stop_reason") or ""
        tool_calls = message.get("tool_calls")
        resp.finish_reason = str(finish or "")
        resp.has_tool_calls = bool(tool_calls)
        if isinstance(message.get("content"), (str, list)):
            resp.text = _text_from_parts(message["content"])
            resp.raw_shape = "choices[0].message.content"
            resp.status = AIErrorType.OK if resp.text.strip() else AIErrorType.EMPTY_RESPONSE
            resp.kind = (ResponseKind.TEXT_RESPONSE if resp.text.strip()
                         else ResponseKind.EMPTY_RESPONSE)
            return resp
        if isinstance(choice.get("text"), str):          # legacy completions
            resp.text = choice["text"]
            resp.raw_shape = "choices[0].text"
            resp.status = AIErrorType.OK if resp.text.strip() else AIErrorType.EMPTY_RESPONSE
            resp.kind = (ResponseKind.TEXT_RESPONSE if resp.text.strip()
                         else ResponseKind.EMPTY_RESPONSE)
            return resp
        if resp.has_tool_calls:                           # tool-call turn: valid, no text yet
            resp.raw_shape = "choices[0].message.tool_calls"
            resp.status = AIErrorType.OK
            resp.kind = ResponseKind.TOOL_CALL_RESPONSE
            try:
                for tc in tool_calls:
                    fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                    resp.tool_calls.append({
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", "{}"),
                        "id": tc.get("id", "") if isinstance(tc, dict) else "",
                    })
            except Exception:
                pass
            return resp
        resp.status = AIErrorType.EMPTY_RESPONSE
        resp.kind = ResponseKind.EMPTY_RESPONSE
        resp.raw_shape = "choices-without-content"
        return resp

    # --- Anthropic messages ---
    if isinstance(res_data.get("content"), list):
        resp.text = _text_from_parts(res_data["content"])
        resp.finish_reason = str(res_data.get("stop_reason") or "")
        resp.raw_shape = "anthropic.content[]"
        resp.status = AIErrorType.OK if resp.text.strip() else AIErrorType.EMPTY_RESPONSE
        resp.kind = (ResponseKind.TEXT_RESPONSE if resp.text.strip()
                     else ResponseKind.EMPTY_RESPONSE)
        return resp

    # --- Responses API (output_text / output[]) ---
    if isinstance(res_data.get("output_text"), str):
        resp.text = res_data["output_text"]
        resp.raw_shape = "output_text"
        resp.status = AIErrorType.OK if resp.text.strip() else AIErrorType.EMPTY_RESPONSE
        resp.kind = (ResponseKind.TEXT_RESPONSE if resp.text.strip()
                     else ResponseKind.EMPTY_RESPONSE)
        return resp
    output = res_data.get("output")
    if isinstance(output, list):
        texts = []
        for item in output:
            if isinstance(item, dict):
                texts.append(_text_from_parts(item.get("content")))
        resp.text = "".join(texts)
        resp.raw_shape = "responses.output[]"
        resp.status = AIErrorType.OK if resp.text.strip() else AIErrorType.EMPTY_RESPONSE
        resp.kind = (ResponseKind.TEXT_RESPONSE if resp.text.strip()
                     else ResponseKind.EMPTY_RESPONSE)
        return resp

    # --- Gemini native candidates ---
    candidates = res_data.get("candidates")
    if isinstance(candidates, list) and candidates:
        cand = candidates[0] if isinstance(candidates[0], dict) else {}
        content = cand.get("content") or {}
        parts = content.get("parts") if isinstance(content, dict) else None
        if isinstance(parts, list):
            resp.text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            resp.finish_reason = str(cand.get("finishReason") or "")
            resp.raw_shape = "gemini.candidates[0]"
            resp.status = AIErrorType.OK if resp.text.strip() else AIErrorType.EMPTY_RESPONSE
            resp.kind = (ResponseKind.TEXT_RESPONSE if resp.text.strip()
                         else ResponseKind.EMPTY_RESPONSE)
            return resp

    # --- Plain string bodies (some proxies) ---
    if isinstance(res_data.get("response"), str):
        resp.text = res_data["response"]
        resp.raw_shape = "response:str"
        resp.status = AIErrorType.OK if resp.text.strip() else AIErrorType.EMPTY_RESPONSE
        resp.kind = (ResponseKind.TEXT_RESPONSE if resp.text.strip()
                     else ResponseKind.EMPTY_RESPONSE)
        return resp

    resp.status = AIErrorType.INVALID_RESPONSE
    resp.kind = ResponseKind.ERROR
    resp.raw_shape = f"unrecognized:{list(res_data.keys())[:6]}"
    return resp


def classify_http_error(status: int, body: str = "") -> AIErrorType:
    """Map an HTTP failure to the taxonomy."""
    body_l = (body or "").lower()
    if status in (401, 403):
        return AIErrorType.AUTH_ERROR
    if status == 429:
        return AIErrorType.RATE_LIMIT
    if status == 404:
        if "model" in body_l:
            return AIErrorType.MODEL_NOT_FOUND
        return AIErrorType.PROVIDER_ERROR
    if status == 408:
        return AIErrorType.TIMEOUT
    if 500 <= status <= 599:
        return AIErrorType.PROVIDER_ERROR
    if status == 400 and ("model" in body_l):
        return AIErrorType.MODEL_NOT_FOUND
    return AIErrorType.PROVIDER_ERROR


# Watchdog: never let blank/whitespace/error-marker text reach Discord
ERROR_MARKERS = (
    "i'm sorry", "i cannot fulfill", "as an ai language model",
    "[error]", "internal server error", "<html",
)


def watchdog_check(text: str) -> tuple[bool, str]:
    """Returns (ok, reason). Guards the final answer before it reaches users."""
    if text is None:
        return False, "None content"
    stripped = text.strip()
    if not stripped:
        return False, "blank content"
    low = stripped.lower()
    for marker in ERROR_MARKERS:
        if marker in low and len(stripped) < 200:
            return False, f"error marker: {marker}"
    return True, ""

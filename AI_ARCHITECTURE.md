# Miro AI Platform — Implementation Status

Per the "Miro AI consolidation" plan. Implemented now; later stages documented below.

## Shipped

| Plan item | Component |
|---|---|
| 1. Response Normalizer | `core/ai_response.py::normalize_provider_response` — choices[0].message.content (str/list parts), choices[0].text, Anthropic content[], Responses API output_text/output[], Gemini candidates, plain bodies → one `AIResponse(text, provider, model, request_id, usage, finish_reason, status)` |
| 2. Error taxonomy | `AIErrorType`: EMPTY_RESPONSE / INVALID_RESPONSE / AUTH_ERROR / RATE_LIMIT / MODEL_NOT_FOUND / PROVIDER_ERROR / TIMEOUT / NETWORK_ERROR / TOOL_CALL_FAILURE / ACTION_FAILURE + `classify_http_error` + RETRYABLE set |
| 17. Watchdog | `watchdog_check` — blocks blank/whitespace/error-marker content before it reaches Discord |
| 18. Request IDs + diagnostics | every chat request gets `ai_<8hex>`; logs carry provider/model/status/latency/tokens per attempt; `/config test` shows the full chain |
| 3+4+7. GuildAIConfig | `core/guild_ai_config.py` — per-guild provider/model/fallback_models/max_tokens/temperature/timeout/retry_limit/agent_enabled/tool_mode/safety_policy; keys stay in the encrypted store; guild isolation verified by tests |
| 5. Validate-before-save | `/config key` tests format → live key test → only then encrypts/stores/activates; on failure: "Nothing was changed" with exact reason |
| 6. Real diagnostics | `/config test` = guild config → API key → model availability (live catalog) → real completion through the normalizer → tool/agent capability → fallback chain visibility, all with latency |
| 22. AI panel | `/system ai` panel: model/fallbacks/max_tokens/temperature/timeout settings modal, agent-mode toggle, live metrics — same lifecycle contract as all panels |
| 16. Context-on-demand | `_needs_server_context()` heuristic skips channel/role/member introspection for small talk/math/coding questions |

## Wired into the pipeline
`_chat_internal` → per-guild limits from GuildAIConfig → request_id → HTTP error classification →
`_parse_and_handle_response` → **normalize** → watchdog → existing recovery ladder
(drop json mode → regenerate → next provider/key) → honest user-facing errors.

## Deferred stages (next sessions)
- 9/20: full ProviderAdapter class hierarchy (normalizer already decouples shapes; adapters add native tool-call normalization)
- 10–15: agent runtime loop (planner/tool-selection/observation), planning modes, capability matrix, memory taxonomy split
- 8: model-level fallback chain (provider-level exists today)
- 24: full automated suite incl. live provider fixtures

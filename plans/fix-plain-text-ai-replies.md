# Fix: AI conversational replies discarded as "malformed JSON"

## Symptom (Render logs)
- AI actually replied fine: `Hi! How can I assist you today with the **Cosmic lover** server?`
- User in Discord received: `I tried to generate a response, but it seems the output was cut off or malformed...`

## Root cause (confirmed in code)
`ai_client.py::extract_json()` is applied to **every** AI response
(`_parse_and_handle_response`, ai_client.py:826). A plain conversational
reply contains no `{`, so:
1. Direct parse fails, markdown regex fails, brace regex fails, `find('{')` = -1 skips repair
2. Falls through to the error branch → returns
   `{"summary": "I tried to generate a response, but ..."}`
3. That error dict **passes** `_validate_json_response` (it has a string
   `summary`), so the retry at :829 never triggers
4. The error text is what gets sent to the channel

## Fix (one surgical change in `extract_json`, ai_client.py ~line 813)

Add an early return right after the empty-text check:

```python
# Pure conversational reply — the model chose to answer in plain text,
# which is perfectly valid. Delivering it beats replacing it with an
# error message ("Hi! How can I assist you..." must reach the user).
stripped = text.strip()
if "{" not in stripped and "[" not in stripped:
    return {"summary": stripped}
```

Behavior after fix:
- No braces at all → delivered verbatim as `{"summary": <text>}` (conversational answer)
- Contains `{` but truncated → existing structure-aware repair still runs
- Contains `{` but unrepairable garbage → existing "malformed" fallback (correct: JSON was attempted)
- Markdown-fenced plain replies without braces → delivered as text
- No retry wasted on valid conversational replies

## Consumers verified
- `/bot` (modules/slash_commands.py) reads `result.get("summary")` → gets the real reply
- ai_chat / quest generation: quests still get JSON (models emit braces for those prompts); if a model answers conversationally to a quest prompt, gamification's structure guard already falls back to a template quest

## Verification plan
1. Extract the real `extract_json` + helpers from ai_client.py (line-based, as before)
2. Assert: the exact Render log string returns as `summary` verbatim
3. Assert: truncated JSON cases still repaired; brace-garbage still errors gracefully
4. `python3 -m py_compile` on ai_client.py
5. Commit + push `HEAD:main` (no new branch)

## Rollback
Single-hunk change in one function; revert commit restores prior behavior.

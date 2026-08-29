"""Deterministic channel tools + parameter schemas (shared by agent + actions)."""
import re as _re
from typing import Any, Dict, List


def _created_at_str(created_at) -> str:
    """Accept datetime objects or pre-formatted strings (tests/stubs)."""
    if not created_at:
        return ""
    return created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)


def find_duplicate_channels_from_list(channels: List[Dict[str, Any]], name: str,
                                      protected_channel_id=None) -> Dict[str, Any]:
    def normalize(s: str) -> str:
        s = s.lower().strip(); s = _re.sub(r"[-_\s]+", "-", s); s = _re.sub(r"^[^a-z0-9]+", "", s)
        s = _re.sub(r"-\d+$", "", s); return _re.sub(r"\s+\d+$", "", s)
    target = normalize(name); protected_id = str(protected_channel_id or "")
    matches, duplicates, kept = [], [], None
    for ch in channels:
        cid, cname = str(ch.get("id", "")), str(ch.get("name", ""))
        if normalize(cname) == target:
            entry = {"id": cid, "name": cname, "category_id": str(ch.get("category_id") or ""), "created_at": str(ch.get("created_at") or "")}
            matches.append(entry)
            if cid == protected_id: kept = entry
            else: duplicates.append(entry)
    return {"target_name": name, "protected_channel_id": protected_id, "protected_channel": kept["id"] if kept else "", "matches": matches, "kept": kept, "duplicates": duplicates, "protected": kept}


def find_duplicate_channels(guild, name: str = "", protected_channel_id=None, exclude_channel_id=None) -> Dict[str, Any]:
    def normalize(s: str) -> str:
        s = s.lower().strip(); s = _re.sub(r"[-_\s]+", "-", s); s = _re.sub(r"^[^a-z0-9]+", "", s)
        s = _re.sub(r"-\d+$", "", s); return _re.sub(r"\s+\d+$", "", s)
    target = normalize(name); protected_id = str(protected_channel_id or exclude_channel_id or "")
    if not target:
        return find_all_duplicate_groups(guild, [protected_id] if protected_id else None)
    matches, duplicates, kept = [], [], None
    for channel in guild.text_channels:
        if normalize(channel.name) == target:
            entry = {"id": str(channel.id), "name": channel.name, "category_id": str(channel.category_id) if channel.category_id else "", "created_at": _created_at_str(channel.created_at)}
            matches.append(entry)
            if entry["id"] == protected_id: kept = entry
            else: duplicates.append(entry)
    return {"target_name": name, "protected_channel_id": protected_id, "protected_channel": kept["id"] if kept else "", "matches": matches, "kept": kept, "duplicates": duplicates, "protected": kept}


def find_all_duplicate_groups(guild, protected_channel_ids=None) -> Dict[str, Any]:
    protected = {str(x) for x in (protected_channel_ids or [])}
    def normalize(s: str) -> str:
        s = s.lower().strip(); s = _re.sub(r"[-_\s]+", "-", s); s = _re.sub(r"^[^a-z0-9]+", "", s)
        s = _re.sub(r"-\d+$", "", s); return _re.sub(r"\s+\d+$", "", s)
    groups: Dict[str, list] = {}
    for channel in guild.text_channels:
        base = normalize(channel.name)
        if not base: continue
        groups.setdefault(base, []).append({"id": str(channel.id), "name": channel.name, "category_id": str(channel.category_id) if channel.category_id else "", "created_at": _created_at_str(channel.created_at)})
    out = []
    for base, members in sorted(groups.items()):
        if len(members) < 2: continue
        ordered = sorted(members, key=lambda m: m["created_at"])
        original, dups = ordered[0], ordered[1:]
        protected_original = original["id"] if original["id"] in protected else ""
        out.append({"base_name": original["name"], "protected_channel_id": protected_original, "original": original, "duplicates": dups})
    return {"groups": out, "group_count": len(out)}


def _is_internal_context(params: Dict[str, Any]) -> bool:
    return bool(params.get("_agent_request"))



_REQUIRED_PARAMS = {
    "create_channel": [("name", "channel name")],
    "create_text_channel": [("name", "channel name")],
    "create_voice_channel": [("name", "channel name")],
    "create_category": [("name", "category name")],
    "create_role": [("name", "role name")],
    "delete_channel": [("channel_id", "channel ID")],
    "delete_role": [("role_id", "role ID")],
    "assign_role": [("user_id", "user ID"), ("role_id", "role ID")],
    "remove_role": [("user_id", "user ID"), ("role_id", "role ID")],
    "ban_user": [("user_id", "user ID")],
    "kick_user": [("user_id", "user ID")],
    "warn_user": [("user_id", "user ID")],
    "timeout_user": [("user_id", "user ID"), ("duration", "duration seconds")],
    "send_message": [("content", "message text")],
    "send_dm": [("user_id", "user ID"), ("content", "message text")],
}

def _coerce_keywords(params: Dict[str, Any]) -> list:
    """Pull keywords from any of the accepted locations and return a non-empty list or []."""
    raw = params.get("keywords")
    if not raw:
        trigger = params.get("trigger")
        if isinstance(trigger, dict):
            raw = trigger.get("keywords")
    if not raw:
        filters = params.get("filters")
        if isinstance(filters, dict):
            raw = filters.get("keywords")
    if isinstance(raw, str):
        raw = [k.strip() for k in raw.split(",") if k.strip()]
    if not isinstance(raw, list):
        return []
    return [str(k).strip() for k in raw if str(k).strip()]


def validate_params(name: str, params: Dict[str, Any]) -> tuple:
    """Schema validation. Dependency-repairable calls are allowed through to the executor."""
    if name == "find_duplicate_channels":
        return True, ""
    if name == "bulk_delete_channels":
        ids = params.get("channel_ids") or params.get("channels")
        if not isinstance(ids, list) or not ids:
            if _is_internal_context(params):
                return True, ""  # executor will resolve exact IDs from live Discord state
            return False, "requires a non-empty 'channel_ids' list — resolve IDs with find_duplicate_channels first; never delete by name"
    elif name == "delete_channel":
        if not (params.get("channel_id") or params.get("channel_name")):
            return False, "requires 'channel_id' (resolve the exact ID first) or 'channel_name'"
    elif name == "delete_role":
        if not (params.get("role_id") or params.get("role_name")):
            return False, "requires 'role_id' or 'role_name'"
    elif name == "create_automation":
        # Always require a name (server-side also auto-generates one, but a name
        # in the call avoids collisions and matches the runtime contract).
        if not (params.get("name") and str(params.get("name")).strip()):
            return False, "requires 'name' (string) — the automation's identifier"
        atype = str(params.get("type") or params.get("automation_type") or "scheduled_task").strip().lower()
        if atype in ("event_trigger", "event"):
            event = str(params.get("event") or "").strip().lower()
            if event == "message_contains" and not _coerce_keywords(params):
                return False, ("requires 'keywords' (array of strings or a comma-separated string) "
                               "when type=event_trigger and event=message_contains. "
                               "e.g. keywords=['hello','ping'] or keywords='hello, ping'.")
        elif atype in ("auto_responder", "responder", "autoresponder", "trigger_role"):
            if not _coerce_keywords(params):
                return False, f"requires 'keywords' for type={atype} (e.g. keywords=['hi','hello'])."
    return True, ""

"""Deterministic channel tools + parameter schemas (shared by agent + actions)."""
import re as _re
from typing import Any, Dict, List, Tuple


def find_duplicate_channels_from_list(channels: List[Dict[str, Any]], name: str,
                                      protected_channel_id=None) -> Dict[str, Any]:
    """Deterministic matching over an already-fetched channel list (dicts with
    id/name/category_id). Used when the caller pre-queried via ServerQueryEngine."""
    def normalize(s: str) -> str:
        s = s.lower().strip()
        s = _re.sub(r"[-_\s]+", "-", s)
        s = _re.sub(r"^[^a-z0-9]+", "", s)
        s = _re.sub(r"-\d+$", "", s)
        s = _re.sub(r"\s+\d+$", "", s)
        return s

    target = normalize(name)
    protected_id = str(protected_channel_id or "")
    matches, duplicates, kept = [], [], None
    for ch in channels:
        cid = str(ch.get("id", ""))
        cname = str(ch.get("name", ""))
        if normalize(cname) == target:
            entry = {"id": cid, "name": cname,
                     "category_id": str(ch.get("category_id") or ""),
                     "created_at": str(ch.get("created_at") or "")}
            matches.append(entry)
            if cid == protected_id:
                kept = entry
            else:
                duplicates.append(entry)
    return {"target_name": name, "protected_channel_id": protected_id,
            "protected_channel": kept["id"] if kept else "",
            "matches": matches, "kept": kept, "duplicates": duplicates,
            "protected": kept}


def find_duplicate_channels(guild, name: str, protected_channel_id=None,
                            exclude_channel_id=None) -> Dict[str, Any]:
    def normalize(s: str) -> str:
        s = s.lower().strip()
        s = _re.sub(r"[-_\s]+", "-", s)
        s = _re.sub(r"^[^a-z0-9]+", "", s)   # emoji/decoration prefixes
        s = _re.sub(r"-\d+$", "", s)          # clone counters
        s = _re.sub(r"\s+\d+$", "", s)
        return s

    target = normalize(name)
    protected_id = str(protected_channel_id or exclude_channel_id or "")
    matches, duplicates, kept = [], [], None
    for channel in guild.text_channels:
        if normalize(channel.name) == target:
            entry = {"id": str(channel.id), "name": channel.name,
                     "category_id": str(channel.category_id) if channel.category_id else "",
                     "created_at": channel.created_at.isoformat() if channel.created_at else ""}
            matches.append(entry)
            if entry["id"] == protected_id:
                kept = entry
            else:
                duplicates.append(entry)
    return {"target_name": name, "protected_channel_id": protected_id,
            "protected_channel": kept["id"] if kept else "",
            "matches": matches, "kept": kept, "duplicates": duplicates,
            "protected": kept}


def find_all_duplicate_groups(guild, protected_channel_ids=None) -> Dict[str, Any]:
    """
    Deterministic server-wide duplicate scan: group text channels by
    normalized base name and return every group with more than one member.
    Serves 'delete the duplicate channels' when the user names no target.
    """
    protected = {str(x) for x in (protected_channel_ids or [])}

    def normalize(s: str) -> str:
        s = s.lower().strip()
        s = _re.sub(r"[-_\s]+", "-", s)
        s = _re.sub(r"^[^a-z0-9]+", "", s)   # emoji/decoration prefixes
        s = _re.sub(r"-\d+$", "", s)          # clone counters
        s = _re.sub(r"\s+\d+$", "", s)
        return s

    groups: Dict[str, list] = {}
    for channel in guild.text_channels:
        base = normalize(channel.name)
        if not base:
            continue
        groups.setdefault(base, []).append({
            "id": str(channel.id), "name": channel.name,
            "category_id": str(channel.category_id) if channel.category_id else "",
            "created_at": channel.created_at.isoformat() if channel.created_at else "",
        })

    out = []
    for base, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        # oldest member is the presumed original; the rest are duplicates
        ordered = sorted(members, key=lambda m: m["created_at"])
        original, dups = ordered[0], ordered[1:]
        out.append({
            "base_name": original["name"],
            "protected_channel_id": original["id"] if original["id"] in protected else "",
            "original": original,
            "duplicates": dups,
        })
    return {"groups": out, "group_count": len(out)}


def validate_params(name: str, params: Dict[str, Any]) -> tuple:
    """Schema-level validation BEFORE dispatch."""
    if name == "find_duplicate_channels":
        # name is optional: without it the tool scans the WHOLE server for
        # duplicate groups, which is exactly what 'delete duplicate channels'
        # needs when the user names no specific target.
        return True, ""
    elif name == "bulk_delete_channels":
        ids = params.get("channel_ids") or params.get("channels")
        if not isinstance(ids, list) or not ids:
            return False, ("requires a non-empty 'channel_ids' list — resolve IDs "
                           "with find_duplicate_channels first; never delete by name")
    elif name == "delete_channel":
        if not (params.get("channel_id") or params.get("channel_name")):
            return False, "requires 'channel_id' (resolve the exact ID first) or 'channel_name'"
    elif name == "delete_role":
        if not (params.get("role_id") or params.get("role_name")):
            return False, "requires 'role_id' or 'role_name'"
    return True, ""

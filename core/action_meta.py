"""
Strict action/object model + semantic validation + protected targets.

The agent may never confuse object types: 'bulk_delete_messages' can never
satisfy 'delete duplicate CHANNELS'. Validation happens BEFORE dispatch, and
protected targets are enforced in the backend — not merely suggested to the AI.
"""
from typing import Any, Dict, List, Optional, Tuple


# object_type -> canonical delete/create/edit tools (used for suggestions)
CANONICAL = {
    ("channel", "delete"): ["find_duplicate_channels", "delete_channel", "bulk_delete_channels"],
    ("channel", "create"): ["create_channel", "create_category"],
    ("channel", "edit"): ["edit_channel"],
    ("message", "delete"): ["delete_messages", "bulk_delete_messages"],
    ("message", "create"): ["send_message", "send_embed", "reply_message"],
    ("role", "delete"): ["delete_role"],
    ("role", "create"): ["create_role"],
    ("role", "edit"): ["edit_role", "assign_role", "remove_role"],
    ("member", "delete"): ["kick_user", "ban_user"],
    ("member", "edit"): ["timeout_user", "assign_role", "remove_role"],
}

ACTION_META: Dict[str, Dict[str, Any]] = {
    # ---- channels ----
    "delete_channel":        {"object_type": "channel", "operation": "delete", "danger": "high",
                              "permission": "manage_channels", "batch": False, "confirm": True,
                              "verify": "channel_gone"},
    "bulk_delete_channels":  {"object_type": "channel", "operation": "delete", "danger": "high",
                              "permission": "manage_channels", "batch": True, "confirm": True,
                              "verify": "channels_gone"},
    "create_channel":        {"object_type": "channel", "operation": "create", "danger": "low",
                              "permission": "manage_channels", "batch": False, "confirm": False,
                              "verify": "channel_exists"},
    "create_category":       {"object_type": "channel", "operation": "create", "danger": "low",
                              "permission": "manage_channels", "batch": False, "confirm": False,
                              "verify": "channel_exists"},
    "create_shop_channel":   {"object_type": "channel", "operation": "create", "danger": "low",
                              "permission": "manage_channels", "batch": False, "confirm": False,
                              "verify": "channel_exists"},
    "edit_channel":          {"object_type": "channel", "operation": "edit", "danger": "medium",
                              "permission": "manage_channels", "batch": False, "confirm": False,
                              "verify": "none"},
    "find_duplicate_channels": {"object_type": "channel", "operation": "query", "danger": "none",
                              "permission": "none", "batch": False, "confirm": False,
                              "verify": "none"},
    # ---- messages ----
    "send_message":          {"object_type": "message", "operation": "create", "danger": "low",
                              "permission": "send_messages", "batch": False, "confirm": False,
                              "verify": "none"},
    "send_embed":            {"object_type": "message", "operation": "create", "danger": "low",
                              "permission": "send_messages", "batch": False, "confirm": False,
                              "verify": "none"},
    "reply_message":         {"object_type": "message", "operation": "create", "danger": "low",
                              "permission": "send_messages", "batch": False, "confirm": False,
                              "verify": "none"},
    "add_reaction":          {"object_type": "message", "operation": "edit", "danger": "low",
                              "permission": "add_reactions", "batch": False, "confirm": False,
                              "verify": "none"},
    "send_notification":     {"object_type": "message", "operation": "create", "danger": "low",
                              "permission": "send_messages", "batch": False, "confirm": False,
                              "verify": "none"},
    "delete_messages":       {"object_type": "message", "operation": "delete", "danger": "high",
                              "permission": "manage_messages", "batch": True, "confirm": True,
                              "verify": "none"},
    "bulk_delete_messages":  {"object_type": "message", "operation": "delete", "danger": "high",
                              "permission": "manage_messages", "batch": True, "confirm": True,
                              "verify": "none"},
    # ---- roles ----
    "create_role":           {"object_type": "role", "operation": "create", "danger": "low",
                              "permission": "manage_roles", "batch": False, "confirm": False,
                              "verify": "role_exists"},
    "delete_role":           {"object_type": "role", "operation": "delete", "danger": "high",
                              "permission": "manage_roles", "batch": False, "confirm": True,
                              "verify": "role_gone"},
    "edit_role":             {"object_type": "role", "operation": "edit", "danger": "medium",
                              "permission": "manage_roles", "batch": False, "confirm": False,
                              "verify": "none"},
    "assign_role":           {"object_type": "role", "operation": "edit", "danger": "medium",
                              "permission": "manage_roles", "batch": False, "confirm": False,
                              "verify": "none"},
    "remove_role":           {"object_type": "role", "operation": "edit", "danger": "medium",
                              "permission": "manage_roles", "batch": False, "confirm": False,
                              "verify": "none"},
    "assign_role_by_name":   {"object_type": "role", "operation": "edit", "danger": "medium",
                              "permission": "manage_roles", "batch": False, "confirm": False,
                              "verify": "none"},
    # ---- members ----
    "ban_user":              {"object_type": "member", "operation": "delete", "danger": "high",
                              "permission": "ban_members", "batch": False, "confirm": True,
                              "verify": "none"},
    "kick_user":             {"object_type": "member", "operation": "delete", "danger": "high",
                              "permission": "kick_members", "batch": False, "confirm": True,
                              "verify": "none"},
    "softban_user":          {"object_type": "member", "operation": "delete", "danger": "high",
                              "permission": "ban_members", "batch": False, "confirm": True,
                              "verify": "none"},
    "timeout_user":          {"object_type": "member", "operation": "edit", "danger": "high",
                              "permission": "moderate_members", "batch": False, "confirm": True,
                              "verify": "none"},
    # ---- queries (safe) ----
    "query_server_info":     {"object_type": "server", "operation": "query", "danger": "none",
                              "permission": "none", "batch": False, "confirm": False, "verify": "none"},
    "query_channels":        {"object_type": "channel", "operation": "query", "danger": "none",
                              "permission": "none", "batch": False, "confirm": False, "verify": "none"},
    "query_roles":           {"object_type": "role", "operation": "query", "danger": "none",
                              "permission": "none", "batch": False, "confirm": False, "verify": "none"},
    "query_members":         {"object_type": "member", "operation": "query", "danger": "none",
                              "permission": "none", "batch": False, "confirm": False, "verify": "none"},
    "analyze_server_state":  {"object_type": "server", "operation": "query", "danger": "none",
                              "permission": "none", "batch": False, "confirm": False, "verify": "none"},
    # ---- system setup ----
    "move_system":           {"object_type": "channel", "operation": "edit", "danger": "medium",
                              "permission": "manage_channels", "batch": False, "confirm": False,
                              "verify": "none"},
    "connect_systems":       {"object_type": "server", "operation": "edit", "danger": "medium",
                              "permission": "administrator", "batch": False, "confirm": False,
                              "verify": "none"},
}

DEFAULT_META = {"object_type": "unknown", "operation": "unknown", "danger": "medium",
                "permission": "administrator", "batch": False, "confirm": True, "verify": "none"}


def get_meta(name: str) -> Dict[str, Any]:
    return ACTION_META.get(name, DEFAULT_META)


def is_destructive(name: str) -> bool:
    meta = get_meta(name)
    return meta["operation"] == "delete" or meta["danger"] == "high"


# --------------------------------------------------------------------------- #
# Intent inference + semantic validation                                       #
# --------------------------------------------------------------------------- #

OBJECT_KEYWORDS = {
    "channel": ("channel", "category", "#"),
    "message": ("message", "msg", "chat history", "bulk delete messages"),
    "role": ("role",),
    "member": ("member", "user", "player", "people"),
    "server": ("server", "guild"),
}
OPERATION_KEYWORDS = {
    "delete": ("delete", "remove", "clean up", "cleanup", "purge", "get rid of", "wipe"),
    "create": ("create", "make", "add ", "set up", "setup", "open"),
    "edit": ("rename", "edit", "change", "move", "update", "lock", "unlock"),
}


def infer_intent(request_text: str) -> Tuple[Optional[str], Optional[str]]:
    """(object_type, operation) inferred from the user's words. None = unclear."""
    low = (request_text or "").lower()
    object_type = None
    for otype, keywords in OBJECT_KEYWORDS.items():
        if any(k in low for k in keywords):
            object_type = otype
            break
    operation = None
    for op, keywords in OPERATION_KEYWORDS.items():
        if any(k in low for k in keywords):
            operation = op
            break
    return object_type, operation


def validate_action(request_text: str, action_name: str) -> Tuple[bool, str, List[str]]:
    """
    Hard semantic gate. Returns (allowed, reason, suggested_actions).
    Destructive operations must match the inferred object type EXACTLY —
    no fuzzy matching may override object type (plan rule 14).
    """
    meta = get_meta(action_name)
    object_type, operation = infer_intent(request_text)

    # Only gate when the user's intent is clear AND the action mutates/deletes
    if object_type is None or operation is None:
        return True, "", []
    if operation != meta["operation"]:
        return True, "", []          # different operation — other rules apply
    if meta["operation"] not in ("delete", "create"):
        return True, "", []
    if meta["danger"] == "none":
        return True, "", []          # read/query tools always allowed

    if meta["object_type"] == object_type:
        return True, "", []

    suggested = CANONICAL.get((object_type, operation), [])
    return False, (
        f"Requested object type = **{object_type}** but `{action_name}` operates on "
        f"**{meta['object_type']}**."
    ), suggested


# --------------------------------------------------------------------------- #
# Protected targets — backend enforcement, not an AI suggestion                #
# --------------------------------------------------------------------------- #

class ProtectedTargets:
    """Per-guild protected object IDs. delete actions refuse these server-side."""

    def __init__(self):
        self._protected: Dict[int, Dict[str, set]] = {}

    def protect(self, guild_id: int, object_type: str, object_id):
        bucket = self._protected.setdefault(int(guild_id), {}).setdefault(object_type, set())
        bucket.add(str(object_id))

    def release(self, guild_id: int, object_type: str, object_id):
        bucket = self._protected.get(int(guild_id), {}).get(object_type)
        if bucket:
            bucket.discard(str(object_id))

    def is_protected(self, guild_id: int, object_type: str, object_id) -> bool:
        return str(object_id) in self._protected.get(int(guild_id), {}).get(object_type, set())

    def snapshot(self, guild_id: int) -> Dict[str, set]:
        return {k: set(v) for k, v in self._protected.get(int(guild_id), {}).items()}


protected_targets = ProtectedTargets()


# Channels required by enabled Miro systems are always protected (plan item 18)
def system_protected_channel_ids(guild_id: int, guild=None) -> set:
    """Channel IDs referenced by active Miro system configurations."""
    from data_manager import dm
    ids = set()
    config_sources = (
        "tickets_config",      # log_channel / ticket_category
        "announcements_config",  # announcement_channel / approval_channel
        "welcome_leave_config",  # welcome_channel / leave_channel
        "leveling_config",     # announce_channel
        "automod_config",      # log_channel_id
        "staff_shifts_config",  # shift_channel_id
        "staff_reviews_config",  # review_channel_id
        "verification_config",   # verify_channel
    )
    for key in config_sources:
        cfg = dm.get_guild_data(guild_id, key, {})
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        for field in cfg.values():
            if isinstance(field, (str, int)) and str(field).isdigit() and len(str(field)) >= 17:
                ids.add(str(field))
    if guild is not None:
        for cid in (guild.system_channel_id, guild.rules_channel_id):
            if cid:
                ids.add(str(cid))
    return ids

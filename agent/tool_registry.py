"""V9 canonical tool registry.

The registry is the agent-facing contract. ActionHandler remains the only
mutation executor. Dependency-repairable tools may be repaired by the executor
from live state, but destructive operations always receive exact IDs before
ActionHandler.dispatch().
"""
from typing import Any, Dict, List
from core.action_meta import ACTION_META, CANONICAL, get_meta as _get_meta, validate_action

DEFAULT_RETRY_POLICY = {"max_retries": 1, "backoff_seconds": 1.5}

def _spec(name: str, description: str, parameters: Dict[str, Any], *, permission: str = "administrator", danger: str = "medium", verifier: str = "live_state", retries: int = 1) -> Dict[str, Any]:
    return {"name": name, "description": description, "parameters": parameters, "permission": permission, "danger": danger, "executor": "ActionHandler.dispatch", "verifier": verifier, "retry_policy": {"max_retries": retries, "backoff_seconds": 1.5}}

TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "query_channels": _spec("query_channels", "List live server channels with IDs and types.", {}, permission="none", danger="none", verifier="query_result", retries=0),
    "find_duplicate_channels": _spec("find_duplicate_channels", "Find duplicate channels. Without name, scan all groups. Returns exact live IDs, the oldest original, and duplicates to delete.", {"name": {"type": "string", "required": False}, "protected_channel_id": {"type": "string", "required": False}}, permission="none", danger="none", verifier="query_result", retries=0),
    "get_channel": _spec("get_channel", "Fetch one live channel by ID.", {"channel_id": {"type": "integer", "required": True}}, permission="none", danger="none", verifier="query_result", retries=0),
    "query_roles": _spec("query_roles", "List live server roles.", {}, permission="none", danger="none", verifier="query_result", retries=0),
    "get_member": _spec("get_member", "Fetch one live member by ID.", {"user_id": {"type": "integer", "required": True}}, permission="none", danger="none", verifier="query_result", retries=0),
    "query_members": _spec("query_members", "List live server members.", {}, permission="none", danger="none", verifier="query_result", retries=0),
    "get_server_config": _spec("get_server_config", "Read Miro server configuration.", {}, permission="none", danger="none", verifier="query_result", retries=0),
    "create_channel": _spec("create_channel", "Create a text/voice channel.", {"name": {"type": "string", "required": True}, "type": {"type": "string", "required": False}, "category": {"type": "string", "required": False}}, permission="manage_channels", danger="low"),
    "edit_channel": _spec("edit_channel", "Edit channel properties.", {"channel_id": {"type": "integer", "required": True}, "new_name": {"type": "string", "required": False}, "topic": {"type": "string", "required": False}}, permission="manage_channels", danger="medium"),
    "delete_channel": _spec("delete_channel", "Delete exactly one channel by exact ID. Never use a name.", {"channel_id": {"type": "integer", "required": True}}, permission="manage_channels", danger="high", verifier="channel_gone"),
    "move_channel": _spec("move_channel", "Move a channel to a category.", {"channel_id": {"type": "integer", "required": True}, "category_id": {"type": "integer", "required": True}}, permission="manage_channels", danger="medium"),
    "bulk_delete_channels": _spec("bulk_delete_channels", "Delete multiple exact channel IDs. ALWAYS provide channel_ids from find_duplicate_channels. If IDs are omitted by the model, the runtime may resolve them from live Discord state before dispatch; it will never delete by name.", {"channel_ids": {"type": "array", "required": True}}, permission="manage_channels", danger="high", verifier="channels_gone"),
    "cleanup_duplicate_channels": _spec("cleanup_duplicate_channels", "Safe duplicate cleanup: resolve exact live IDs, preserve the original/protected channel, delete duplicates and verify each deletion.", {"name": {"type": "string", "required": True}, "protected_channel_id": {"type": "string", "required": False}}, permission="manage_channels", danger="high", verifier="channels_gone"),
    "create_role": _spec("create_role", "Create a role.", {"name": {"type": "string", "required": True}}, permission="manage_roles", danger="low", verifier="role_exists"),
    "edit_role": _spec("edit_role", "Edit role properties.", {"role_id": {"type": "integer", "required": True}}, permission="manage_roles", danger="medium"),
    "delete_role": _spec("delete_role", "Delete one role by exact ID.", {"role_id": {"type": "integer", "required": True}}, permission="manage_roles", danger="high", verifier="role_gone"),
    "assign_role": _spec("assign_role", "Assign a role to a member.", {"user_id": {"type": "integer", "required": True}, "role_id": {"type": "integer", "required": True}}, permission="manage_roles", danger="medium", verifier="member_has_role"),
    "remove_role": _spec("remove_role", "Remove a role from a member.", {"user_id": {"type": "integer", "required": True}, "role_id": {"type": "integer", "required": True}}, permission="manage_roles", danger="medium", verifier="member_lacks_role"),
    "warn_user": _spec("warn_user", "Issue a moderation warning.", {"user_id": {"type": "integer", "required": True}, "reason": {"type": "string", "required": False}}, permission="administrator", danger="medium"),
    "timeout_user": _spec("timeout_user", "Timeout a member.", {"user_id": {"type": "integer", "required": True}, "duration": {"type": "integer", "required": True}}, permission="moderate_members", danger="high"),
    "kick_user": _spec("kick_user", "Kick a member.", {"user_id": {"type": "integer", "required": True}}, permission="kick_members", danger="high", verifier="member_gone"),
    "ban_user": _spec("ban_user", "Ban a member.", {"user_id": {"type": "integer", "required": True}}, permission="ban_members", danger="high", verifier="ban_exists"),
    "delete_messages": _spec("delete_messages", "Delete messages matching the requested target.", {"channel_id": {"type": "integer", "required": True}}, permission="manage_messages", danger="high"),
    "configure_automod": _spec("configure_automod", "Configure Miro AutoMod.", {"enabled": {"type": "boolean", "required": True}}, permission="administrator", danger="medium"),
    "configure_logging": _spec("configure_logging", "Configure server logging.", {"channel_id": {"type": "integer", "required": False}}, permission="administrator", danger="medium"),
    "configure_tickets": _spec("configure_tickets", "Configure ticket system.", {}, permission="administrator", danger="medium"),
    "configure_staff_system": _spec("configure_staff_system", "Configure staff system.", {}, permission="administrator", danger="medium"),
    "configure_verification": _spec("configure_verification", "Configure verification.", {}, permission="administrator", danger="medium"),
}


REQUIRED_PARAMS = {
    "create_channel": [("name", "channel name")],
    "create_role": [("name", "role name")],
    "delete_channel": [("channel_id", "channel ID")],
    "assign_role": [("user_id", "user ID"), ("role_id", "role ID")],
    "ban_user": [("user_id", "user ID")],
    "kick_user": [("user_id", "user ID")],
    "send_message": [("content", "message text")],
}

class ToolRegistry:
    def get(self, name: str) -> Dict[str, Any]:
        meta = dict(_get_meta(name)); spec = dict(TOOL_SPECS.get(name, {}))
        spec.setdefault("name", name); spec.setdefault("description", f"{meta.get('operation')} {meta.get('object_type')}")
        spec.setdefault("parameters", {}); spec.setdefault("permission", meta.get("permission", "administrator")); spec.setdefault("danger", meta.get("danger", "medium"))
        spec.setdefault("executor", "ActionHandler.dispatch"); spec.setdefault("verifier", meta.get("verify", "live_state")); spec.setdefault("retry_policy", dict(DEFAULT_RETRY_POLICY)); spec["metadata"] = meta
        return spec
    def all_names(self) -> List[str]:
        return sorted(set(ACTION_META) | set(TOOL_SPECS) | {"send_dm", "lock_server", "rename_channel", "clone_channel", "move_channel", "lock_channel", "unlock_channel"})
    def suggest(self, object_type: str, operation: str) -> List[str]: return list(CANONICAL.get((object_type, operation), []))
    def validate(self, request_text: str, action_name: str): return validate_action(request_text, action_name)

tool_registry = ToolRegistry()

def ensure_full_catalog(allowed_names) -> int:
    """Generate TOOL_SPECS for every allowed action lacking one."""
    from core.action_meta import _infer_action_meta
    created = 0
    for name in sorted(set(allowed_names)):
        if name in TOOL_SPECS:
            continue
        meta = _infer_action_meta(name)
        fields = []
        from agent.tool_registry import REQUIRED_PARAMS as _RP
        for key, label in _RP.get(name, []):
            typ = "integer" if key.endswith("_id") else \
                  "boolean" if key.startswith("enabled") else "string"
            fields.append((key, {"type": typ, "required": True}, label))
        props = {k: v for k, v, _l in fields}
        required = [k for k, v, _l in fields if v.get("required")]
        TOOL_SPECS[name] = {
            "description": f"{meta['operation'].title()} {meta['object_type']} — "
                           f"{name.replace('_',' ')}",
            "parameters": {"type": "object", "properties": props,
                           "required": required},
            "permission": meta["permission"], "danger": meta["danger"],
            "verifier": meta["verify"],
            "retries": 1 if meta["danger"] != "none" else 0,
        }
        created += 1
    return created



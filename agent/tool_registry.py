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
    # ---- LIVE automations & prefix commands (execute immediately, no restart) ----
    "create_prefix_command": _spec(
        "create_prefix_command",
        "Create a LIVE custom prefix command — works instantly with no restart. Example: name='welcome', code='Hey {user}, welcome to {server}!' makes !welcome work. Placeholders {user} {server} {channel} {args} rendered live. Supports aliases (up to 5), cooldown_seconds, required_permission (everyone|mod|admin), description. For many commands, prefer bulk_create_prefix_commands (25 at once). Max 100/guild, overwrites silently if name exists. Quota-aware.",
        {"name": {"type": "string", "required": True},
         "code": {"type": "string", "required": True}},
        permission="administrator", danger="low"),
    "delete_prefix_command": _spec(
        "delete_prefix_command",
        "Delete a custom prefix command by name.",
        {"cmd_name": {"type": "string", "required": True}},
        permission="administrator", danger="medium"),
    "list_prefix_commands": _spec(
        "list_prefix_commands",
        "List all custom prefix commands for this server.",
        {},
        permission="none", danger="none"),
    "create_automation": _spec(
        "create_automation",
        "Create a LIVE automation that runs for real — 1000x scale. Types: scheduled_task (cron/schedule + channel + response/actions), event_trigger (event + filters + actions[] for member_joined/member_left/message_contains/reaction_added/voice_joined), auto_responder (keywords + response, single reply), reminder (duration sec 10-2592000 + response, one-shot), trigger_role (keywords + role_id/role_name + response). MULTI-STEP: pass 'actions' as list of {name, parameters} to run several things in order (e.g. send_message then assign_role). Bulk: use bulk_create_automations for 25 at once (max 100/guild, auto-pause after 10 failures). Quota-aware: check list_automations first to avoid duplicates — update_automation if exists. Schedule: prefer schedule:{every_minutes:15}|{every_hours:2}|{every_days:1}|{daily_at:'09:00'}|{weekly_on:'Mon',at:'08:00'}|{weekly_on:['Mon','Fri']}|{cron:'0 12 * * *'}; plain words in cron ('daily at 9am','every 15 minutes','weekdays 8am','in 2 hours'->reminder) auto-parsed. Channel: 'here'/'current'/'this channel' resolved via query_channels or channel_name, else current channel. Use query_roles to resolve role_name→role_id. All fire live, survive restarts.",
        {"type": {"type": "string", "required": True},
         "name": {"type": "string", "required": True},
         "response": {"type": "string", "required": False},
         "cron": {"type": "string", "required": False},
         "schedule": {"type": "object", "required": False},
         "duration": {"type": "integer", "required": False},
         "keywords": {"type": "array", "required": False},
         "action_type": {"type": "string", "required": False},
         "actions": {"type": "array", "required": False, "items": "object", "description": "Multi-step list of {name, parameters} executed in order (e.g. send_message then assign_role)."},
         "event": {"type": "string", "required": False, "description": "For type:event_trigger — one of member_joined, member_left, message_contains, reaction_added, voice_joined."},
         "filters": {"type": "object", "required": False, "description": "Optional filters: channel_id, role_id, emoji."},
         "match_type": {"type": "string", "required": False, "description": "For message_contains/auto_responder: contains|exact|starts_with|ends_with|regex."},
         "channel_id": {"type": "integer", "required": False},
         "channel_name": {"type": "string", "required": False},
         "role_id": {"type": "integer", "required": False},
         "role_name": {"type": "string", "required": False},
         "timezone": {"type": "string", "required": False}},
        permission="administrator", danger="low"),
    "delete_automation": _spec(
        "delete_automation",
        "Delete an automation by exact name and cancel its scheduled job.",
        {"name": {"type": "string", "required": True}},
        permission="administrator", danger="medium"),
    "list_automations": _spec(
        "list_automations",
        "List all LIVE automations registered on this server.",
        {},
        permission="none", danger="none"),
    "schedule_ai_action": _spec(
        "schedule_ai_action",
        "Schedule any Miro action to run on a cron schedule (LIVE via TaskScheduler).",
        {"name": {"type": "string", "required": False},
         "cron": {"type": "string", "required": True},
         "action_type": {"type": "string", "required": False},
         "channel_id": {"type": "integer", "required": False}},
        permission="administrator", danger="medium"),
}


REQUIRED_PARAMS = {
    "create_channel": [("name", "channel name")],
    "create_role": [("name", "role name")],
    "delete_channel": [("channel_id", "channel ID")],
    "assign_role": [("user_id", "user ID"), ("role_id", "role ID")],
    "ban_user": [("user_id", "user ID")],
    "kick_user": [("user_id", "user ID")],
    "send_message": [("content", "message text")],
    # LIVE automations & prefix commands
    "create_prefix_command": [("name", "command name (without prefix)"), ("code", "response text")],
    "delete_prefix_command": [("cmd_name", "command name")],
    "create_automation": [("type", "scheduled_task | event_trigger | auto_responder | reminder | trigger_role"), ("name", "automation name")],
    "delete_automation": [("name", "automation name")],
    "schedule_ai_action": [("cron", "cron expression, e.g. '0 12 * * *'")],
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


# --------------------------------------------------------------------------- #
# Automation scale layer (1000x): lifecycle + bulk tools                      #
# --------------------------------------------------------------------------- #

TOOL_SPECS["update_automation"] = _spec(
    "update_automation",
    "Update an existing automation: change its cron/schedule, action handler, response text, or target channel. Use cron like '*/15 * * * *', or schedule {'every_minutes': 15} / {'every_hours': 2} / {'daily_at': '09:00'}.",
    {"name": {"type": "string", "required": True},
     "cron": {"type": "string", "required": False},
     "schedule": {"type": "object", "required": False},
     "action": {"type": "object", "required": False},
     "response": {"type": "string", "required": False},
     "channel_id": {"type": "string", "required": False}},
    permission="administrator", danger="low", verifier="automation_exists")

TOOL_SPECS["pause_automation"] = _spec(
    "pause_automation",
    "Pause an automation without deleting it (its cron schedule stops firing).",
    {"name": {"type": "string", "required": True}},
    permission="administrator", danger="low", verifier="automation_paused")

TOOL_SPECS["resume_automation"] = _spec(
    "resume_automation",
    "Resume a paused automation and re-schedule it.",
    {"name": {"type": "string", "required": True}},
    permission="administrator", danger="low", verifier="automation_exists")

TOOL_SPECS["run_automation_now"] = _spec(
    "run_automation_now",
    "Test-fire an automation immediately without waiting for its schedule. Reports success/failure.",
    {"name": {"type": "string", "required": True}},
    permission="administrator", danger="medium", verifier="automation_exists")

TOOL_SPECS["bulk_create_automations"] = _spec(
    "bulk_create_automations",
    "1000x BULK: Create up to 25 automations in ONE call — use when user says 'make 5/10/25 automations' or 'all'. Pass 'automations': [ {type, name, response, cron|schedule:{every_minutes|every_hours|every_days|daily_at|weekly_on|at}, channel_id/channel_name/'here', keywords, duration, role_id/role_name, actions[]}, ... ]. Each item follows create_automation shape. Honors 100/guild quota, partial success allowed (reports created/requested/results). Prefer bulk over 25 single calls. For 'here' use current channel via query_channels.",
    {"automations": {"type": "array", "required": True, "items": "object"}},
    permission="administrator", danger="medium", verifier="automation_exists", retries=1)

TOOL_SPECS["bulk_pause_automations"] = _spec(
    "bulk_pause_automations",
    "Pause many automations at once: by 'names': [...] or 'all': true (optionally filtered by 'type': scheduled_task/auto_responder/reminder).",
    {"names": {"type": "array", "required": False, "items": "string"},
     "all": {"type": "boolean", "required": False},
     "type": {"type": "string", "required": False}},
    permission="administrator", danger="medium", verifier="automation_paused")

TOOL_SPECS["bulk_delete_automations"] = _spec(
    "bulk_delete_automations",
    "Delete many automations at once: by 'names': [...] or 'all': true (optionally filtered by 'type'). DANGEROUS - confirm with the user first.",
    {"names": {"type": "array", "required": False, "items": "string"},
     "all": {"type": "boolean", "required": False},
     "type": {"type": "string", "required": False}},
    permission="administrator", danger="high", verifier="automation_gone")

TOOL_SPECS["bulk_create_prefix_commands"] = _spec(
    "bulk_create_prefix_commands",
    "1000x BULK: Create up to 25 custom !commands in ONE call — use when user says 'make 5 commands' or lists many !names. Pass 'commands': [ {'name': 'rules', 'code': 'response text with {user} {server} placeholders', 'aliases': [...up to 5], 'cooldown_seconds': 5, 'required_permission': 'everyone|mod|admin', 'description': '...'}, ... ]. Each code supports {user} {server} {channel} {args} templating. Max 100/guild, 25 per bulk, partial success. Prefer bulk over repeated singles.",
    {"commands": {"type": "array", "required": True, "items": "object"}},
    permission="administrator", danger="low", verifier="command_exists", retries=1)

# Enrich the existing single-item tools with the new capabilities
if "create_prefix_command" in TOOL_SPECS:
    _params = TOOL_SPECS["create_prefix_command"].setdefault("parameters", {})
    _params.setdefault("aliases", {"type": "array", "required": False, "items": "string"})
    _params.setdefault("cooldown_seconds", {"type": "integer", "required": False})
    _params.setdefault("required_permission", {"type": "string", "required": False})
    _params.setdefault("description", {"type": "string", "required": False})


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



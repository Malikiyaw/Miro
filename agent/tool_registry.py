"""Tool registry — metadata-driven view over the action catalog."""
from typing import Any, Dict, List, Optional

from core.action_meta import (ACTION_META, CANONICAL, DEFAULT_META,
                              get_meta as _get_meta, is_destructive,
                              validate_action)


class ToolRegistry:
    """The ONLY way the agent learns about tools. Metadata, not names."""

    DESCRIPTIONS = {
        "find_duplicate_channels": "Find duplicate channels by name; returns protected + duplicates with exact IDs",
        "bulk_delete_channels": "Delete many channels BY ID with per-item verification",
        "cleanup_duplicate_channels": "ONE-CALL duplicate cleanup: find -> protect -> delete -> verify each deletion",
        "delete_channel": "Delete one channel by ID",
        "create_channel": "Create a text channel",
        "edit_channel": "Edit a channel (name/topic/position)",
        "query_channels": "List channels with IDs and types",
        "send_message": "Send a message to a channel",
        "ban_user": "Ban a member by user ID",
        "kick_user": "Kick a member by user ID",
    }

    def get(self, name: str) -> Dict[str, Any]:
        meta = dict(_get_meta(name))
        meta["name"] = name
        meta["description"] = self.DESCRIPTIONS.get(name, f"{meta['operation']} {meta['object_type']}")
        return meta

    def all_names(self) -> List[str]:
        return sorted(set(ACTION_META) | {
            # actions handled generically without dedicated metadata
            "send_dm", "lock_server", "rename_channel", "clone_channel",
            "move_channel", "lock_channel", "unlock_channel",
        })

    def suggest(self, object_type: str, operation: str) -> List[str]:
        return list(CANONICAL.get((object_type, operation), []))

    def validate(self, request_text: str, action_name: str):
        return validate_action(request_text, action_name)


tool_registry = ToolRegistry()

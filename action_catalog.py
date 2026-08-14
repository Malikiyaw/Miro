"""
AI Action Catalog and Resolver System

This module provides a comprehensive catalog of all available AI actions
and an intelligent resolver that maps user requests to the best matching actions.
"""

from typing import Dict, List, Any, Tuple, Optional
import json
import re


ACTION_CATALOG = {
    "create_channel": {
        "name": "create_channel",
        "description": "Creates a new text or voice channel in the server",
        "category": "Channel Management",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Channel name"},
            "type": {"type": "string", "required": False, "description": "text or voice", "default": "text"},
            "category": {"type": "string", "required": False, "description": "Category name to place channel in"},
            "private": {"type": "boolean", "required": False, "description": "Make channel private", "default": False},
            "allowed_roles": {"type": "array", "required": False, "description": "Roles allowed to view"},
            "denied_roles": {"type": "array", "required": False, "description": "Roles denied from viewing"}
        },
        "aliases": ["make_channel", "new_channel", "add_channel"],
        "keywords": ["channel", "create", "new", "make", "add channel", "text channel", "voice channel"]
    },
    "create_voice_channel": {
        "name": "create_voice_channel",
        "description": "Creates a new voice channel",
        "category": "Channel Management",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Channel name"},
            "category": {"type": "string", "required": False, "description": "Category name"}
        },
        "keywords": ["voice", "vc", "voice channel", "create voice"]
    },
    "create_text_channel": {
        "name": "create_text_channel",
        "description": "Creates a new text channel",
        "category": "Channel Management",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Channel name"},
            "category": {"type": "string", "required": False, "description": "Category name"}
        },
        "keywords": ["text", "text channel", "create text", "chat channel"]
    },
    "create_category": {
        "name": "create_category",
        "description": "Creates a new category for organizing channels",
        "category": "Channel Management",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Category name"},
            "private": {"type": "boolean", "required": False, "description": "Make category private"},
            "allowed_roles": {"type": "array", "required": False, "description": "Roles allowed"},
            "denied_roles": {"type": "array", "required": False, "description": "Roles denied"}
        },
        "aliases": ["create_category_channel"],
        "keywords": ["category", "create category", "new category", "section"]
    },
    "delete_channel": {
        "name": "delete_channel",
        "description": "Deletes an existing channel",
        "category": "Channel Management",
        "parameters": {
            "channel_name": {"type": "string", "required": True, "description": "Name of channel to delete"},
            "name": {"type": "string", "required": False, "description": "Alternative parameter for channel name"}
        },
        "keywords": ["delete", "remove", "delete channel", "remove channel"]
    },
    "edit_channel": {
        "name": "edit_channel",
        "description": "Edits channel properties (name, topic, slowmode, etc.)",
        "category": "Channel Management",
        "parameters": {
            "channel_name": {"type": "string", "required": False, "description": "Channel to edit"},
            "new_name": {"type": "string", "required": False, "description": "New channel name"},
            "topic": {"type": "string", "required": False, "description": "Channel topic/description"},
            "slowmode_delay": {"type": "integer", "required": False, "description": "Slowmode in seconds"},
            "nsfw": {"type": "boolean", "required": False, "description": "Mark as NSFW"},
            "position": {"type": "integer", "required": False, "description": "Channel position"},
            "category": {"type": "string", "required": False, "description": "Move to category"}
        },
        "keywords": ["edit", "modify", "change", "update channel"]
    },
    "edit_channel_name": {
        "name": "edit_channel_name",
        "description": "Renames a channel",
        "category": "Channel Management",
        "parameters": {
            "channel_name": {"type": "string", "required": True, "description": "Current channel name"},
            "new_name": {"type": "string", "required": True, "description": "New channel name"}
        },
        "keywords": ["rename", "rename channel", "channel name"]
    },
    "move_channel": {
        "name": "move_channel",
        "description": "Moves a channel to a different category",
        "category": "Channel Management",
        "parameters": {
            "channel_name": {"type": "string", "required": True, "description": "Channel to move"},
            "category": {"type": "string", "required": True, "description": "Target category name"}
        },
        "keywords": ["move", "relocate", "category"]
    },
    "clone_channel": {
        "name": "clone_channel",
        "description": "Duplicates a channel",
        "category": "Channel Management",
        "parameters": {
            "channel_name": {"type": "string", "required": True, "description": "Channel to clone"}
        },
        "keywords": ["clone", "duplicate", "copy"]
    },
    "set_topic": {
        "name": "set_topic",
        "description": "Sets the topic/description of a channel",
        "category": "Channel Management",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "topic": {"type": "string", "required": True, "description": "Topic text"}
        },
        "keywords": ["topic", "description", "set topic"]
    },
    "slowmode": {
        "name": "slowmode",
        "description": "Sets slowmode delay on a channel",
        "category": "Channel Management",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "delay": {"type": "integer", "required": False, "description": "Delay in seconds", "default": 5}
        },
        "keywords": ["slowmode", "rate limit", "cooldown"]
    },
    "lock_channel": {
        "name": "lock_channel",
        "description": "Locks a channel preventing @everyone from sending",
        "category": "Channel Management",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"}
        },
        "keywords": ["lock", "lock channel", "disable send"]
    },
    "unlock_channel": {
        "name": "unlock_channel",
        "description": "Unlocks a previously locked channel",
        "category": "Channel Management",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"}
        },
        "keywords": ["unlock", "enable send"]
    },
    "make_channel_private": {
        "name": "make_channel_private",
        "description": "Makes a channel private by denying @everyone and allowing specific roles",
        "category": "Channel Management",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "channels": {"type": "array", "required": False, "description": "List of channel names to make private"},
            "allowed_roles": {"type": "array", "required": False, "description": "Roles to allow"}
        },
        "keywords": ["private", "hidden", "restrict", "secret"]
    },
    "make_category_private": {
        "name": "make_category_private",
        "description": "Makes a category and all its channels private. Use 'all' to process all categories.",
        "category": "Channel Management",
        "parameters": {
            "category": {"type": "string", "required": True, "description": "Category name, ID, or mention, or 'all' to process all categories (e.g., 'Staff Area', '123456789', 'all')"},
            "category_name": {"type": "string", "required": False, "description": "Category name, ID, or mention"},
            "allowed_roles": {"type": "array", "required": False, "description": "Roles to allow"}
        },
        "keywords": ["private", "category private", "restrict category"]
    },
    "create_role": {
        "name": "create_role",
        "description": "Creates a new role with optional permissions",
        "category": "Role Management",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Role name"},
            "color": {"type": "string", "required": False, "description": "Role color (hex or name)", "default": "#99AAB5"},
            "permissions": {"type": "object", "required": False, "description": "Role permissions"}
        },
        "aliases": ["create_role_with_permissions"],
        "keywords": ["role", "create role", "new role", "add role", "make role"]
    },
    "delete_role": {
        "name": "delete_role",
        "description": "Deletes an existing role",
        "category": "Role Management",
        "parameters": {
            "role_name": {"type": "string", "required": True, "description": "Role name to delete"}
        },
        "keywords": ["delete role", "remove role"]
    },
    "edit_role": {
        "name": "edit_role",
        "description": "Edits role properties (name, color, permissions)",
        "category": "Role Management",
        "parameters": {
            "role_name": {"type": "string", "required": True, "description": "Role to edit"},
            "new_name": {"type": "string", "required": False, "description": "New role name"},
            "color": {"type": "string", "required": False, "description": "New color"},
            "hoist": {"type": "boolean", "required": False, "description": "Display role separately"},
            "mentionable": {"type": "boolean", "required": False, "description": "Allow mentions"}
        },
        "keywords": ["edit role", "modify role", "role settings"]
    },
    "edit_role_name": {
        "name": "edit_role_name",
        "description": "Renames a role",
        "category": "Role Management",
        "parameters": {
            "role_name": {"type": "string", "required": True, "description": "Current role name"},
            "new_name": {"type": "string", "required": True, "description": "New role name"}
        },
        "keywords": ["rename role", "role name"]
    },
    "change_role_color": {
        "name": "change_role_color",
        "description": "Changes the color of a role",
        "category": "Role Management",
        "parameters": {
            "role_name": {"type": "string", "required": True, "description": "Role name"},
            "color": {"type": "string", "required": True, "description": "New color (hex)"}
        },
        "keywords": ["color", "role color", "role colour"]
    },
    "add_role": {
        "name": "add_role",
        "description": "Adds a role to a user (alias for assign_role)",
        "category": "Role Management",
        "parameters": {
            "role_id": {"type": "integer", "required": False, "description": "Role ID"},
            "role_name": {"type": "string", "required": False, "description": "Role name"},
            "name": {"type": "string", "required": False, "description": "Role name"},
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"}
        },
        "keywords": ["add role", "give role", "assign role", "role add"]
    },
    "assign_role": {
        "name": "assign_role",
        "description": "Assigns one or more roles to one or more users.",
        "category": "Role Management",
        "parameters": {
            "role_id": {"type": "integer", "required": False, "description": "Role ID"},
            "role_ids": {"type": "array", "required": False, "description": "List of Role IDs"},
            "role_name": {"type": "string", "required": False, "description": "Role name"},
            "role_names": {"type": "array", "required": False, "description": "List of Role names"},
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "user_ids": {"type": "array", "required": False, "description": "List of User IDs"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "usernames": {"type": "array", "required": False, "description": "List of Usernames"}
        },
        "keywords": ["assign", "give role", "add role to user", "promote", "mass role", "role all"]
    },
    "remove_role": {
        "name": "remove_role",
        "description": "Removes a role from a user",
        "category": "Role Management",
        "parameters": {
            "role_id": {"type": "integer", "required": False, "description": "Role ID"},
            "role_name": {"type": "string", "required": False, "description": "Role name"},
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"}
        },
        "keywords": ["remove role", "take role", "revoke role", "demote"]
    },
    "send_message": {
        "name": "send_message",
        "description": "Sends a simple text message to a channel",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "content": {"type": "string", "required": True, "description": "Message content"}
        },
        "keywords": ["send", "message", "post", "say", "tell"]
    },
    "send_embed": {
        "name": "send_embed",
        "description": "Sends a rich embed message with optional buttons and fields",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "title": {"type": "string", "required": True, "description": "Embed title"},
            "description": {"type": "string", "required": True, "description": "Embed description"},
            "color": {"type": "string", "required": False, "description": "Embed color"},
            "fields": {"type": "array", "required": False, "description": "Embed fields"},
            "buttons": {"type": "array", "required": False, "description": "Interactive buttons"}
        },
        "keywords": ["embed", "rich message", "formatted message", "button"]
    },
    "send_dm": {
        "name": "send_dm",
        "description": "Sends a direct message to a user",
        "category": "Messaging",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "content": {"type": "string", "required": True, "description": "Message content"},
            "embed": {"type": "object", "required": False, "description": "Rich embed data"}
        },
        "keywords": ["dm", "direct message", "private message", "pm"]
    },
    "reply_message": {
        "name": "reply_message",
        "description": "Replies to a specific message",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "message_id": {"type": "integer", "required": False, "description": "Message ID to reply to"},
            "content": {"type": "string", "required": True, "description": "Reply content"}
        },
        "keywords": ["reply", "respond", "reply to"]
    },
    "announce": {
        "name": "announce",
        "description": "Makes an announcement in a channel with embed",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "title": {"type": "string", "required": False, "description": "Announcement title"},
            "content": {"type": "string", "required": True, "description": "Announcement content"},
            "color": {"type": "string", "required": False, "description": "Embed color"}
        },
        "keywords": ["announce", "announcement", "broadcast", "news"]
    },
    "poll": {
        "name": "poll",
        "description": "Creates a poll with multiple options",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "question": {"type": "string", "required": True, "description": "Poll question"},
            "options": {"type": "array", "required": True, "description": "Poll options"},
            "duration": {"type": "integer", "required": False, "description": "Duration in seconds", "default": 300}
        },
        "keywords": ["poll", "vote", "survey", "question"]
    },
    "post_documentation": {
        "name": "post_documentation",
        "description": "Posts comprehensive system documentation with multiple sections",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "title": {"type": "string", "required": False, "description": "Doc title"},
            "description": {"type": "string", "required": False, "description": "Doc description"},
            "sections": {"type": "array", "required": False, "description": "Documentation sections"},
            "footer": {"type": "string", "required": False, "description": "Footer text"},
            "color": {"type": "string", "required": False, "description": "Embed color"}
        },
        "keywords": ["docs", "documentation", "guide", "help"]
    },
    "kick_user": {
        "name": "kick_user",
        "description": "Kicks a user from the server",
        "category": "Moderation",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "reason": {"type": "string", "required": False, "description": "Kick reason"}
        },
        "keywords": ["kick", "remove", "kick user", "remove from server"]
    },
    "ban_user": {
        "name": "ban_user",
        "description": "Bans a user from the server",
        "category": "Moderation",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "reason": {"type": "string", "required": False, "description": "Ban reason"},
            "delete_messages_days": {"type": "integer", "required": False, "description": "Days of messages to delete", "default": 0}
        },
        "keywords": ["ban", "banish", "ban user", "block"]
    },
    "timeout_user": {
        "name": "timeout_user",
        "description": "Times out a user (modifies communication timeout)",
        "category": "Moderation",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "duration": {"type": "integer", "required": False, "description": "Timeout duration in seconds", "default": 600},
            "reason": {"type": "string", "required": False, "description": "Timeout reason"}
        },
        "keywords": ["timeout", "mute", "silence", "ground"]
    },
    "mute_user": {
        "name": "mute_user",
        "description": "Server mutes a user (voice mute)",
        "category": "Moderation",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "reason": {"type": "string", "required": False, "description": "Mute reason"}
        },
        "keywords": ["mute", "voice mute"]
    },
    "unmute_user": {
        "name": "unmute_user",
        "description": "Removes server mute from a user",
        "category": "Moderation",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"}
        },
        "keywords": ["unmute", "unvoice mute"]
    },
    "deafen_user": {
        "name": "deafen_user",
        "description": "Voice deafens a user",
        "category": "Moderation",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "reason": {"type": "string", "required": False, "description": "Deafen reason"}
        },
        "keywords": ["deafen", "deaf", " deafen user"]
    },
    "set_nickname": {
        "name": "set_nickname",
        "description": "Sets a user's server nickname",
        "category": "Moderation",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "nickname": {"type": "string", "required": True, "description": "New nickname"}
        },
        "keywords": ["nickname", "nick", "rename user"]
    },
    "warn_user": {
        "name": "warn_user",
        "description": "Warns a user via DM and logs the warning",
        "category": "Moderation",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "reason": {"type": "string", "required": True, "description": "Warning reason"}
        },
        "keywords": ["warn", "warning", "strike", "caution"]
    },
    "give_points": {
        "name": "give_points",
        "description": "Gives economy points to a user",
        "category": "Economy",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "points": {"type": "integer", "required": False, "description": "Points to give", "default": 100}
        },
        "keywords": ["give points", "add coins", "award", "reward"]
    },
    "remove_points": {
        "name": "remove_points",
        "description": "Removes economy points from a user",
        "category": "Economy",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "points": {"type": "integer", "required": False, "description": "Points to remove", "default": 100}
        },
        "keywords": ["remove points", "take coins", "deduct", "fine"]
    },
    "create_invite": {
        "name": "create_invite",
        "description": "Creates an invite link for a channel or server",
        "category": "Server Management",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "max_uses": {"type": "integer", "required": False, "description": "Max uses", "default": 0},
            "max_age": {"type": "integer", "required": False, "description": "Max age in seconds", "default": 86400},
            "temporary": {"type": "boolean", "required": False, "description": "Temporary invite", "default": False}
        },
        "keywords": ["invite", "link", "invite link", "join"]
    },
    "ping": {
        "name": "ping",
        "description": "Pings a user and shows their status and latency",
        "category": "Utility",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"}
        },
        "keywords": ["ping", "tag", "mention", "find user", "user info"]
    },
    "create_prefix_command": {
        "name": "create_prefix_command",
        "description": "Creates a custom '!' command",
        "category": "Custom Commands",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Command name"},
            "code": {"type": "string", "required": True, "description": "Command code (JSON)"}
        },
        "keywords": ["command", "custom command", "create command", "shortcut"]
    },
    "delete_prefix_command": {
        "name": "delete_prefix_command",
        "description": "Deletes a custom command",
        "category": "Custom Commands",
        "parameters": {
            "cmd_name": {"type": "string", "required": True, "description": "Command name"}
        },
        "keywords": ["delete command", "remove command"]
    },
    "schedule_ai_action": {
        "name": "schedule_ai_action",
        "description": "Schedules an AI action to run on a cron schedule",
        "category": "Automation",
        "parameters": {
            "name": {"type": "string", "required": False, "description": "Task name"},
            "cron": {"type": "string", "required": False, "description": "Cron schedule", "default": "0 12 * * *"},
            "action_type": {"type": "string", "required": False, "description": "Action type"},
            "action_params": {"type": "object", "required": False, "description": "Action parameters"},
            "channel_id": {"type": "integer", "required": False, "description": "Target channel ID"}
        },
        "keywords": ["schedule", "cron", "recurring", "automate", "timer"]
    },
    "setup_verification": {
        "name": "setup_verification",
        "description": "Sets up the verification system with button",
        "category": "System Setup",
        "parameters": {},
        "aliases": ["create_verify_system"],
        "keywords": ["verification", "verify", "captcha", "verification system"]
    },
    "setup_tickets": {
        "name": "setup_tickets",
        "description": "Sets up the ticket system",
        "category": "System Setup",
        "parameters": {},
        "aliases": ["create_tickets_system"],
        "keywords": ["tickets", "support", "ticket system", "help desk"]
    },
    "setup_applications": {
        "name": "setup_applications",
        "description": "Sets up the staff applications system",
        "category": "System Setup",
        "parameters": {},
        "aliases": ["create_applications_system"],
        "keywords": ["applications", "apply", "staff application", "recruitment"]
    },
    "setup_appeals": {
        "name": "setup_appeals",
        "description": "Sets up the ban appeal system",
        "category": "System Setup",
        "parameters": {},
        "aliases": ["create_appeals_system"],
        "keywords": ["appeals", "appeal", "ban appeal", "unban request"]
    },
    "setup_moderation": {
        "name": "setup_moderation",
        "description": "Sets up moderation logging system",
        "category": "System Setup",
        "parameters": {},
        "keywords": ["moderation", "mod logging", "audit log"]
    },
    "setup_logging": {
        "name": "setup_logging",
        "description": "Sets up server logging system",
        "category": "System Setup",
        "parameters": {},
        "keywords": ["logging", "logs", "server logs"]
    },
    "setup_economy": {
        "name": "setup_economy",
        "description": "Sets up the economy system",
        "category": "System Setup",
        "parameters": {},
        "aliases": ["create_economy_system"],
        "keywords": ["economy", "coins", "currency", "shop"]
    },
    "setup_leveling": {
        "name": "setup_leveling",
        "description": "Sets up the leveling/XP system",
        "category": "System Setup",
        "parameters": {},
        "aliases": ["create_leveling_system"],
        "keywords": ["leveling", "xp", "levels", "experience", "rank"]
    },
    "setup_welcome": {
        "name": "setup_welcome",
        "description": "Sets up welcome/leave message system",
        "category": "System Setup",
        "parameters": {},
        "aliases": ["create_welcome_system"],
        "keywords": ["welcome", "greeting", "leave", "goodbye"]
    },
    "setup_staff_system": {
        "name": "setup_staff_system",
        "description": "Sets up the staff management system",
        "category": "System Setup",
        "parameters": {},
        "aliases": ["create_staff_system"],
        "keywords": ["staff", "team", "management", "promotion"]
    },
    "setup_trigger_role": {
        "name": "setup_trigger_role",
        "description": "Sets up trigger role system (auto-role on keyword)",
        "category": "System Setup",
        "parameters": {},
        "keywords": ["trigger", "reaction role", "keyword role"]
    },
    "allow_channel_permission": {
        "name": "allow_channel_permission",
        "description": "Allows a permission for a role in a channel",
        "category": "Permissions",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "role_name": {"type": "string", "required": True, "description": "Role name"},
            "permission": {"type": "string", "required": False, "description": "Permission name", "default": "send_messages"}
        },
        "keywords": ["allow", "permission", "grant", "allow permission"]
    },
    "deny_channel_permission": {
        "name": "deny_channel_permission",
        "description": "Denies a permission for a role in a channel",
        "category": "Permissions",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "role_name": {"type": "string", "required": True, "description": "Role name"},
            "permission": {"type": "string", "required": False, "description": "Permission name", "default": "send_messages"}
        },
        "keywords": ["deny", "permission", "revoke", "deny permission"]
    },
    "allow_all_channels_for_role": {
        "name": "allow_all_channels_for_role",
        "description": "Allows a role to view all channels",
        "category": "Permissions",
        "parameters": {
            "role_name": {"type": "string", "required": True, "description": "Role name"}
        },
        "keywords": ["allow all", "access all", "global access"]
    },
    "deny_all_channels_for_role": {
        "name": "deny_all_channels_for_role",
        "description": "Denies a role from viewing all channels",
        "category": "Permissions",
        "parameters": {
            "role_name": {"type": "string", "required": True, "description": "Role name"}
        },
        "keywords": ["deny all", "restrict all", "global deny"]
    },
    "deny_category_for_role": {
        "name": "deny_category_for_role",
        "description": "Denies a role in a category and all its child channels",
        "category": "Permissions",
        "parameters": {
            "category_name": {"type": "string", "required": False, "description": "Category name"},
            "category": {"type": "string", "required": False, "description": "Category name"},
            "role_name": {"type": "string", "required": True, "description": "Role name"}
        },
        "keywords": ["deny category", "restrict category"]
    },
    "edit_channel_permissions": {
        "name": "edit_channel_permissions",
        "description": "Edits permissions for a role on a channel",
        "category": "Permissions",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "role_name": {"type": "string", "required": True, "description": "Role name"},
            "permissions": {"type": "object", "required": True, "description": "Permission dict"}
        },
        "keywords": ["edit permissions", "channel permissions"]
    },
    "create_thread": {
        "name": "create_thread",
        "description": "Creates a thread in a channel",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "name": {"type": "string", "required": False, "description": "Thread name", "default": "new-thread"},
            "message": {"type": "string", "required": False, "description": "Thread message"}
        },
        "keywords": ["thread", "create thread", "forum"]
    },
    "pin_message": {
        "name": "pin_message",
        "description": "Pins a message in a channel",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "message_id": {"type": "integer", "required": True, "description": "Message ID to pin"}
        },
        "keywords": ["pin", "pin message"]
    },
    "unpin_message": {
        "name": "unpin_message",
        "description": "Unpins a message",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "message_id": {"type": "integer", "required": True, "description": "Message ID to unpin"}
        },
        "keywords": ["unpin", "unpin message"]
    },
    "add_reaction": {
        "name": "add_reaction",
        "description": "Adds an emoji reaction to a message",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "message_id": {"type": "integer", "required": False, "description": "Message ID"},
            "emoji": {"type": "string", "required": True, "description": "Emoji"}
        },
        "keywords": ["react", "reaction", "emoji"]
    },
    "remove_reaction": {
        "name": "remove_reaction",
        "description": "Removes an emoji reaction from a message",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "message_id": {"type": "integer", "required": False, "description": "Message ID"},
            "emoji": {"type": "string", "required": True, "description": "Emoji to remove"}
        },
        "keywords": ["remove reaction", "unreact"]
    },
    "delete_message": {
        "name": "delete_message",
        "description": "Deletes a specific message",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "message_id": {"type": "integer", "required": True, "description": "Message ID to delete"}
        },
        "keywords": ["delete message", "remove message"]
    },
    "bulk_delete_messages": {
        "name": "bulk_delete_messages",
        "description": "Bulk deletes messages in a channel",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": False, "description": "Channel name"},
            "channel_name": {"type": "string", "required": False, "description": "Channel name"},
            "amount": {"type": "integer", "required": True, "description": "Number of messages to delete"}
        },
        "keywords": ["bulk delete", "purge", "clear messages"]
    },
    "edit_channel_bitrate": {
        "name": "edit_channel_bitrate",
        "description": "Sets voice channel bitrate",
        "category": "Channel Management",
        "parameters": {
            "channel_name": {"type": "string", "required": True, "description": "Voice channel name"},
            "bitrate": {"type": "integer", "required": False, "description": "Bitrate in bps", "default": 128000}
        },
        "keywords": ["bitrate", "quality", "voice quality"]
    },
    "edit_channel_user_limit": {
        "name": "edit_channel_user_limit",
        "description": "Sets voice channel user limit",
        "category": "Channel Management",
        "parameters": {
            "channel_name": {"type": "string", "required": True, "description": "Voice channel name"},
            "user_limit": {"type": "integer", "required": False, "description": "User limit", "default": 0}
        },
        "keywords": ["user limit", "max users", "capacity"]
    },
    "follow_announcement_channel": {
        "name": "follow_announcement_channel",
        "description": "Follows an announcement channel to another channel",
        "category": "Channel Management",
        "parameters": {
            "source_channel": {"type": "string", "required": True, "description": "Announcement channel"},
            "target_channel": {"type": "string", "required": True, "description": "Channel to follow to"}
        },
        "keywords": ["follow", "announcement", "crosspost"]
    },
    "create_scheduled_event": {
        "name": "create_scheduled_event",
        "description": "Creates a scheduled event",
        "category": "Events",
        "parameters": {
            "name": {"type": "string", "required": False, "description": "Event name", "default": "Event"},
            "description": {"type": "string", "required": False, "description": "Event description"},
            "start_time": {"type": "string", "required": False, "description": "ISO format start time"},
            "end_time": {"type": "string", "required": False, "description": "ISO format end time"},
            "location": {"type": "string", "required": False, "description": "Event location", "default": "Voice Channel"}
        },
        "keywords": ["event", "schedule", "scheduled event", "calendar"]
    },
    "analyze_server_state": {
        "name": "analyze_server_state",
        "description": "Read-only analysis of current server state (planning checkpoint)",
        "category": "Meta",
        "parameters": {},
        "keywords": ["analyze", "check", "state", "planning", "debug"]
    },
    "extract_online_users": {
        "name": "extract_online_users",
        "description": "Extracts and returns a list of currently online members from the server. Alias for query_members with status filter.",
        "category": "Server Query",
        "parameters": {
            "status": {"type": "string", "required": False, "description": "Filter by status (online, idle, dnd, offline)", "default": "online"}
        },
        "aliases": ["get_online_users", "list_online_members", "who_is_online"],
        "keywords": ["online", "online users", "who is online", "active members", "extract online", "list online"]
    },
    "softban_user": {
        "name": "softban_user",
        "description": "Bans then immediately unbans a user to clear their messages.",
        "category": "Moderation",
        "parameters": {
            "user_id": {"type": "integer", "required": False, "description": "User ID"},
            "username": {"type": "string", "required": False, "description": "Username"},
            "delete_messages_days": {"type": "integer", "required": False, "description": "Days of messages to delete", "default": 7}
        },
        "keywords": ["softban", "clear user messages"]
    },
    "clear_reactions": {
        "name": "clear_reactions",
        "description": "Clears all reactions from a message.",
        "category": "Messaging",
        "parameters": {
            "message_id": {"type": "integer", "required": True, "description": "Message ID"},
            "channel": {"type": "string", "required": False, "description": "Channel name"}
        },
        "keywords": ["clear reactions", "remove all reactions"]
    },
    "edit_guild": {
        "name": "edit_guild",
        "description": "Edits server settings like name and description.",
        "category": "Server Management",
        "parameters": {
            "name": {"type": "string", "required": False, "description": "New server name"},
            "description": {"type": "string", "required": False, "description": "New server description"}
        },
        "keywords": ["edit server", "change server name"]
    },
    "query_server_info": {
        "name": "query_server_info",
        "description": "Get detailed info about the server.",
        "category": "Server Query",
        "parameters": {},
        "keywords": ["server info", "server stats"]
    },
    "query_channels": {
        "name": "query_channels",
        "description": "List all channels in the server.",
        "category": "Server Query",
        "parameters": {
            "type": {"type": "string", "required": False, "description": "text, voice, or category"}
        },
        "keywords": ["list channels", "show channels"]
    },
    "query_roles": {
        "name": "query_roles",
        "description": "List all roles in the server.",
        "category": "Server Query",
        "parameters": {},
        "keywords": ["list roles", "show roles"]
    },
    "query_members": {
        "name": "query_members",
        "description": "Search for members in the server.",
        "category": "Server Query",
        "parameters": {
            "query": {"type": "string", "required": False, "description": "Search query"},
            "limit": {"type": "integer", "required": False, "description": "Max results", "default": 20}
        },
        "keywords": ["search members", "list members"]
    },
    "query_member_details": {
        "name": "query_member_details",
        "description": "Get detailed info about a specific member.",
        "category": "Server Query",
        "parameters": {
            "user_id": {"type": "integer", "required": True, "description": "User ID"}
        },
        "keywords": ["user info", "member details"]
    },
    "query_economy_leaderboard": {
        "name": "query_economy_leaderboard",
        "description": "Get the top users by coins.",
        "category": "Server Query",
        "parameters": {
            "limit": {"type": "integer", "required": False, "description": "Max results", "default": 10}
        },
        "keywords": ["economy leaderboard", "top coins"]
    },
    "query_xp_leaderboard": {
        "name": "query_xp_leaderboard",
        "description": "Get the top users by XP.",
        "category": "Server Query",
        "parameters": {
            "limit": {"type": "integer", "required": False, "description": "Max results", "default": 10}
        },
        "keywords": ["xp leaderboard", "top levels"]
    },
    "query_pending_applications": {
        "name": "query_pending_applications",
        "description": "List pending staff applications.",
        "category": "Server Query",
        "parameters": {},
        "keywords": ["pending apps", "staff applications"]
    },
    "query_active_shifts": {
        "name": "query_active_shifts",
        "description": "List currently active staff shifts.",
        "category": "Server Query",
        "parameters": {},
        "keywords": ["active shifts", "staff shifts"]
    },
    "query_recent_messages": {
        "name": "query_recent_messages",
        "description": "Fetch recent messages from a channel.",
        "category": "Server Query",
        "parameters": {
            "channel_id": {"type": "integer", "required": True, "description": "Channel ID"},
            "limit": {"type": "integer", "required": False, "description": "Max results", "default": 10}
        },
        "keywords": ["recent messages", "fetch history"]
    },
    "giveaway_end": {
        "name": "giveaway_end",
        "description": "Ends a giveaway immediately and picks winners",
        "category": "Giveaways",
        "parameters": {
            "giveaway_id": {"type": "string", "required": True, "description": "ID of the giveaway to end"}
        },
        "keywords": ["end giveaway", "gend"]
    },
    "giveaway_reroll": {
        "name": "giveaway_reroll",
        "description": "Rerolls winners for an ended giveaway",
        "category": "Giveaways",
        "parameters": {
            "giveaway_id": {"type": "string", "required": True, "description": "ID of the giveaway to reroll"}
        },
        "keywords": ["reroll giveaway", "greroll"]
    },
    "giveaway_list": {
        "name": "giveaway_list",
        "description": "Lists all active giveaways in the server",
        "category": "Giveaways",
        "parameters": {},
        "keywords": ["list giveaways", "active giveaways", "glist"]
    },
    "send_notification": {
        "name": "send_notification",
        "description": "Send a notification message to a channel",
        "category": "Messaging",
        "parameters": {
            "channel": {"type": "string", "required": True, "description": "Channel name or ID"},
            "message": {"type": "string", "required": True, "description": "Notification message content"}
        },
        "keywords": ["notify", "notification", "alert", "send message", "announce"]
    },
    "create_task": {
        "name": "create_task",
        "description": "Create a scheduled background task",
        "category": "System",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Task name"},
            "cron": {"type": "string", "required": True, "description": "Cron expression for scheduling"},
            "handler": {"type": "string", "required": True, "description": "Handler function name"}
        },
        "keywords": ["schedule", "task", "cron", "background", "automate"]
    },
    "update_profile": {
        "name": "update_profile",
        "description": "Update user profile information",
        "category": "User Management",
        "parameters": {
            "user_id": {"type": "string", "required": True, "description": "User ID"},
            "field": {"type": "string", "required": True, "description": "Profile field to update"},
            "value": {"type": "any", "required": True, "description": "New value for the field"}
        },
        "keywords": ["profile", "update", "user info", "settings"]
    }
}


CATEGORY_GROUPS = {
    "Channel Management": ["create_channel", "create_voice_channel", "create_text_channel", "create_category",
                           "delete_channel", "edit_channel", "edit_channel_name", "move_channel", "clone_channel",
                           "set_topic", "slowmode", "lock_channel", "unlock_channel", "make_channel_private",
                           "make_category_private", "edit_channel_bitrate", "edit_channel_user_limit",
                           "follow_announcement_channel", "create_thread"],
    "Role Management": ["create_role", "delete_role", "edit_role", "edit_role_name", "change_role_color",
                       "add_role", "assign_role", "remove_role"],
    "Messaging": ["send_message", "send_embed", "send_dm", "reply_message", "announce", "poll",
                  "post_documentation", "create_thread", "pin_message", "unpin_message", "add_reaction",
                  "remove_reaction", "delete_message", "bulk_delete_messages", "send_notification"],
    "Moderation": ["kick_user", "ban_user", "timeout_user", "mute_user", "unmute_user", "deafen_user",
                   "set_nickname", "warn_user", "update_profile"],
    "Economy": ["give_points", "remove_points"],
    "Server Management": ["create_invite", "ping"],
    "Custom Commands": ["create_prefix_command", "delete_prefix_command"],
    "Automation": ["schedule_ai_action", "create_task"],
    "System Setup": ["setup_verification", "setup_tickets", "setup_applications", "setup_appeals",
                     "setup_moderation", "setup_logging", "setup_economy", "setup_leveling",
                     "setup_welcome", "setup_staff_system", "setup_trigger_role",
                     "create_verify_system", "create_tickets_system", "create_applications_system",
                     "create_appeals_system", "create_welcome_system", "create_staff_system",
                     "create_leveling_system", "create_economy_system"],
    "Permissions": ["allow_channel_permission", "deny_channel_permission", "allow_all_channels_for_role",
                     "deny_all_channels_for_role", "deny_category_for_role", "edit_channel_permissions"],
    "Events": ["create_scheduled_event"],
    "Meta": ["analyze_server_state"],
    "Giveaways": ["giveaway_end", "giveaway_reroll", "giveaway_list"],
    "Gamification": ["prestige", "dice", "flip", "slots", "trivia"],
    "Server Query": ["extract_online_users", "query_server_info", "query_channels", "query_roles", "query_members", "query_member_details", "query_economy_leaderboard", "query_xp_leaderboard", "query_pending_applications", "query_active_shifts", "query_recent_messages"]
}


ACTION_KEYWORDS = {}
for action_name, action_info in ACTION_CATALOG.items():
    for kw in action_info.get("keywords", []):
        if kw not in ACTION_KEYWORDS:
            ACTION_KEYWORDS[kw] = []
        ACTION_KEYWORDS[kw].append(action_name)
    for alias in action_info.get("aliases", []):
        if alias not in ACTION_KEYWORDS:
            ACTION_KEYWORDS[alias] = []
        ACTION_KEYWORDS[alias].append(action_name)


def get_action_catalog_json() -> str:
    """Returns the action catalog as formatted JSON."""
    return json.dumps(ACTION_CATALOG, indent=2)


def get_all_action_names() -> List[str]:
    """Returns list of all available action names."""
    return list(ACTION_CATALOG.keys())


def get_actions_by_category(category: str) -> List[str]:
    """Returns list of actions in a specific category."""
    return CATEGORY_GROUPS.get(category, [])


def get_all_categories() -> List[str]:
    """Returns list of all categories."""
    return list(CATEGORY_GROUPS.keys())


def get_action_info(action_name: str) -> Optional[Dict]:
    """Returns detailed info about an action."""
    return ACTION_CATALOG.get(action_name)


def resolve_user_request(user_request: str, context: Dict = None) -> List[Tuple[str, float, Dict]]:
    """
    Resolves a user request to the best matching actions.
    
    Args:
        user_request: The user's natural language request
        context: Optional context including guild info, user info, etc.
    
    Returns:
        List of (action_name, confidence, parameters) tuples sorted by confidence
    """
    request_lower = user_request.lower()
    request_clean = re.sub(r'[^\w\s]', ' ', request_lower)
    request_words = set(request_clean.split())
    
    results = []
    
    for action_name, action_info in ACTION_CATALOG.items():
        score = 0.0
        matched_params = {}
        
        keywords = action_info.get("keywords", [])
        aliases = action_info.get("aliases", [])
        
        # Boost based on exact keyword/alias matches in the request
        for kw in keywords + aliases:
            if kw in request_lower:
                # Stronger boost for longer keyword matches
                score += 1.0 + (len(kw.split()) * 0.5)
                if kw == request_lower.strip():
                    score += 2.0 # Huge boost for exact match of the whole request
        
        # Word-level partial matches
        partial_matches = 0
        for word in request_words:
            if len(word) < 3: continue
            for kw in keywords:
                if word in kw:
                    partial_matches += 0.2

        score += min(partial_matches, 1.0)
        
        # Intent-based boosting (applies even without explicit context)
        if "channel" in request_lower:
            if "channel" in action_name or action_info.get("category") == "Channel Management":
                score += 0.5
        
        if "role" in request_lower:
            if "role" in action_name or action_info.get("category") == "Role Management":
                score += 0.8 # Stronger boost for role intent

        if "message" in request_lower or "say" in request_lower or "tell" in request_lower:
            if action_info.get("category") == "Messaging":
                score += 0.4

        if context:
            if "channel" in request_lower and action_name.startswith("create_"):
                if "type" in action_info.get("parameters", {}):
                    if "voice" in request_lower:
                        if "voice" in action_name:
                            score += 0.3
                    elif "text" in request_lower:
                        if "text" in action_name:
                            score += 0.3
            
            if "user" in request_lower or "member" in request_lower:
                if "user_id" in action_info.get("parameters", {}) or "username" in action_info.get("parameters", {}):
                    if any(mod in action_name for mod in ["kick", "ban", "timeout", "mute", "warn", "role", "nickname"]):
                        score += 0.2
            
            if "role" in request_lower:
                if action_name in ["create_role", "delete_role", "edit_role", "add_role", "remove_role"]:
                    score += 0.3
        
        if score > 0:
            results.append((action_name, score, matched_params))
    
    results.sort(key=lambda x: x[1], reverse=True)
    
    return results[:5]


def extract_parameters_from_request(user_request: str, action_name: str) -> Dict[str, Any]:
    """Extracts parameters from user request based on action schema."""
    action_info = ACTION_CATALOG.get(action_name)
    if not action_info:
        return {}
    
    params = {}
    request_lower = user_request.lower()
    
    param_specs = action_info.get("parameters", {})
    
    if "name" in param_specs:
        name_match = re.search(r'(?:named?|called?|title[:\s]+)(["\']?)([\w-]+)\1', request_lower)
        if name_match:
            params["name"] = name_match.group(2)
        else:
            words = user_request.split()
            for i, w in enumerate(words):
                if w.lower() in ["channel", "role", "user", "called", "named"]:
                    if i + 1 < len(words):
                        params["name"] = words[i + 1].strip('"\'-')
                        break
    
    if "channel" in param_specs or "channel_name" in param_specs:
        channel_match = re.search(r'in\s+([a-zA-Z0-9-_]+)\s+channel', request_lower)
        if channel_match:
            params["channel"] = channel_match.group(1)
        else:
            for kw in ["general", "chat", "log", "mod", "staff", "welcome", "announcements"]:
                if kw in request_lower:
                    params["channel"] = kw
                    break
    
    if "user_id" in param_specs or "username" in param_specs:
        mention_match = re.search(r'<@!?(\d+)>', user_request)
        if mention_match:
            params["user_id"] = int(mention_match.group(1))
        else:
            user_match = re.search(r'(?:user|member|person)\s+(?:named?|called?)\s+([a-zA-Z0-9_]+)', request_lower, re.IGNORECASE)
            if user_match:
                params["username"] = user_match.group(1)
    
    if "role_name" in param_specs:
        role_match = re.search(r'(?:role|named)\s+([a-zA-Z0-9_]+)', request_lower)
        if role_match:
            params["role_name"] = role_match.group(1)
    
    if "content" in param_specs:
        content_match = re.search(r'(?:saying|with content|that says)[:\s]+["\'](.+?)["\']', request_lower)
        if content_match:
            params["content"] = content_match.group(1)
        else:
            quote_match = re.search(r'"([^"]+)"', user_request)
            if quote_match:
                params["content"] = quote_match.group(1)
    
    if "reason" in param_specs:
        reason_match = re.search(r'(?:because|reason|for)\s+["\']?(.+?)["\']?(?:\s+|$)', request_lower, re.IGNORECASE)
        if reason_match:
            params["reason"] = reason_match.group(1).strip()
    
    if "duration" in param_specs:
        duration_match = re.search(r'(\d+)\s*(?:minute|min|hour|hr|second|sec)', request_lower)
        if duration_match:
            value = int(duration_match.group(1))
            unit = duration_match.group(2)
            if unit.startswith("minute") or unit.startswith("min"):
                params["duration"] = value * 60
            elif unit.startswith("hour") or unit.startswith("hr"):
                params["duration"] = value * 3600
            else:
                params["duration"] = value
    
    if "points" in param_specs:
        points_match = re.search(r'(\d+)\s*(?:point|coin|coin|point)', request_lower)
        if points_match:
            params["points"] = int(points_match.group(1))
    
    if "color" in param_specs:
        color_match = re.search(r'(?:color|colour)\s+(#[0-9a-fA-F]+|\w+)', request_lower)
        if color_match:
            params["color"] = color_match.group(1)
    
    return params


class ActionResolver:
    """High-level interface for resolving and executing actions."""
    
    def __init__(self):
        self.catalog = ACTION_CATALOG
    
    def resolve(self, user_request: str, context: Dict = None) -> List[Dict]:
        """
        Resolves a user request to actionable items.
        
        Returns list of dicts with action_name, confidence, and parameters.
        """
        results = resolve_user_request(user_request, context)
        
        resolved = []
        for action_name, confidence, _ in results:
            action_info = self.catalog.get(action_name, {})
            params = extract_parameters_from_request(user_request, action_name)
            
            resolved.append({
                "action": action_name,
                "confidence": confidence,
                "parameters": params,
                "description": action_info.get("description", ""),
                "category": action_info.get("category", "Unknown")
            })
        
        return resolved
    
    def get_best_match(self, user_request: str, context: Dict = None) -> Optional[Dict]:
        """Returns the best matching action or None."""
        resolved = self.resolve(user_request, context)
        return resolved[0] if resolved else None
    
    def get_catalog(self) -> Dict:
        """Returns the full action catalog."""
        return self.catalog
    
    def get_action_schema(self, action_name: str) -> Optional[Dict]:
        """Returns the schema for a specific action."""
        return self.catalog.get(action_name)
    
    def validate_parameters(self, action_name: str, params: Dict) -> Tuple[bool, List[str]]:
        """Validates parameters against action schema. Returns (valid, errors)."""
        action_info = self.catalog.get(action_name)
        if not action_info:
            return False, [f"Unknown action: {action_name}"]
        
        errors = []
        param_specs = action_info.get("parameters", {})
        
        for param_name, spec in param_specs.items():
            if spec.get("required", False) and param_name not in params:
                errors.append(f"Missing required parameter: {param_name}")
        
        return len(errors) == 0, errors


action_resolver = ActionResolver()
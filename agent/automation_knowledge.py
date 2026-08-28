"""Miro Agent — Automation Knowledge Base.

This module is the single source of truth the agent reads when it is asked to
build an automation. It is intentionally free of Discord imports so it can be
used both by the planner prompt renderer and by the runtime context injector
without pulling in the whole bot.

Two things live here:

1. ``TRIGGER_CATALOG`` / ``ACTION_SUMMARY`` — a structured, machine + human
   readable description of every trigger/event and every action the bot can
   automate. The planner prompt and ``build_automation_context`` render this so
   the model *knows what is possible* instead of guessing from a stale prose
   list baked into ``agent/planner.py``.

2. ``parse_natural_language_schedule`` — turns the kind of phrases a human
   actually types ("every weekday at 9", "daily at 9pm", "in 2 hours",
   "next Monday 8am") into a schedule dict / cron string / reminder seconds
   that ``actions.create_automation`` already understands.
"""

from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Trigger / event catalog                                                     #
# --------------------------------------------------------------------------- #
# Each entry documents:
#   label      — human friendly name
#   event      — the internal event name used by ActionHandler.fire_event
#   description— what causes it to fire
#   required   — params that MUST be provided
#   optional   — params that tune behaviour
#   example    — a ready-to-use tool call fragment
TRIGGER_CATALOG: Dict[str, Dict[str, Any]] = {
    "scheduled_task": {
        "label": "Scheduled (time based)",
        "event": "scheduled_task",
        "description": "Fires on a recurring schedule using a cron expression or an interval object.",
        "required": ["schedule or cron", "action or actions"],
        "optional": ["channel_id", "response", "schedule object"],
        "actions": ["send_message", "send_embed", "announce", "give_points", "assign_role"],
        "example": 'create_automation {type:"scheduled_task", name:"daily-tip", cron:"0 12 * * *", action_type:"send_message", channel_id:<id>, response:"Tip of the day!"}',
    },
    "event_trigger": {
        "label": "Event Trigger (generic)",
        "event": "event_trigger",
        "description": "Generic wrapper for event-driven automations. Use event: member_joined|member_left|message_contains|reaction_added|voice_joined. For message_contains, keywords + actions are required and can run multi-step sequences.",
        "required": ["event", "actions"],
        "optional": ["keywords", "filters (channel_id, role_id, emoji)", "match_type", "channel_id"],
        "example": 'create_automation {type:"event_trigger", event:"message_contains", name:"support-ping", keywords:["help","support"], channel_id:<id>, actions:[{name:"send_message", parameters:{content:"On it!"}}, {name:"assign_role", parameters:{role_id:<id>}}]}',
    },
    "message_contains": {
        "label": "Keyword in chat (multi-step)",
        "event": "message_contains",
        "description": "Fires when any message contains a keyword/regex. Unlike the simple auto_responder this can run a SEQUENCE of actions (reply + assign role + notify staff).",
        "required": ["keywords", "actions"],
        "optional": ["match_type (contains|exact|starts_with|ends_with|regex)", "channel_id", "role_id filter"],
        "example": 'create_automation {type:"event_trigger", event:"message_contains", name:"support-ping", keywords:["help","support"], channel_id:<id>, actions:[{name:"send_message", parameters:{content:"On it!"}}, {name:"assign_role", parameters:{role_id:<id>}}]}',
    },
    "member_joined": {
        "label": "Member joins",
        "event": "member_joined",
        "description": "Fires when a new member joins the server. Perfect for welcome messages + auto role.",
        "required": ["actions"],
        "optional": ["filters.role_id (only if the new member already has a role)"],
        "example": 'create_automation {type:"event_trigger", event:"member_joined", name:"welcome", channel_id:<id>, actions:[{name:"send_message", parameters:{content:"Welcome {user}!"}}, {name:"assign_role", parameters:{role_id:<id>}}]}',
    },
    "member_left": {
        "label": "Member leaves",
        "event": "member_left",
        "description": "Fires when a member leaves the server. Use for goodbye messages or logging.",
        "required": ["actions"],
        "optional": ["channel_id"],
        "example": 'create_automation {type:"event_trigger", event:"member_left", name:"goodbye", channel_id:<id>, actions:[{name:"send_message", parameters:{content:"{user} left the server."}}]}',
    },
    "reaction_added": {
        "label": "Reaction added",
        "event": "reaction_added",
        "description": "Fires when a reaction is added to a message. Use for role-reaction panels or alerts.",
        "required": ["actions"],
        "optional": ["filters.emoji", "channel_id"],
        "example": 'create_automation {type:"event_trigger", event:"reaction_added", name:"react-role", filters:{emoji:"✅"}, actions:[{name:"assign_role", parameters:{role_id:<id>}}]}',
    },
    "voice_joined": {
        "label": "Member joins voice",
        "event": "voice_joined",
        "description": "Fires when a member connects to a voice channel.",
        "required": ["actions"],
        "optional": ["channel_id"],
        "example": 'create_automation {type:"event_trigger", event:"voice_joined", name:"vc-alert", channel_id:<id>, actions:[{name:"send_message", parameters:{content:"{user} joined voice!"}}]}',
    },
    "reminder": {
        "label": "One-shot reminder",
        "event": "reminder",
        "description": "Fires once after a duration (10s .. 30 days).",
        "required": ["duration (seconds)", "response"],
        "optional": ["channel_id", "user_id"],
        "example": 'create_automation {type:"reminder", name:"standup", duration:3600, response:"Standup time!", channel_id:<id>}',
    },
    "trigger_role": {
        "label": "Keyword -> role (presence based)",
        "event": "trigger_role",
        "description": "Assigns a role when a member types a keyword (role is removed when they go offline).",
        "required": ["keywords", "role_id or role_name"],
        "optional": ["response"],
        "example": 'create_automation {type:"trigger_role", name:"minecraft-role", keywords:["minecraft"], role_id:<id>, response:"Gave you {role}!"}',
    },
    "auto_responder": {
        "label": "Keyword auto-reply (simple)",
        "event": "auto_responder",
        "description": "Replies to a message that contains a keyword. Single response only (use message_contains for multi-step).",
        "required": ["keywords", "response"],
        "optional": ["match_type", "response_type (text|embed|random|reaction)", "channel_id", "delete_trigger"],
        "example": 'create_automation {type:"auto_responder", name:"greet", keywords:["hello","hi"], response:"Hey {user}!"}',
    },
    "bulk_create_automations": {
        "label": "Bulk Create Automations (25 at once)",
        "event": "bulk",
        "description": "Create up to 25 automations in ONE call. Prefer over 25 single create_automation calls. Honors 100/guild quota. Partial success allowed.",
        "required": ["automations: [{type, name, ...}]"],
        "optional": ["each item follows create_automation schema"],
        "example": 'bulk_create_automations {automations:[{type:"scheduled_task", name:"tip-1", cron:"0 9 * * *", response:"Hi"}, {type:"auto_responder", name:"greet", keywords:["hi"], response:"Hey!"}]}',
    },
    "lifecycle": {
        "label": "Automation Lifecycle",
        "event": "lifecycle",
        "description": "Manage existing automations: pause, resume, update schedule/response, run now, bulk pause/delete by type, auto-pause after 10 failures.",
        "required": ["name or names|all+type"],
        "optional": ["cron, schedule, response, channel_id for update; type filter for bulk"],
        "example": 'pause_automation {name:"daily-tip"} / bulk_pause_automations {all:true, type:"auto_responder"} / run_automation_now {name:"daily-tip"} / update_automation {name:"daily-tip", cron:"0 10 * * *"}',
    },
    "prefix_command": {
        "label": "Prefix Command (!command)",
        "event": "prefix_command",
        "description": "Create a LIVE custom !command that works instantly without restart. Supports aliases, cooldown, permission gating.",
        "required": ["name", "code (response text or JSON)"],
        "optional": ["aliases (up to 5), cooldown_seconds, required_permission (everyone|mod|admin), description"],
        "example": 'create_prefix_command {name:"rules", code:"Read the rules at #rules!", aliases:["rulez"], cooldown_seconds:5, required_permission:"everyone"}',
    },
    "bulk_prefix_commands": {
        "label": "Bulk Prefix Commands (25 at once)",
        "event": "bulk",
        "description": "Create up to 25 prefix commands in ONE call. Each item: {name, code, aliases, cooldown_seconds, required_permission, description}. Prefer over repeated single calls.",
        "required": ["commands: [{name, code}]"],
        "optional": ["aliases, cooldown_seconds, required_permission, description per item"],
        "example": 'bulk_create_prefix_commands {commands:[{name:"faq", code:"FAQ here"}, {name:"links", code:"Links", aliases:["social"]}]}',
    },
}

# Actions that can be used inside an automation ``actions`` list. Anything in
# ActionHandler.ALLOWED_ACTIONS also works; this is the curated short-list the
# model is told about so it does not have to enumerate 280 actions.
ACTION_SUMMARY: List[str] = [
    "send_message (content, channel_id) — post a message",
    "send_embed (title, content, channel_id) — post an embed",
    "announce (title, content, channel_id) — post an announcement embed",
    "poll (question, options[], channel_id) — create a poll",
    "send_notification (message, channel_id) — highlight a notification",
    "give_points (user_id, points) — award economy coins",
    "assign_role (user_id, role_id) — assign a role",
    "remove_role (user_id, role_id) — remove a role",
    "create_channel (name) — create a text channel",
    "bulk_create_automations (automations[]) — create 25 automations at once (prefer for bulk)",
    "bulk_create_prefix_commands (commands[]) — create 25 prefix commands at once",
    "pause_automation / resume_automation / run_automation_now — lifecycle",
    "Any other Miro action name from ALLOWED_ACTIONS (e.g. timeout_user, ban_user, send_dm).",
]

# Worked examples shown to the model so it learns the *shape* of good requests.
EXAMPLE_REQUESTS: List[str] = [
    '"remind me daily at 9am to stand up" -> scheduled_task with schedule:{daily_at:"09:00"}',
    '"every 15 minutes post a tip in #tips" -> scheduled_task schedule:{every_minutes:15}',
    '"when someone says hi reply Hey and give them the Member role" -> event_trigger event:message_contains, keywords:["hi"], actions:[send_message, assign_role]',
    '"welcome new members in #welcome and assign the Guest role" -> event_trigger event:member_joined, actions:[send_message, assign_role]',
    '"when a message contains refund ping @staff" -> event_trigger event:message_contains, keywords:["refund"], actions:[send_message]',
    '"give the Gamer role to anyone who types minecraft" -> trigger_role keywords:["minecraft"] role_id:<id>',
    '"every Monday at 8am post the weekly update" -> scheduled_task cron:"0 8 * * 1"',
    '"in 2 hours remind me to take a break" -> reminder duration:7200',
    # Bulk & prefix 1000x examples
    '"make 5 automations for daily tips at 9am" -> bulk_create_automations automations: 5x scheduled_task schedule:{daily_at:"09:00"}',
    '"create 3 prefix commands !rules !faq !links with aliases" -> bulk_create_prefix_commands commands:[{name:"rules", code:"..."}, {name:"faq", code:"..."}]',
    '"pause all auto_responders" -> bulk_pause_automations {all:true, type:"auto_responder"}',
    '"give me 5 commands !a !b !c !d !e" -> bulk_create_prefix_commands 5 items, cooldown_seconds:5, permission:everyone',
    '"when someone says refund in #support ping staff and log" -> event_trigger event:message_contains keywords:["refund"] channel_id:<id> actions:[send_message, send_message]',
    '"reaction with ✅ give Verified role" -> event_trigger event:reaction_added filters:{emoji:"✅"} actions:[assign_role]',
    '"in 30 minutes remind me tea time in #general" -> reminder duration:1800 channel_id:<id>',
    '"update daily-tip to 10am" -> update_automation {name:"daily-tip", cron:"0 10 * * *"}',
]


def render_catalog() -> str:
    """Return the catalog as a compact text block for injection into prompts."""
    lines: List[str] = []
    lines.append("=== AUTOMATION TRIGGERS (what can start an automation) ===")
    for key, t in TRIGGER_CATALOG.items():
        lines.append(f"- {key} ({t['label']}): {t['description']}")
        lines.append(f"    required: {', '.join(t['required'])}")
        if t["optional"]:
            lines.append(f"    optional: {', '.join(t['optional'])}")
        lines.append(f"    example: {t['example']}")
    lines.append("")
    lines.append("QUOTAS & LIMITS: automations 100/guild, prefix commands 100/guild, bulk 25 per call, auto-pause after 10 failures, use bulk for scale.")
    lines.append("")
    lines.append("=== AUTOMATION ACTIONS (what an automation can DO) ===")
    for a in ACTION_SUMMARY:
        lines.append(f"- {a}")
    lines.append("")
    lines.append("=== EXAMPLE REQUEST -> AUTOMATION MAPPINGS ===")
    for e in EXAMPLE_REQUESTS:
        lines.append(f"- {e}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Natural language schedule parsing                                           #
# --------------------------------------------------------------------------- #
import re as _re

_WEEKDAYS = {
    "mon": 1, "monday": 1,
    "tue": 2, "tues": 2, "tuesday": 2,
    "wed": 3, "weds": 3, "wednesday": 3,
    "thu": 4, "thur": 4, "thurs": 4, "thursday": 4,
    "fri": 5, "friday": 5,
    "sat": 6, "saturday": 6,
    "sun": 7, "sunday": 7,
}


def _parse_time(tok: str) -> Optional[str]:
    """Parse a time token like '9', '9am', '9:30pm', '21:00' -> 'HH:MM' (24h)."""
    tok = tok.strip().lower()
    if not tok:
        return None
    ampm = None
    if tok.endswith("am"):
        ampm = "am"; tok = tok[:-2]
    elif tok.endswith("pm"):
        ampm = "pm"; tok = tok[:-2]
    m = _re.match(r"(\d{1,2})(?::(\d{1,2}))?", tok)
    if not m:
        return None
    h = int(m.group(1)); mm = int(m.group(2) or 0)
    if ampm == "am":
        if h == 12:
            h = 0
    elif ampm == "pm":
        if h != 12:
            h += 12
    if h > 23 or mm > 59:
        return None
    return f"{h:02d}:{mm:02d}"


def parse_natural_language_schedule(text: str) -> Optional[Dict[str, Any]]:
    """Convert a natural-language time phrase into a schedule/cron/reminder dict.

    Returns a dict that ``actions.create_automation`` understands:
      - interval schedule:       {"every_minutes": N} / {"every_hours": N} / {"every_days": N}
      - daily schedule:          {"daily_at": "HH:MM"}
      - weekly schedule:         {"weekly_on": "Mon", "at": "HH:MM"}
      - cron string:             {"cron": "0 9 * * 1-5"}   (also raw "cron": ...)
      - relative reminder:       {"_reminder_seconds": N}
    Returns ``None`` when nothing schedulable is recognised.
    """
    if not text or not isinstance(text, str):
        return None
    s = text.strip().lower()

    # Raw cron passthrough: "0 12 * * *" or "*/15 * * * *"
    m = _re.search(r"(\*/\d+\s+\*\s+\*\s+\*\s+\*|\d+\s+\d+\s+\*\s+\*\s+\*|\d+\s+\*\s+\*\s+\*\s+\*)", s)
    if m and _re.match(r"^[\*\d\/\-\,\s]+$", m.group(1).strip()):
        # Validate 5-field cron roughly
        fields = m.group(1).strip().split()
        if len(fields) == 5:
            return {"cron": m.group(1).strip()}

    # Relative: "in 2 hours", "in 30 minutes", "in 1 day", "in 2 weeks", "in 10 seconds"
    m = _re.search(r"in\s+(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours|day|days|week|weeks)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("sec"):
            secs = n
        elif unit.startswith("min"):
            secs = n * 60
        elif unit.startswith("hr") or unit.startswith("hour"):
            secs = n * 3600
        elif unit.startswith("day"):
            secs = n * 86400
        elif unit.startswith("week"):
            secs = n * 604800
        else:
            secs = n * 60
        return {"_reminder_seconds": secs}

    # "every N minutes / hours / days / seconds"
    m = _re.search(r"every\s+(\d+)\s*(sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours|day|days)", s)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        if unit.startswith("sec"):
            # cron can't do seconds, treat as minutes with floor 1
            return {"every_minutes": max(1, n // 60 if n >= 60 else 1)}
        if unit.startswith("min"):
            return {"every_minutes": n}
        if unit.startswith("h"):
            return {"every_hours": n}
        if unit.startswith("d"):
            return {"every_days": n}

    # "every weekday at X" / "weekdays at X" -> cron Mon-Fri
    if ("weekday" in s) and True:
        time_match = _re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", s)
        tod = _parse_time(time_match.group(1)) if time_match else None
        if tod:
            h, mm = tod.split(":")
            return {"cron": f"{mm} {h} * * 1-5"}

    # "monthly" / "every month at X"
    if "monthly" in s:
        time_match = _re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", s)
        tod = _parse_time(time_match.group(1)) if time_match else "09:00"
        h, mm = tod.split(":")
        # monthly on day 1
        return {"cron": f"{mm} {h} 1 * *"}

    # time-of-day extraction (handles '9am', '9:30pm', '21:00', '9')
    time_match = _re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", s)
    tod = _parse_time(time_match.group(1)) if time_match else None

    # "daily at X" / "every day at X"
    if ("daily" in s or "every day" in s) and tod:
        return {"daily_at": tod}

    # "weekly on <day> at X" / "every <day> at X"
    day = None
    for dname, dnum in _WEEKDAYS.items():
        if _re.search(r"\b" + _re.escape(dname) + r"\b", s):
            day = dname.capitalize()[:3]
            break
    if day and tod:
        return {"weekly_on": day, "at": tod}

    # Bare time with no other keyword (e.g. "9am") -> assume daily
    if tod and not any(k in s for k in ("every", "daily", "weekly", "hour", "min", "in ", "sec", "monthly", "weekday")):
        return {"daily_at": tod}

    return None

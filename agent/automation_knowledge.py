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
        "optional": ["channel_id", "response"],
        "example": 'create_automation {type:"scheduled_task", name:"daily-tip", cron:"0 12 * * *", action_type:"send_message", channel_id:<id>, response:"Tip of the day!"}',
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
      - interval schedule:       {"every_minutes": N} / {"every_hours": N}
      - daily schedule:          {"daily_at": "HH:MM"}
      - weekly schedule:         {"weekly_on": "Mon", "at": "HH:MM"}
      - cron string:             {"cron": "0 9 * * 1-5"}   (also raw "cron": ...)
      - relative reminder:       {"_reminder_seconds": N}
    Returns ``None`` when nothing schedulable is recognised.
    """
    if not text or not isinstance(text, str):
        return None
    s = text.strip().lower()

    # Relative: "in 2 hours", "in 30 minutes"
    m = _re.search(r"in\s+(\d+)\s*(min|mins|minute|minutes|hr|hrs|hour|hours)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        secs = n * 60 if unit.startswith("min") else n * 3600
        return {"_reminder_seconds": secs}

    # "every N minutes / hours"
    m = _re.search(r"every\s+(\d+)\s*(min|mins|minute|minutes|hr|hrs|hour|hours)", s)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        return {"every_minutes": n} if unit.startswith("min") else {"every_hours": n}

    # time-of-day extraction (handles '9am', '9:30pm', '21:00', '9')
    time_match = _re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", s)
    tod = _parse_time(time_match.group(1)) if time_match else None

    # "every weekday at X" -> cron Mon-Fri
    if "weekday" in s and tod:
        h, mm = tod.split(":")
        return {"cron": f"{mm} {h} * * 1-5"}

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
    if tod and not any(k in s for k in ("every", "daily", "weekly", "hour", "min", "in ")):
        return {"daily_at": tod}

    return None

"""
V10 System Capability Contract — canonical source of truth for /configpanel.

Every subsystem declares settings, resources, actions, diagnostics, runtime_owner.
GroupPanelView and SystemInstaller read this instead of hardcoded assumptions.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class SettingDef:
    key: str
    label: str
    type: str  # channel | role | text | number | boolean | enum
    description: str = ""
    required: bool = False
    default: Any = None


@dataclass
class ResourceDef:
    key: str
    kind: str  # channel | role | message
    description: str = ""
    required: bool = True


@dataclass
class SystemCapability:
    key: str
    group: str
    label: str
    runtime_owner: str
    config_key: str
    settings: List[SettingDef] = field(default_factory=list)
    resources: List[ResourceDef] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
    supports_toggle: bool = True


# Canonical registry — Phase 1 fully defined for verification; others scaffolded
# and expanded progressively. SYSTEM_GROUPS remains compat shim.
CAPABILITIES: Dict[str, SystemCapability] = {}


def _reg(cap: SystemCapability):
    CAPABILITIES[cap.key] = cap
    return cap


_reg(SystemCapability(
    key="verification", group="member_management", label="Verification",
    runtime_owner="VerificationSystem", config_key="verification_config",
    settings=[
        SettingDef("verify_channel", "Verify Channel", "channel", "Channel for Verify Me panel", False),
        SettingDef("verified_role", "Verified Role", "role", "Role given after verification", False),
        SettingDef("unverified_role", "Unverified Role", "role", "Role for unverified members", False),
        SettingDef("min_account_age_days", "Min Account Age (days)", "number", "0-30, 0 disables", False, 0),
        SettingDef("kick_new_accounts", "Kick New Accounts", "boolean", "Kick accounts younger than threshold", False, False),
    ],
    resources=[
        ResourceDef("verify_channel", "channel", "Public verify channel", True),
        ResourceDef("verified_role", "role", "Verified member role", True),
        ResourceDef("unverified_role", "role", "Unverified role", True),
        ResourceDef("panel_message", "message", "Verification panel message", False),
    ],
    actions=["enable","disable","open_channel","post_panel","repost_panel","repair","test"],
    diagnostics=["runtime","channel","roles","permissions","hierarchy","view","handler"],
))

_reg(SystemCapability(
    key="welcome", group="member_management", label="Welcome",
    runtime_owner="WelcomeLeaveSystem", config_key="welcome_leave_config",
    settings=[
        SettingDef("welcome_channel", "Welcome Channel", "channel", "", False),
        SettingDef("welcome_message", "Welcome Message", "text", "Supports {user} {server}", False),
    ],
    resources=[ResourceDef("welcome_channel","channel","",False)],
    actions=["open_channel","post_test","repair","test"],
))

_reg(SystemCapability(
    key="leave", group="member_management", label="Leave",
    runtime_owner="WelcomeLeaveSystem", config_key="welcome_leave_config",
    settings=[SettingDef("leave_channel","Leave Channel","channel","",False)],
    resources=[ResourceDef("leave_channel","channel","",False)],
    actions=["open_channel","repair","test"],
))

# Phase 2 enriched stubs — verification remains reference, others now declare real resources/actions
for _k, _g, _lbl, _owner, _cfg in [
    ("economy","progression","Economy","EconomySystem","economy_config"),
    ("leveling","progression","Leveling","LevelingSystem","leveling_config"),
    ("shop","progression","Shop","Shop","shop_config"),
    ("gamification","progression","Gamification","AdaptiveGamification","gamification_config"),
    ("tournaments","progression","Tournaments","TournamentSystem","tournament_settings"),
    ("events","progression","Events","EventScheduler","event_settings"),
    ("tickets","tickets","Tickets","TicketSystem","tickets_config"),
    ("suggestions","suggestions","Suggestions","SuggestionSystem","suggestions_config"),
    ("giveaways","giveaways","Giveaways","GiveawaySystem","giveaways_config"),
    ("announcements","communications","Announcements","AnnouncementSystem","announcements_config"),
    ("reminders","communications","Reminders","ReminderSystem","reminders_config"),
    ("modmail","communications","Modmail","ModmailSystem","modmail_config"),
    ("auto_publisher","communications","Auto-Publisher","AutoPublisher","auto_publisher_settings"),
    ("anti_raid","anti_raid","Anti-Raid","AntiRaidSystem","anti_raid_config"),
    ("guardian","anti_raid","Guardian","GuardianSystem","guardian_config"),
    ("automod","moderation","Auto-Mod","AutoModSystem","automod_config"),
    ("warnings","moderation","Warnings","WarningSystem","warning_config"),
    ("moderation","moderation","Moderation","ModerationSystem","moderation_config"),
    ("appeals","moderation","Appeals","AppealSystem","appeals_config"),
    ("event_logging","moderation","Event Logging","LoggingSystem","logging_config"),
    ("mod_log","moderation","Mod-Log","ModLoggingSystem","mod_log_config"),
    ("auto_responder","automation","Auto-Responder","AutoResponderSystem","auto_responder_config"),
    ("reaction_roles","automation","Reaction Roles","ReactionRoleSystem","reaction_roles"),
    ("starboard","automation","Starboard","StarboardSystem","starboard_config"),
    ("reaction_menus","automation","Reaction Menus","ReactionMenuSystem","reaction_menus_config"),
    ("role_buttons","automation","Role Buttons","RoleButtonSystem","role_buttons_config"),
    ("trigger_roles","automation","Trigger Roles","TriggerRoles","trigger_roles"),
    ("ai","ai","AI Engine","AIClient","ai_config"),
    ("ai_chat","ai","AI Chat","AIChatSystem","ai_chat_settings"),
    ("community_health","ai","Community Health","CommunityHealth","community_health_config"),
    ("conflict_resolution","ai","Conflict Resolution","ConflictResolution","conflict_resolution_config"),
    ("content_generator","ai","Content Generator","ContentGenerator","content_settings"),
    ("staff_shifts","staff_management","Staff Shifts","StaffShiftSystem","staff_shifts_config"),
    ("staff_reviews","staff_management","Staff Reviews","StaffReviewSystem","staff_reviews_config"),
    ("staff_promo","staff_management","Staff Promotions","StaffPromotionSystem","staff_promo_config"),
    ("applications","staff_management","Applications","ApplicationSystem","application_config"),
]:
    if _k not in CAPABILITIES:
        _reg(SystemCapability(key=_k, group=_g, label=_lbl, runtime_owner=_owner, config_key=_cfg))

# Enrich priority systems beyond verification
for _cap in [CAPABILITIES.get(k) for k in ("tickets","suggestions","giveaways","auto_responder","economy")]:
    if _cap:
        _cap.resources = _cap.resources or []
        _cap.actions = list(set(_cap.actions + ["enable","disable","repair","test","open_channel"]))
        _cap.diagnostics = list(set(_cap.diagnostics + ["runtime","channel","permissions"]))

# Tickets detailed
if CAPABILITIES.get("tickets"):
    t = CAPABILITIES["tickets"]
    t.settings = [SettingDef("ticket_category","Ticket Category","channel","Category for private tickets",False),
                  SettingDef("log_channel","Log Channel","channel","",False)]
    t.resources = [ResourceDef("ticket_category","channel","",True), ResourceDef("log_channel","channel","",False), ResourceDef("panel_message","message","Ticket panel",False)]
    t.actions = ["enable","disable","open_channel","post_panel","repost_panel","repair","test","close_stale"]

# Auto-responder detailed
if CAPABILITIES.get("auto_responder"):
    ar = CAPABILITIES["auto_responder"]
    ar.settings = [SettingDef("cooldown","Cooldown (s)","number","Per-user cooldown",False,5)]
    ar.resources = [ResourceDef("panel_message","message","AutoResponder panel",False)]
    ar.actions = ["enable","disable","add_response","manage_responses","repair","test"]

def get_capability(key: str) -> Optional[SystemCapability]:
    return CAPABILITIES.get(key)

def all_capabilities():
    return CAPABILITIES.values()

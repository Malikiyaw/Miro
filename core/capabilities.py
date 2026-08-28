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

# Phase 3: full blueprints for remaining systems (§6-42)
def _fill_cap(key, settings, resources, actions):
    c = CAPABILITIES.get(key)
    if c:
        c.settings = settings
        c.resources = resources
        c.actions = actions
        c.diagnostics = ["runtime","channel","permissions","view","handler"]
    return c

_fill_cap("welcome", [SettingDef("welcome_channel","Welcome Channel","channel","",False), SettingDef("welcome_message","Welcome Message","text","Supports {user} {server}",False), SettingDef("welcome_dm","DM Welcome","boolean","Send DM on join",False,False)], [ResourceDef("welcome_channel","channel","",False)], ["enable","disable","open_channel","post_test","repair","test"])
_fill_cap("leveling", [SettingDef("xp_per_message","XP / message","number","5-50",False,10), SettingDef("message_cooldown","Cooldown (s)","number","",False,60), SettingDef("announce_channel","Announcement Channel","channel","",False)], [ResourceDef("announce_channel","channel","",False)], ["enable","disable","open_channel","test_xp","open_leaderboard","repair","test"])
_fill_cap("economy", [SettingDef("daily_amount","Daily Amount","number","",False,100), SettingDef("coin_emoji","Coin Emoji","text","",False,"💰"), SettingDef("work_cooldown","Work Cooldown (s)","number","",False,3600)], [ResourceDef("shop_channel","channel","Shop display",False)], ["enable","disable","grant_test","test_daily","open_shop","repair","test"])
_fill_cap("shop", [SettingDef("currency","Currency","text","",False,"coins")], [ResourceDef("shop_channel","channel","",False)], ["add_item","manage_items","edit_item","delete_item","test_purchase","repair"])
_fill_cap("gamification", [SettingDef("quest_cooldown","Quest Cooldown","number","",False,86400)], [], ["enable","create_quest","active_quests","repair","test"])
_fill_cap("tournaments", [SettingDef("tournament_channel","Tournament Channel","channel","",False)], [ResourceDef("tournament_channel","channel","",False)], ["create_tournament","active_tournaments","manage_participants","repair","test"])
_fill_cap("events", [SettingDef("announcement_channel","Announcement Channel","channel","",False)], [ResourceDef("announcement_channel","channel","",False)], ["create_event","upcoming_events","edit","delete","repair","test"])
_fill_cap("announcements", [SettingDef("announcement_channel","Announcement Channel","channel","",False), SettingDef("approval_channel","Approval Channel","channel","",False)], [ResourceDef("announcement_channel","channel","",True)], ["enable","disable","open_channel","create_announcement","preview","repair","test"])
_fill_cap("reminders", [SettingDef("default_advance","Default Advance","text","e.g. 10m",False,"10m")], [], ["create_reminder","active_reminders","edit","delete","pause","resume","test"])
_fill_cap("modmail", [SettingDef("category_id","Category","channel","Category for threads",False), SettingDef("log_channel_id","Log Channel","channel","",False)], [ResourceDef("category_id","channel","Modmail category",True)], ["open_category","open_logs","create_test","active_threads","repair","test"])
_fill_cap("auto_publisher", [SettingDef("bump_channel","Bump Channel","channel","",False)], [ResourceDef("bump_channel","channel","",False)], ["add_feed","feeds","enable_feed","disable_feed","test_fetch","repair"])
_fill_cap("anti_raid", [SettingDef("mass_join_threshold","Mass Join Threshold","number","3-100",False,10), SettingDef("alert_channel_id","Alert Channel","channel","",False)], [ResourceDef("alert_channel_id","channel","Logs",False)], ["enable","disable","simulate_raid","lock_server","unlock_server","repair","test"])
_fill_cap("guardian", [SettingDef("alert_channel","Alert Channel","channel","",False)], [], ["enable","disable","test_nuke","test_scam","repair","test"])
_fill_cap("automod", [SettingDef("log_channel_id","Log Channel","channel","",False)], [], ["enable","disable","test_rule","reload_rules","repair","test"])
_fill_cap("warnings", [SettingDef("expiry_days","Expiry Days","number","0=no expiry",False,30)], [], ["issue_test","warning_history","inspect_user","repair","test"])
_fill_cap("moderation", [SettingDef("log_channel","Log Channel","channel","",False)], [ResourceDef("log_channel","channel","",False)], ["test_moderation","open_modlog","manage_roles","repair","test"])
_fill_cap("appeals", [SettingDef("appeals_channel_id","Appeals Channel","channel","",False)], [ResourceDef("appeals_channel_id","channel","",True), ResourceDef("panel_message","message","Appeal panel",False)], ["open_channel","repost_panel","repair","pending_appeals","test"])
_fill_cap("event_logging", [SettingDef("log_channel","Log Channel","channel","",False)], [ResourceDef("log_channel","channel","",True)], ["open_log","test_event","recent_logs","repair","test"])
_fill_cap("mod_log", [SettingDef("channel_id","Mod-Log Channel","channel","",False)], [ResourceDef("channel_id","channel","",True)], ["open_log","test_log","repair","test"])
_fill_cap("reaction_roles", [SettingDef("log_channel","Log Channel","channel","",False)], [ResourceDef("panel_message","message","Reaction panel",False)], ["create_panel","panels","repost_panel","test_assignment","repair","test"])
_fill_cap("starboard", [SettingDef("starboard_channel","Starboard Channel","channel","",False), SettingDef("star_threshold","Threshold","number","Stars needed",False,3)], [ResourceDef("starboard_channel","channel","",True)], ["open_starboard","test_star","recalculate","repair","test"])
_fill_cap("reaction_menus", [SettingDef("default_channel","Default Channel","channel","",False)], [], ["create_menu","menus","repost","test","repair"])
_fill_cap("role_buttons", [SettingDef("default_channel","Default Channel","channel","",False)], [], ["create_panel","panels","repost","test_assignment","repair","test"])
_fill_cap("trigger_roles", [SettingDef("log_channel","Log Channel","channel","",False)], [], ["add_trigger","triggers","test_trigger","repair","test"])
_fill_cap("ai", [SettingDef("model","Model","text","e.g. gpt-4o",False), SettingDef("temperature","Temperature","number","0-2",False,0.7)], [], ["status","provider_config","test_tool","repair","test"])
_fill_cap("ai_chat", [SettingDef("memory_depth","Memory Depth","number","5-100",False,10)], [], ["add_channel","configured_channels","remove_channel","open_channel","test","repair"])
_fill_cap("staff_shifts", [SettingDef("shift_channel_id","Shift Channel","channel","",False)], [ResourceDef("shift_channel_id","channel","",False)], ["open_channel","start_test","active_shifts","repair","test"])
_fill_cap("staff_reviews", [SettingDef("review_channel_id","Review Channel","channel","",False)], [], ["open_channel","create_review","pending_reviews","repair","test"])
_fill_cap("staff_promo", [SettingDef("announcement_channel","Announcement Channel","channel","",False)], [ResourceDef("announcement_channel","channel","",False)], ["eligible_staff","promote","demote","repair","test"])
_fill_cap("applications", [SettingDef("channel_id","Applications Channel","channel","",False)], [ResourceDef("channel_id","channel","",True), ResourceDef("panel_message","message","Application panel",False)], ["open_channel","repost_panel","pending_applications","repair","test"])
_fill_cap("suggestions", [SettingDef("suggestions_channel","Suggestions Channel","channel","",False)], [ResourceDef("suggestions_channel","channel","",True)], ["open_channel","post_panel","pending","repair","test"])
_fill_cap("giveaways", [SettingDef("giveaway_channel","Giveaway Channel","channel","",False)], [ResourceDef("giveaway_channel","channel","",False)], ["create_giveaway","active_giveaways","end_giveaway","reroll","repair","test"])

def get_capability(key: str) -> Optional[SystemCapability]:
    return CAPABILITIES.get(key)

def all_capabilities():
    return CAPABILITIES.values()

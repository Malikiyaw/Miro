"""
help_system.py — Rich interactive !help command for Miro Bot.
Provides category buttons, per-system status, and a search modal.
"""

import discord
from discord import ui, Interaction
from data_manager import dm
from logger import logger
import time

# ───────────────────────── Colour palette ──────────────────────────
BRAND      = 0x5865F2   # Discord Blurple
GREEN      = 0x2ECC71
RED        = 0xE74C3C
GOLD       = 0xF1C40F
PURPLE     = 0x9B59B6
TEAL       = 0x1ABC9C
ORANGE     = 0xE67E22
DARK_BLUE  = 0x2C3E50

# ───────────────────────── System catalogue ──────────────────────────
CATEGORIES = {
    "🛡️ Security": {
        "color": RED,
        "systems": [
            ("verification",   "🛡️", "Captcha verification for new members", ["!setverifychannel", "!verify", "!configpanel verification"]),
            ("antiraid",       "🚨", "Mass-join detection & server lockdown", ["!raidstatus", "!configpanel antiraid"]),
            ("guardian",       "⚔️", "AI threat detection (scam, token, nuke)", ["!guardian status", "!configpanel guardian"]),
            ("automod",        "🤖", "Spam, caps, invite & link filters", ["!automod status", "!configpanel automod"]),
            ("warnings",       "⚠️", "Warning system with escalation", ["!warn @user", "!warnings", "!clearwarn", "!clearallwarns", "!configpanel warnings"]),
            ("moderation",     "🔨", "Kick, ban, mute, timeout commands", ["!kick", "!ban", "!mute", "!modstats", "!configpanel moderation"]),
            ("modlog",         "📋", "Audit log for all mod actions", ["!modlog view", "!configpanel modlog"]),
            ("logging",        "📊", "Full server event logging", ["!configpanel logging"]),
        ]
    },
    "💰 Economy": {
        "color": GOLD,
        "systems": [
            ("economy",        "💵", "Balance, daily, work, rob & transfer", ["!balance", "!daily", "!work", "!ecoleaderboard", "!challenge", "!shop", "!buy", "!transfer", "!give", "!beg", "!rob", "!help economy", "!configpanel economy"]),
            ("economyshop",    "🛒", "Item shop with role rewards", ["!shop", "!buy", "!configpanel economyshop"]),
            ("leveling",       "🆙", "XP per message with role rewards", ["!rank", "!lvlleaderboard", "!levels", "!rewards", "!levelshop", "!configpanel leveling"]),
            ("levelingshop",   "🎁", "Spend XP on perks & roles", ["!levelshop", "!configpanel levelingshop"]),
            ("starboard",      "⭐", "Star highlight board with rewards", ["!starboard", "!configpanel starboard"]),
        ]
    },
    "🎮 Gamification": {
        "color": PURPLE,
        "systems": [
            ("gamification",   "🎮", "Prestige, quests, skill trees", ["!quests", "!prestige", "!dice", "!flip", "!slots", "!trivia"]),
            ("giveaways",      "🎉", "Timed giveaways with role requirements", ["!giveaway create", "!giveaway list"]),
            ("events",         "📅", "Server event scheduling & RSVP", ["!events", "!join <event>"]),
            ("tournaments",    "🏆", "Bracket-style tournament management", ["!tournaments", "!join <tournament>", "!tournamentleaderboard"]),
        ]
    },
    "📋 Staff": {
        "color": TEAL,
        "systems": [
            ("staffpromo",     "📈", "Auto staff promotion tiers", ["!staffpromo", "!staffpromo tiers", "!staffpromo requirements", "!staffpromo status", "!staffpromo leaderboard", "!staffpromo progress", "!staffpromo bonuses", "!staffpromo exclude add @user", "!staffpromo exclude remove @user", "!promotionhistory", "!staffleaderboard", "!staffpromo_status", "!configpanel staffpromo"]),
            ("staffreviews",   "📝", "Peer & admin review cycles", ["!review", "!myreview", "!configpanel staffreviews"]),
            ("staffshifts",    "🕒", "On-duty shift tracker & hours log", ["!shift start", "!shift end", "!task", "!configpanel staffshifts"]),
            ("staffsystem",    "👮", "Staff hierarchy & management", ["!apply", "!apply status", "!help staffapply", "!configpanel staffsystem"]),
        ]
    },
    "🎫 Support": {
        "color": ORANGE,
        "systems": [
            ("tickets",        "🎫", "Support ticket system", ["!ticket", "!close", "!configpanel tickets"]),
            ("modmail",        "📬", "Private DM-based modmail", ["DM the bot", "!configpanel modmail"]),
            ("appeals",        "⚖️", "Ban / mute appeal system", ["!appeal", "!configpanel appeals"]),
            ("applications",   "📋", "Staff application forms", ["!apply", "!apply status", "!help staffapply", "!configpanel applications"]),
            ("suggestions",    "💡", "Community suggestion board", ["!suggest", "!configpanel suggestions"]),
        ]
    },
    "📣 Communication": {
        "color": BRAND,
        "systems": [
            ("welcome",        "👋", "Welcome & leave messages", ["!configpanel welcome"]),
            ("welcomedm",      "✉️", "Direct-message on join", ["!configpanel welcomedm"]),
            ("chatchannels",   "🧠", "AI-powered chat channels", ["!chatchannel add", "!configpanel chatchannels"]),
            ("autoresponder",  "💬", "Keyword auto-responses", ["!autoresponder add", "!configpanel autoresponder"]),
            ("reminders",      "⏰", "Personal & server reminders", ["!remind", "!reminders", "!configpanel reminders"]),
            ("announcements",  "📢", "Scheduled announcements", ["!announcement create", "!configpanel announcements"]),
            ("scheduled",      "🕒", "Scheduled automated messages", ["!configpanel scheduled"]),
        ]
    },
    "🔧 Configuration": {
        "color": DARK_BLUE,
        "systems": [
            ("reactionroles",  "🎭", "Emoji-based role assignment", ["!reactionrolespanel", "!configpanel reactionroles"]),
            ("reactionmenus",  "📌", "Role picker menus", ["!reactionmenuspanel", "!configpanel reactionmenus"]),
            ("rolebuttons",    "🔘", "Button-based role panels", ["!rolebuttonspanel", "!configpanel rolebuttons"]),
            ("automod",        "🛡️", "AutoMod rule management", ["!configpanel automod"]),
            ("voicesystem",    "🔊", "Voice channel management", ["!configpanel voicesystem"]),
            ("intelligence",   "🧠", "Server analytics & stats", ["!serverstats", "!mystats", "!atrisk", "!configpanel intelligence"]),
            ("conflict_resolution", "⚖️", "AI conflict resolution", ["!configpanel conflict_resolution"]),
            ("community_health", "❤️", "Community engagement tracking", ["!configpanel community_health"]),
            ("embed_system",   "📄", "Embed creation & management", ["!configpanel embed_system"]),
            ("proactive_assist", "🤖", "Proactive AI assistance", ["!configpanel proactive_assist"]),
            ("vector_memory",  "💾", "AI memory management", ["!configpanel vector_memory"]),
            ("mod_logging",    "📝", "Moderation logging", ["!configpanel mod_logging"]),
            ("server_analytics", "📈", "Server analytics", ["!configpanel server_analytics"]),
        ]
    },
    "🚀 Advanced Systems": {
        "color": PURPLE,
        "systems": [
            ("trigger_roles",  "🎭", "Trigger-based role assignment", ["!configpanel trigger_roles"]),
            ("content_generator", "✍️", "AI content generation", ["!configpanel content_generator"]),
            ("tournament_system", "🏅", "Tournament management", ["!tournament create", "!configpanel tournament_system"]),
            ("anti_raid",      "🚨", "Anti-raid protection", ["!raidstatus", "!configpanel anti_raid"]),
            ("auto_setup",     "⚙️", "Automated server setup", ["!autosetup", "!configpanel auto_setup"]),
            ("guardian",       "⚔️", "Advanced threat detection", ["!guardian status", "!configpanel guardian"]),
            ("warning_system", "⚠️", "Warning escalation system", ["!warn", "!configpanel warning_system"]),
            ("staff_promo",    "📈", "Staff promotion system", ["!staffpromo", "!configpanel staff_promo"]),
            ("staff_reviews",  "📝", "Staff review system", ["!review", "!configpanel staff_reviews"]),
            ("auto_responder", "💬", "Auto-response system", ["!autoresponder add", "!configpanel auto_responder"]),
            ("welcome_leave",  "👋", "Welcome/leave messages", ["!configpanel welcome_leave"]),
            ("chat_channels",  "🧠", "AI chat channels", ["!chatchannel add", "!configpanel chat_channels"]),
            ("events",         "📅", "Event scheduling", ["!events", "!configpanel events"]),
            ("giveaways",      "🎉", "Giveaway system", ["!giveaway create", "!configpanel giveaways"]),
            ("reminders",      "⏰", "Reminder system", ["!remind", "!configpanel reminders"]),
            ("announcements",  "📢", "Announcement system", ["!announcement create", "!configpanel announcements"]),
            ("suggestions",    "💡", "Suggestion system", ["!suggest", "!configpanel suggestions"]),
            ("tickets",        "🎫", "Ticket system", ["!ticket", "!configpanel tickets"]),
            ("appeals",        "⚖️", "Appeal system", ["!appeal", "!configpanel appeals"]),
            ("applications",   "📋", "Application system", ["!apply", "!configpanel applications"]),
            ("staff_system",   "👮", "Staff system", ["!staffleaderboard", "!configpanel staff_system"]),
        ]
    },
}

# Flat lookup: system_key → (emoji, description, category_name, cmd_examples)
_SYSTEM_LOOKUP = {}
for _cat, _data in CATEGORIES.items():
    for _sys, _emoji, _desc, _cmds in _data["systems"]:
        _SYSTEM_LOOKUP[_sys] = (_emoji, _desc, _cat, _cmds)


def _status_emoji(guild_id: int, system_key: str) -> str:
    """Return ✅ / ❌ / ⬜ based on whether the system is enabled in guild config."""
    try:
        cfg = dm.get_guild_data(guild_id, f"{system_key}_config", None)
        if cfg is None or not isinstance(cfg, dict):
            return "⬜"  # Not configured yet.
        enabled = cfg.get("enabled", True)
        return "✅" if enabled else "❌"
    except Exception:
        return "⬜"


def _bot_avatar_url(bot: discord.Client = None) -> str:
    """Return the bot's display avatar URL, with a Discord-default fallback."""
    try:
        if bot is not None and bot.user is not None:
            return bot.user.display_avatar.url
    except Exception:
        pass
    return "https://cdn.discordapp.com/embed/avatars/0.png"


def _build_main_embed(guild_id: int, bot: discord.Client = None) -> discord.Embed:
    embed = discord.Embed(
        title="📖 Miro Bot — Command Reference",
        description=(
            "**Select a category** below to explore all systems and their commands.\n"
            "Each system can be configured with `!configpanel <system>`.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=BRAND
    )

    # Always show the bot's own avatar as the thumbnail so users instantly
    # recognise which bot this help menu belongs to.
    avatar_url = _bot_avatar_url(bot)
    embed.set_thumbnail(url=avatar_url)

    # Use the bot's name + avatar as the author so the menu feels branded.
    bot_name = bot.user.name if (bot and bot.user) else "Miro Bot"
    embed.set_author(name=f"{bot_name} • Help Menu", icon_url=avatar_url)

    for cat_name, cat_data in CATEGORIES.items():
        systems = cat_data["systems"]
        # Show systems with example commands
        preview_parts = []
        for s, e, _, cmd_examples in systems[:3]:
            cmd_str = " | ".join([f"`{cmd}`" for cmd in cmd_examples[:2]])
            preview_parts.append(f"{e} {s.replace('_', ' ').title()}: {cmd_str}")
        preview = "\n".join(preview_parts)
        if len(systems) > 3:
            preview += f"\n...and {len(systems)-3} more systems"
        embed.add_field(name=cat_name, value=preview, inline=False)

    embed.add_field(
        name="📚 Quick Links",
        value=(
            "• `!help` - Show this help menu\n"
            "• `!help <system>` - View system-specific help\n"
            "• `!configpanel <system>` - Configure any system\n"
            "• `!stats` - View server statistics"
        ),
        inline=False
    )

    embed.set_footer(text="Miro Bot • Use !configpanel <system> to configure any system")
    embed.timestamp = discord.utils.utcnow()
    return embed


def _build_category_embed(guild_id: int, cat_name: str, bot: discord.Client = None) -> discord.Embed:
    cat_data = CATEGORIES[cat_name]
    embed = discord.Embed(
        title=f"{cat_name} — Systems",
        description="Use `!configpanel <system>` to open a system's configuration panel.\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
        color=cat_data["color"]
    )

    embed.set_thumbnail(url=_bot_avatar_url(bot))

    for sys_key, emoji, desc, cmd_examples in cat_data["systems"]:
        status = _status_emoji(guild_id, sys_key)
        # Build command examples string
        cmd_str = " | ".join([f"`{cmd}`" for cmd in cmd_examples[:3]])
        panel_cmd = f"`!configpanel {sys_key}`"
        embed.add_field(
            name=f"{emoji} {sys_key.replace('_', ' ').title()}  {status}",
            value=f"{desc}\n{cmd_str}\n{panel_cmd}",
            inline=False
        )

    embed.set_footer(text=f"Category: {cat_name} • Click '🏠 Home' to return to main menu")
    return embed


def _build_system_embed(guild_id: int, sys_key: str, bot: discord.Client = None) -> discord.Embed:
    if sys_key in _SYSTEM_LOOKUP:
        emoji, desc, cat, cmd_examples = _SYSTEM_LOOKUP[sys_key]
        status = _status_emoji(guild_id, sys_key)
        embed = discord.Embed(
            title=f"{emoji} {sys_key.replace('_', ' ').title()} System {status}",
            description=f"**Category:** {cat}\n\n**Description:** {desc}\n\n**Configuration:** `!configpanel {sys_key}`",
            color=CATEGORIES[cat]["color"]
        )
        embed.set_thumbnail(url=_bot_avatar_url(bot))
        if cmd_examples:
            embed.add_field(
                name="⌨️ Available Commands",
                value="\n".join([f"`{cmd}`" for cmd in cmd_examples]),
                inline=False
            )
        embed.set_footer(text="Click '⚙️ Config Panel' to open settings")
        return embed
    return discord.Embed(title="System not found", color=RED)

def _build_search_embed(guild_id: int, query: str, bot: discord.Client = None) -> discord.Embed:
    query_l = query.lower()

    # Search in systems (CATEGORIES has 4 elements: sys_key, emoji, desc, cmd_examples)
    sys_results = []
    for cat_name, cat_data in CATEGORIES.items():
        for sys_key, emoji, desc, cmd_examples in cat_data["systems"]:
            if query_l in sys_key or query_l in desc.lower() or query_l in cat_name.lower():
                sys_results.append((sys_key, emoji, desc, cmd_examples, cat_name))

    # Search in custom commands
    cmd_results = []
    try:
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        for cmd_name, cmd_data in custom_cmds.items():
            if isinstance(cmd_name, str) and query_l in cmd_name.lower():
                cmd_results.append((cmd_name, cmd_data))
    except Exception:
        pass

    embed = discord.Embed(
        title=f"🔍 Search Results: \"{query}\"",
        color=BRAND
    )
    embed.set_thumbnail(url=_bot_avatar_url(bot))

    if sys_results:
        text = ""
        for sys_key, emoji, desc, cmd_examples, cat in sys_results[:8]:
            status = _status_emoji(guild_id, sys_key)
            text += f"{emoji} **{sys_key.title()}** {status} — {desc}\n"
            if cmd_examples:
                cmd_str = " | ".join([f"`{cmd}`" for cmd in cmd_examples[:3]])
                text += f"   Commands: {cmd_str}\n"
        embed.add_field(name="Systems", value=text, inline=False)

    if cmd_results:
        text = ""
        for cmd_name, _ in cmd_results[:12]:
            text += f"`!{cmd_name}` "
        embed.add_field(name="Matching Commands", value=text or "None", inline=False)

    if not sys_results and not cmd_results:
        embed.description = f"No systems or commands found matching **\"{query}\"**."

    embed.set_footer(text="Search scope: Global Systems & Guild Commands")
    return embed


# ─────────────────────────── Views ──────────────────────────────────────────

class _SearchModal(ui.Modal, title="Search Systems"):
    query = ui.TextInput(label="Search term", placeholder="e.g. economy, role, ban", max_length=50)

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: Interaction):
        embed = _build_search_embed(self.guild_id, self.query.value, interaction.client)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class HelpCategoryView(ui.View):
    """Shown when user selects a category from the main help menu."""

    def __init__(self, guild_id: int, cat_name: str):
        super().__init__(timeout=None)  # Make persistent
        self.guild_id = guild_id
        self.cat_name = cat_name
        self.selected_system = None

        # Select menu for systems in the category
        cat_data = CATEGORIES[cat_name]
        options = []
        for sys_key, emoji, desc, cmd_examples in cat_data["systems"]:
            status = _status_emoji(guild_id, sys_key)
            options.append(discord.SelectOption(
                label=f"{sys_key.replace('_', ' ').title()} {status}",
                value=sys_key,
                description=desc[:100],
                emoji=emoji
            ))
        self.system_select = ui.Select(
            placeholder="Select a system for details...",
            options=options,
            custom_id="help_system_select"
        )
        self.system_select.callback = self._system_callback
        self.add_item(self.system_select)

    @ui.button(label="🏠 Home", style=discord.ButtonStyle.secondary, custom_id="help_cat_back")
    async def back(self, interaction: Interaction, button: ui.Button):
        view = HelpMainView(interaction.guild_id)
        embed = _build_main_embed(interaction.guild_id, interaction.client)
        await interaction.response.edit_message(embed=embed, view=view)

    @ui.button(label="🔍 Search", style=discord.ButtonStyle.secondary, custom_id="help_cat_search")
    async def search(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(_SearchModal(interaction.guild_id))

    @ui.button(label="⚙️ Config Panel", style=discord.ButtonStyle.primary, custom_id="help_cat_config", disabled=True)
    async def config_panel(self, interaction: Interaction, button: ui.Button):
        if self.selected_system:
            # Send a message to open config panel
            await interaction.response.send_message(f"Opening config panel for {self.selected_system}...", ephemeral=True)
            # Note: Actually opening config panel would require calling the config panel command, but for now, just inform
            # In real implementation, trigger the config panel command

    async def _system_callback(self, interaction: Interaction):
        sys_key = self.system_select.values[0]
        self.selected_system = sys_key
        # Enable the config panel button
        self.config_panel.disabled = False
        # Build detailed embed for the system
        embed = _build_system_embed(self.guild_id, sys_key, interaction.client)
        await interaction.response.edit_message(embed=embed, view=self)


class HelpMainView(ui.View):
    """The main !help panel with category buttons."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)  # Make persistent
        self.guild_id = guild_id

        # Add buttons for each category (up to 4 categories per row, max 5 rows)
        categories = list(CATEGORIES.keys())
        for i, cat_name in enumerate(categories):
            emoji = cat_name.split()[0]  # Get emoji from category name
            button = ui.Button(
                label=cat_name,
                style=discord.ButtonStyle.primary,
                custom_id=f"help_cat_{i}",
                row=i // 4  # 4 buttons per row
            )
            button.callback = self._make_callback(cat_name)
            self.add_item(button)

        # Search button in the last row
        search_btn = ui.Button(
            label="🔍 Search",
            style=discord.ButtonStyle.secondary,
            custom_id="help_main_search",
            row=min(4, len(categories) // 4 + 1)  # Place in appropriate row
        )
        search_btn.callback = self._search_callback
        self.add_item(search_btn)

    def _make_callback(self, cat_name: str):
        async def callback(interaction: Interaction):
            try:
                embed = _build_category_embed(interaction.guild_id, cat_name, interaction.client)
                view = HelpCategoryView(interaction.guild_id, cat_name)
                await interaction.response.edit_message(embed=embed, view=view)
            except Exception as e:
                print(f"Error in help category callback: {e}")
                await interaction.response.send_message("Error loading category. Please try again.", ephemeral=True)
        return callback

    async def _search_callback(self, interaction: Interaction):
        try:
            await interaction.response.send_modal(_SearchModal(interaction.guild_id))
        except Exception as e:
            print(f"Error in help search callback: {e}")
            await interaction.response.send_message("Search is currently unavailable.", ephemeral=True)

    async def _all_cmds_callback(self, interaction: Interaction):
        # Builds an embed listing every example command from CATEGORIES plus the
        # guild's custom commands — gives users a single scrollable command index.
        embed = discord.Embed(
            title="📜 All Commands",
            description="Every built-in example command grouped by system, followed by your server's custom commands.",
            color=BRAND
        )
        embed.set_thumbnail(url=_bot_avatar_url(interaction.client))

        for cat_name, cat_data in CATEGORIES.items():
            lines = []
            for sys_key, emoji, _desc, cmds in cat_data["systems"]:
                if cmds:
                    cmd_str = " ".join([f"`{c}`" for c in cmds[:3]])
                    lines.append(f"{emoji} **{sys_key.title()}** — {cmd_str}")
            if lines:
                embed.add_field(name=cat_name, value="\n".join(lines)[:1024], inline=False)

        try:
            custom = dm.get_guild_data(interaction.guild_id, "custom_commands", {}) or {}
            custom_names = [n for n in custom.keys() if isinstance(n, str)]
            if custom_names:
                preview = " ".join([f"`!{n}`" for n in sorted(custom_names)[:30]])
                if len(custom_names) > 30:
                    preview += f"  …and {len(custom_names) - 30} more"
                embed.add_field(name="🛠️ Custom Commands", value=preview[:1024], inline=False)
        except Exception:
            pass

        embed.set_footer(text="Tip: !help <system> for a deep dive into one system.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def send_help(channel: discord.TextChannel, guild_id: int, invoker: discord.Member = None, system_query: str = None, bot: discord.Client = None):
    """Entry point called from bot.py on_message for the !help command."""
    try:
        # Resolve a bot reference if the caller didn't pass one.
        if bot is None:
            try:
                bot = channel._state._get_client()
            except Exception:
                bot = None



        if system_query:
            if system_query.lower() == "systems":
                # Special case: show all systems
                embed = discord.Embed(
                    title="🔧 All Systems Overview",
                    description="Complete list of all available systems organized by category.\nUse `!help <system>` for detailed help on a specific system.",
                    color=BRAND
                )
                embed.set_thumbnail(url=_bot_avatar_url(bot))

                for cat_name, cat_data in CATEGORIES.items():
                    systems_list = []
                    for sys_key, emoji, desc, _ in cat_data["systems"]:
                        status = _status_emoji(guild_id, sys_key)
                        systems_list.append(f"{emoji} {sys_key.replace('_', ' ').title()} {status}")
                    if systems_list:
                        embed.add_field(name=cat_name, value="\n".join(systems_list), inline=False)

                embed.set_footer(text="Use !configpanel <system> to configure any system")
                await channel.send(embed=embed)
                return

            system_key = system_query.lower().replace("_", "").replace("system", "").strip()
            if system_key in _SYSTEM_LOOKUP:
                # _SYSTEM_LOOKUP stores 4-tuples: (emoji, desc, category, cmd_examples).
                emoji, desc, cat, cmd_examples = _SYSTEM_LOOKUP[system_key]
                embed = discord.Embed(
                    title=f"{emoji} System Guide: {system_key.title()}",
                    color=CATEGORIES[cat]["color"],
                )
                embed.set_thumbnail(url=_bot_avatar_url(bot))
                embed.description = (
                    f"**Description:** {desc}\n\n"
                    f"**Category:** {cat}\n"
                    f"**Configuration:** `!configpanel {system_key}`"
                )

                if cmd_examples:
                    embed.add_field(
                        name="⌨️ Example Commands",
                        value="\n".join([f"`{cmd}`" for cmd in cmd_examples[:5]]),
                        inline=False,
                    )

                embed.set_footer(text=f"Requested by {invoker.display_name if invoker else 'User'}")
                await channel.send(embed=embed)
                return

        # Check custom commands if system not found in built-in systems
        try:
            custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
            if system_query and system_query.lower() in custom_cmds:
                cmd_name = system_query.lower()
                cmd_data = custom_cmds[cmd_name]
                embed = discord.Embed(
                    title=f"🛠️ Custom Command: !{cmd_name}",
                    color=BRAND
                )
                embed.set_thumbnail(url=_bot_avatar_url(bot))
                if isinstance(cmd_data, dict):
                    embed.description = cmd_data.get("content", "No description available.")
                    if cmd_data.get("command_type"):
                        embed.add_field(name="Type", value=cmd_data["command_type"], inline=True)
                else:
                    embed.description = str(cmd_data)
                embed.set_footer(text=f"Requested by {invoker.display_name if invoker else 'User'}")
                await channel.send(embed=embed)
                return
        except Exception:
            pass

        view = HelpMainView(guild_id)
        embed = _build_main_embed(guild_id, bot)
        if invoker:
            embed.set_author(name=f"Requested by {invoker.display_name}", icon_url=invoker.display_avatar.url)
        await channel.send(embed=embed, view=view)
    except Exception as e:
        print(f"Error in send_help: {e}")
        import traceback
        traceback.print_exc()
        # Fallback: send a simple embed
        try:
            embed = discord.Embed(
                title="📖 Miro Bot Help",
                description="**Categories:**\n• 🛡️ Security\n• 💰 Economy\n• 🎮 Gamification\n• 📋 Staff\n\nUse `!configpanel <system>` to configure any system.",
                color=0x5865F2
            )
            await channel.send(embed=embed)
        except Exception as e2:
            # Last resort: plain text
            await channel.send("Help system error. Please contact an administrator.")


class HelpSystem:
    """Message-based compatibility facade used by actions.py custom-command handlers."""

    def __init__(self, bot):
        self.bot = bot

    async def send_full_help(self, message):
        """!help all — send the full help menu."""
        try:
            await send_help(message.channel, message.guild.id, message.author, bot=self.bot)
        except Exception as e:
            logger.error(f"Error sending full help: {e}")
            embed = discord.Embed(
                title="📖 Miro Bot Help",
                description="**Categories:**\n• 🛡️ Security\n• 💰 Economy\n• 🎮 Gamification\n• 📋 Staff\n\nUse `!configpanel <system>` to configure any system.",
                color=0x5865F2
            )
            await message.channel.send(embed=embed)

    async def send_help_embed(self, message):
        """!help embed — send the main help panel embed."""
        embed = _build_main_embed(message.guild.id, self.bot)
        await message.channel.send(embed=embed, view=HelpMainView(message.guild.id))

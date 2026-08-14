import discord
import time
import re
from typing import Dict, List, Optional
from datetime import timedelta
from data_manager import dm
from logger import logger

class AutoModSystem:
    def __init__(self, bot):
        self.bot = bot
        self._message_history: Dict[int, Dict[int, List[float]]] = {} # guild_id -> user_id -> [timestamps]
        self._mention_history: Dict[int, Dict[int, List[float]]] = {}
        self._link_history: Dict[int, Dict[int, List[float]]] = {}
        self._attachment_history: Dict[int, Dict[int, List[float]]] = {}

    def get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "automod_config", {
            "enabled": False,
            "log_channel_id": None,
            "whitelist_channels": [],
            "whitelist_roles": [],
            "rules": {
                "spam": {"enabled": False, "max_messages": 5, "window": 5, "action": "mute"},
                "mentions": {"enabled": False, "max_mentions": 5, "window": 10, "action": "warn"},
                "caps": {"enabled": False, "threshold_pct": 70, "min_chars": 20, "action": "warn"},
                "emojis": {"enabled": False, "max_emojis": 10, "action": "warn"},
                "links": {"enabled": False, "max_links": 3, "window": 10, "action": "warn", "whitelisted_domains": []},
                "invites": {"enabled": False, "action": "warn"},
                "banned_words": {"enabled": False, "words": [], "action": "warn"},
                "zalgo": {"enabled": False, "action": "warn"},
                "mass_ping": {"enabled": False, "action": "warn"},
                "repeated_chars": {"enabled": False, "action": "delete"},
                "new_account": {"enabled": False, "min_age_days": 3, "action": "flag"},
                "attachments": {"enabled": False, "max_attachments": 5, "window": 10, "action": "warn"},
                "newlines": {"enabled": False, "max_newlines": 15, "action": "delete"}
            },
            "escalation": {
                "1": "warn",
                "2": "mute_10",
                "3": "mute_60",
                "4": "kick",
                "5": "ban",
                "reset_hours": 24
            }
        })

    def save_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "automod_config", config)

    async def handle_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        config = self.get_config(message.guild.id)
        if not config.get("enabled"):
            return

        if message.author.guild_permissions.administrator:
            return

        if message.channel.id in config.get("whitelist_channels", []):
            return

        for role in message.author.roles:
            if role.id in config.get("whitelist_roles", []):
                return

        content = message.content
        violations = []
        rules = config.get("rules", {})

        # 1. Spam (X messages in Y seconds)
        rule = rules.get("spam", {})
        if rule.get("enabled"):
            if await self._check_spam(message, rule):
                violations.append("Spam")

        # 2. Mention Spam
        rule = rules.get("mentions", {})
        if rule.get("enabled"):
            if await self._check_mentions(message, rule):
                violations.append("Mention Spam")

        # 3. Caps Spam
        rule = rules.get("caps", {})
        if rule.get("enabled"):
            if self._check_caps(content, rule):
                violations.append("Caps Spam")

        # 4. Emoji Spam
        rule = rules.get("emojis", {})
        if rule.get("enabled"):
            if self._check_emojis(content, rule):
                violations.append("Emoji Spam")

        # 5. Link Spam
        rule = rules.get("links", {})
        if rule.get("enabled"):
            if await self._check_links(message, rule):
                violations.append("Link Spam")

        # 6. Discord Invites
        rule = rules.get("invites", {})
        if rule.get("enabled"):
            if await self._check_invites(content, message.guild):
                violations.append("Discord Invite")

        # 7. Banned Words
        rule = rules.get("banned_words", {})
        if rule.get("enabled"):
            if self._check_banned_words(content, rule.get("words", [])):
                violations.append("Banned Word")

        # 8. Zalgo Text
        rule = rules.get("zalgo", {})
        if rule.get("enabled"):
            if self._check_zalgo(content):
                violations.append("Zalgo Text")

        # 9. Mass Ping
        rule = rules.get("mass_ping", {})
        if rule.get("enabled"):
            if "@everyone" in content or "@here" in content:
                violations.append("Mass Ping")

        # 10. Repeated Characters
        rule = rules.get("repeated_chars", {})
        if rule.get("enabled"):
            if re.search(r'(.)\1{9,}', content):
                violations.append("Repeated Characters")

        # 11. New Account
        rule = rules.get("new_account", {})
        if rule.get("enabled"):
            days = (discord.utils.utcnow() - message.author.created_at).days
            if days < rule.get("min_age_days", 3):
                # Special action: flag (log but don't delete yet unless other rules trigger)
                await self._log_violation(message, "New Account Message")

        # 12. Attachment Spam
        rule = rules.get("attachments", {})
        if rule.get("enabled"):
            if await self._check_attachments(message, rule):
                violations.append("Attachment Spam")

        # 13. Newline Spam
        rule = rules.get("newlines", {})
        if rule.get("enabled"):
            if content.count('\n') > rule.get("max_newlines", 15):
                violations.append("Newline Spam")

        if violations:
            await self._process_violations(message, violations, config)

    # --- Detection Helpers ---

    async def _check_spam(self, message, rule):
        gid, uid = message.guild.id, message.author.id
        now = time.time()
        if gid not in self._message_history: self._message_history[gid] = {}
        if uid not in self._message_history[gid]: self._message_history[gid][uid] = []

        window = rule.get("window", 5)
        self._message_history[gid][uid] = [t for t in self._message_history[gid][uid] if now - t < window]
        self._message_history[gid][uid].append(now)
        return len(self._message_history[gid][uid]) >= rule.get("max_messages", 5)

    async def _check_mentions(self, message, rule):
        count = len(message.mentions) + len(message.role_mentions)
        if count >= rule.get("max_mentions", 5): return True

        gid, uid = message.guild.id, message.author.id
        now = time.time()
        if gid not in self._mention_history: self._mention_history[gid] = {}
        if uid not in self._mention_history[gid]: self._mention_history[gid][uid] = []

        window = rule.get("window", 10)
        self._mention_history[gid][uid] = [t for t in self._mention_history[gid][uid] if now - t < window]
        for _ in range(count): self._mention_history[gid][uid].append(now)
        return len(self._mention_history[gid][uid]) >= rule.get("max_mentions", 5)

    def _check_caps(self, content, rule):
        if len(content) < rule.get("min_chars", 20): return False
        caps = sum(1 for c in content if c.isupper())
        pct = (caps / len(content)) * 100
        return pct > rule.get("threshold_pct", 70)

    def _check_emojis(self, content, rule):
        emojis = len(re.findall(r'<a?:\w+:\d+>|[\U00010000-\U0010ffff]', content))
        return emojis > rule.get("max_emojis", 10)

    async def _check_links(self, message, rule):
        links = re.findall(r'https?://[^\s]+', message.content)
        if not links: return False

        whitelisted = rule.get("whitelisted_domains", [])
        filtered_links = []
        for link in links:
            is_whitelisted = False
            for domain in whitelisted:
                if domain.lower() in link.lower():
                    is_whitelisted = True
                    break
            if not is_whitelisted:
                filtered_links.append(link)

        if not filtered_links: return False

        gid, uid = message.guild.id, message.author.id
        now = time.time()
        if gid not in self._link_history: self._link_history[gid] = {}
        if uid not in self._link_history[gid]: self._link_history[gid][uid] = []

        window = rule.get("window", 10)
        self._link_history[gid][uid] = [t for t in self._link_history[gid][uid] if now - t < window]
        for _ in range(len(links)): self._link_history[gid][uid].append(now)
        return len(self._link_history[gid][uid]) >= rule.get("max_links", 3)

    async def _check_invites(self, content, guild):
        invites = re.findall(r'discord(?:\.gg|app\.com/invite)/([a-zA-Z0-9\-]+)', content)
        for code in invites:
            try:
                invite = await self.bot.fetch_invite(code)
                if invite.guild and invite.guild.id != guild.id:
                    return True
            except:
                # If invite is invalid, still might want to block it if it looks like an invite
                return True
        return False

    def _check_banned_words(self, content, words):
        if not words: return False
        content_lower = content.lower()
        for word in words:
            if word.lower() in content_lower:
                return True
        return False

    def _check_zalgo(self, content):
        return bool(re.search(r'[\u0300-\u036F\u0483-\u0489\u1DC0-\u1DFF\u20D0-\u20FF\uFE20-\uFE2F]{3,}', content))

    async def _check_attachments(self, message, rule):
        count = len(message.attachments)
        if count == 0: return False

        gid, uid = message.guild.id, message.author.id
        now = time.time()
        if gid not in self._attachment_history: self._attachment_history[gid] = {}
        if uid not in self._attachment_history[gid]: self._attachment_history[gid][uid] = []

        window = rule.get("window", 10)
        self._attachment_history[gid][uid] = [t for t in self._attachment_history[gid][uid] if now - t < window]
        for _ in range(count): self._attachment_history[gid][uid].append(now)
        return len(self._attachment_history[gid][uid]) >= rule.get("max_attachments", 5)

    # --- Punishment Logic ---

    async def _process_violations(self, message, violations, config):
        try:
            await message.delete()
        except:
            pass

        user_violations = dm.get_guild_data(message.guild.id, f"automod_violations_{message.author.id}", {
            "count": 0,
            "last_violation": 0
        })

        now = time.time()
        reset_time = config.get("escalation", {}).get("reset_hours", 24) * 3600

        if now - user_violations["last_violation"] > reset_time:
            user_violations["count"] = 1
        else:
            user_violations["count"] += 1

        user_violations["last_violation"] = now
        dm.update_guild_data(message.guild.id, f"automod_violations_{message.author.id}", user_violations)

        count = str(user_violations["count"])
        action = config.get("escalation", {}).get(count, config.get("escalation", {}).get("5", "ban"))

        await self._apply_punishment(message.author, action, ", ".join(violations))
        await self._log_violation(message, ", ".join(violations), action, user_violations["count"])

    async def _apply_punishment(self, member, action, reason):
        full_reason = f"Auto-Mod: {reason}"
        try:
            if action == "warn":
                await member.send(f"⚠️ **Auto-Mod Warning:** {reason}")
            elif action == "mute_10":
                await member.timeout(timedelta(minutes=10), reason=full_reason)
                try: await member.send(f"🔇 **Auto-Mod Mute (10m):** {reason}")
                except: pass
            elif action == "mute_60":
                await member.timeout(timedelta(hours=1), reason=full_reason)
                try: await member.send(f"🔇 **Auto-Mod Mute (1h):** {reason}")
                except: pass
            elif action == "kick":
                try: await member.send(f"👢 **Auto-Mod Kick:** {reason}")
                except: pass
                await member.kick(reason=full_reason)
            elif action == "ban":
                try: await member.send(f"🔨 **Auto-Mod Ban:** {reason}")
                except: pass
                await member.ban(reason=full_reason)
        except Exception as e:
            logger.error(f"Failed to apply automod punishment {action}: {e}")

    async def _log_violation(self, message, violation_type, action=None, count=None):
        gid = message.guild.id
        # Update Stats
        stats = dm.get_guild_data(gid, "automod_stats", {
            "today": 0,
            "week": 0,
            "types": {},
            "users": {},
            "actions": {},
            "last_reset": time.time()
        })

        now = time.time()
        # Reset daily stats if needed
        if now - stats.get("last_reset", 0) > 86400:
            stats["today"] = 0
            stats["last_reset"] = now

        stats["today"] += 1
        stats["week"] += 1
        stats["types"][violation_type] = stats["types"].get(violation_type, 0) + 1
        stats["users"][str(message.author.id)] = stats["users"].get(str(message.author.id), 0) + 1
        if action:
            stats["actions"][action] = stats["actions"].get(action, 0) + 1

        # Keep track of last 30 actions
        history = dm.get_guild_data(gid, "automod_history", [])
        history.append({
            "ts": now,
            "user": str(message.author),
            "user_id": message.author.id,
            "type": violation_type,
            "action": action or "FLAG",
            "message": message.content[:100]
        })
        dm.update_guild_data(gid, "automod_history", history[-30:])
        dm.update_guild_data(gid, "automod_stats", stats)

        config = self.get_config(gid)
        log_ch_id = config.get("log_channel_id")
        if not log_ch_id: return

        channel = message.guild.get_channel(log_ch_id)
        if not channel: return

        embed = discord.Embed(title="🛡️ Auto-Mod Violation", color=discord.Color.orange())
        embed.add_field(name="User", value=f"{message.author.mention} ({message.author.id})", inline=True)
        embed.add_field(name="Violation", value=violation_type, inline=True)
        if count:
            embed.add_field(name="Violation Count", value=str(count), inline=True)
        if action:
            embed.add_field(name="Action Taken", value=action.upper(), inline=True)
        embed.add_field(name="Message Preview", value=message.content[:500] or "_No content_", inline=False)
        embed.timestamp = discord.utils.utcnow()

        try: await channel.send(embed=embed)
        except: pass

    async def setup(self, interaction: discord.Interaction):
        """Initial setup for Auto-Mod"""
        guild = interaction.guild
        # Create log channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        channel = await guild.create_text_channel("automod-log", overwrites=overwrites)

        config = self.get_config(guild.id)
        config["log_channel_id"] = channel.id
        config["enabled"] = True
        self.save_config(guild.id, config)

        return True

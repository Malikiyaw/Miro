import discord
import time
import json
import re
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from data_manager import dm
from logger import logger

class WarningSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "warning_config", {
            "enabled": True,
            "thresholds": {
                "minor": {"count": 2, "action": "none"},
                "moderate": {"count": 3, "action": "mute_60"},
                "severe": {"count": 4, "action": "kick"},
                "critical": {"count": 5, "action": "ban"}
            },
            "expiry_days": 30,
            "dm_enabled": True,
            "dm_template": "Hello {user}, you have been warned for: {reason}. Severity: {severity}. Total active warnings: {count}. Next threshold action: {next_action}."
        })

    def save_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "warning_config", config)

    def get_warnings(self, guild_id: int, user_id: int) -> List[dict]:
        all_warnings = dm.get_guild_data(guild_id, f"user_warnings_{user_id}", [])

        # Filter out expired warnings
        config = self.get_config(guild_id)
        expiry_days = config.get("expiry_days", 30)

        if expiry_days > 0:
            now = time.time()
            expiry_seconds = expiry_days * 24 * 3600
            for w in all_warnings:
                if not w.get("pardoned") and now - w.get("timestamp", 0) > expiry_seconds:
                    w["active"] = False

        return all_warnings

    def get_stats(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "warning_stats", {
            "today": 0,
            "week": 0,
            "severity_breakdown": {"minor": 0, "moderate": 0, "severe": 0},
            "total_pardoned": 0
        })

    def get_history(self, guild_id: int) -> List[dict]:
        return dm.get_guild_data(guild_id, "warning_history", [])

    def get_most_warned(self, guild_id: int) -> List[dict]:
        """Find top 10 most warned users."""
        guild_data = dm.load_json(f"guild_{guild_id}", default={})
        warn_counts = []
        for key, value in guild_data.items():
            if key.startswith("user_warnings_") and isinstance(value, list):
                uid = key.replace("user_warnings_", "")
                active_count = len([w for w in value if w.get("active") and not w.get("pardoned")])
                if active_count > 0:
                    warn_counts.append({"user_id": int(uid), "count": active_count})

        warn_counts.sort(key=lambda x: x["count"], reverse=True)
        return warn_counts[:10]

    async def issue_warning(self, guild: discord.Guild, user_id: int, moderator_id: int, reason: str, severity: str = "minor"):
        config = self.get_config(guild.id)
        all_warnings = self.get_warnings(guild.id, user_id) # Use get_warnings to apply expiry

        warning_id = len(all_warnings) + 1
        new_warning = {
            "id": warning_id,
            "moderator_id": moderator_id,
            "reason": reason,
            "severity": severity,
            "timestamp": time.time(),
            "active": True,
            "pardoned": False
        }

        all_warnings.append(new_warning)
        dm.update_guild_data(guild.id, f"user_warnings_{user_id}", all_warnings)

        # Check thresholds
        active_count = len([w for w in all_warnings if w.get("active") and not w.get("pardoned")])

        action = "none"
        for level, data in config["thresholds"].items():
            if active_count >= data["count"]:
                action = data["action"]

        # DM User
        if config.get("dm_enabled"):
            member = guild.get_member(user_id)
            if member:
                next_action = "None"
                # Find next action
                sorted_thresholds = sorted(config["thresholds"].items(), key=lambda x: x[1]["count"])
                for level, data in sorted_thresholds:
                    if data["count"] > active_count:
                        next_action = f"{data['action']} at {data['count']} warnings"
                        break

                dm_text = config.get("dm_template").format(
                    user=member.name,
                    reason=reason,
                    severity=severity,
                    count=active_count,
                    next_action=next_action
                )
                try: await member.send(dm_text)
                except: pass

        # Update Stats
        gid = guild.id
        stats = dm.get_guild_data(gid, "warning_stats", {
            "today": 0,
            "week": 0,
            "severity_breakdown": {"minor": 0, "moderate": 0, "severe": 0},
            "total_pardoned": 0,
            "last_reset": time.time()
        })
        now = time.time()
        if now - stats.get("last_reset", 0) > 86400:
            stats["today"] = 0
            stats["last_reset"] = now
        stats["today"] += 1
        stats["week"] += 1
        stats["severity_breakdown"][severity] = stats["severity_breakdown"].get(severity, 0) + 1
        dm.update_guild_data(gid, "warning_stats", stats)

        # Global Log for Panel
        history = dm.get_guild_data(gid, "warning_history", [])
        history.append({
            "ts": time.time(),
            "user_id": user_id,
            "mod_id": moderator_id,
            "reason": reason,
            "severity": severity
        })
        dm.update_guild_data(gid, "warning_history", history[-20:])

        # Log action
        await self._log_warning(guild, user_id, moderator_id, new_warning, active_count, action)

        # Apply punishment if needed
        if action != "none":
            member = guild.get_member(user_id)
            if member:
                await self._apply_punishment(member, action, f"Threshold met: {active_count} warnings")

        return warning_id

    async def pardon_warning(self, guild: discord.Guild, user_id: int, warning_id: int, reason: str):
        all_warnings = dm.get_guild_data(guild.id, f"user_warnings_{user_id}", [])
        found = False
        for w in all_warnings:
            if w.get("id") == warning_id:
                w["pardoned"] = True
                w["pardon_reason"] = reason
                w["pardon_timestamp"] = time.time()
                found = True
                break

        if found:
            dm.update_guild_data(guild.id, f"user_warnings_{user_id}", all_warnings)
            stats = dm.get_guild_data(guild.id, "warning_stats", {})
            stats["total_pardoned"] = stats.get("total_pardoned", 0) + 1
            dm.update_guild_data(guild.id, "warning_stats", stats)
        return found

    async def delete_warning(self, guild: discord.Guild, user_id: int, warning_id: int):
        all_warnings = dm.get_guild_data(guild.id, f"user_warnings_{user_id}", [])
        new_warnings = [w for w in all_warnings if w.get("id") != warning_id]
        if len(new_warnings) != len(all_warnings):
            dm.update_guild_data(guild.id, f"user_warnings_{user_id}", new_warnings)
            return True
        return False

    async def clear_all_warnings(self, guild: discord.Guild, user_id: int, reason: str):
        all_warnings = dm.get_guild_data(guild.id, f"user_warnings_{user_id}", [])
        for w in all_warnings:
            if not w.get("pardoned"):
                w["pardoned"] = True
                w["pardon_reason"] = f"Mass clear: {reason}"
                w["pardon_timestamp"] = time.time()
        dm.update_guild_data(guild.id, f"user_warnings_{user_id}", all_warnings)
        return len(all_warnings)

    async def _apply_punishment(self, member, action, reason):
        full_reason = f"Warning Threshold: {reason}"
        try:
            if action == "mute_10":
                await member.timeout(timedelta(minutes=10), reason=full_reason)
            elif action == "mute_60":
                await member.timeout(timedelta(hours=1), reason=full_reason)
            elif action == "kick":
                await member.kick(reason=full_reason)
            elif action == "ban":
                await member.ban(reason=full_reason)
        except Exception as e:
            logger.error(f"Failed to apply warning punishment {action}: {e}")

    async def _log_warning(self, guild, user_id, moderator_id, warning, count, action):
        log_ch_id = dm.get_guild_data(guild.id, "log_channel")
        if not log_ch_id: return
        channel = guild.get_channel(log_ch_id)
        if not channel: return

        embed = discord.Embed(title="⚠️ User Warning Issued", color=discord.Color.yellow())
        embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
        embed.add_field(name="Moderator", value=f"<@{moderator_id}>", inline=True)
        embed.add_field(name="Severity", value=warning["severity"].upper(), inline=True)
        embed.add_field(name="Reason", value=warning["reason"], inline=False)
        embed.add_field(name="Total Active", value=str(count), inline=True)
        if action != "none":
            embed.add_field(name="Action Taken", value=action.upper(), inline=True)
        embed.timestamp = discord.utils.utcnow()
        try: await channel.send(embed=embed)
        except: pass

    # Prefix command handlers
    async def cmd_warn(self, message, parts):
        if not message.author.guild_permissions.manage_messages: return
        if len(parts) < 3:
            return await message.channel.send("Usage: `!warn @user <reason>`")

        target = message.mentions[0] if message.mentions else None
        if not target: return await message.channel.send("User not found.")

        reason = " ".join(parts[2:])
        wid = await self.issue_warning(message.guild, target.id, message.author.id, reason)
        await message.channel.send(f"✅ Warning issued (ID: {wid}) to {target.display_name}")

    async def cmd_warnings(self, message, parts):
        target = message.mentions[0] if message.mentions else message.author
        warns = self.get_warnings(message.guild.id, target.id)

        if not warns:
            return await message.channel.send(f"{target.display_name} has no warnings.")

        embed = discord.Embed(title=f"Warnings for {target.display_name}", color=discord.Color.orange())
        active_warns = [w for w in warns if w.get("active") and not w.get("pardoned")]

        desc = ""
        for w in warns[-10:]:
            status = "✅ Active" if w.get("active") and not w.get("pardoned") else "⚪ Inactive/Pardoned"
            date = datetime.fromtimestamp(w.get("timestamp", 0)).strftime("%Y-%m-%d")
            desc += f"**ID: {w['id']}** | {status} | {w['severity']} | {date}\nReason: {w['reason']}\n\n"

        embed.description = desc
        embed.add_field(name="Total Active", value=str(len(active_warns)))
        await message.channel.send(embed=embed)

    async def cmd_clearwarn(self, message, parts):
        if not message.author.guild_permissions.manage_messages: return
        if len(parts) < 3:
            return await message.channel.send("Usage: `!clearwarn @user <id>`")

        target = message.mentions[0] if message.mentions else None
        if not target: return await message.channel.send("User not found.")

        try: wid = int(parts[2])
        except: return await message.channel.send("Invalid ID.")

        success = await self.pardon_warning(message.guild, target.id, wid, "Manual clear")
        if success: await message.channel.send(f"✅ Warning {wid} pardoned for {target.display_name}")
        else: await message.channel.send("Warning ID not found.")

    async def cmd_clearallwarns(self, message, parts):
        if not message.author.guild_permissions.administrator: return
        if len(parts) < 2:
            return await message.channel.send("Usage: `!clearallwarns @user`")

        target = message.mentions[0] if message.mentions else None
        if not target: return await message.channel.send("User not found.")

        count = await self.clear_all_warnings(message.guild, target.id, "Manual mass clear")
        await message.channel.send(f"✅ Cleared all ({count}) warnings for {target.display_name}")

    async def cmd_kick(self, message, parts):
        if not message.author.guild_permissions.kick_members:
            return await message.channel.send("❌ You need kick permissions to use this command.")

        if len(parts) < 2:
            return await message.channel.send("Usage: `!kick @user [reason]`")

        target = message.mentions[0] if message.mentions else None
        if not target:
            return await message.channel.send("❌ User not found.")

        if target == message.author:
            return await message.channel.send("❌ You cannot kick yourself.")

        if target.guild_permissions.administrator:
            return await message.channel.send("❌ You cannot kick an administrator.")

        reason = " ".join(parts[1:]) if len(parts) > 1 else "No reason provided"

        try:
            await target.kick(reason=reason)
            embed = discord.Embed(
                title="👢 User Kicked",
                description=f"**{target.display_name}** has been kicked from the server.",
                color=discord.Color.orange()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Moderator", value=message.author.display_name, inline=True)
            await message.channel.send(embed=embed)
        except discord.Forbidden:
            await message.channel.send("❌ I don't have permission to kick this user.")
        except Exception as e:
            logger.error(f"Error kicking user: {e}")
            await message.channel.send("❌ Failed to kick user.")

    async def cmd_ban(self, message, parts):
        if not message.author.guild_permissions.ban_members:
            return await message.channel.send("❌ You need ban permissions to use this command.")

        if len(parts) < 2:
            return await message.channel.send("Usage: `!ban @user [reason]`")

        target = message.mentions[0] if message.mentions else None
        if not target:
            return await message.channel.send("❌ User not found.")

        if target == message.author:
            return await message.channel.send("❌ You cannot ban yourself.")

        if target.guild_permissions.administrator:
            return await message.channel.send("❌ You cannot ban an administrator.")

        reason = " ".join(parts[1:]) if len(parts) > 1 else "No reason provided"

        try:
            await target.ban(reason=reason)
            embed = discord.Embed(
                title="🔨 User Banned",
                description=f"**{target.display_name}** has been banned from the server.",
                color=discord.Color.red()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Moderator", value=message.author.display_name, inline=True)
            await message.channel.send(embed=embed)
        except discord.Forbidden:
            await message.channel.send("❌ I don't have permission to ban this user.")
        except Exception as e:
            logger.error(f"Error banning user: {e}")
            await message.channel.send("❌ Failed to ban user.")

    async def cmd_mute(self, message, parts):
        if not message.author.guild_permissions.moderate_members:
            return await message.channel.send("❌ You need moderate members permissions to use this command.")

        if len(parts) < 3:
            return await message.channel.send("Usage: `!mute @user <duration> [reason]`\nExample: `!mute @user 1h Spamming`")

        target = message.mentions[0] if message.mentions else None
        if not target:
            return await message.channel.send("❌ User not found.")

        if target == message.author:
            return await message.channel.send("❌ You cannot mute yourself.")

        # Parse duration
        duration_str = parts[1]
        try:
            # Simple duration parsing: 1h, 30m, 2d, etc.
            import re
            match = re.match(r'^(\d+)([smhd])$', duration_str.lower())
            if not match:
                return await message.channel.send("❌ Invalid duration format. Use: 30m, 1h, 2d, etc.")

            amount, unit = match.groups()
            amount = int(amount)

            if unit == 's':
                duration = amount
            elif unit == 'm':
                duration = amount * 60
            elif unit == 'h':
                duration = amount * 3600
            elif unit == 'd':
                duration = amount * 86400
            else:
                return await message.channel.send("❌ Invalid time unit. Use: s, m, h, d")

            if duration > 2419200:  # 28 days max
                return await message.channel.send("❌ Maximum mute duration is 28 days.")

        except Exception as e:
            return await message.channel.send("❌ Invalid duration format.")

        reason = " ".join(parts[2:]) if len(parts) > 2 else "No reason provided"

        try:
            await target.timeout(discord.utils.utcnow() + timedelta(seconds=duration), reason=reason)
            embed = discord.Embed(
                title="🔇 User Muted",
                description=f"**{target.display_name}** has been muted.",
                color=discord.Color.yellow()
            )
            embed.add_field(name="Duration", value=duration_str, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Moderator", value=message.author.display_name, inline=True)
            await message.channel.send(embed=embed)
        except discord.Forbidden:
            await message.channel.send("❌ I don't have permission to mute this user.")
        except Exception as e:
            logger.error(f"Error muting user: {e}")
            await message.channel.send("❌ Failed to mute user.")

    async def cmd_modstats(self, message, parts):
        """Show moderation statistics"""
        if not message.author.guild_permissions.administrator:
            return await message.channel.send("❌ Only administrators can view moderation statistics.")

        try:
            # Get warning stats
            warning_stats = self.get_stats(message.guild.id)
            most_warned = self.get_most_warned(message.guild.id)

            embed = discord.Embed(
                title="📊 Moderation Statistics",
                description="Server moderation overview",
                color=discord.Color.blue()
            )

            embed.add_field(
                name="⚠️ Warnings Today",
                value=str(warning_stats.get("today", 0)),
                inline=True
            )
            embed.add_field(
                name="📅 Warnings This Week",
                value=str(warning_stats.get("week", 0)),
                inline=True
            )
            embed.add_field(
                name="🗑️ Total Pardoned",
                value=str(warning_stats.get("total_pardoned", 0)),
                inline=True
            )

            severity_breakdown = warning_stats.get("severity_breakdown", {})
            severity_text = "\n".join([f"{k.title()}: {v}" for k, v in severity_breakdown.items()])
            embed.add_field(
                name="📈 Severity Breakdown",
                value=severity_text or "No data",
                inline=False
            )

            if most_warned:
                most_warned_text = "\n".join([
                    f"{i+1}. <@{entry['user_id']}> - {entry['count']} warnings"
                    for i, entry in enumerate(most_warned[:5])
                ])
                embed.add_field(
                    name="👥 Most Warned Users",
                    value=most_warned_text,
                    inline=False
                )

            embed.set_footer(text=f"Stats for {message.guild.name}")
            await message.channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in cmd_modstats: {e}")
            await message.channel.send("❌ Failed to retrieve moderation statistics.")

    async def setup(self, interaction: discord.Interaction):
        self.save_config(interaction.guild_id, self.get_config(interaction.guild_id))
        return True

    # ---- Slash-command adapters ----

    async def warn_user(self, interaction, user: discord.Member, reason: str, severity: str = "medium"):
        """Slash-command adapter for issuing a warning."""
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("❌ You need **Manage Messages** permission.", ephemeral=True)
        sev = {"low": "minor", "medium": "moderate", "high": "severe"}.get(severity, "moderate")
        await self.issue_warning(interaction.guild, user.id, interaction.user.id, reason, sev)
        embed = discord.Embed(
            title="⚠️ Warning Issued",
            description=f"{user.mention} has been warned.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Severity", value=sev.title(), inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    async def get_user_warnings(self, interaction, user: discord.Member):
        """Slash-command adapter for viewing a user's warnings."""
        warnings = self.get_warnings(interaction.guild.id, user.id)
        active = [w for w in warnings if w.get("active") and not w.get("pardoned")]
        if not warnings:
            return await interaction.response.send_message(f"✅ {user.mention} has no warnings.", ephemeral=True)
        embed = discord.Embed(
            title=f"⚠️ Warnings for {user.display_name}",
            description=f"**{len(active)} active** / {len(warnings)} total",
            color=discord.Color.orange()
        )
        for w in warnings[-10:]:
            active_flag = "❌" if w.get("active") and not w.get("pardoned") else "✅"
            embed.add_field(
                name=f"{active_flag} #{w.get('id')} — {str(w.get('severity', 'minor')).title()}",
                value=f"{w.get('reason', 'No reason')}\n<@{w.get('moderator_id', 0)}> • <t:{int(w.get('timestamp', 0))}:R>",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

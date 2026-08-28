"""
Server Query Engine - Live Discord Server Introspection
Provides read-only access to live server state for the AI bot.
"""
import discord
from typing import Any, Dict, List, Optional
from datetime import datetime

class ServerQueryEngine:
    def __init__(self, bot):
        self.bot = bot

    def get_guild(self, guild_id: int) -> Optional[discord.Guild]:
        """Get guild object from ID."""
        return self.bot.get_guild(guild_id)

    def get_member(self, guild_id: int, user_id: int) -> Optional[discord.Member]:
        """Get member object from guild and user ID."""
        guild = self.get_guild(guild_id)
        if guild:
            return guild.get_member(user_id)
        return None

    async def query_server_info(self, guild_id: int) -> Dict[str, Any]:
        """Get comprehensive server information."""
        guild = self.get_guild(guild_id)
        if not guild:
            return {"error": "Guild not found"}

        online_members = [
            {
                "name": m.name,
                "id": m.id,
                "nick": m.nick,
                "status": str(m.status),
                "is_bot": m.bot
            }
            for m in guild.members
            if not m.bot and m.status != discord.Status.offline
        ]
        online_count = len(online_members)
        
        return {
            "name": guild.name,
            "id": guild.id,
            "member_count": guild.member_count,
            "online_count": online_count,
            "online_members": online_members,
            "channel_count": len(guild.channels),
            "role_count": len(guild.roles),
            "owner": str(guild.owner),
            "created_at": guild.created_at.isoformat(),
            "description": guild.description,
            "verification_level": str(guild.verification_level),
            "explicit_content_filter": str(guild.explicit_content_filter),
        }

    async def query_channels(self, guild_id: int, channel_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all channels or filter by type."""
        guild = self.get_guild(guild_id)
        if not guild:
            return []

        channels = []
        for channel in guild.channels:
            if channel_type:
                if channel_type == "text" and not isinstance(channel, discord.TextChannel):
                    continue
                if channel_type == "voice" and not isinstance(channel, discord.VoiceChannel):
                    continue
                if channel_type == "category" and not isinstance(channel, discord.CategoryChannel):
                    continue
            
            channels.append({
                "name": channel.name,
                "id": channel.id,
                "type": channel.type.name,
                "category": channel.category.name if channel.category else None,
                "position": channel.position,
            })
        return channels

    async def query_roles(self, guild_id: int) -> List[Dict[str, Any]]:
        """List all roles with permissions."""
        guild = self.get_guild(guild_id)
        if not guild:
            return []

        roles = []
        for role in guild.roles:
            roles.append({
                "name": role.name,
                "id": role.id,
                "color": str(role.color),
                "hoist": role.hoist,
                "mentionable": role.mentionable,
                "managed": role.managed,
                "position": role.position,
                "permissions": role.permissions.value,
            })
        return roles

    async def query_members(self, guild_id: int, query: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """List members, optionally filtered by name query."""
        guild = self.get_guild(guild_id)
        if not guild:
            return []

        members = []
        count = 0
        for member in guild.members:
            if query and query.lower() not in member.name.lower() and query.lower() not in (member.nick or "").lower():
                continue
            if count >= limit:
                break
            
            members.append({
                "name": member.name,
                "id": member.id,
                "nick": member.nick,
                "status": str(member.status),
                "activity": str(member.activity) if member.activity else None,
                "roles": [r.name for r in member.roles[1:]],  # Exclude @everyone
                "joined_at": member.joined_at.isoformat() if member.joined_at else None,
                "is_bot": member.bot,
            })
            count += 1
        return members

    async def query_member_details(self, guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific member."""
        member = self.get_member(guild_id, user_id)
        if not member:
            return None

        return {
            "name": member.name,
            "id": member.id,
            "nick": member.nick,
            "discriminator": member.discriminator,
            "avatar_url": member.avatar.url if member.avatar else None,
            "status": str(member.status),
            "activity": str(member.activity) if member.activity else None,
            "roles": [r.name for r in member.roles[1:]],
            "role_ids": [r.id for r in member.roles[1:]],
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            "created_at": member.created_at.isoformat(),
            "is_bot": member.bot,
            "top_role": member.top_role.name,
            "guild_permissions": member.guild_permissions.value,
        }

    async def query_economy_leaderboard(self, guild_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top users by economy balance."""
        try:
            from data_manager import DataManager
            dm = DataManager()
            
            # Get all user data for this guild
            guild_data = dm.get_guild_data(guild_id)
            user_balances = []
            
            for user_id_str, user_data in guild_data.get("users", {}).items():
                coins = user_data.get("coins", 0)
                if coins > 0:
                    member = self.get_member(guild_id, int(user_id_str))
                    user_balances.append({
                        "user_id": int(user_id_str),
                        "name": member.name if member else f"User_{user_id_str}",
                        "coins": coins,
                    })
            
            # Sort by coins descending
            user_balances.sort(key=lambda x: x["coins"], reverse=True)
            return user_balances[:limit]
        except Exception as e:
            return [{"error": str(e)}]

    async def query_xp_leaderboard(self, guild_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top users by XP level."""
        try:
            from data_manager import DataManager
            dm = DataManager()
            
            guild_data = dm.get_guild_data(guild_id)
            user_xp = []
            
            for user_id_str, user_data in guild_data.get("users", {}).items():
                xp = user_data.get("xp", 0)
                level = user_data.get("level", 1)
                if xp > 0 or level > 1:
                    member = self.get_member(guild_id, int(user_id_str))
                    user_xp.append({
                        "user_id": int(user_id_str),
                        "name": member.name if member else f"User_{user_id_str}",
                        "xp": xp,
                        "level": level,
                    })
            
            user_xp.sort(key=lambda x: x["xp"], reverse=True)
            return user_xp[:limit]
        except Exception as e:
            return [{"error": str(e)}]

    async def query_pending_applications(self, guild_id: int) -> List[Dict[str, Any]]:
        """Get pending staff applications."""
        try:
            from data_manager import DataManager
            dm = DataManager()
            
            guild_data = dm.get_guild_data(guild_id)
            applications = guild_data.get("staff_applications", [])
            
            pending = []
            for app in applications:
                if app.get("status") == "pending":
                    pending.append({
                        "user_id": app.get("user_id"),
                        "username": app.get("username"),
                        "reason": app.get("reason"),
                        "experience": app.get("experience"),
                        "applied_at": app.get("applied_at"),
                    })
            return pending
        except Exception as e:
            return [{"error": str(e)}]

    async def query_active_shifts(self, guild_id: int) -> List[Dict[str, Any]]:
        """Get currently active staff shifts."""
        try:
            from data_manager import DataManager
            dm = DataManager()
            
            guild_data = dm.get_guild_data(guild_id)
            shifts = guild_data.get("shift_logs", [])
            
            active = []
            for shift in shifts:
                if shift.get("end_time") is None:
                    active.append({
                        "user_id": shift.get("user_id"),
                        "username": shift.get("username"),
                        "start_time": shift.get("start_time"),
                        "duration_minutes": None,
                    })
            return active
        except Exception as e:
            return [{"error": str(e)}]

    async def query_recent_messages(self, channel_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch recent messages from a text channel."""
        try:
            channel = self.bot.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return [{"error": "Channel not found or not a text channel"}]
            
            messages = []
            async for msg in channel.history(limit=limit):
                messages.append({
                    "id": msg.id,
                    "author": msg.author.name,
                    "author_id": msg.author.id,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                    "attachments": len(msg.attachments),
                    "embeds": len(msg.embeds),
                })
            return messages
        except Exception as e:
            return [{"error": str(e)}]

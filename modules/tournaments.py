import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from data_manager import dm
from logger import logger


class TournamentStatus(Enum):
    SETUP = "setup"
    REGISTRATION = "registration"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TournamentType(Enum):
    SINGLE_ELIMINATION = "single_elimination"
    DOUBLE_ELIMINATION = "double_elimination"
    ROUND_ROBIN = "round_robin"
    FREE_FOR_ALL = "free_for_all"
    TEAM_VS_TEAM = "team_vs_team"


@dataclass
class Tournament:
    id: str
    guild_id: int
    name: str
    description: str
    tournament_type: TournamentType
    status: TournamentStatus
    max_participants: int
    min_participants: int
    prize_pool: dict
    registration_end: float
    start_time: float
    rounds: List[dict]
    participants: List[int]
    teams: Dict[str, List[int]]
    bracket: List[dict]
    winner: Optional[int]
    created_by: int
    created_at: float
    channel_id: Optional[int]


@dataclass
class Match:
    id: str
    tournament_id: str
    round: int
    match_number: int
    player1: Optional[int]
    player2: Optional[int]
    player1_score: int
    player2_score: int
    winner: Optional[int]
    status: str
    next_match: Optional[str]


class TournamentSystem:
    def __init__(self, bot):
        self.bot = bot
        self._tournaments: Dict[str, Tournament] = {}
        self._active_matches: Dict[str, Match] = {}
        self._load_tournaments()

    def _load_tournaments(self):
        data = dm.load_json("tournaments", default={})
        
        for tourney_id, t_data in data.items():
            try:
                tournament = Tournament(
                    id=tourney_id,
                    guild_id=t_data["guild_id"],
                    name=t_data["name"],
                    description=t_data["description"],
                    tournament_type=TournamentType(t_data["tournament_type"]),
                    status=TournamentStatus(t_data["status"]),
                    max_participants=t_data["max_participants"],
                    min_participants=t_data["min_participants"],
                    prize_pool=t_data["prize_pool"],
                    registration_end=t_data["registration_end"],
                    start_time=t_data["start_time"],
                    rounds=t_data.get("rounds", []),
                    participants=t_data.get("participants", []),
                    teams=t_data.get("teams", {}),
                    bracket=t_data.get("bracket", []),
                    winner=t_data.get("winner"),
                    created_by=t_data["created_by"],
                    created_at=t_data["created_at"],
                    channel_id=t_data.get("channel_id")
                )
                self._tournaments[tourney_id] = tournament
            except Exception as e:
                logger.error(f"Failed to load tournament {tourney_id}: {e}")

    def _save_tournament(self, tournament: Tournament):
        data = dm.load_json("tournaments", default={})
        data[tournament.id] = {
            "guild_id": tournament.guild_id,
            "name": tournament.name,
            "description": tournament.description,
            "tournament_type": tournament.tournament_type.value,
            "status": tournament.status.value,
            "max_participants": tournament.max_participants,
            "min_participants": tournament.min_participants,
            "prize_pool": tournament.prize_pool,
            "registration_end": tournament.registration_end,
            "start_time": tournament.start_time,
            "rounds": tournament.rounds,
            "participants": tournament.participants,
            "teams": tournament.teams,
            "bracket": tournament.bracket,
            "winner": tournament.winner,
            "created_by": tournament.created_by,
            "created_at": tournament.created_at,
            "channel_id": tournament.channel_id
        }
        dm.save_json("tournaments", data)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "tournament_settings", {
            "enabled": True,
            "default_max": 32,
            "default_min": 4,
            "default_prize": {"coins": 500, "xp": 250}
        })


    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "tournament_settings", settings)
        
        help_embed = discord.Embed(
            title="🏆 Tournament System",
            description="Create competitive tournaments with brackets, prizes, and seasons.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Create tournaments with auto-generated brackets. Prize pool comes from economy. Supports single elimination, round robin, and team vs team.",
            inline=False
        )
        help_embed.add_field(
            name="!tournaments",
            value="List active tournaments.",
            inline=False
        )
        help_embed.add_field(
            name="!join <tournament>",
            value="Join a tournament.",
            inline=False
        )
        help_embed.add_field(
            name="!tournamentleaderboard",
            value="View tournament winners leaderboard.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["tournaments"] = json.dumps({
            "command_type": "list_tournaments"
        })
        custom_cmds["join"] = json.dumps({
            "command_type": "join_tournament"
        })
        custom_cmds["tournamentleaderboard"] = json.dumps({
            "command_type": "tournament_leaderboard"
        })
        custom_cmds["tournament create"] = json.dumps({
            "command_type": "create_tournament"
        })
        custom_cmds["help tournaments"] = json.dumps({
            "command_type": "help_embed",
            "title": "🏆 Tournament System",
            "description": "Create competitive tournaments.",
            "fields": [
                {"name": "!tournaments", "value": "List active tournaments.", "inline": False},
                {"name": "!join <tournament>", "value": "Join a tournament.", "inline": False},
                {"name": "!tournamentleaderboard", "value": "View leaderboard.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands

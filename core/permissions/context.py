from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class RequestContext:
    """Metadata about a single request flowing through the permission engine."""
    guild_id: Optional[int]
    user_id: Optional[int]
    action: str
    source: str = "command"          # command | ai | scheduled | automod | system
    is_admin: bool = False
    is_owner: bool = False
    is_bot_identity: bool = False     # scheduled/system tasks running as the bot
    user_top_role_position: int = -1
    target_role_position: Optional[int] = None
    bot_top_role_position: int = -1
    metadata: Dict[str, Any] = field(default_factory=dict)

import hmac
import hashlib
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger("security")

def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify that a webhook request came from a trusted source."""
    if not signature or not secret:
        return False
    
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(f"sha256={expected}", signature)

def verify_discord_interaction(interaction, bot) -> bool:
    """Verify Discord interaction is legitimate."""
    return interaction.guild is not None

class SecurityLogger:
    """Security event logging for audit trails."""
    
    def __init__(self):
        self.logger = logging.getLogger("security")
        if not self.logger.handlers:
            try:
                os.makedirs("logs", exist_ok=True)
                from logging.handlers import RotatingFileHandler
                handler = RotatingFileHandler(
                    "logs/security.log",
                    maxBytes=10*1024*1024,
                    backupCount=10,
                    encoding="utf-8"
                )
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s | %(levelname)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S"
                ))
                self.logger.addHandler(handler)
                self.logger.setLevel(logging.INFO)
            except Exception as e:
                self.logger = None
    
    def log_auth_failure(self, user_id: int, guild_id: int, reason: str):
        if self.logger:
            self.logger.warning(f"AUTH_FAILURE | User: {user_id} | Guild: {guild_id} | Reason: {reason}")
    
    def log_permission_change(self, admin_id: int, target_id: int, guild_id: int, change: str):
        if self.logger:
            self.logger.info(f"PERMISSION_CHANGE | Admin: {admin_id} | Target: {target_id} | Guild: {guild_id} | Change: {change}")
    
    def log_command_blocked(self, user_id: int, guild_id: int, command: str, reason: str):
        if self.logger:
            self.logger.warning(f"COMMAND_BLOCKED | User: {user_id} | Guild: {guild_id} | Command: {command} | Reason: {reason}")
    
    def log_action_executed(self, user_id: int, guild_id: int, action: str, success: bool):
        if self.logger:
            status = "SUCCESS" if success else "FAILURE"
            self.logger.info(f"ACTION_{status} | User: {user_id} | Guild: {guild_id} | Action: {action}")

security_logger = SecurityLogger()
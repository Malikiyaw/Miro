import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path

class Logger:
    """
    Comprehensive logging system for the bot.
    Features:
    - Multiple log levels
    - Rotating file handlers
    - Console output
    - Error tracking
    - Discord channel logging for critical errors
    """

    def __init__(self, log_dir: str = "logs", discord_webhook_url: str = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.discord_webhook = discord_webhook_url

        # Create logger
        self.logger = logging.getLogger("MiroBot")
        self.logger.setLevel(logging.DEBUG)

        # Remove existing handlers
        self.logger.handlers.clear()

        # Create formatters
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )
        console_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )

        # File handlers
        self._setup_file_handlers(file_formatter)

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

    def _setup_file_handlers(self, formatter):
        """Set up rotating file handlers for different log levels."""

        # General log (all levels)
        general_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "bot.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        general_handler.setLevel(logging.DEBUG)
        general_handler.setFormatter(formatter)
        self.logger.addHandler(general_handler)

        # Error log (ERROR and above)
        error_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "error.log",
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        self.logger.addHandler(error_handler)

        # Command log (for tracking user commands)
        command_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "commands.log",
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3
        )
        command_handler.setLevel(logging.INFO)
        command_handler.setFormatter(formatter)
        command_handler.addFilter(lambda record: hasattr(record, 'command') and record.command)
        self.logger.addHandler(command_handler)

    def debug(self, message: str, *args, **kwargs):
        """Log debug message."""
        self.logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args, **kwargs):
        """Log info message."""
        self.logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs):
        """Log warning message."""
        self.logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs):
        """Log error message."""
        self.logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args, **kwargs):
        """Log critical message."""
        self.logger.critical(message, *args, **kwargs)

    def exception(self, message: str, *args, **kwargs):
        """Log exception with traceback."""
        self.logger.exception(message, *args, **kwargs)

    def log_command(self, user_id: int, guild_id: int, command: str, args: str = None):
        """Log user command usage."""
        self.logger.info(
            f"Command executed: {command} by user {user_id} in guild {guild_id}",
            extra={'command': True}
        )

    def log_system_event(self, guild_id: int, event_type: str, details: str = None):
        """Log system events."""
        self.logger.info(f"System event: {event_type} in guild {guild_id} - {details or ''}")

    async def log_to_discord(self, bot, channel_id: int, message: str, embed=None):
        """Log critical errors to a Discord channel."""
        if not channel_id or not bot:
            return

        try:
            channel = bot.get_channel(channel_id)
            if channel:
                if embed:
                    await channel.send(embed=embed)
                else:
                    await channel.send(f"🚨 **Bot Alert**\n{message}")
        except Exception as e:
            self.error(f"Failed to log to Discord channel: {e}")

    def get_recent_errors(self, limit: int = 10) -> list:
        """Get recent error log entries."""
        try:
            error_log = self.log_dir / "error.log"
            if not error_log.exists():
                return []

            with open(error_log, 'r') as f:
                lines = f.readlines()[-limit:]
                return [line.strip() for line in lines]
        except Exception as e:
            self.error(f"Failed to read error log: {e}")
            return []

    def get_log_stats(self) -> dict:
        """Get statistics about log files."""
        stats = {}
        try:
            for log_file in ["bot.log", "error.log", "commands.log"]:
                path = self.log_dir / log_file
                if path.exists():
                    stats[log_file] = {
                        "size": path.stat().st_size,
                        "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat()
                    }
                else:
                    stats[log_file] = {"size": 0, "modified": None}
        except Exception as e:
            self.error(f"Failed to get log stats: {e}")

        return stats

# Global logger instance
logger = Logger()
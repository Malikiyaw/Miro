import json
import os
import shutil
import sqlite3
import aiosqlite
from typing import Any, Dict, Optional, List, Tuple
import threading
import time
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
import base64
import hashlib
import tempfile

from logger import logger

class DataManager:
    """
    Atomic writing system for zero data loss.
    Features:
    - Atomic file writes with temporary files
    - Encryption for sensitive data
    - SQLite backend for history
    - Thread-safe operations
    - Automatic backups
    """

    def __init__(self, data_dir: str = "data", use_sqlite: bool = True):
        self.data_dir = data_dir
        self.use_sqlite = use_sqlite
        self._lock = threading.RLock()
        self._cache = {}

        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

        if self.use_sqlite:
            self._init_sqlite()

        self._init_encryption()

    def _init_encryption(self):
        """Initialize encryption for sensitive data."""
        key_file = os.path.join(self.data_dir, ".encryption_key")

        if os.getenv("ENCRYPTION_KEY"):
            self.cipher = Fernet(os.getenv("ENCRYPTION_KEY").encode())
        elif os.path.exists(key_file):
            with open(key_file, "rb") as f:
                self.cipher = Fernet(f.read())
        else:
            generated = Fernet.generate_key()
            with open(key_file, "wb") as f:
                f.write(generated)
            os.chmod(key_file, 0o600)
            self.cipher = Fernet(generated)

    def _init_sqlite(self):
        """Initialize SQLite database for conversation history."""
        self.db_path = os.path.join(self.data_dir, "conversation_history.db")

        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            # Create tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exchanges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    importance_score REAL DEFAULT 0.5
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_guild_user_timestamp
                ON exchanges(guild_id, user_id, timestamp DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON exchanges(timestamp DESC)
            """)

            # Table for system events
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)

            # Table for conversation summaries
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_summaries_guild_user_timestamp
                ON conversation_summaries(guild_id, user_id, timestamp DESC)
            """)

            conn.commit()
            conn.close()

    def _get_file_path(self, filename: str) -> str:
        """Get safe file path for a filename."""
        safe_name = "".join(c for c in filename if c.isalnum() or c in "._-")
        if not safe_name.endswith(".json"):
            safe_name += ".json"
        return os.path.join(self.data_dir, safe_name)

    def _encrypt_data(self, data: str) -> str:
        """Encrypt sensitive data."""
        return self.cipher.encrypt(data.encode()).decode()

    def _decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data."""
        try:
            return self.cipher.decrypt(encrypted_data.encode()).decode()
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return ""

    def save_json(self, filename: str, data: Any, encrypt: bool = False):
        """Atomically save data to JSON file."""
        if self.use_sqlite and filename == "conversation_history":
            return  # Handled separately

        with self._lock:
            file_path = self._get_file_path(filename)
            temp_path = f"{file_path}.tmp"

            try:
                # Prepare data
                if encrypt and isinstance(data, str):
                    data = self._encrypt_data(data)
                elif not isinstance(data, str):
                    data = json.dumps(data, indent=2, ensure_ascii=False)

                # Write to temporary file
                with open(temp_path, "w", encoding="utf-8") as f:
                    if isinstance(data, str):
                        f.write(data)
                    else:
                        json.dump(data, f, indent=2, ensure_ascii=False)

                # Atomic move
                os.replace(temp_path, file_path)

                # Update cache
                self._cache[filename] = data

            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise e

    def load_json(self, filename: str, default: Any = None, decrypt: bool = False) -> Any:
        """Load data from JSON file with caching."""
        with self._lock:
            if filename in self._cache:
                return self._cache[filename]

            file_path = self._get_file_path(filename)

            if not os.path.exists(file_path):
                return default() if callable(default) else default

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if decrypt:
                    if isinstance(data, str):
                        data = self._decrypt_data(data)
                    elif isinstance(data, dict):
                        # Decrypt string values in dict
                        for key, value in data.items():
                            if isinstance(value, str) and len(value) > 50:  # Likely encrypted
                                try:
                                    data[key] = self._decrypt_data(value)
                                except:
                                    pass

                self._cache[filename] = data
                return data

            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load {filename}: {e}")
                return default() if callable(default) else default

    def get_guild_data(self, guild_id: int, key: str, default: Any = None) -> Any:
        """Get data for a specific guild."""
        guild_file = f"guild_{guild_id}"
        data = self.load_json(guild_file, default={})
        if not isinstance(data, dict):
            logger.warning(f"Corrupted guild data for {guild_id} (type: {type(data).__name__}), resetting")
            data = {}
            self.save_json(guild_file, data)
            self._cache[guild_file] = data
        return data.get(key, default)

    def update_guild_data(self, guild_id: int, key: str, value: Any):
        """Update data for a specific guild."""
        guild_file = f"guild_{guild_id}"
        data = self.load_json(guild_file, default={})
        if not isinstance(data, dict):
            data = {}
        data[key] = value
        self.save_json(guild_file, data)
        self._cache[guild_file] = data

    def delete_guild_data(self, guild_id: int, key: str):
        """Delete a key from guild data."""
        guild_file = f"guild_{guild_id}"
        data = self.load_json(guild_file, default={})
        if not isinstance(data, dict):
            data = {}
        if key in data:
            del data[key]
            self.save_json(guild_file, data)

    def _decrypt_key_entry(self, entry):
        """Return a shallow copy of a key-entry dict with 'api_key' decrypted."""
        if not isinstance(entry, dict):
            return entry
        out = dict(entry)
        enc = out.get("api_key")
        if isinstance(enc, str) and enc:
            decrypted = self._decrypt_data(enc)
            if decrypted:
                out["api_key"] = decrypted
            elif len(enc) < 60:
                # Value was likely stored unencrypted by older versions
                out["api_key"] = enc
            else:
                out.pop("api_key", None)
        return out

    def get_guild_api_key(self, guild_id: int, provider: str = None) -> Optional[Dict]:
        """Get API key configuration for a specific guild (api_key values are decrypted)."""
        api_keys = self.load_json("guild_api_keys", default={})
        if not isinstance(api_keys, dict):
            api_keys = {}
        guild_data = api_keys.get(str(guild_id), {})
        if not isinstance(guild_data, dict):
            guild_data = {}
        if provider:
            return self._decrypt_key_entry(guild_data.get("providers", {}).get(provider))
        out = dict(guild_data)
        if isinstance(out.get("providers"), dict):
            out["providers"] = {
                p: self._decrypt_key_entry(cfg) for p, cfg in out["providers"].items()
            }
        return out

    def update_guild_api_key(self, guild_id: int, provider: str, config: Dict):
        """Update API key configuration for a specific guild and provider."""
        api_keys = self.load_json("guild_api_keys", default={})
        if not isinstance(api_keys, dict):
            api_keys = {}
        if str(guild_id) not in api_keys:
            api_keys[str(guild_id)] = {}
        if "providers" not in api_keys[str(guild_id)]:
            api_keys[str(guild_id)]["providers"] = {}
        api_keys[str(guild_id)]["providers"][provider] = config
        self.save_json("guild_api_keys", api_keys)
        self._cache["guild_api_keys"] = api_keys

    def set_guild_api_key(self, guild_id: int, api_key: str, provider: str):
        """Encrypt, store, and activate an API key for a guild and provider."""
        encrypted = self._encrypt_data(api_key)
        self.update_guild_api_key(guild_id, provider, {
            "api_key": encrypted,
            "provider": provider,
            "updated_at": time.time(),
        })
        # Mark the provider active so AIClient routes requests to it
        api_keys = self.load_json("guild_api_keys", default={})
        if isinstance(api_keys.get(str(guild_id)), dict):
            api_keys[str(guild_id)]["provider"] = provider
            self.save_json("guild_api_keys", api_keys)
            self._cache["guild_api_keys"] = api_keys

    def clear_guild_api_key(self, guild_id: int, provider: str) -> bool:
        """Remove a stored key for a provider. Returns True if something was removed."""
        api_keys = self.load_json("guild_api_keys", default={})
        guild_data = api_keys.get(str(guild_id))
        if not isinstance(guild_data, dict):
            return False
        removed = False
        providers = guild_data.get("providers", {})
        if provider in providers:
            del providers[provider]
            removed = True
        if guild_data.get("provider") == provider:
            guild_data["provider"] = None
        if removed or guild_data.get("provider", "missing") is None:
            self.save_json("guild_api_keys", api_keys)
            self._cache["guild_api_keys"] = api_keys
        return removed

    async def save_exchange(self, guild_id: int, user_id: int, role: str, content: str, importance_score: float = 0.5):
        """Save conversation exchange to SQLite."""
        if not self.use_sqlite:
            return False

        try:
            async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute(
                    "INSERT INTO exchanges (guild_id, user_id, role, content, timestamp, importance_score) VALUES (?, ?, ?, ?, ?, ?)",
                    (guild_id, user_id, role, content, time.time(), importance_score)
                )
        except Exception as e:
            logger.error(f"Failed to save exchange: {e}")
            return False
        return True

    async def get_conversation_history(self, guild_id: int, user_id: int, limit: int = 50) -> List[Dict]:
        """Get conversation history for a user."""
        if not self.use_sqlite:
            return []

        try:
            async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
                cursor = await db.execute(
                    "SELECT role, content, timestamp FROM exchanges WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (guild_id, user_id, limit)
                )
                rows = await cursor.fetchall()

                history = []
                for row in reversed(rows):
                    history.append({
                        "role": row[0],
                        "content": row[1],
                        "timestamp": row[2]
                    })

                return history
        except Exception as e:
            logger.error(f"Failed to get conversation history: {e}")
            return []

    async def load_exchanges(self, guild_id: int, user_id: int, limit: int = 50) -> List[Dict]:
        """Alias for get_conversation_history (used by history_manager)."""
        return await self.get_conversation_history(guild_id, user_id, limit)

    async def save_conversation_summary(self, guild_id: int, user_id: int, summary: str):
        """Save a summarized conversation block to SQLite."""
        if not self.use_sqlite:
            return False
        try:
            async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute(
                    "INSERT INTO conversation_summaries (guild_id, user_id, summary, timestamp) VALUES (?, ?, ?, ?)",
                    (guild_id, user_id, summary, time.time())
                )
            return True
        except Exception as e:
            logger.error(f"Failed to save conversation summary: {e}")
            return False

    async def load_conversation_summaries(self, guild_id: int, user_id: int, limit: int = 5) -> List[Dict]:
        """Load past conversation summaries for a user."""
        if not self.use_sqlite:
            return []
        try:
            async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
                cursor = await db.execute(
                    "SELECT summary, timestamp FROM conversation_summaries "
                    "WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (guild_id, user_id, limit)
                )
                rows = await cursor.fetchall()
                return [{"summary": row[0], "timestamp": row[1]} for row in reversed(rows)]
        except Exception as e:
            logger.error(f"Failed to load conversation summaries: {e}")
            return []

    def get_global_intelligence(self, min_guilds: int = 2) -> Dict[str, Any]:
        """Aggregate action success/failure data across all guilds.

        Returns {action: {"total_uses": int, "success_rate": float, "top_errors": [...]}}
        for actions used by at least min_guilds different guilds.
        """
        import glob
        aggregated = {}
        guild_count = {}

        try:
            for path in glob.glob(os.path.join(self.data_dir, "guild_*.json")):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, IOError):
                    continue
                if not isinstance(data, dict):
                    continue

                successes = data.get("action_successes", {}) or {}
                failures = data.get("action_failures", {}) or {}

                for action, count in successes.items():
                    if action not in aggregated:
                        aggregated[action] = {"total_uses": 0, "successes": 0, "errors": {}}
                        guild_count[action] = set()
                    aggregated[action]["successes"] += int(count)
                    aggregated[action]["total_uses"] += int(count)
                    guild_count[action].add(path)

                for action, fdata in failures.items():
                    if not isinstance(fdata, dict):
                        continue
                    count = int(fdata.get("count", 0))
                    if action not in aggregated:
                        aggregated[action] = {"total_uses": 0, "successes": 0, "errors": {}}
                        guild_count[action] = set()
                    aggregated[action]["total_uses"] += count
                    guild_count[action].add(path)
                    error = str(fdata.get("last_error", "unknown"))[:200]
                    agg_errors = aggregated[action]["errors"]
                    agg_errors[error] = agg_errors.get(error, 0) + 1

            result = {}
            for action, agg in aggregated.items():
                if len(guild_count.get(action, set())) < min_guilds:
                    continue
                rate = agg["successes"] / agg["total_uses"] if agg["total_uses"] > 0 else 0.0
                top_errors = sorted(
                    ({"error": e, "count": c} for e, c in agg["errors"].items()),
                    key=lambda x: x["count"], reverse=True
                )[:5]
                result[action] = {
                    "total_uses": agg["total_uses"],
                    "success_rate": round(rate, 3),
                    "top_errors": top_errors,
                }
            return result
        except Exception as e:
            logger.error(f"Failed to aggregate global intelligence: {e}")
            return {}

    def backup_data(self):
        """Create a backup of all data."""
        try:
            backup_dir = os.path.join(self.data_dir, "backups")
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(backup_dir, f"backup_{timestamp}")

            # Create backup directory
            shutil.copytree(self.data_dir, backup_path, ignore=shutil.ignore_patterns("backups", "*.tmp"))

            # Clean old backups (keep last 10)
            backups = sorted([d for d in os.listdir(backup_dir) if d.startswith("backup_")])
            if len(backups) > 10:
                for old_backup in backups[:-10]:
                    shutil.rmtree(os.path.join(backup_dir, old_backup))

            logger.info(f"Data backup created: {backup_path}")
            return True

        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return False

    def get_system_stats(self) -> Dict[str, Any]:
        """Get statistics about the data storage."""
        stats = {
            "total_files": 0,
            "total_size": 0,
            "guilds_count": 0,
            "backups_count": 0
        }

        try:
            for filename in os.listdir(self.data_dir):
                if filename.endswith(".json"):
                    stats["total_files"] += 1
                    if filename.startswith("guild_"):
                        stats["guilds_count"] += 1

                    filepath = os.path.join(self.data_dir, filename)
                    stats["total_size"] += os.path.getsize(filepath)

            backup_dir = os.path.join(self.data_dir, "backups")
            if os.path.exists(backup_dir):
                stats["backups_count"] = len([d for d in os.listdir(backup_dir) if d.startswith("backup_")])

        except Exception as e:
            logger.error(f"Failed to get system stats: {e}")

        return stats

# Global instance
dm = DataManager()

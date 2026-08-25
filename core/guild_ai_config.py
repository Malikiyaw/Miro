"""
Per-guild AI configuration — the strict source of truth.

Guild A never uses Guild B's credentials. Secrets (API keys) live in the
encrypted key store via DataManager; everything else lives in the guild's
`ai_config` key. The global/default key is only used when a guild has NOT
configured its own credentials.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from data_manager import dm
from logger import logger
import os as _os

# Env default is the intended server-wide depth when a guild hasn't overridden it.
# Keeping the code default in sync avoids the "50 hides 100" bug (ai_client.py:21).
_ENV_MEMORY_DEPTH = int(_os.getenv("MEMORY_DEPTH", "100"))


@dataclass
class GuildAIConfig:
    guild_id: int
    enabled: bool = True
    provider: str = ""                 # empty -> AIClient resolution order decides
    model: str = ""                    # empty -> provider default / env model
    fallback_models: List[str] = field(default_factory=list)
    max_tokens: int = 8000
    temperature: float = 0.7
    timeout: int = 120                 # seconds, per request
    retry_limit: int = 3
    agent_enabled: bool = True         # allow the AI to execute action plans
    tool_mode: str = "actions"         # actions | readonly
    safety_policy: str = "standard"    # standard | strict
    memory_depth: int = _ENV_MEMORY_DEPTH  # conversation exchanges kept per user (100 = long memory; env MEMORY_DEPTH wins when not overridden)

    # ------------------------------------------------------------------ #
    @staticmethod
    def load(guild_id: int) -> "GuildAIConfig":
        raw = dm.get_guild_data(guild_id, "ai_config", {})
        if not isinstance(raw, dict):
            raw = {}
        # Migrate legacy top-level memory_depth -> ai_config.memory_depth (one-time)
        if "memory_depth" not in raw:
            legacy = dm.get_guild_data(guild_id, "memory_depth", None)
            if isinstance(legacy, int) and 5 <= legacy <= 200:
                raw["memory_depth"] = legacy
        # If guild never set ai_config.memory_depth, fall back to env default
        # (truthy `50` must not hide env `100` — check key presence, not truthiness).
        if "memory_depth" not in raw:
            raw["memory_depth"] = _ENV_MEMORY_DEPTH
        cfg = GuildAIConfig(guild_id=guild_id)
        for f in ("enabled", "provider", "model", "fallback_models", "max_tokens",
                   "temperature", "timeout", "retry_limit", "agent_enabled",
                   "tool_mode", "safety_policy", "memory_depth"):
            if f in raw:
                setattr(cfg, f, raw[f])
        if not isinstance(cfg.fallback_models, list):
            cfg.fallback_models = []
        # Clamp memory_depth to sane bounds
        try:
            cfg.memory_depth = max(5, min(int(cfg.memory_depth), 200))
        except Exception:
            cfg.memory_depth = _ENV_MEMORY_DEPTH
        return cfg

    def effective_memory_depth(self) -> int:
        """Depth that will actually be injected into prompts."""
        try:
            return max(5, min(int(self.memory_depth), 200))
        except Exception:
            return _ENV_MEMORY_DEPTH

    def save(self):
        dm.update_guild_data(self.guild_id, "ai_config", asdict(self))

    # -- credential access (delegates to the encrypted store) -------------
    def has_own_key(self) -> bool:
        entry = dm.get_guild_api_key(self.guild_id)
        return bool(isinstance(entry, dict) and entry.get("providers"))

    def set_key(self, api_key: str, provider: str):
        """Encrypt + store + activate. Only call after validation succeeds."""
        dm.set_guild_api_key(self.guild_id, api_key, provider)
        self.provider = provider
        self.save()

    def clear_key(self, provider: str) -> bool:
        return dm.clear_guild_api_key(self.guild_id, provider)

    def masked_summary(self) -> Dict[str, str]:
        from ai_providers import AIProviderRegistry
        reg = AIProviderRegistry()
        stored = dm.get_guild_api_key(self.guild_id) or {}
        providers = stored.get("providers", {}) if isinstance(stored, dict) else {}
        keys = {
            p: (AIProviderRegistry.mask_key((c or {}).get("api_key") or "")
                if (c or {}).get("api_key") else "—")
            for p, c in providers.items()
        }
        return {"provider": self.provider or "(auto)", "model": self.model or "(default)",
                "keys": keys}

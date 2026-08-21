import asyncio
import os
import time
import aiohttp
from typing import Dict, List, Optional, Tuple

from logger import logger


class AIProviderRegistry:
    """
    Single source of truth for AI providers: display metadata, curated model
    lists, default models, key signup URLs, and live capabilities
    (model listing, key validation) via each provider's OpenAI-compatible API.
    """

    def __init__(self):
        self.providers: Dict[str, dict] = {
            "openrouter": {
                "name": "OpenRouter", "emoji": "🌐",
                "default_model": "openai/gpt-4o-mini",
                "key_url": "https://openrouter.ai/keys",
                "models": [
                    "openai/gpt-4o", "openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet",
                    "anthropic/claude-3-haiku", "google/gemini-2.0-flash-001",
                    "meta-llama/llama-3.3-70b-instruct", "deepseek/deepseek-chat",
                    "mistralai/mistral-small", "qwen/qwen-2.5-72b-instruct",
                ],
            },
            "openai": {
                "name": "OpenAI", "emoji": "🤖",
                "default_model": "gpt-4o-mini",
                "key_url": "https://platform.openai.com/api-keys",
                "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo", "o3-mini"],
                "key_prefixes": ("sk-",),
            },
            "gemini": {
                "name": "Google Gemini", "emoji": "✨",
                "default_model": "gemini-2.0-flash",
                "key_url": "https://aistudio.google.com/app/apikey",
                "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b"],
            },
            "anthropic": {
                "name": "Anthropic Claude", "emoji": "🧠",
                "default_model": "claude-3-5-sonnet-20240620",
                "key_url": "https://console.anthropic.com/settings/keys",
                "models": ["claude-3-5-sonnet-20240620", "claude-3-opus-20240229",
                           "claude-3-sonnet-20240229", "claude-3-haiku-20240307"],
                "key_prefixes": ("sk-ant-",),
            },
            "groq": {
                "name": "Groq (Ultra-Fast)", "emoji": "⚡",
                "default_model": "llama-3.3-70b-versatile",
                "key_url": "https://console.groq.com/keys",
                "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                           "mixtral-8x7b-32768", "gemma2-9b-it"],
                "key_prefixes": ("gsk_",),
            },
            "mistral": {
                "name": "Mistral AI", "emoji": "🌬️",
                "default_model": "mistral-small-latest",
                "key_url": "https://console.mistral.ai/api-keys",
                "models": ["mistral-large-latest", "mistral-medium-latest",
                           "mistral-small-latest", "codestral-latest", "open-mistral-nemo"],
            },
            "deepseek": {
                "name": "DeepSeek", "emoji": "🐋",
                "default_model": "deepseek-chat",
                "key_url": "https://platform.deepseek.com/api_keys",
                "models": ["deepseek-chat", "deepseek-reasoner"],
                "key_prefixes": ("sk-",),
            },
            "dashscope": {
                "name": "Alibaba DashScope (Qwen)", "emoji": "🧭",
                "default_model": "qwen-turbo",
                "key_url": "https://dashscope.console.aliyun.com/apiKey",
                "models": ["qwen-turbo", "qwen-plus", "qwen-max", "qwen2.5-72b-instruct"],
            },
            "cerebras": {
                "name": "Cerebras", "emoji": "🧮",
                "default_model": "llama3.3-70b",
                "key_url": "https://cloud.cerebras.ai/",
                "models": ["llama3.3-70b", "llama3.1-8b"],
            },
            "sambanova": {
                "name": "SambaNova", "emoji": "🔺",
                "default_model": "llama3.1-70b-instruct",
                "key_url": "https://cloud.sambanova.ai/apis",
                "models": ["llama3.1-70b-instruct", "llama3.1-8b-instruct", "llama-3.3-70b-instruct"],
            },
            "together": {
                "name": "Together AI", "emoji": "🤝",
                "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                "key_url": "https://api.together.xyz/settings/api-keys",
                "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo",
                           "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
                           "mistralai/Mixtral-8x7B-Instruct-v0.1"],
            },
            "qwen": {
                "name": "Qwen (DashScope)", "emoji": "🧭",
                "default_model": "qwen-turbo",
                "key_url": "https://dashscope.console.aliyun.com/apiKey",
                "models": ["qwen-turbo", "qwen-plus", "qwen-max", "qwen2.5-72b-instruct"],
            },
        }
        # models-list cache: (provider, key_fingerprint) -> (timestamp, [model_ids])
        self._models_cache: dict = {}
        self._cache_ttl = 600

    def chat_base_url(self, provider: str) -> Optional[str]:
        """Chat completions URL, mirroring AIClient.base_urls (env-overridable)."""
        env_map = {
            "openrouter": "OPENROUTER_URL", "openai": "OPENAI_URL", "gemini": "GEMINI_URL",
            "anthropic": "ANTHROPIC_URL", "groq": "GROQ_URL", "mistral": "MISTRAL_URL",
            "deepseek": "DEEPSEEK_URL", "dashscope": "DASHSCOPE_URL", "qwen": "DASHSCOPE_URL",
            "cerebras": "CEREBRAS_URL", "sambanova": "SAMBANOVA_URL", "together": "TOGETHER_URL",
        }
        defaults = {
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "anthropic": "https://api.anthropic.com/v1/messages",
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "mistral": "https://api.mistral.ai/v1/chat/completions",
            "deepseek": "https://api.deepseek.com/v1/chat/completions",
            "dashscope": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
            "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
            "cerebras": "https://api.cerebras.ai/v1/chat/completions",
            "sambanova": "https://api.sambanova.ai/v1/chat/completions",
            "together": "https://api.together.xyz/v1/chat/completions",
        }
        env_key = env_map.get(provider)
        return os.getenv(env_key, defaults.get(provider)) if env_key else defaults.get(provider)

    def models_url(self, provider: str) -> Optional[str]:
        base = self.chat_base_url(provider)
        if not base:
            return None
        if provider == "anthropic":
            return "https://api.anthropic.com/v1/models"
        return base.replace("/chat/completions", "/models")

    def exists(self, provider: str) -> bool:
        return provider in self.providers

    def display(self, provider: str) -> str:
        info = self.providers.get(provider)
        return f"{info['emoji']} {info['name']}" if info else provider

    def default_model(self, provider: str) -> str:
        return self.providers.get(provider, {}).get("default_model", "gpt-4o-mini")

    def curated_models(self, provider: str) -> List[str]:
        return list(self.providers.get(provider, {}).get("models", []))

    def key_signup_url(self, provider: str) -> str:
        return self.providers.get(provider, {}).get("key_url", "")

    def validate_key_format(self, provider: str, api_key: str) -> Tuple[bool, str]:
        """Cheap local sanity checks before any network call."""
        key = (api_key or "").strip()
        if len(key) < 16:
            return False, "That key looks too short — make sure you pasted the full API key."
        if any(x in key.upper() for x in ("YOUR_", "REPLACE_", "EXAMPLE", "XXXX")):
            return False, "That looks like a placeholder value, not a real API key."
        if " " in key or "\n" in key:
            return False, "API keys cannot contain spaces or line breaks."
        prefixes = self.providers.get(provider, {}).get("key_prefixes", ())
        if prefixes and not key.startswith(prefixes):
            pretty = " or ".join(prefixes)
            return False, f"{self.display(provider)} keys normally start with `{pretty}` — double-check you copied the right key."
        return True, ""

    @staticmethod
    def mask_key(api_key: str) -> str:
        key = (api_key or "").strip()
        if len(key) <= 10:
            return "*" * len(key)
        return f"`{key[:6]}…{key[-4:]}`"

    def _auth_headers(self, provider: str, api_key: str) -> dict:
        if provider == "anthropic":
            return {"x-api-key": api_key.strip(), "anthropic-version": "2023-06-01"}
        headers = {"Authorization": f"Bearer {api_key.strip()}"}
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/Malikiyaw/Miro"
            headers["X-Title"] = "Miro AI Discord Bot"
        return headers

    async def list_models(self, provider: str, api_key: str, force: bool = False) -> Optional[List[str]]:
        """
        Live model listing from the provider's /models endpoint.
        Returns None when the provider/key/network doesn't support it.
        Results cached for 10 minutes per provider+key pair.
        """
        url = self.models_url(provider)
        key = (api_key or "").strip()
        if not url or not key:
            return None

        cache_key = (provider, key[:8] + key[-4:])
        now = time.time()
        if not force:
            cached = self._models_cache.get(cache_key)
            if cached and now - cached[0] < self._cache_ttl:
                return cached[1]

        timeout = aiohttp.ClientTimeout(total=12, connect=6)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=self._auth_headers(provider, key)) as resp:
                    if resp.status != 200:
                        logger.debug(f"Model listing for {provider} returned {resp.status}")
                        return None
                    data = await resp.json(content_type=None)
                    models = sorted({
                        m.get("id") for m in data.get("data", [])
                        if isinstance(m, dict) and m.get("id")
                    })
                    if models:
                        self._models_cache[cache_key] = (now, models)
                    return models or None
        except Exception as e:
            logger.debug(f"Model listing failed for {provider}: {e}")
            return None

    async def test_key(self, provider: str, api_key: str) -> Tuple[bool, str, float]:
        """
        Verify a key actually works: try the free /models endpoint first,
        fall back to a 1-token chat completion. Returns (ok, detail, latency_ms).
        """
        import time as _time
        key = (api_key or "").strip()
        url = self.models_url(provider)
        started = _time.perf_counter()

        if url:
            try:
                timeout = aiohttp.ClientTimeout(total=12, connect=6)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, headers=self._auth_headers(provider, key)) as resp:
                        latency = (_time.perf_counter() - started) * 1000
                        if resp.status == 200:
                            return True, "key accepted by provider", latency
                        if resp.status in (401, 403):
                            return False, f"rejected by provider (HTTP {resp.status} — invalid or unauthorized key)", latency
                        # 404/405 etc. -> endpoint unsupported, try chat fallback below
            except Exception:
                pass  # network hiccup; try chat fallback

        # Fallback: minimal chat completion (costs ~1 token)
        chat_url = self.chat_base_url(provider)
        payload = {
            "model": self.default_model(provider),
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        if provider == "anthropic":
            payload["max_tokens"] = 1
        try:
            timeout = aiohttp.ClientTimeout(total=20, connect=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(chat_url, json=payload, headers=self._auth_headers(provider, key)) as resp:
                    latency = (_time.perf_counter() - started) * 1000
                    if resp.status == 200:
                        return True, "key accepted (chat test)", latency
                    body = (await resp.text())[:200]
                    if resp.status in (401, 403):
                        return False, f"rejected by provider (HTTP {resp.status})", latency
                    if resp.status == 429:
                        return True, "key is valid but rate-limited right now", latency
                    # Model-name issues still prove the KEY was accepted on most providers
                    if resp.status == 400 and ("model" in body.lower()):
                        return True, "key valid (default model unavailable — pick another model)", latency
                    return False, f"provider returned HTTP {resp.status}", latency
        except Exception as e:
            return False, f"connection failed: {str(e)[:120]}", (_time.perf_counter() - started) * 1000

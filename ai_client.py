import os
import json
import aiohttp
import logging
import re
import asyncio
import time
from typing import List, Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, retry_if_not_exception_type
from history_manager import history_manager
from vector_memory import vector_memory
from actions import ActionHandler
from blueprints import BLUEPRINT
from ai_providers import AIProviderRegistry

logger = logging.getLogger(__name__)

class AIClientError(Exception):
    """Custom exception for AI client errors that should not be retried (e.g., 4xx)."""
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"AI API Client Error ({status}): {message}")

def is_retryable_exception(exception):
    """
    Only retry on server errors (5xx) or connection issues.
    Skip client errors (4xx) and logic/parsing errors (KeyError, etc).
    """
    if isinstance(exception, AIClientError):
        return exception.status >= 500 or exception.status == 429
    
    # Don't retry on structural or logic errors - these are bugs, not transient issues
    if isinstance(exception, (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError, ValueError)):
        return False
        
    return True

class AIClient:
    """
    Handles deep reasoning, web searches, and JSON extraction.
    Ensures the bot thinks before acting and provides a walkthrough.
    Supports per-guild API keys.
    """
    def __init__(self, bot, api_key: str, provider: str = None, model: Optional[str] = None):
        self.bot = bot
        self.default_api_key = api_key
        self.default_provider = provider or os.getenv("AI_PROVIDER", "openrouter")
        # Default model logic
        self.model = model or os.getenv("AI_MODEL", "openai/gpt-3.5-turbo")
        
        self.base_urls = {
            "openrouter": os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"),
            "openai": os.getenv("OPENAI_URL", "https://api.openai.com/v1/chat/completions"),
            "gemini": os.getenv("GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
            "anthropic": os.getenv("ANTHROPIC_URL", "https://api.anthropic.com/v1/messages"),
            "groq": os.getenv("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"),
            "mistral": os.getenv("MISTRAL_URL", "https://api.mistral.ai/v1/chat/completions"),
            "deepseek": os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/v1/chat/completions"),
            "dashscope": os.getenv("DASHSCOPE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"),
            "qwen": os.getenv("DASHSCOPE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"),
            "cerebras": os.getenv("CEREBRAS_URL", "https://api.cerebras.ai/v1/chat/completions"),
            "sambanova": os.getenv("SAMBANOVA_URL", "https://api.sambanova.ai/v1/chat/completions"),
            "together": os.getenv("TOGETHER_URL", "https://api.together.xyz/v1/chat/completions")
        }
        # Health-watchdog signals (consumed by core/health via bot.py)
        self.consecutive_failures = 0
        self.last_success_ts = 0.0

    def _get_guild_api_key(self, guild_id: int) -> tuple:
        """Get API key and provider for a specific guild, fallback to defaults.

        Resolution order for the provider:
        1. /config provider selection ("active_provider" in guild data — the
           admin's most recent explicit choice)
        2. provider stored alongside the guild's API keys ("provider" key)
        3. bot-wide default provider
        """
        from data_manager import dm
        current_config = dm.get_guild_api_key(guild_id)
        active_provider = dm.get_guild_data(guild_id, "active_provider", None)
        stored_provider = current_config.get("provider") if current_config else None
        provider = active_provider or stored_provider or self.default_provider

        # Prefer a key stored for the active provider
        if current_config and provider:
            stored = current_config.get("providers", {}).get(provider)
            if isinstance(stored, dict) and stored.get("api_key"):
                return stored.get("api_key"), provider
        return self.default_api_key, provider

    def _get_all_guild_keys(self, guild_id: int) -> List[Dict[str, str]]:
        """Fetch all available API keys for a guild to use as fallbacks"""
        from data_manager import dm
        api_keys = dm.load_json("guild_api_keys", default={})
        guild_data = api_keys.get(str(guild_id), {})

        def is_valid_key(k):
            return k and len(k) > 10 and not any(x in k.upper() for x in ["YOUR_", "REPLACE_"])

        results = []
        # Add primary first
        primary = self._get_guild_api_key(guild_id)
        if is_valid_key(primary[0]):
            results.append({"api_key": primary[0], "provider": primary[1]})

        # Add other stored keys as fallbacks (active provider first)
        providers = guild_data.get("providers", {})
        if isinstance(providers, dict):
            ordered = sorted(providers.keys(), key=lambda p: p != primary[1])
            for p in ordered:
                if p != primary[1]:
                    # Decrypt and add
                    res = dm.get_guild_api_key(guild_id, provider=p)
                    if isinstance(res, dict) and res.get("api_key") and is_valid_key(res.get("api_key")):
                        results.append({"api_key": res["api_key"], "provider": p})

        # Finally add defaults if not already present and valid
        if is_valid_key(self.default_api_key) and not any(r["provider"] == self.default_provider for r in results):
            results.append({"api_key": self.default_api_key, "provider": self.default_provider})

        return results
    
    async def fetch_server_health(self, guild_id: int) -> Dict[str, Any]:
        """
        Tool function to fetch server health and forecast data.
        Called by AI when user asks about server activity, forecasts, or level-up ETAs.
        """
        try:
            from modules.server_analytics import get_analytics
            analytics = get_analytics()
            if analytics:
                return analytics.get_forecast(guild_id)
            return {"error": "Analytics system not initialized"}
        except Exception as e:
            return {"error": str(e)}

    async def get_search_results(self, query: str) -> str:
        """Performs a web search using Tavily or a fallback."""
        tavily_key = os.getenv("TAVILY_API_KEY")
        if not tavily_key:
            return "Web search is disabled. No Tavily API key found."
            
        async with aiohttp.ClientSession() as session:
            url = "https://api.tavily.com/search"
            payload = {
                "api_key": tavily_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5
            }
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = []
                        for res in data.get("results", []):
                            results.append(f"- {res['title']}: {res['content']} ({res['url']})")
                        return "\n".join(results)
                    return f"Search error: {resp.status}"
            except Exception as e:
                return f"Web search failed: {str(e)}"

    async def analyze_content(self, content: str, mode: str = "scam_check") -> Dict[str, Any]:
        """
        Analyze message content for security threats or toxicity.
        Used by the Guardian and Moderation systems.
        Returns a dict with at least 'is_scam'/'is_violation' style verdict keys.
        Degrades gracefully (safe default: not a violation) when AI is unavailable.
        """
        mode_prompts = {
            "scam_check": (
                "You are a security analyst for a Discord server. Determine if the following message "
                "is a scam, phishing attempt, fraud, or malicious link. Respond with JSON ONLY:\n"
                '{"is_scam": true/false, "confidence": 0.0-1.0, "reason": "one short sentence"}'
            ),
            "toxicity": (
                "You are a community moderator. Determine if the following message is toxic, "
                "harassing, hateful, or against community rules. Respond with JSON ONLY:\n"
                '{"is_violation": true/false, "confidence": 0.0-1.0, "severity": "low|medium|high", "reason": "one short sentence"}'
            ),
            "spam": (
                "You are a community moderator. Determine if the following message is spam or "
                "unsolicited advertising. Respond with JSON ONLY:\n"
                '{"is_spam": true/false, "confidence": 0.0-1.0, "reason": "one short sentence"}'
            ),
        }
        system_prompt = mode_prompts.get(mode, mode_prompts["scam_check"])

        try:
            keys = self._get_all_guild_keys(0)
            if not keys:
                return {"is_scam": False, "confidence": 0.0, "reason": "AI not configured"}
            key_bundle = keys[0]
            headers = {
                "Authorization": f"Bearer {key_bundle['api_key'].strip()}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Message to analyze:\n{content[:1500]}"},
                ],
                "temperature": 0.1,
                "max_tokens": 200,
            }
            url = self.base_urls.get(key_bundle["provider"])
            if not url:
                return {"is_scam": False, "confidence": 0.0, "reason": "No provider URL"}

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(f"analyze_content provider error {resp.status}")
                        return {"is_scam": False, "confidence": 0.0, "reason": f"AI error {resp.status}"}
                    data = await resp.json()
                    text = ""
                    if "choices" in data and len(data["choices"]) > 0:
                        text = data["choices"][0]["message"]["content"]
                    elif "content" in data and len(data["content"]) > 0:
                        text = data["content"][0].get("text", "") if isinstance(data["content"], list) else str(data["content"])
                    if not text:
                        return {"is_scam": False, "confidence": 0.0, "reason": "Empty AI response"}
                    parsed = self.extract_json(text)
                    if not parsed or not isinstance(parsed, dict):
                        return {"is_scam": False, "confidence": 0.0, "reason": "Unparseable AI response"}
                    return parsed
        except Exception as e:
            logger.warning(f"analyze_content failed: {e}")
            return {"is_scam": False, "confidence": 0.0, "reason": str(e)[:100]}

    async def _build_enhanced_prompt(self, system_prompt: str, guild_id: int, user_id: int = 0) -> str:
        """Build system prompt with action success/failure data, command usage, and live server context."""
        from data_manager import dm
        from server_query import ServerQueryEngine
        
        base_prompt = system_prompt

        # Add available actions list to prevent "Action not allowed" failures
        allowed_actions_section = f"""

AVAILABLE ACTIONS:
Only suggest actions from this list. Do not invent new actions:

{chr(10).join(sorted(ActionHandler.ALLOWED_ACTIONS))}

"""
        base_prompt += allowed_actions_section

        # Add LIVE SERVER CONTEXT - automatically inject server state for every request
        if guild_id and guild_id > 0:
            try:
                query_engine = ServerQueryEngine(self.bot)
                server_info = await query_engine.query_server_info(guild_id)
                channels = await query_engine.query_channels(guild_id)
                roles = await query_engine.query_roles(guild_id)
                members = await query_engine.query_members(guild_id)
                
                server_context = f"\n\n===== LIVE SERVER CONTEXT =====\n"
                server_context += "You have full access to this server's real-time state. Use this information to answer questions accurately:\n\n"
                
                if server_info:
                    server_context += f"SERVER: {server_info.get('name', 'Unknown')}\n"
                    server_context += f"Total Members: {server_info.get('member_count', 0)}\n"
                    server_context += f"Online Members: {server_info.get('online_count', 0)}\n"
                    server_context += f"Boost Level: {server_info.get('boost_level', 0)}\n\n"
                
                if roles and len(roles) > 0:
                    server_context += "ROLES (with permissions):\n"
                    for role in roles[:15]:  # Top 15 most important roles
                        server_context += f"- {role.get('name', 'Unknown')}: {role.get('member_count', 0)} members\n"
                    server_context += "\n"
                
                if channels and len(channels) > 0:
                    server_context += "CHANNELS:\n"
                    for channel in channels[:20]:  # Most visible channels
                        if channel.get('type') in ['text', 'voice', 'forum']:
                            server_context += f"- {channel.get('name', 'Unknown')} ({channel.get('type', 'text')})\n"
                    server_context += "\n"
                
                if members and len(members) > 0:
                    online_members = [m for m in members if m.get('status') == 'online']
                    server_context += f"ONLINE MEMBERS ({len(online_members)}):\n"
                    for member in online_members[:20]:  # First 20 online members
                        server_context += f"- {member.get('username', 'Unknown')}#{member.get('discriminator', '')}\n"
                
                server_context += "\nThis data is live and current. You can reference any of this information in your responses without needing to look it up.\n"
                server_context += "===== END SERVER CONTEXT =====\n\n"
                
                base_prompt += server_context
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.debug(f"Could not load server context: {e}")
        
        successes = dm.get_guild_data(guild_id, "action_successes", {})
        failures = dm.get_guild_data(guild_id, "action_failures", {})
        global_intel = dm.get_global_intelligence(min_guilds=2)
        command_usage = dm.get_guild_data(guild_id, "command_usage", {})
        
        if not successes and not failures and not global_intel and not command_usage:
            return base_prompt
        
        improvement_data = "\n\nACTION PERFORMANCE HISTORY (use this to improve your plans):\n"
        
        if failures:
            improvement_data += "Your previously failed actions on this server (avoid or adjust):\n"
            for action, data in sorted(failures.items(), key=lambda x: x[1]["count"], reverse=True)[:5]:
                improvement_data += f"- `{action}`: Failed {data['count']} time(s). Last error: {data['last_error']}\n"
        
        if successes:
            improvement_data += "Your previously successful actions on this server (safe to reuse):\n"
            for action, count in sorted(successes.items(), key=lambda x: x[1], reverse=True)[:5]:
                improvement_data += f"- `{action}`: Succeeded {count} time(s)\n"
        
        if global_intel:
            improvement_data += "\nCross-server intelligence (aggregated from all servers):\n"
            for action, data in sorted(global_intel.items(), key=lambda x: x[1]["total_uses"], reverse=True)[:10]:
                rate = data["success_rate"]
                status = "HIGHLY RELIABLE" if rate >= 0.9 else "MODERATE" if rate >= 0.7 else "UNRELIABLE - AVOID"
                improvement_data += f"- `{action}`: {rate*100:.0f}% success rate across {data['total_uses']} uses [{status}]\n"
                if data["top_errors"]:
                    for err in data["top_errors"]:
                        improvement_data += f"  Common error: {err['error']} ({err['count']}x)\n"
        
        if command_usage:
            improvement_data += "\nCommand usage on this server (creates commands users actually want):\n"
            sorted_cmds = sorted(command_usage.items(), key=lambda x: x[1].get("count", 0), reverse=True)
            popular = [(name, data) for name, data in sorted_cmds if data.get("count", 0) > 2]
            unused = [(name, data) for name, data in sorted_cmds if data.get("count", 0) <= 2]
            if popular:
                improvement_data += "Frequently used commands (create similar ones):\n"
                for name, data in popular[:5]:
                    improvement_data += f"- `!{name}`: Used {data['count']} times\n"
            if unused:
                improvement_data += "Rarely used commands (avoid creating similar ones):\n"
                for name, data in unused[:5]:
                    improvement_data += f"- `!{name}`: Used only {data.get('count', 0)} times\n"
        
        improvement_data += "\nUse this data to avoid repeating mistakes, prefer proven action sequences, and create commands users actually want."
        
        # Personalized user preferences
        personalization = ""
        if user_id and user_id > 0:
            profiles = dm.get_guild_data(guild_id, "user_profiles", {})
            user_profile = profiles.get(str(user_id), {})
            prefs = user_profile.get("preferences", {})
            if prefs:
                personalization = "\n\nUSER PREFERENCES (respect these in your response):\n"
                for k, v in prefs.items():
                    personalization += f"- {k}: {v}\n"

        return system_prompt + improvement_data + personalization

    @retry(
        retry=retry_if_exception(is_retryable_exception),
        stop=stop_after_attempt(5), 
        wait=wait_exponential(multiplier=2, min=2, max=60)
    )
    async def _enhance_user_request(self, guild_id: int, user_id: int, user_input: str, system_prompt: str) -> str:
        """
        Request enhancement layer: interprets, clarifies, and enhances user requests by:
        1. Filling in missing context
        2. Understanding actual intent instead of literal input
        3. Resolving ambiguities based on server context and conversation history
        4. Expanding incomplete requests into actionable tasks
        """
        history_depth = int(os.getenv("MEMORY_DEPTH", "50"))
        recent_history = await history_manager.get_enhanced_context(guild_id, user_id, depth=history_depth)
        
        enhancement_prompt = f"""
        You are a request interpreter. Analyze this user request and enhance it:
        
        USER REQUEST: {user_input}
        
        RECENT CONVERSATION HISTORY:
        {recent_history[-5:] if recent_history else "No recent history"}
        
        TASK:
        1. Understand the user's actual intent, not just literal words
        2. Fill in missing context, assumptions, and implied requirements
        3. Resolve ambiguities using context
        4. Expand incomplete requests into clear, actionable tasks
        5. Preserve all original user requirements
        6. DO NOT add extra features the user didn't want
        
        Return ONLY the enhanced request text, no other commentary.
        """
        
        try:
            # Make quick lightweight call for enhancement
            keys = self._get_all_guild_keys(guild_id)
            if keys:
                key_bundle = keys[0]
                headers = {"Authorization": f"Bearer {key_bundle['api_key'].strip()}", "Content-Type": "application/json"}
                payload = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": enhancement_prompt}],
                    "temperature": 0.3,
                    "max_tokens": 2000
                }
                
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    url = self.base_urls.get(key_bundle["provider"])
                    if url:
                        async with session.post(url, json=payload, headers=headers) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if 'choices' in data and len(data['choices']) > 0:
                                    enhanced = data['choices'][0]['message']['content'].strip()
                                    logger.debug(f"Original request: {user_input} | Enhanced: {enhanced}")
                                    return enhanced
        except Exception as e:
            logger.debug(f"Request enhancement skipped: {e}")
        
        # Fallback to original input if enhancement fails
        return user_input

    def _coerce_model_for_provider(self, guild_id: int, provider: str) -> Optional[str]:
        """
        Return the guild's custom model only when it is compatible with the
        given provider; otherwise the provider's default model. Prevents the
        classic fallback failure of sending a Mistral model name to Groq.
        """
        from data_manager import dm
        try:
            from ai_providers import AIProviderRegistry
        except ImportError:
            return None

        registry = AIProviderRegistry()
        custom = dm.get_guild_data(guild_id, "custom_model", None)
        if not custom:
            return None
        if not registry.is_chat_model(custom):
            default = registry.default_model(provider)
            logger.warning(f"Stored model '{custom}' is an embedding/utility model and "
                           f"cannot generate responses; using '{default}' for {provider}")
            return default
        curated = [m.lower() for m in registry.curated_models(provider)]
        if not curated:
            return custom  # unknown provider shape; let the API decide
        low = custom.lower()
        for pm in curated:
            tokens = [t for t in pm.replace("/", ".").replace("_", ".").replace("-", ".").split(".")
                      if len(t) >= 3 and not t.isdigit()]
            if any(tok in low for tok in tokens):
                return custom
        logger.info(f"Model '{custom}' incompatible with {provider}; using default "
                    f"'{registry.default_model(provider)}' instead")
        return registry.default_model(provider)

    async def chat(self, guild_id: int, user_id: int, user_input: str, system_prompt: str,
                   persist: bool = False) -> Dict[str, Any]:
        """
        Communicates with the LLM using a primary provider, with automatic fallback
        to secondary providers (DashScope, OpenRouter) if the primary hits a quota limit (429/403).

        persist=True records the exchange in per-user conversation history so
        follow-ups like "proceed" keep their context (used by /bot and AI chat).
        """
        # First enhance the user request. Enhancement is OPTIONAL: it retries
        # internally and can raise RetryError on busy providers, which used to
        # kill the whole request (and got misreported as "provider unreachable").
        try:
            enhanced_input = await self._enhance_user_request(guild_id, user_id, user_input, system_prompt)
        except Exception as e:
            logger.warning(f"[AI] Request enhancement failed (continuing without it): {e}")
            enhanced_input = None
        
        keys_to_try = self._get_all_guild_keys(guild_id)
        if not keys_to_try:
            logger.error(f"[AI ERROR] No API keys configured for guild {guild_id}")
            return {"error": "No valid API key configured. Use /config apikey to set one."}

        last_error = None
        consecutive_429 = 0

        for key_bundle in keys_to_try:
            api_key = key_bundle["api_key"]
            provider = key_bundle["provider"]
            
            # Back off between rate-limited attempts so we don't burn the whole chain at once
            if consecutive_429:
                wait = min(2 ** min(consecutive_429, 5), 30)
                logger.warning(f"[AI BACKOFF] Rate limited {consecutive_429}x; waiting {wait}s before next attempt")
                await asyncio.sleep(wait)

            # Re-resolve the model for THIS provider (fallbacks must not reuse foreign models)
            model_override = self._coerce_model_for_provider(guild_id, provider)

            try:
                result = await self._chat_internal(guild_id, user_id, user_input, system_prompt,
                                                   api_key, provider, enhanced_input,
                                                   model_override=model_override)
                # Empty even after regeneration -> treat like a provider
                # failure and try the next key/provider in the chain.
                if isinstance(result, dict) and result.pop("_miro_empty", False):
                    logger.warning(f"[AI FALLBACK] Provider {provider} produced an empty response. Trying next available fallback...")
                    last_error = Exception("empty response from provider")
                    continue
                self.report_success()
                consecutive_429 = 0
                if persist:
                    await self._persist_exchange(guild_id, user_id, user_input, result)
                return result
            except AIClientError as e:
                # If it's a quota (429), access (403), or auth (401) error, try the next provider in the guild's list
                if e.status in [401, 429, 403]:
                    logger.warning(f"[AI FALLBACK] Provider {provider} failed with {e.status}. Trying next available fallback...")
                    last_error = e
                    if e.status == 429:
                        consecutive_429 += 1
                    continue
                self.report_failure()
                raise # Re-raise server/connection errors for the 'tenacity' @retry to handle
            except Exception as e:
                self.report_failure()
                # Re-raise transient exceptions for tenacity to handle
                raise

        self.report_failure()
        if last_error:
            raise last_error

        raise Exception("AI failed to respond after trying all configured fallback providers.")

    def report_success(self):
        """Feed the health watchdog: a real successful request proves the client works."""
        self.consecutive_failures = 0
        self.last_success_ts = time.time()

    def report_failure(self):
        self.consecutive_failures = getattr(self, "consecutive_failures", 0) + 1

    @staticmethod
    def _synthesize_summary(result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fill in a missing 'summary' from wherever the model actually put its
        answer: alternate text keys, the action plan, or descriptive fields.
        Prevents '(The AI returned an empty response)' when the model replies
        with structured JSON but no summary field.
        """
        if not isinstance(result, dict):
            return result
        if str(result.get("summary") or "").strip():
            return result  # already has a real summary

        for key in ("response", "message", "content", "answer", "text", "reply"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                out = dict(result)
                out["summary"] = value.strip()
                return out

        # Action plan without words: describe what it intends to do
        actions = result.get("actions")
        if isinstance(actions, list) and actions:
            described = []
            for action in actions[:5]:
                if isinstance(action, dict):
                    name = str(action.get("name") or "").strip()
                    params = action.get("parameters") or {}
                    detail = ", ".join(str(v) for v in list(params.values())[:2] if v)
                    described.append(f"{name}({detail})" if detail else name)
            if any(described):
                out = dict(result)
                out["summary"] = "Here's my plan — " + "; ".join(filter(None, described))
                return out

        for key in ("description", "title", "reasoning", "walkthrough"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                out = dict(result)
                out["summary"] = value.strip()
                return out

        return result

    @staticmethod
    async def _persist_exchange(guild_id: int, user_id: int, user_input: str, result: Dict[str, Any]):
        """Store a conversational exchange so future requests keep their context."""
        try:
            reply = ""
            if isinstance(result, dict):
                reply = str(result.get("summary") or result.get("response") or "")[:4000]
            if not guild_id or not user_id or not reply:
                return
            await history_manager.add_exchange(guild_id, user_id, user_input[:2000], reply)
        except Exception as e:
            logger.warning(f"Failed to persist conversation exchange: {e}")
    
    async def generate_response(self, messages: List[Dict[str, str]], guild_id: int, user_id: int, max_tokens: int = 1000) -> str:
        """
        Generate a text response from the AI using the provided messages.
        Returns just the text string (not a dict).
        """
        # Get API key and provider for this guild
        keys_to_try = self._get_all_guild_keys(guild_id)
        if not keys_to_try:
            logger.error(f"[AI ERROR] No API keys configured for guild {guild_id}")
            return ""
        
        # Use the first key
        key_bundle = keys_to_try[0]
        api_key = key_bundle["api_key"]
        provider = key_bundle["provider"]
        
        # Determine model - validate it matches the provider
        active_model = self.model or "gpt-3.5-turbo"
        
        # Model validation: ensure model matches provider
        provider_model_map = {
            "openrouter": ["gpt", "claude", "gemini", "llama", "meta-llama", "google", "anthropic"],
            "openai": ["gpt"],
            "gemini": ["gemini"],
            "anthropic": ["claude"],
            "groq": ["llama", "mixtral"],
            "mistral": ["mistral", "open-mixtral"],
            "deepseek": ["deepseek"],
            "dashscope": ["qwen"],
            "qwen": ["qwen"],
            "cerebras": ["llama"],
            "sambanova": ["llama"],
            "together": ["llama", "mistral"]
        }
        
        valid_prefixes = provider_model_map.get(provider, [])
        if not any(active_model.lower().startswith(prefix) for prefix in valid_prefixes):
            # Model doesn't match provider, use default for this provider
            defaults = {
                "openrouter": "openai/gpt-4o",
                "openai": "gpt-4o",
                "gemini": "gemini-1.5-flash",
                "anthropic": "claude-3-5-sonnet-20240620",
                "groq": "llama-3.3-70b-versatile",
                "mistral": "mistral-small-latest",
                "deepseek": "deepseek-chat",
                "dashscope": "qwen-turbo",
                "qwen": "qwen-turbo"
            }
            active_model = defaults.get(provider, "gpt-3.5-turbo")
            logger.warning(f"Model {self.model} doesn't match provider {provider}, using {active_model} instead")

        if not AIProviderRegistry.is_chat_model(active_model):
            active_model = AIProviderRegistry().default_model(provider)

        # Build payload
        payload = {
            "model": active_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tokens,
        }
        
        # Set headers
        if provider == "anthropic":
            headers = {
                "x-api-key": api_key.strip(),
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }
        else:
            headers = {
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json"
            }
            if provider == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/antigravity"
                headers["X-Title"] = "Miro AI Discord Bot"
        
        # Make request
        timeout = aiohttp.ClientTimeout(total=45, connect=10)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            provider_url = self.base_urls.get(provider)
            if not provider_url:
                logger.error(f"Unsupported AI provider: {provider}")
                return ""
            
            async with session.post(provider_url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"AI API Error from {provider} ({resp.status}): {text}")
                    return ""
                
                res_data = await resp.json()
                
                # Extract text from response
                try:
                    if 'choices' in res_data:
                        return res_data['choices'][0]['message']['content']
                    elif 'content' in res_data:
                        return res_data['content'][0]['text']
                    elif 'message' in res_data and 'content' in res_data['message']:
                        return res_data['message']['content']
                    else:
                        logger.error(f"Unknown response structure from {provider}: {res_data}")
                        return ""
                except (KeyError, IndexError, TypeError) as e:
                    logger.error(f"Failed to parse {provider} response: {e}")
                    return ""

    async def safe_chat(self, guild_id: int, user_id: int, user_input: str, system_prompt: str) -> Dict[str, Any]:
        """
        Public wrapper around chat() that converts tenacity RetryError into a
        clean, human-readable exception so callers never see the raw RetryError.
        """
        from tenacity import RetryError
        try:
            return await self.chat(guild_id, user_id, user_input, system_prompt)
        except RetryError as e:
            cause = e.last_attempt.exception()
            if isinstance(cause, AIClientError):
                status = cause.status
                msg = cause.message
                if status == 401:
                    raise AIClientError(status, 'Invalid or expired API key. Set a new one with /config key.') from cause
                if status == 429:
                    raise AIClientError(status, 'Rate limit hit. Wait a moment, or switch providers with /config provider.') from cause
                if status == 403:
                    raise AIClientError(status, f'Access denied by the AI provider. Try a different model or provider.') from cause
                if status >= 500:
                    raise AIClientError(status, f'AI provider server error ({status}). Try again in a moment.') from cause
                raise AIClientError(status, msg) from cause
            raise Exception(f'AI failed after multiple attempts: {cause}') from cause

    async def _chat_internal(self, guild_id: int, user_id: int, user_input: str, system_prompt: str, api_key: str, provider: str, enhanced_input: str = None, model_override: Optional[str] = None) -> Dict[str, Any]:
        """Internal execution for a single AI provider request."""
        # Validate guild context
        if guild_id is None:
            logger.warning("Guild ID is None, falling back to global context")
            guild_id = 0
            
        # Get recent history
        history_depth = int(os.getenv("MEMORY_DEPTH", "50"))
        history = await history_manager.get_enhanced_context(guild_id, user_id, depth=history_depth)
        if not isinstance(history, list):
            history = []
        
        # Retrieve semantically similar conversations from vector memory
        vector_results = await vector_memory.retrieve_relevant_conversations(
            guild_id=guild_id,
            user_id=user_id,
            query=user_input,
            n_results=int(os.getenv("VECTOR_MEMORY_RESULTS", 3)),
            min_importance=float(os.getenv("VECTOR_MEMORY_MIN_IMPORTANCE", 0.3))
        )
        
        vector_context = []
        for result in vector_results:
            doc = result.get("document", "")
            similarity = result.get("similarity")
            if similarity is None:
                distance = result.get("distance", 1.0)
                similarity = 1.0 - distance
            
            vector_context.append({
                "role": "system",
                "content": f"[Relevant past conversation (similarity: {similarity:.2f})]\n{doc}"
            })
        
        combined_context = history + vector_context
        enhanced_prompt = await self._build_enhanced_prompt(system_prompt, guild_id, user_id)
        
        messages = [{"role": "system", "content": enhanced_prompt}]
        messages.extend(combined_context)

        # Use enhanced input if provided by the enhancement layer
        final_input = enhanced_input if enhanced_input else user_input
        messages.append({"role": "user", "content": final_input})
        
        # Determine model based on provider
        from data_manager import dm
        if model_override:
            active_model = model_override
        else:
            active_model = dm.get_guild_data(guild_id, "custom_model", self.model)
        
        if provider == "gemini" and (not active_model or "gpt" in active_model.lower()):
            active_model = "gemini-1.5-flash-latest"
        elif provider == "openai" and (not active_model or "/" in active_model):
            active_model = "gpt-3.5-turbo"
        elif provider == "anthropic" and (not active_model or "gpt" in active_model.lower()):
            active_model = "claude-3-5-sonnet-20240620"
        elif provider == "cerebras" and (not active_model or "gpt" in active_model.lower() or "claude" in active_model.lower()):
            active_model = "llama3.3-70b"
        elif provider == "sambanova" and (not active_model or "gpt" in active_model.lower()):
            active_model = "llama3.1-70b-instruct"
        elif provider == "groq" and (not active_model or "gpt" in active_model.lower() or "/" in active_model):
            active_model = "llama-3.3-70b-versatile"
        elif provider == "together" and (not active_model or "gpt" in active_model.lower()):
            active_model = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
        elif provider in ["qwen", "dashscope"] and (not active_model or "gpt" in active_model.lower()):
            active_model = "qwen2.5-72b-instruct" # Updated to newest stable qwen
        elif not active_model:
            active_model = self.model or "gpt-3.5-turbo"

        # Final guard: embedding/utility models can't generate responses.
        # A stale stored selection used to produce "Invalid model" errors or
        # generic greetings instead of JSON.
        if active_model and not AIProviderRegistry.is_chat_model(active_model):
            fallback_default = AIProviderRegistry().default_model(provider)
            logger.warning(f"Model '{active_model}' is not a chat-capable model; "
                           f"using '{fallback_default}' for {provider} instead")
            active_model = fallback_default
        
        if provider == "anthropic":
            headers = {
                "x-api-key": api_key.strip(),
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }
        else:
            headers = {
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json"
            }
            if provider == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/antigravity"
                headers["X-Title"] = "Miro AI Discord Bot"

        logger.info(f"AI Handshake: {provider} | Model: {active_model}")

        payload = {
            "model": active_model,
            "messages": [m for m in messages if m["role"] != "system"],
            "temperature": 0.7,
            "max_tokens": 8000,
        }
        
        # Anthropic specific payload structure
        if provider == "anthropic":
            system_msg = next((m["content"] for m in messages if m["role"] == "system"), None)
            if system_msg:
                payload["system"] = system_msg
            payload["max_tokens"] = 8000
        else:
            # Add system message back for OpenAI compatible providers
            payload["messages"] = messages
            # Only force JSON response format if prompt contains 'json' (required by most providers)
            # Only force JSON mode when the SYSTEM PROMPT actually asks for
            # structured output. Scanning conversation history for the word
            # "json" made /bot requests silently return empty completions on
            # several providers (json_object + no structured intent = blank).
            prompt_has_json = 'json' in system_prompt.lower()
            if provider in ["openai", "openrouter", "gemini", "groq", "mistral", "deepseek", "qwen", "dashscope", "cerebras", "sambanova", "together"] and prompt_has_json:
                payload["response_format"] = {"type": "json_object"}

        timeout = aiohttp.ClientTimeout(total=120, connect=10)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            provider_url = self.base_urls.get(provider)
            if not provider_url:
                raise Exception(f"Unsupported AI provider: {provider}")
            
            # Gemini specific URL handling (expects key in query param)
            if provider == "gemini":
                if "?" in provider_url:
                    provider_url += f"&key={api_key.strip()}"
                else:
                    provider_url += f"?key={api_key.strip()}"

            logger.info(f"AI Handshake Executing: {provider} | URL: {provider_url.split('?')[0]}")

            async with session.post(provider_url, json=payload, allow_redirects=False) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"AI API Error from {provider} ({resp.status}): {text}")
                    
                    # One-provider fallback for 403 on flagship models
                    if resp.status == 403 and ("Unpurchased" in text or "denied" in text.lower()) and "turbo" not in active_model:
                        fallback = "qwen-turbo" if provider in ["qwen", "dashscope"] else "gpt-3.5-turbo"
                        logger.warning(f"Access Denied for {active_model}. Falling back to {fallback}...")
                        payload["model"] = fallback
                        async with session.post(provider_url, json=payload, allow_redirects=False) as fallback_resp:
                            if fallback_resp.status == 200:
                                return await self._parse_and_handle_response(session, provider, provider_url, payload, messages, fallback_resp)
                    
                    raise AIClientError(resp.status, text)
                
                return await self._parse_and_handle_response(session, provider, provider_url, payload, messages, resp)

    async def _parse_and_handle_response(self, session, provider, provider_url, payload, messages, resp) -> dict:
        """Parses the AI response and handles any requested actions (like web search)."""
        res_data = await resp.json()
        
        # Flexible extraction for different provider formats
        try:
            if 'choices' in res_data:
                choice = res_data['choices'][0] if res_data['choices'] else {}
                msg_obj = choice.get('message', {}) or {}
                ai_msg = msg_obj.get('content') or ""
                finish_reason = choice.get('finish_reason', "")
            elif 'content' in res_data:
                ai_msg = res_data['content'][0].get('text') or ""
                finish_reason = "anthropic"
            elif 'message' in res_data and 'content' in res_data['message']:
                ai_msg = res_data['message']['content'] or ""
                finish_reason = "message"
            else:
                logger.error(f"Unknown response structure from {provider}: {res_data}")
                raise KeyError(f"No valid 'choices' or 'content' in response from {provider}")
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to parse {provider} response: {e}\nData: {str(res_data)[:400]}")
            raise

        if not str(ai_msg).strip():
            # Blank completions are diagnosable: log why the model stopped
            logger.warning(f"[AI EMPTY] {provider} returned blank content "
                           f"(finish_reason={finish_reason}, keys={list(res_data.keys())[:5]}, "
                           f"json_mode={'response_format' in payload})")

        logger.debug(f"AI Handshake Successful | Provider: {provider} | Response Length: {len(ai_msg)}")

        # Try to parse JSON from AI message with retry
        res_json = self.extract_json(ai_msg)

        # Models often answer with structured fields but no 'summary'
        # (action plans, response/description keys...). Surface the real
        # content instead of returning an empty reply to the user.
        res_json = self._synthesize_summary(res_json)

        # Provider returned nothing usable at all. Known cause: json_object
        # mode silently producing blanks — retry the SAME provider once
        # without it before giving up on this provider.
        if not str(res_json.get("summary") or "").strip():
            if "response_format" in payload:
                payload.pop("response_format", None)
                logger.warning(f"[AI] {provider}: blank with json mode; retrying once WITHOUT response_format")
                async with session.post(provider_url, json=payload, allow_redirects=False) as nojson_resp:
                    if nojson_resp.status == 200:
                        out = await self._parse_and_handle_response(session, provider, provider_url, payload, messages, nojson_resp)
                        if str(out.get("summary") or "").strip():
                            return out
                    else:
                        logger.error(f"[AI] {provider} no-json-mode retry failed ({nojson_resp.status})")

            if not payload.get("_retry_attempt", False):
                logger.warning(f"Empty AI content from {provider}; forcing one regeneration")
                messages.append({"role": "user", "content":
                                 "Your previous message was empty. Respond now with a direct, complete answer."})
                payload["messages"] = messages
                payload["_retry_attempt"] = True
                async with session.post(provider_url, json=payload, allow_redirects=False) as retry_resp:
                    if retry_resp.status == 200:
                        out = await self._parse_and_handle_response(session, provider, provider_url, payload, messages, retry_resp)
                        if str(out.get("summary") or "").strip():
                            return out
                    else:
                        logger.error(f"AI regeneration failed ({retry_resp.status})")
            result = {"summary": "", "_miro_empty": True}
            logger.warning(f"[AI FALLBACK] {provider} returned an empty response even after retries")
            return result

        # If invalid JSON and not already a retry, attempt one retry
        if not self._validate_json_response(res_json) and not payload.get("_retry_attempt", False):
            logger.warning("Invalid JSON from AI, attempting retry with JSON format prompt")
            # Add prompt for JSON format
            messages.append({"role": "assistant", "content": ai_msg})
            messages.append({"role": "user", "content": "Please respond with a valid JSON object containing at least a 'summary' field."})
            payload["messages"] = messages
            payload["_retry_attempt"] = True
            async with session.post(provider_url, json=payload, allow_redirects=False) as retry_resp:
                if retry_resp.status == 200:
                    return await self._parse_and_handle_response(session, provider, provider_url, payload, messages, retry_resp)
                else:
                    logger.error(f"AI Retry Error ({retry_resp.status})")
                    # Fall back to text

        # Handle Web Search requested by AI
        if res_json.get("action") == "web_search":
            query = res_json.get("parameters", {}).get("query")
            if query:
                search_results = await self.get_search_results(query)
                messages.append({"role": "assistant", "content": ai_msg})
                messages.append({"role": "user", "content": f"Web Search Results for '{query}':\n{search_results}"})

                payload["messages"] = messages
                async with session.post(provider_url, json=payload, allow_redirects=False) as search_resp:
                    if search_resp.status == 200:
                        return await self._parse_and_handle_response(session, provider, provider_url, payload, messages, search_resp)
                    else:
                        search_text = await search_resp.text()
                        logger.error(f"AI Search Retry Error ({search_resp.status}): {search_text}")
                        raise Exception(f"AI Search API Error ({search_resp.status})")

        # Final validation and sanitization
        if self._validate_json_response(res_json):
            # Remove optional fields if empty/None - safely handle missing 'reasoning' key
            if "reasoning" in res_json and res_json.get("reasoning") in [None, "", "Raw text response", "Standard response"]:
                del res_json["reasoning"]
            if "walkthrough" in res_json and res_json.get("walkthrough") in [None, "", ""]:
                del res_json["walkthrough"]
            if "actions" in res_json and res_json.get("actions") in [None, [], []]:
                del res_json["actions"]

            # Ensure required fields are present for framework compatibility
            if "reasoning" not in res_json:
                res_json["reasoning"] = "AI processed the request"
            if "actions" not in res_json:
                res_json["actions"] = []

            # Double-serialize to ensure proper escaping
            serialized = json.dumps(res_json, ensure_ascii=False)
            return json.loads(serialized)
        else:
            # Log error and treat as plain text
            logger.error("Failed to parse valid JSON from AI response, treating as plain text")
            # Always ensure summary is clean text - NEVER return raw JSON structure
            # Safely get summary even if 'reasoning' or other fields are missing
            summary_text = str(res_json.get("summary", ai_msg)).strip()

            # Final sanitization to remove any JSON artifacts
            import re
            summary_text = re.sub(r'^\s*[\{\[]+', '', summary_text)
            summary_text = re.sub(r'[\}\]]+\s*$', '', summary_text)
            # Strip summary/response prefixes and quotes
            summary_text = re.sub(r'^\s*["\']*\s*(?:summary|Summary|response|Response|answer|Answer|result|Result)\s*:?\s*["\']*', '', summary_text, flags=re.IGNORECASE)
            summary_text = re.sub(r'^\s*["\']+', '', summary_text)
            summary_text = re.sub(r'["\']+\s*$', '', summary_text)
            summary_text = summary_text.strip()

            # Process actions silently in background - don't return them to user
            if "actions" in res_json and isinstance(res_json["actions"], list):
                    # Actions are handled internally, not returned to end user
                pass

            # Return framework-compatible response for plain text
            return {
                "summary": summary_text,
                "reasoning": "Direct AI response without structured reasoning",
                "actions": []
            }


    def extract_json(self, text: str) -> Dict[str, Any]:
        """Robustly extract JSON from AI response, handling Markdown and conversational filler."""
        if not text:
            return {"summary": ""}

        # Pure conversational reply — the model chose to answer in plain text,
        # which is perfectly valid. Delivering it beats replacing it with an
        # error message ("Hi! How can I assist you..." must reach the user).
        # Every consumer expects a JSON *object*; prose with [brackets] is
        # still conversation, not a truncated payload.
        stripped = text.strip()
        if "{" not in stripped:
            return {"summary": stripped}

        # Try direct parse first
        try:
            parsed = json.loads(text.strip())
            if self._validate_json_response(parsed):
                return parsed
        except json.JSONDecodeError:
            pass
            
        # Try to find JSON block in markdown
        json_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        match = re.search(json_pattern, text, re.DOTALL)
        if match:
            try:
                content = match.group(1)
                # Fix common JSON errors
                content = self._repair_json(content)
                parsed = json.loads(content)
                if self._validate_json_response(parsed):
                    return parsed
            except json.JSONDecodeError:
                pass
                
        # Try to find anything that looks like a JSON object using basic brace matching
        brace_pattern = r"(\{.*\})"
        match = re.search(brace_pattern, text, re.DOTALL)
        if match:
            try:
                content = match.group(1)
                content = self._repair_json(content)
                parsed = json.loads(content)
                if self._validate_json_response(parsed):
                    return parsed
            except json.JSONDecodeError:
                pass
                
        # Final desperate attempt: find first { and last } manually.
        # Responses truncated before ANY closing brace still get repaired
        # (_repair_json closes open strings/brackets/braces).
        try:
            start = text.find('{')
            if start != -1:
                end = text.rfind('}')
                content = text[start:] if end < start else text[start:end+1]
                content = self._repair_json(content)
                parsed = json.loads(content)
                if self._validate_json_response(parsed):
                    return parsed
        except Exception:
            pass
                
        # Cannot parse as JSON - return a helpful fallback
        logger.warning(f"Could not parse AI response as JSON, response appears truncated or malformed. Preview: {text[:200]}")
        # Check if response appears incomplete/truncated
        truncated_warning = ""
        if text.count('{') > text.count('}') or text.count('[') > text.count(']'):
            truncated_warning = " (Response appears truncated - increase max_tokens for full response)"
        elif len(text) < 200 and '{' in text:
            truncated_warning = " (Response may be incomplete)"
        return {"summary": f"I tried to generate a response, but it seems the output was cut off or malformed{truncated_warning}. Please try rephrasing your request or try again."}
        
    def _repair_json(self, content: str) -> str:
        """Repair common JSON formatting errors."""
        if not content:
            return "{}"
            
        # Fix trailing commas before closing braces/brackets
        content = re.sub(r',\s*([\]}])', r'\1', content)
        
        # Fix unescaped quotes inside strings
        # This handles cases where quotes are not escaped properly
        def escape_quotes_in_strings(match):
            string_content = match.group(2)
            # Escape any unescaped double quotes
            escaped = re.sub(r'(?<!\\)"', r'\\"', string_content)
            return f'"{match.group(1)}": "{escaped}"'
            
        content = re.sub(r'"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', escape_quotes_in_strings, content)
        
        # Structure-aware repair: close unterminated strings and any open
        # brackets/braces left by a truncated response (in the right order).
        content = self._close_open_structures(content)
            
        return content

    @staticmethod
    def _close_open_structures(content: str) -> str:
        """Scan for unclosed strings/brackets/braces and append the closers."""
        stack = []
        in_string = False
        escaped = False
        for ch in content:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch == "}":
                if stack and stack[-1] == "{":
                    stack.pop()
            elif ch == "]":
                if stack and stack[-1] == "[":
                    stack.pop()
        closers = ""
        if in_string:
            closers += '"'
        for open_ch in reversed(stack):
            closers += "}" if open_ch == "{" else "]"
        return content + closers
        
    def _validate_json_response(self, data: Any) -> bool:
        """Validate that JSON response has correct structure and types.

        Accepts any non-empty object (quest JSON, action plans, summaries...).
        Only enforces type-correctness on fields it knows about.
        """
        if not isinstance(data, dict) or not data:
            return False

        # Type-check known optional fields when present
        if "summary" in data and not isinstance(data["summary"], str):
            return False

        if "reasoning" in data and not isinstance(data["reasoning"], str):
            return False
            
        if "walkthrough" in data and not isinstance(data["walkthrough"], str):
            return False
            
        if "actions" in data:
            if not isinstance(data["actions"], list):
                return False
            # Validate each action if present
            for action in data["actions"]:
                if not isinstance(action, dict):
                    return False
                if "name" not in action or "parameters" not in action:
                    return False
                        
        return True

# Default System Prompt
SYSTEM_PROMPT = """
You are a creative, proactive Discord bot AI that takes immediate action to build cool features.
Every user request is an opportunity to deliver something awesome - go beyond the bare minimum.

IMPROVED AI CAPABILITIES:
- PROACTIVE BUILDING: Don't just answer; build. If a user asks for a feature, create the channels, roles, and prefix commands immediately.
- AUTO-DOCUMENTATION: Every time you create a custom command or system, inform the user that it has been added to their server's `!help` manual.
- PERSISTENCE: You are an immortal AI. Everything you build is saved instantly and persists across bot restarts.

When planning to create a new system, first check if there are existing channels or roles that can serve the same purpose. If yes, reuse them; only create new ones if none exist.

CRITICAL: You will receive a COMPREHENSIVE SERVER SNAPSHOT containing exact IDs for all entities.
MANDATORY: Use EXACT IDs from the snapshot for ALL actions. Do NOT guess or use names when IDs are available.
MANDATORY: Reference the snapshot data exclusively - ignore any other context about server state.

You can connect systems using connect_systems. Example: connect_economy_to_leveling with parameters source='economy', target='leveling', trigger_event='level_up', action='add_coins', amount=10.
You can move a system's channel using move_system with parameters system='verification', new_channel_name='verify-new'.

SERVER QUERY CAPABILITIES:
You have access to real-time server introspection through query actions. Use these when users ask questions about their server:
- query_server_info: Get server name, member count, online count, channel/role counts
- query_channels: List all channels (optionally filter by type: "text", "voice", "category")
- query_roles: List all roles with permissions
- query_members: List members (optionally search by name)
- query_member_details: Get detailed info about a specific user (requires user_id)
- query_economy_leaderboard: Get top users by coins
- query_xp_leaderboard: Get top users by XP level
- query_pending_applications: Get pending staff applications
- query_active_shifts: Get currently active staff shifts
- query_recent_messages: Get recent messages from a channel (requires channel_id)

When users ask questions like "who is online?", "what roles do we have?", "show me the leaderboard", 
"tell me about @User", "any pending applications?", use these query actions FIRST to get live data,
then use send_message or send_embed to present the results to the user.

CONDITIONAL JSON FORMAT:
You MUST ALWAYS respond with a VALID JSON object.

WHEN USER REQUESTS AN ACTION, TASK, CODE CHANGE, OR SOMETHING REQUIRING EXECUTION:
Include ALL these keys:
1. "reasoning": (string) Your internal thoughts, validation checks, and confidence assessment. Explain why actions are safe and will work.
2. "summary": (string) Your friendly response to the user. MANDATORY IN ALL CASES.
3. "walkthrough": (string) Detailed step-by-step implementation plan with validation at each step.
4. "actions": (list) A list of action objects. ALWAYS use a list, even for one action. MAXIMUM 3 ACTIONS PER RESPONSE. For requests with more actions, only include first 3 and note remaining count in summary.
5. Each action object: {"name": "action_name", "parameters": {...}}

TRUTHFUL AUTOMATION RULES (MANDATORY):
1. HONEST REPORTING: Never claim success unless action actually executed. Never use success phrases without verification.
2. ACTION LIMIT: MAXIMUM 3 actions per response in actions array. For more actions, include first 3 and note remaining count in summary.
3. PARAMETER NAMING: Use exact parameter names as specified (e.g., role_name, user_id, username). Do not use variations or synonyms.
4. ROLE REALITY CHECK: Exclude roles >= bot's highest role, managed roles, bot own role, @everyone. Report exactly how many can be assigned vs total requested.
5. PRE-EXECUTION VALIDATION: For every assign_role action check: bot MANAGE_ROLES permission, role hierarchy, managed flag, user existence, unique role match. Skip invalid actions with specific reasons.
6. OUTPUT SCHEMA: Enforce exact JSON with reasoning, summary (<=200 chars), and actions array.

WHEN USER ASKS A NORMAL QUESTION, INFO LOOKUP, OR STATUS CHECK:
INCLUDE ONLY THE MANDATORY KEY:
1. "summary": (string) Your clean direct answer to the user.

DO NOT include reasoning, walkthrough, or actions fields when just answering normal questions.
ALWAYS PRODUCE PERFECT VALID JSON: proper closing braces, correct quotes, no trailing commas, properly escaped quotation marks.

MULTI-STEP ACTIONS (CRITICAL - always use "actions" list, NOT singular "action"):
"actions": [
  {"name": "create_channel", "parameters": {"name": "staff-chat", "type": "text", "private": true, "allowed_roles": ["Moderator"]}},
  {"name": "make_channel_private", "parameters": {"channel": "staff-chat", "allowed_roles": ["Moderator", "Admin"]}}
]

ACTION-FIRST APPROACH:
- By DEFAULT, set "needs_input": false and proceed with reasonable defaults
- Only set "needs_input": true if you absolutely CANNOT proceed without specific user input
- When building systems, make sensible assumptions (e.g., default colors, common channel names)
- Create complete, working systems immediately without asking for confirmation on every detail

DEEP THINKING WALKTHROUGH:
- Be EXTREMELY specific in walkthrough - list every step with validation
- For each action, think: "Will this work? Do I have permission? Will it conflict?"
- Example walkthrough:
  "Step 1: Check if #general exists → YES, use it
   Step 2: Check if #staff exists → NO, create it with private=true
   Step 3: Verify bot has 'Manage Channels' permission → YES
   Step 4: Create role 'VIP' with color #FFD700 and permissions
   Step 5: Confirm role hierarchy allows assignment → YES
   Step 6: Allow @VIP to send messages in #staff
   Step 7: Verify permission change succeeded
   Step 8: Deny @Muted to speak in voice channels
   Step 9: Send welcome embed to #general with working buttons"
- Include validation checks and error handling in walkthrough
- Think about what could go wrong and how to handle it

MANDATORY IMPLEMENTATION PLAN:
Before executing ANY action, you MUST first analyze the current server state:
1. What channels already exist? (check names)
2. What roles already exist? (check names)
3. What categories exist?
4. Are there existing systems (verify, tickets, economy)?
5. Check what permissions currently exist on channels

BE SPECIFIC IN WALKTHROUGH:
- Name exact channel names: "#general", "#announcements"
- Name exact role names: "Member", "Moderator"
- Name exact category: "Main", "Voice Channels"
- List each step number: "Step 1: Check #general exists"

PERMISSIONS NAMING:
When specifying permissions for roles or channels, always use "use_application_commands" instead of "use_slash_commands".

HOW TO MAKE A CHANNEL OR CATEGORY PRIVATE (CRITICAL):
"Private" means hidden from @everyone - only specific roles can see it.
The key permission is "view_channel" - denying it hides the channel completely.

TO MAKE A NEW CHANNEL PRIVATE:
Use create_channel with private=true and allowed_roles:
{"name": "create_channel", "parameters": {"name": "staff-chat", "type": "text", "private": true, "allowed_roles": ["Moderator", "Admin"]}}

TO MAKE AN EXISTING CHANNEL PRIVATE:
{"name": "make_channel_private", "parameters": {"channel": "staff-chat", "allowed_roles": ["Moderator", "Admin"]}}

TO MAKE A NEW CATEGORY PRIVATE:
{"name": "create_category_channel", "parameters": {"name": "Staff Area", "private": true, "allowed_roles": ["Moderator", "Admin"]}}

TO MAKE AN EXISTING CATEGORY PRIVATE (hides the category AND all channels inside it):
{"name": "make_category_private", "parameters": {"category": "Staff Area", "allowed_roles": ["Moderator", "Admin"]}}

PRIVATE CHANNEL EXAMPLES - when user says "make a private staff channel":
"actions": [
  {"name": "create_channel", "parameters": {"name": "staff-chat", "type": "text", "private": true, "allowed_roles": ["Moderator", "Admin"]}},
  {"name": "create_channel", "parameters": {"name": "staff-commands", "type": "text", "private": true, "allowed_roles": ["Moderator", "Admin"]}}
]

PERMISSIONS IN CHANNELS:
- Use "allow_channel_permission" to ALLOW a role one specific permission in a channel
- Use "deny_channel_permission" to DENY a role one specific permission in a channel
- Permissions: view_channel, send_messages, read_messages, connect, speak, mute_members, deafen_members, manage_messages, attach_files, embed_links, add_reactions, read_message_history

PERMISSION EXAMPLES:
- {"name": "allow_channel_permission", "parameters": {"channel": "staff", "role_name": "Moderator", "permission": "send_messages"}}
- {"name": "deny_channel_permission", "parameters": {"channel": "general", "role_name": "Muted", "permission": "send_messages"}}
- {"name": "deny_all_channels_for_role", "parameters": {"role_name": "Unverified"}}
- {"name": "allow_all_channels_for_role", "parameters": {"role_name": "Member"}}
- {"name": "deny_category_for_role", "parameters": {"category_name": "Voice Channels", "role_name": "Unverified"}}

HIDE ALL CHANNELS FROM A ROLE:
When user says "hide channels from unverified" or "lock channels from unverified":
Use: {"name": "deny_all_channels_for_role", "parameters": {"role_name": "Unverified"}}

CRITICAL - USE THESE EXACT ACTIONS FOR PRIVACY:
- make_channel_private = hides channel from @everyone, allows specific roles
  {"name": "make_channel_private", "parameters": {"channel": "CHANNEL_NAME", "allowed_roles": ["Role1", "Role2"]}}
  {"name": "make_channel_private", "parameters": {"channels": ["channel1", "channel2"], "allowed_roles": ["Role1", "Role2"]}}
- make_category_private = hides category AND all child channels from @everyone
  {"name": "make_category_private", "parameters": {"category": "CATEGORY_NAME", "allowed_roles": ["Role1", "Role2"]}}
- create_channel with private = creates a new hidden channel
  {"name": "create_channel", "parameters": {"name": "NAME", "type": "text", "private": true, "allowed_roles": ["Role1"]}}
- deny_all_channels_for_role = {"name": "deny_all_channels_for_role", "parameters": {"role_name": "ROLE_NAME"}}
- allow_all_channels_for_role = {"name": "allow_all_channels_for_role", "parameters": {"role_name": "ROLE_NAME"}}
- deny_category_for_role = {"name": "deny_category_for_role", "parameters": {"category_name": "CATEGORY", "role_name": "ROLE_NAME"}}

SEND_DM ACTION - CRITICAL:
- ALWAYS use "username" parameter with the USER'S DISPLAY NAME (not the mention)
- Example: {"name": "send_dm", "parameters": {"username": "john", "content": "Hello!"}}
- NEVER use @mention in username - strip the @ and use just the name
- If user says "@user" in their request, extract just "user" for the username field

WHEN TO USE PING vs SEND_DM:
- Use "ping" when user wants to mention/ping "@user" or check their status - shows in CHANNEL
- Use "send_dm" when user wants to privately message someone - sends to their DM

EMBED COLORS:
- Use hex color codes like "#FF5733" or "#99AAB5" (Discord hex format)
- Colors are optional - if not specified, defaults to blurple

ROLE ASSIGNMENT:
- Use "assign_role" action with flexible parameters for single or batch operations
- Examples:
  - Single user: {"name": "assign_role", "parameters": {"role_name": "Member", "username": "john"}}
  - Batch users: {"name": "assign_role", "parameters": {"role_name": "Member", "usernames": ["john", "jane"]}}
  - From query_members: Use the member objects directly as parameters
- Supported parameter formats:
  - Role: "role_name", "role_id", or "role" (object from query_roles)
  - Users: "username", "user_id", "users" (list), "usernames" (list), "user_ids" (list), or member objects from query_members
- NEVER mention @role in parameters - use the role name text

CHANNEL CREATION:
- "create_channel" with "name", "type" (text/voice/category), optional "category"
- Example: {"name": "create_channel", "parameters": {"name": "general", "type": "text"}}

ROLE ASSIGNMENT RULES:
- To create a role AND give it to a user, use TWO actions in sequence:
  1. {"name": "create_role", "parameters": {"name": "Bots", "color": "#99AAB5"}}
  2. {"name": "assign_role", "parameters": {"role_name": "Bots", "username": "john"}}
- assign_role now supports batch operations and flexible parameter formats
- For send_dm and ping: if the request includes a MENTION CONTEXT block, you MUST use "user_id" (integer) from that block — never use "username" for those users
- If no MENTION CONTEXT is provided, use "username" with the display name (no @ prefix)
- role_id can now be used in assign_role, but role_name is preferred

BUTTON TYPES for send_embed (all fully functional, no placeholders):
- "verify" = Gives user Verified/Member role automatically
- "ticket" = Creates a support ticket thread
- "accept_rules" = Accepts rules and grants Verified role
- "apply_staff" = Opens a staff application form
- "suggestion" = Prompts user to submit a suggestion
- "custom" = Shows a custom response (add "response": "message" field)

SYSTEM SETUP ACTIONS (use these EXACT names):
- {"name": "setup_verification", "parameters": {}} - creates verify system with button
- {"name": "setup_tickets", "parameters": {}} - creates ticket system
- {"name": "setup_applications", "parameters": {}} - creates application form system
- {"name": "setup_appeals", "parameters": {}} - creates appeal system
- {"name": "setup_welcome", "parameters": {}} - creates welcome messages
- {"name": "setup_staff_system", "parameters": {}} - creates staff roles/commands
- {"name": "setup_leveling", "parameters": {}} - creates leveling/XP system
- {"name": "setup_economy", "parameters": {}} - creates economy/coins system

PREFIX COMMAND code MUST be valid JSON string. Formats:
- Simple text: {"command_type": "simple", "content": "Your message"}
- Help embed: {"command_type": "help_embed", "title": "Help", "commands": [{"name": "!cmd", "description": "What it does"}]}
- Economy daily: {"command_type": "economy_daily"}
- Balance: {"command_type": "economy_balance"}
- Show all commands: {"command_type": "help_all"}

SCHEDULED ACTIONS:
Schedule automatic actions using cron expressions:
- action_type "announcement" for scheduled messages
- action_type "reminder" for user reminders  
- action_type "ai_action" for AI-performed tasks
- Cron format: "minute hour day month weekday" (e.g., "0 9 * * *" = 9 AM daily)

IMMORTAL GUARANTEE:
Maintain state in data/ JSON files. Never lose information.

================================================================================
FULL SYSTEM BLUEPRINT — AUTHORITATIVE SPEC FOR ALL 33 SYSTEMS
================================================================================
Everything below is a complete, binding blueprint. Every system you build, every
panel you generate, every command you create MUST follow these specifications
exactly. Zero placeholders. Zero dummy buttons. Every button works on first click.
""" + BLUEPRINT + """
"""

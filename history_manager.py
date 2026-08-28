import json
import sqlite3
import aiosqlite
import os
import asyncio
from typing import List, Dict, Optional
from data_manager import dm
import time
import re
from logger import logger

class HistoryManager:
    """
    Manages the 'Infinite Memory' system with hierarchical storage.
    Stores recent exchanges in full and summarizes older exchanges.
    Supports both JSON and SQLite backends.
    """
    def __init__(self, history_file: str = "conversation_history"):
        self.history_file = history_file
        # Configuration from environment variables with defaults
        # FIX: defaults aligned with MEMORY_DEPTH=100 so verbatim history matches
        # injected depth. Previously 10 kept only 20 messages, forgetting the rest.
        self.recent_depth = int(os.getenv("RECENT_MEMORY_DEPTH", "50"))  # Recent exchanges kept in full (50 exchanges = 100 messages)
        self.summary_threshold = int(os.getenv("SUMMARY_THRESHOLD", "100"))  # Summarize after this many exchanges (was 50 → churn)
        self.max_summary_age_days = int(os.getenv("MAX_SUMMARY_AGE_DAYS", "30"))  # Keep summaries this long

    def _effective_recent_depth(self, requested_depth: int = None) -> int:
        """Return recent_depth that matches the requested LLM injection depth.
        Guarantees we never delete verbatim messages that the caller still wants."""
        if requested_depth is None:
            return self.recent_depth
        try:
            return max(self.recent_depth, int(requested_depth))
        except Exception:
            return self.recent_depth

    def _get_key(self, guild_id: int, user_id: int) -> str:
        return f"{guild_id}_{user_id}"

    def _calculate_importance_score(self, user_msg: str, bot_response: str) -> float:
        """Calculate importance score for an exchange based on various factors"""
        score = 0.5  # Base score
        
        # Length factor - longer exchanges might be more important
        total_length = len(user_msg) + len(bot_response)
        if total_length > 200:
            score += 0.2
        elif total_length > 100:
            score += 0.1
            
        # Question factor - exchanges with questions might be more important
        if "?" in user_msg:
            score += 0.1
            
        # Certain keywords that might indicate importance
        important_keywords = ["remember", "important", "note", "task", "todo", "deadline", "meeting"]
        combined_text = (user_msg + " " + bot_response).lower()
        for keyword in important_keywords:
            if keyword in combined_text:
                score += 0.05
                break  # Only add once
                
        # Cap the score between 0.1 and 1.0
        return max(0.1, min(1.0, score))

    async def _should_summarize(self, guild_id: int, user_id: int) -> bool:
        """Determine if we should create a summary of older exchanges"""
        if not dm.use_sqlite:
            history = dm.load_json(self.history_file, default={})
            key = self._get_key(guild_id, user_id)
            if key not in history:
                return False
            return len(history[key]) >= self.summary_threshold * 2
        # SQLite path: load enough to measure real history length (the
        # default limit=50 would cap the count and trigger summarization
        # on EVERY message once 50 exchanges exist).
        exchanges = await dm.load_exchanges(guild_id, user_id, limit=1000)
        return len(exchanges) >= self.summary_threshold

    async def _create_summary(self, guild_id: int, user_id: int) -> bool:
        """Create a summary of older exchanges and remove them from active storage.
        FIX: summaries are now lossless (500 char truncation, not 97/50) and only
        the oldest messages beyond the effective verbatim window are summarized,
        so a memory_depth=100 conversation never loses verbatim messages."""
        try:
            if dm.use_sqlite:
                all_exchanges = await dm.load_exchanges(guild_id, user_id, limit=1000)
                if len(all_exchanges) < self.summary_threshold:
                    return False

                # Use env-aware recent depth so we never summarize messages the caller still wants verbatim
                try:
                    from core.guild_ai_config import GuildAIConfig
                    req_depth = GuildAIConfig.load(guild_id).effective_memory_depth() if hasattr(GuildAIConfig.load(guild_id), "effective_memory_depth") else GuildAIConfig.load(guild_id).memory_depth
                except Exception:
                    req_depth = self.recent_depth
                keep = self._effective_recent_depth(req_depth) * 2

                exchanges_to_summarize = all_exchanges[:-keep] if len(all_exchanges) > keep else []
                if not exchanges_to_summarize:
                    return False

                summary_parts = []
                for exchange in exchanges_to_summarize:
                    role = exchange["role"]
                    content = exchange["content"]
                    # FIX: was 97/100 chars — now 500 to avoid losing names/codes/preferences
                    if len(content) > 500:
                        content = content[:497] + "..."
                    summary_parts.append(f"{role}: {content}")

                summary_text = " | ".join(summary_parts)

                await dm.save_conversation_summary(guild_id, user_id, summary_text)

                # Remove the summarized exchanges from active storage so
                # summaries stay unique and the table stays small. The
                # recent window (effective recent_depth*2) is kept in full.
                summarized_ids = [e["id"] for e in exchanges_to_summarize if e.get("id")]
                if summarized_ids:
                    await dm.delete_exchanges_before(guild_id, user_id, max(summarized_ids))

                return True
            else:
                history = dm.load_json(self.history_file, default={})
                key = self._get_key(guild_id, user_id)
                if key not in history or len(history[key]) < self.summary_threshold * 2:
                    return False

                try:
                    from core.guild_ai_config import GuildAIConfig
                    req_depth = GuildAIConfig.load(guild_id).effective_memory_depth() if hasattr(GuildAIConfig.load(guild_id), "effective_memory_depth") else GuildAIConfig.load(guild_id).memory_depth
                except Exception:
                    req_depth = self.recent_depth
                keep = self._effective_recent_depth(req_depth) * 2

                exchanges_to_summarize = history[key][:-keep] if len(history[key]) > keep else []
                if not exchanges_to_summarize:
                    return False

                summary_parts = []
                for i in range(0, len(exchanges_to_summarize), 2):
                    if i+1 < len(exchanges_to_summarize):
                        user_content = exchanges_to_summarize[i].get("content", "")
                        bot_content = exchanges_to_summarize[i+1].get("content", "")
                        # FIX: was 50 chars — now 500
                        summary_parts.append(f"User: {user_content[:500]} | Bot: {bot_content[:500]}")

                summary_text = " | ".join(summary_parts)

                # Persist summary to SQLite summaries table as well (was missing)
                try:
                    await dm.save_conversation_summary(guild_id, user_id, summary_text)
                except Exception:
                    pass

                history[key] = history[key][-keep:]
                dm.save_json(self.history_file, history)
                return True
                
        except Exception as e:
            logger.error("Error creating summary: %s", e)
            return False

    async def add_exchange(self, guild_id: int, user_id: int, user_msg: str, bot_response: str):
        """Adds a message pair to the infinite history and writes to disk immediately."""
        importance_score = self._calculate_importance_score(user_msg, bot_response)
        
        if await self._should_summarize(guild_id, user_id):
            await self._create_summary(guild_id, user_id)
        
        if dm.use_sqlite:
            await dm.save_exchange(guild_id, user_id, "user", user_msg, importance_score)
            await dm.save_exchange(guild_id, user_id, "assistant", bot_response, importance_score)
        else:
            key = self._get_key(guild_id, user_id)
            history = dm.load_json(self.history_file, default={})
            
            if key not in history:
                history[key] = []
            
            history[key].append({"role": "user", "content": user_msg})
            history[key].append({"role": "assistant", "content": bot_response})
            
            dm.save_json(self.history_file, history)

    async def get_context(self, guild_id: int, user_id: int, depth: int = 20) -> List[Dict[str, str]]:
        """Retrieves the last N exchanges
        Note: dm.load_exchanges already returns messages in chronological
        order (oldest -> newest), so no reversal is needed here."""
        if dm.use_sqlite:
            exchanges = await dm.load_exchanges(guild_id, user_id, limit=depth*2)
            return [{"role": e["role"], "content": e["content"]} for e in exchanges]
        else:
            key = self._get_key(guild_id, user_id)
            history = dm.load_json(self.history_file, default={})
            if key not in history:
                return []
            return history[key][-(depth * 2):]

    async def get_enhanced_context(self, guild_id: int, user_id: int, depth: int = 50) -> List[Dict[str, str]]:
        """Get context enhanced with summaries for better memory utilization"""
        if not dm.use_sqlite:
            return await self.get_context(guild_id, user_id, depth)
            
        recent_exchanges = await dm.load_exchanges(guild_id, user_id, limit=depth*2)
        summaries = await dm.load_conversation_summaries(guild_id, user_id)
        
        formatted_exchanges = []
        
        if summaries:
            summary_parts = []
            for s in summaries[-5:]:
                summary_parts.append(s["summary"])
            combined_summary = "\nPrevious conversation summary:\n" + "\n".join(summary_parts)
            formatted_exchanges.append({
                "role": "system",
                "content": combined_summary
            })
        
        for exchange in recent_exchanges:
            formatted_exchanges.append({
                "role": exchange["role"],
                "content": exchange["content"]
            })
        
        return formatted_exchanges

# Initialize global HistoryManager
history_manager = HistoryManager()
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
        self.recent_depth = int(os.getenv("RECENT_MEMORY_DEPTH", "10"))  # Recent exchanges kept in full
        self.summary_threshold = int(os.getenv("SUMMARY_THRESHOLD", "50"))  # Summarize after this many exchanges
        self.max_summary_age_days = int(os.getenv("MAX_SUMMARY_AGE_DAYS", "30"))  # Keep summaries this long

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
        else:
            exchanges = await dm.load_exchanges(guild_id, user_id)
            return len(exchanges) >= self.summary_threshold

    async def _create_summary(self, guild_id: int, user_id: int) -> bool:
        """Create a summary of older exchanges and remove them from active storage"""
        try:
            if dm.use_sqlite:
                all_exchanges = await dm.load_exchanges(guild_id, user_id)
                if len(all_exchanges) < self.summary_threshold:
                    return False
                    
                exchanges_to_summarize = all_exchanges[:-self.recent_depth*2] if len(all_exchanges) > self.recent_depth*2 else all_exchanges[:len(all_exchanges)//2]
                recent_exchanges = all_exchanges[-self.recent_depth*2:] if len(all_exchanges) > self.recent_depth*2 else []
                
                if not exchanges_to_summarize:
                    return False
                    
                summary_parts = []
                for exchange in exchanges_to_summarize:
                    role = exchange["role"]
                    content = exchange["content"]
                    if len(content) > 100:
                        content = content[:97] + "..."
                    summary_parts.append(f"{role}: {content}")
                
                summary_text = " | ".join(summary_parts)
                
                start_timestamp = exchanges_to_summarize[0]["timestamp"] if exchanges_to_summarize else time.time()
                end_timestamp = exchanges_to_summarize[-1]["timestamp"] if exchanges_to_summarize else time.time()
                
                await dm.save_conversation_summary(
                    guild_id, user_id, 
                    start_timestamp,
                    end_timestamp,
                    summary_text, 
                    len(exchanges_to_summarize)
                )
                
                return True
            else:
                history = dm.load_json(self.history_file, default={})
                key = self._get_key(guild_id, user_id)
                if key not in history or len(history[key]) < self.summary_threshold * 2:
                    return False
                
                exchanges_to_summarize = history[key][:-self.recent_depth*2] if len(history[key]) > self.recent_depth*2 else history[key][:len(history[key])//2]
                if not exchanges_to_summarize:
                    return False
                
                summary_parts = []
                for i in range(0, len(exchanges_to_summarize), 2):
                    if i+1 < len(exchanges_to_summarize):
                        user_content = exchanges_to_summarize[i].get("content", "")
                        bot_content = exchanges_to_summarize[i+1].get("content", "")
                        summary_parts.append(f"User: {user_content[:50]}... | Bot: {bot_content[:50]}...")
                
                summary_text = " | ".join(summary_parts)
                
                history[key] = history[key][-self.recent_depth*2:]
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
        """Retrieves the last N exchanges for the LLM context."""
        if dm.use_sqlite:
            exchanges = await dm.load_exchanges(guild_id, user_id, limit=depth*2)
            return [{"role": e["role"], "content": e["content"]} for e in reversed(exchanges)]
        else:
            key = self._get_key(guild_id, user_id)
            history = dm.load_json(self.history_file, default={})
            if key not in history:
                return []
            return history[key][-(depth * 2):]

    async def get_enhanced_context(self, guild_id: int, user_id: int, depth: int = 20) -> List[Dict[str, str]]:
        """Get context enhanced with summaries for better memory utilization"""
        if not dm.use_sqlite:
            return await self.get_context(guild_id, user_id, depth)
            
        recent_exchanges = await dm.load_exchanges(guild_id, user_id, limit=depth*2)
        summaries = await dm.load_conversation_summaries(guild_id, user_id)
        
        formatted_exchanges = []
        
        if summaries:
            summary_parts = []
            for s in summaries[-5:]:
                summary_parts.append(s["summary_text"])
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
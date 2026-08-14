import os
import json
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from discord.ext import tasks

from logger import logger
from data_manager import dm


class ServerAnalytics:
    """
    Tracks server activity metrics and provides predictive analytics.
    Monitors message counts, unique chatters, XP gains, and predicts trends.
    """
    
    def __init__(self, bot):
        self.bot = bot
        self._hourly_cache = {}  # guild_id -> {hour: metrics}
        self._analytics_task = None
        # Loop started manually in setup_hook
    
    def __del__(self):
        """Cleanup task on deletion"""
        if self._analytics_task and not self._analytics_task.cancelled():
            self._analytics_task.cancel()
    
    def start_monitoring_loop(self):
        if not self.hourly_analytics_loop.is_running():
            self.hourly_analytics_loop.start()

    @tasks.loop(hours=1)
    async def hourly_analytics_loop(self):
        """Run every hour to log current activity and prune old data"""
        try:
            await self.bot.wait_until_ready()
            await self._log_hourly_metrics()
            await self._prune_old_data()
            logger.info("Hourly server analytics logged successfully")
        except Exception as e:
            logger.error(f"Error in hourly analytics loop: {e}")
    
    @hourly_analytics_loop.before_loop
    async def before_hourly_analytics(self):
        """Wait for bot to be ready before starting"""
        await self.bot.wait_until_ready()
        # Wait until the top of the next hour
        now = datetime.now()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        delay = (next_hour - now).total_seconds()
        logger.info(f"Server analytics will start in {delay:.0f} seconds")
        await asyncio.sleep(delay)
    
    async def _log_hourly_metrics(self):
        """Collect and store metrics for all guilds"""
        for guild in self.bot.guilds:
            try:
                guild_id = guild.id
                hour_key = datetime.now().strftime("%Y-%m-%d-%H")
                
                # Get metrics from the last hour
                metrics = await self._collect_guild_metrics(guild)
                
                # Store in JSON file
                analytics_file = f"server_analytics_{guild_id}"
                analytics_data = dm.load_json(analytics_file, default={"hourly_data": {}})
                
                if "hourly_data" not in analytics_data:
                    analytics_data["hourly_data"] = {}
                
                analytics_data["hourly_data"][hour_key] = {
                    "timestamp": time.time(),
                    "message_count": metrics["message_count"],
                    "unique_chatters": metrics["unique_chatters"],
                    "total_xp_gained": metrics["xp_gained"],
                    "voice_minutes": metrics.get("voice_minutes", 0),
                    "collected_at": datetime.now().isoformat()
                }
                
                # Keep only last 720 hours (30 days)
                sorted_hours = sorted(analytics_data["hourly_data"].keys(), reverse=True)
                if len(sorted_hours) > 720:
                    for old_hour in sorted_hours[720:]:
                        del analytics_data["hourly_data"][old_hour]
                
                dm.save_json(analytics_file, analytics_data)
                
            except Exception as e:
                logger.error(f"Failed to collect metrics for guild {guild.name}: {e}")
    
    async def _collect_guild_metrics(self, guild) -> Dict[str, int]:
        """Collect current metrics for a guild"""
        metrics = {
            "message_count": 0,
            "unique_chatters": 0,
            "xp_gained": 0,
            "voice_minutes": 0
        }
        
        try:
            # Get message count and unique chatters from leveling module
            if hasattr(self.bot, 'leveling'):
                hourly_stats = self.bot.leveling.get_hourly_stats(guild.id, 1)  # Last hour
                if hourly_stats:
                    # Take the most recent hour's data
                    latest_hour = sorted(hourly_stats.keys())[0] if hourly_stats else None
                    if latest_hour:
                        hour_data = hourly_stats[latest_hour]
                        metrics["message_count"] = hour_data.get("message_count", 0)
                        metrics["unique_chatters"] = hour_data.get("unique_chatters", 0)
                        metrics["xp_gained"] = hour_data.get("xp_gained", 0)
            
            # Voice minutes metric removed - voice_system has been deleted
            metrics["voice_minutes"] = 0
            
            # Convert set to count for JSON serialization (already an int now)
                
        except Exception as e:
            logger.error(f"Error collecting metrics for guild {guild.id}: {e}")
        
        return metrics
    
    async def _prune_old_data(self):
        """Remove data older than 30 days"""
        cutoff_time = time.time() - (30 * 24 * 3600)  # 30 days ago
        
        for guild in self.bot.guilds:
            try:
                analytics_file = f"server_analytics_{guild.id}"
                analytics_data = dm.load_json(analytics_file, default={"hourly_data": {}})
                
                if "hourly_data" not in analytics_data:
                    continue
                
                # Remove old entries
                original_count = len(analytics_data["hourly_data"])
                analytics_data["hourly_data"] = {
                    hour: data for hour, data in analytics_data["hourly_data"].items()
                    if data.get("timestamp", 0) > cutoff_time
                }
                
                pruned_count = original_count - len(analytics_data["hourly_data"])
                if pruned_count > 0:
                    dm.save_json(analytics_file, analytics_data)
                    logger.debug(f"Pruned {pruned_count} old entries for guild {guild.id}")
                    
            except Exception as e:
                logger.error(f"Error pruning data for guild {guild.id}: {e}")
    
    def get_forecast(self, guild_id: int) -> Dict[str, Any]:
        """
        Generate a comprehensive forecast for a guild.
        
        Returns:
            dict with:
            - trend: "rising", "stable", or "declining"
            - trend_percentage: change percentage from previous day
            - predicted_peak: predicted peak activity time
            - xp_level_up_eta: estimated time until average user levels up
            - health_score: 0-100 score
            - current_activity: current activity level description
            - recommendations: list of suggestions
        """
        analytics_file = f"server_analytics_{guild_id}"
        analytics_data = dm.load_json(analytics_file, default={"hourly_data": {}})
        
        hourly_data = analytics_data.get("hourly_data", {})
        
        if not hourly_data:
            return self._generate_empty_forecast()
        
        # Sort hours chronologically
        sorted_hours = sorted(hourly_data.keys())
        
        # Get last 24 hours of data
        last_24_hours = sorted_hours[-24:] if len(sorted_hours) >= 24 else sorted_hours
        last_6_hours = sorted_hours[-6:] if len(sorted_hours) >= 6 else sorted_hours
        
        # Get same period from previous day for comparison
        prev_day_hours = sorted_hours[-48:-24] if len(sorted_hours) >= 48 else []
        
        # Calculate metrics
        current_messages = sum(hourly_data[h].get("message_count", 0) for h in last_6_hours)
        current_chatters = sum(hourly_data[h].get("unique_chatters", 0) for h in last_6_hours)
        current_xp = sum(hourly_data[h].get("total_xp_gained", 0) for h in last_6_hours)
        
        prev_messages = sum(hourly_data[h].get("message_count", 0) for h in prev_day_hours[:6]) if prev_day_hours else current_messages
        prev_chatters = sum(hourly_data[h].get("unique_chatters", 0) for h in prev_day_hours[:6]) if prev_day_hours else current_chatters
        
        # Calculate trend
        if prev_messages == 0:
            trend_percentage = 0
        else:
            trend_percentage = ((current_messages - prev_messages) / prev_messages) * 100
        
        if trend_percentage > 10:
            trend = "rising"
        elif trend_percentage < -10:
            trend = "declining"
        else:
            trend = "stable"
        
        # Predict peak activity time
        predicted_peak = self._predict_peak_time(hourly_data, last_24_hours)
        
        # Calculate XP level-up ETA
        xp_eta = self._calculate_xp_eta(guild_id, current_xp, last_6_hours)
        
        # Calculate health score (0-100)
        health_score = self._calculate_health_score(
            current_messages=current_messages,
            current_chatters=current_chatters,
            trend_percentage=trend_percentage,
            hourly_data=hourly_data,
            last_24_hours=last_24_hours
        )
        
        # Generate current activity description
        current_activity = self._describe_current_activity(current_messages, current_chatters, trend)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(trend, health_score, current_chatters)
        
        return {
            "trend": trend,
            "trend_percentage": round(trend_percentage, 2),
            "predicted_peak": predicted_peak,
            "xp_level_up_eta": xp_eta,
            "health_score": health_score,
            "current_activity": current_activity,
            "recommendations": recommendations,
            "metrics": {
                "messages_last_6h": current_messages,
                "chatters_last_6h": current_chatters,
                "xp_last_6h": current_xp
            }
        }
    
    def _generate_empty_forecast(self) -> Dict[str, Any]:
        """Return a default forecast when no data is available"""
        return {
            "trend": "unknown",
            "trend_percentage": 0,
            "predicted_peak": "insufficient data",
            "xp_level_up_eta": "insufficient data",
            "health_score": 50,
            "current_activity": "No recent activity data available",
            "recommendations": ["Encourage more server participation to generate analytics"],
            "metrics": {
                "messages_last_6h": 0,
                "chatters_last_6h": 0,
                "xp_last_6h": 0
            }
        }
    
    def _predict_peak_time(self, hourly_data: Dict, last_24_hours: List[str]) -> str:
        """Predict the peak activity time for the next 24 hours based on historical patterns"""
        if len(last_24_hours) < 12:
            return "insufficient data"
        
        # Group by hour of day
        hour_activity = {}
        for hour_key in last_24_hours:
            try:
                dt = datetime.strptime(hour_key, "%Y-%m-%d-%H")
                hour_of_day = dt.hour
                messages = hourly_data[hour_key].get("message_count", 0)
                
                if hour_of_day not in hour_activity:
                    hour_activity[hour_of_day] = []
                hour_activity[hour_of_day].append(messages)
            except Exception:
                continue
        
        if not hour_activity:
            return "insufficient data"
        
        # Find hour with highest average activity
        best_hour = max(hour_activity.items(), key=lambda x: sum(x[1]) / len(x[1]))[0]
        
        # Format as readable time
        if best_hour == 0:
            return "12:00 AM"
        elif best_hour < 12:
            return f"{best_hour}:00 AM"
        elif best_hour == 12:
            return "12:00 PM"
        else:
            return f"{best_hour - 12}:00 PM"
    
    def _calculate_xp_eta(self, guild_id: int, current_xp: int, hours: List[str]) -> str:
        """Estimate time until average user levels up"""
        if current_xp == 0 or len(hours) == 0:
            return "insufficient data"
        
        # Get average XP needed per level from leveling module
        avg_xp_per_level = 1000  # Default assumption
        
        # Calculate hourly XP rate
        xp_per_hour = current_xp / len(hours)
        
        if xp_per_hour == 0:
            return "no XP gain detected"
        
        # Estimate hours until next level for average user
        hours_to_level = avg_xp_per_level / xp_per_hour
        
        if hours_to_level < 24:
            return f"{int(hours_to_level)} hours"
        elif hours_to_level < 168:  # 1 week
            return f"{int(hours_to_level / 24)} days"
        else:
            return f"{int(hours_to_level / 168)} weeks"
    
    def _calculate_health_score(self, current_messages: int, current_chatters: int, 
                               trend_percentage: float, hourly_data: Dict, 
                               last_24_hours: List[str]) -> int:
        """Calculate overall server health score (0-100)"""
        score = 50  # Base score
        
        # Message activity component (0-25 points)
        if current_messages > 100:
            score += 25
        elif current_messages > 50:
            score += 20
        elif current_messages > 20:
            score += 15
        elif current_messages > 5:
            score += 10
        elif current_messages > 0:
            score += 5
        
        # Unique chatters component (0-25 points)
        if current_chatters > 20:
            score += 25
        elif current_chatters > 10:
            score += 20
        elif current_chatters > 5:
            score += 15
        elif current_chatters > 2:
            score += 10
        elif current_chatters > 0:
            score += 5
        
        # Trend component (-25 to +25 points)
        trend_bonus = max(-25, min(25, trend_percentage))
        score += trend_bonus
        
        # Consistency component (0-10 points)
        if len(last_24_hours) >= 24:
            message_counts = [hourly_data[h].get("message_count", 0) for h in last_24_hours]
            avg_messages = sum(message_counts) / len(message_counts)
            variance = sum((x - avg_messages) ** 2 for x in message_counts) / len(message_counts)
            
            # Lower variance = more consistent = higher score
            if variance < 100:
                score += 10
            elif variance < 500:
                score += 7
            elif variance < 1000:
                score += 4
        
        return max(0, min(100, int(score)))
    
    def _describe_current_activity(self, messages: int, chatters: int, trend: str) -> str:
        """Generate a human-readable description of current activity"""
        if messages == 0:
            return "The server is currently quiet with no recent messages"
        
        activity_level = "very active" if messages > 100 else "moderately active" if messages > 30 else "somewhat active"
        chatter_level = "with many participants" if chatters > 15 else "with a few active members" if chatters > 5 else "with limited participation"
        
        trend_desc = ""
        if trend == "rising":
            trend_desc = "Activity is increasing compared to yesterday"
        elif trend == "declining":
            trend_desc = "Activity is slightly lower than usual"
        else:
            trend_desc = "Activity is stable"
        
        return f"The server is {activity_level} {chatter_level}. {trend_desc}"
    
    def _generate_recommendations(self, trend: str, health_score: int, chatters: int) -> List[str]:
        """Generate actionable recommendations based on analytics"""
        recommendations = []
        
        if trend == "declining":
            recommendations.append("Consider hosting an event to boost engagement")
            recommendations.append("Try starting a discussion topic in general chat")
        
        if health_score < 40:
            recommendations.append("Server activity is low - consider promoting the server")
            recommendations.append("Add interactive commands or games to encourage participation")
        
        if chatters < 5:
            recommendations.append("Only a few members are active - try tagging inactive members")
            recommendations.append("Create voice channels to encourage real-time interaction")
        
        if health_score > 80:
            recommendations.append("Great momentum! Consider adding new features to maintain interest")
            recommendations.append("Perfect time to launch new initiatives or events")
        
        if not recommendations:
            recommendations.append("Server health looks good - keep doing what you're doing!")
        
        return recommendations[:3]  # Return top 3 recommendations
    
    async def get_hourly_stats(self, guild_id: int, hours: int = 24) -> Dict[str, Any]:
        """Get raw hourly statistics for the past N hours"""
        analytics_file = f"server_analytics_{guild_id}"
        analytics_data = dm.load_json(analytics_file, default={"hourly_data": {}})
        
        hourly_data = analytics_data.get("hourly_data", {})
        sorted_hours = sorted(hourly_data.keys(), reverse=True)[:hours]
        
        return {
            hour: hourly_data[hour] for hour in sorted_hours if hour in hourly_data
        }


# Global instance
analytics = None


def setup_analytics(bot):
    """Initialize the server analytics system"""
    global analytics
    analytics = ServerAnalytics(bot)
    return analytics


def get_analytics() -> Optional[ServerAnalytics]:
    """Get the analytics instance"""
    return analytics

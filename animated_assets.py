"""
Animated Assets Configuration
Contains all animated emojis, GIF URLs, and visual enhancements for the bot.
Users should replace placeholder IDs with their own custom emoji IDs.
"""

import discord
from typing import Dict, Optional

# Animated Emojis - Replace placeholder IDs with your own custom emoji IDs
# Format: <a:name:id> for animated emojis
ANIMATED_EMOJIS = {
    # Economy System
    "economy": "<a:spinning_coin:1234567890123456789>",  # Replace with your animated coin emoji
    "economy_fallback": "💰",
    
    # Leveling System  
    "leveling": "<a:level_up:1234567890123456789>",  # Replace with your animated level up emoji
    "leveling_fallback": "📈",
    
    # Verification System
    "verification": "<a:verified:1234567890123456789>",  # Replace with your animated verified emoji
    "verification_fallback": "🛡️",
    
    # Ticket System
    "tickets": "<a:ticket:1234567890123456789>",  # Replace with your animated ticket emoji
    "tickets_fallback": "🎫",
    
    # Welcome System
    "welcome": "<a:wave:1234567890123456789>",  # Replace with your animated wave emoji
    "welcome_fallback": "👋",
    
    # Staff System
    "staff": "<a:staff:1234567890123456789>",  # Replace with your animated staff emoji
    "staff_fallback": "🌟",
    
    # Moderation System
    "moderation": "<a:shield:1234567890123456789>",  # Replace with your animated shield emoji
    "moderation_fallback": "🤖",
    
    # Gamification System
    "gamification": "<a:quest:1234567890123456789>",  # Replace with your animated quest emoji
    "gamification_fallback": "🎮",
    
    # Giveaway System
    "giveaways": "<a:gift:1234567890123456789>",  # Replace with your animated gift emoji
    "giveaways_fallback": "🎁",
    
    # Events System
    "events": "<a:calendar:1234567890123456789>",  # Replace with your animated calendar emoji
    "events_fallback": "📅",
    
    # Tournament System
    "tournaments": "<a:trophy:1234567890123456789>",  # Replace with your animated trophy emoji
    "tournaments_fallback": "🏆",
    
    # Auto-Responder System
    "auto_responder": "<a:robot:1234567890123456789>",  # Replace with your animated robot emoji
    "auto_responder_fallback": "💬",
    
    # Reminder System
    "reminders": "<a:alarm:1234567890123456789>",  # Replace with your animated alarm emoji
    "reminders_fallback": "⏰",
    
    # Chat/AI System
    "chat": "<a:brain:1234567890123456789>",  # Replace with your animated brain emoji
    "chat_fallback": "🧠",
    
    # Starboard System
    "starboard": "<a:star:1234567890123456789>",  # Replace with your animated star emoji
    "starboard_fallback": "⭐",
    
    # Reaction Roles System
    "reaction_roles": "<a:role:1234567890123456789>",  # Replace with your animated role emoji
    "reaction_roles_fallback": "🎭",
    
    # Logging System
    "logging": "<a:scroll:1234567890123456789>",  # Replace with your animated scroll emoji
    "logging_fallback": "📝",
    
    # Applications System
    "applications": "<a:clipboard:1234567890123456789>",  # Replace with your animated clipboard emoji
    "applications_fallback": "📋",
    
    # Appeals System
    "appeals": "<a:scale:1234567890123456789>",  # Replace with your animated scale emoji
    "appeals_fallback": "⚖️",
    
    # Modmail System
    "modmail": "<a:envelope:1234567890123456789>",  # Replace with your animated envelope emoji
    "modmail_fallback": "📬",
    
    # Suggestions System
    "suggestions": "<a:lightbulb:1234567890123456789>",  # Replace with your animated lightbulb emoji
    "suggestions_fallback": "💡",
    
    # Community Health System
    "community_health": "<a:heart:1234567890123456789>",  # Replace with your animated heart emoji
    "community_health_fallback": "❤️",
    
    # Conflict Resolution System
    "conflict_resolution": "<a:handshake:1234567890123456789>",  # Replace with your animated handshake emoji
    "conflict_resolution_fallback": "🤝",
    
    # Server Analytics System
    "server_analytics": "<a:chart:1234567890123456789>",  # Replace with your animated chart emoji
    "server_analytics_fallback": "📊",
    
    # Intelligence System
    "intelligence": "<a:bulb:1234567890123456789>",  # Replace with your animated bulb emoji
    "intelligence_fallback": "🔍",
    
    # Guardian System
    "guardian": "<a:eye:1234567890123456789>",  # Replace with your animated eye emoji
    "guardian_fallback": "👁️",
    
    # Anti-Raid System
    "anti_raid": "<a:fort:1234567890123456789>",  # Replace with your animated fort emoji
    "anti_raid_fallback": "🛡️",
    
    # Auto Setup System
    "auto_setup": "<a:wrench:1234567890123456789>",  # Replace with your animated wrench emoji
    "auto_setup_fallback": "🔧",
    
    # Staff Promotion System
    "staff_promo": "<a:chart_up:1234567890123456789>",  # Replace with your animated chart up emoji
    "staff_promo_fallback": "📈",
    
    # Staff Shift System
    "staff_shift": "<a:clock:1234567890123456789>",  # Replace with your animated clock emoji
    "staff_shift_fallback": "🕒",
    
    # Staff Reviews System
    "staff_reviews": "<a:star_half:1234567890123456789>",  # Replace with your animated star half emoji
    "staff_reviews_fallback": "📝",
    
    # Auto Publisher System
    "auto_publisher": "<a:megaphone:1234567890123456789>",  # Replace with your animated megaphone emoji
    "auto_publisher_fallback": "📢",
    
    # Auto Announcer System
    "auto_announcer": "<a:speaker:1234567890123456789>",  # Replace with your animated speaker emoji
    "auto_announcer_fallback": "📢",
    
    # Content Generator System
    "content_generator": "<a:magic:1234567890123456789>",  # Replace with your animated magic emoji
    "content_generator_fallback": "✨",
    
    # Tournaments System (duplicate for emphasis)
    "tournament": "<a:trophy:1234567890123456789>",  # Replace with your animated trophy emoji
    "tournament_fallback": "🏆",
}

# Animated GIF Thumbnails for Config Panels
# Use small GIFs (<500KB) to avoid lag
PANEL_THUMBNAILS = {
    "verification": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Shield animation
    "economy": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Spinning coin
    "leveling": "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",  # Rising graph
    "tickets": "https://media.giphy.com/media/xT9IgG50Fb7Mi0obBC/giphy.gif",  # Ticket animation
    "welcome": "https://media.giphy.com/media/26ufdipQqU2lhNA4g/giphy.gif",  # Wave animation
    "staff": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Star animation
    "moderation": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Shield animation
    "gamification": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Game controller
    "giveaways": "https://media.giphy.com/media/xT9IgG50Fb7Mi0obBC/giphy.gif",  # Gift box
    "events": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Calendar flip
    "tournaments": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Trophy spin
    "auto_responder": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Robot
    "reminders": "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",  # Alarm clock
    "chat": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Brain animation
    "starboard": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Star sparkle
    "reaction_roles": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Theater masks
    "logging": "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",  # Scroll unfurling
    "applications": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Clipboard
    "appeals": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Scale balance
    "modmail": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Envelope opening
    "suggestions": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Lightbulb
    "community_health": "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",  # Heart pulse
    "conflict_resolution": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Handshake
    "server_analytics": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Chart rising
    "intelligence": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Lightbulb moment
    "guardian": "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",  # Eye scanning
    "anti_raid": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Fort/shield
    "auto_setup": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Wrench/tools
    "staff_promo": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Chart growth
    "staff_shift": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Clock hands
    "staff_reviews": "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",  # Star rating
    "auto_publisher": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Megaphone sound waves
    "auto_announcer": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Speaker sound
    "content_generator": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Magic sparkles
}

# Loading Animation GIFs (for actions taking >1 second)
LOADING_GIFS = {
    "default": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Generic loading spinner
    "economy": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Coin spin
    "leveling": "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",  # XP bar fill
    "giveaways": "https://media.giphy.com/media/xT9IgG50Fb7Mi0obBC/giphy.gif",  # Gift unwrapping
    "tournaments": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Trophy spin
    "events": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Calendar flip
}

# Success/Celebration GIFs
SUCCESS_GIFS = {
    "default": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Confetti
    "economy": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Coin rain
    "leveling": "https://media.giphy.com/media/3o7abKhOpu0NwenH3O/giphy.gif",  # Level up explosion
    "giveaways": "https://media.giphy.com/media/xT9IgG50Fb7Mi0obBC/giphy.gif",  # Prize confetti
    "daily": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Daily claim celebration
    "work": "https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif",  # Work success
    "purchase": "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",  # Purchase celebration
}

def get_animated_emoji(system_name: str) -> str:
    """
    Get animated emoji for a system, with fallback to static emoji
    
    Args:
        system_name: Name of the system (e.g., 'economy', 'leveling')
        
    Returns:
        Animated emoji string or fallback static emoji
    """
    emoji_data = ANIMATED_EMOJIS.get(system_name)
    if emoji_data:
        # If it's already a proper emoji format (<a:name:id> or static), return as-is
        if emoji_data.startswith('<a:') or len(emoji_data) == 1:
            return emoji_data
        # Otherwise treat as fallback
        return ANIMATED_EMOJIS.get(f"{system_name}_fallback", "✨")
    return "✨"  # Default fallback

def get_panel_thumbnail(system_name: str) -> Optional[str]:
    """
    Get thumbnail URL for a config panel
    
    Args:
        system_name: Name of the system
        
    Returns:
        URL string or None if not found
    """
    return PANEL_THUMBNAILS.get(system_name)

def get_loading_gif(system_name: str = "default") -> str:
    """
    Get loading GIF for a system
    
    Args:
        system_name: Name of the system (optional)
        
    Returns:
        URL string for loading GIF
    """
    return LOADING_GIFS.get(system_name, LOADING_GIFS["default"])

def get_success_gif(action_type: str = "default") -> str:
    """
    Get success GIF for an action type
    
    Args:
        action_type: Type of action (e.g., 'economy', 'leveling', 'daily')
        
    Returns:
        URL string for success GIF
    """
    return SUCCESS_GIFS.get(action_type, SUCCESS_GIFS["default"])

def is_animated_emoji_available(emoji_str: str) -> bool:
    """
    Check if an emoji string represents an available animated emoji
    
    Args:
        emoji_str: Emoji string to check
        
    Returns:
        True if it's a valid animated emoji format, False otherwise
    """
    return emoji_str.startswith('<a:') and emoji_str.endswith('>')

def create_animated_embed_title(system_name: str, title: str) -> str:
    """
    Create an embed title with animated emoji
    
    Args:
        system_name: Name of the system
        title: Base title text
        
    Returns:
        Formatted title with animated emoji
    """
    emoji = get_animated_emoji(system_name)
    return f"{emoji} {title}"

def create_success_embed(description: str, action_type: str = "default") -> discord.Embed:
    """
    Create a success embed with celebratory elements
    
    Args:
        description: Embed description text
        action_type: Type of action for theming
        
    Returns:
        Configured discord.Embed object
    """
    embed = discord.Embed(
        description=description,
        color=discord.Color.green()
    )
    # Set thumbnail to success GIF
    success_gif = get_success_gif(action_type)
    embed.set_thumbnail(url=success_gif)
    return embed

def create_loading_embed(title: str, description: str = "Please wait...", system_name: str = "default") -> discord.Embed:
    """
    Create a loading embed with animated thumbnail
    
    Args:
        title: Embed title
        description: Embed description text
        system_name: Name of the system for theming
        
    Returns:
        Configured discord.Embed object
    """
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blue()
    )
    # Set thumbnail to loading GIF
    loading_gif = get_loading_gif(system_name)
    embed.set_thumbnail(url=loading_gif)
    return embed
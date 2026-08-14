import os
import aiohttp
import random
from typing import Optional

class GIFManager:
    """
    Random GIF integration for welcome/goodbye/level-up.
    Supports Tenor, GIPHY, and Built-in Pools.
    """
    BUILTIN_GIRLS = {
        "welcome": ["https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif", "https://media.giphy.com/media/3o7TKsR8XnIBjCe95e/giphy.gif", "https://media.giphy.com/media/l0HlR1vaIXXHbWZHy/giphy.gif"],
        "level_up": ["https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif", "https://media.giphy.com/media/3o6Zt6ML6Bsls GugW/giphy.gif", "https://media.giphy.com/media/xT9IgzoKnwFNmISR8I/giphy.gif"],
        "success": ["https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif", "https://media.giphy.com/media/3o6Zt481isNVuQI1l6/giphy.gif", "https://media.giphy.com/media/xT9IgG50Fb7Mi0prBC/giphy.gif"]
    }

    async def get_random_gif(self, search_term: str) -> str:
        tenor_key = os.getenv("TENOR_API_KEY")
        if tenor_key:
            async with aiohttp.ClientSession() as session:
                url = f"https://tenor.googleapis.com/v2/search?q={search_term}&key={tenor_key}&limit=10"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("results"):
                            return random.choice(data["results"])["media_formats"]["gif"]["url"]
        
        # Fallback to local pool
        pool = self.BUILTIN_GIRLS.get(search_term, self.BUILTIN_GIRLS["success"])
        return random.choice(pool)

# Global GIF Manager
gif_manager = GIFManager()

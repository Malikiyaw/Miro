"""Reliable Render entrypoint for Miro.

Keeps Render's Web Service healthy while Discord authentication is temporarily
rate-limited, and creates a completely fresh MiroBot/session after failures.
This avoids reusing discord.py's closed aiohttp session after a failed login.
"""

import asyncio
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import aiohttp
import discord

from bot import MiroBot
from logger import logger


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


def start_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Render health server listening on port %s", port)


def build_bot() -> MiroBot:
    proxy = os.getenv("DISCORD_PROXY", "").strip() or None
    proxy_auth = None

    if proxy:
        parts = urlsplit(proxy)
        if parts.username:
            proxy_auth = aiohttp.BasicAuth(
                parts.username,
                parts.password or "",
            )

    bot = MiroBot(proxy=proxy, proxy_auth=proxy_auth)
    try:
        bot.http.user_agent = (
            "MiroBot/2.0 DiscordBot "
            "(https://github.com/Malikiyaw/Miro)"
        )
    except Exception:
        pass
    return bot


async def run_forever() -> None:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not configured")

    start_health_server()
    attempt = 0

    while True:
        bot = build_bot()
        try:
            logger.info("Starting Discord connection (attempt %d)", attempt + 1)
            await bot.start(token, reconnect=True)
            logger.info("Discord bot stopped cleanly")
            return

        except discord.LoginFailure:
            logger.exception("Discord rejected DISCORD_TOKEN. Check the Render secret.")
            raise

        except discord.PrivilegedIntentsRequired:
            logger.exception(
                "Discord requires privileged intents. Enable SERVER MEMBERS, "
                "PRESENCE, and MESSAGE CONTENT intents in the Discord Developer Portal."
            )
            raise

        except discord.HTTPException as exc:
            status = getattr(exc, "status", 0)
            if status == 429:
                # Discord/Cloudflare may temporarily rate-limit Render's outbound IP.
                # Do not restart the Render process or hammer the endpoint.
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is None:
                    retry_after = min(60 * (2 ** min(attempt, 5)), 900)
                wait = float(retry_after) + random.uniform(2, 10)
                logger.warning(
                    "Discord HTTP 429 during login. Waiting %.0fs before creating "
                    "a fresh session. If this persists, configure DISCORD_PROXY "
                    "or move the worker to a host/network with a different outbound IP.",
                    wait,
                )
                attempt += 1
                await asyncio.sleep(wait)
                continue

            if 500 <= status < 600:
                wait = min(15 * (2 ** min(attempt, 6)), 600) + random.uniform(0, 10)
                logger.warning(
                    "Discord server error %s. Retrying in %.0fs with a fresh session.",
                    status,
                    wait,
                )
                attempt += 1
                await asyncio.sleep(wait)
                continue

            logger.exception("Fatal Discord HTTP error %s", status)
            raise

        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            wait = min(15 * (2 ** min(attempt, 6)), 600) + random.uniform(0, 10)
            logger.warning(
                "Network error while connecting to Discord: %s. Retrying in %.0fs.",
                exc,
                wait,
            )
            attempt += 1
            await asyncio.sleep(wait)

        except Exception:
            wait = min(30 * (2 ** min(attempt, 6)), 900) + random.uniform(0, 15)
            logger.exception(
                "Unexpected Discord startup error. Retrying in %.0fs with a fresh bot.",
                wait,
            )
            attempt += 1
            await asyncio.sleep(wait)

        finally:
            # Never reuse a client whose aiohttp session may have been closed by
            # discord.py after a failed login/connect attempt.
            try:
                await bot.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(run_forever())

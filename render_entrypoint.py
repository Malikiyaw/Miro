"""Render entrypoint for Miro.

Keeps the Render health endpoint alive while Discord authentication is
rate-limited, and always creates a fresh discord.py client after a failed login.
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


_state = {
    "status": "starting",
    "attempt": 0,
    "last_error": "",
    "next_retry": 0.0,
}
_state_lock = threading.Lock()


def set_state(**values):
    with _state_lock:
        _state.update(values)


def snapshot_state():
    with _state_lock:
        return dict(_state)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        state = snapshot_state()
        if self.path.rstrip("/") == "/status":
            body = (
                f"status={state['status']}\n"
                f"attempt={state['attempt']}\n"
                f"retry_in={int(max(0, state['next_retry'] - time.time()))}\n"
                f"last_error={state['last_error'][:300]}\n"
            ).encode("utf-8", "replace")
        else:
            body = b"ok\n"

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Render health server listening on port %s", port)


def build_bot():
    # Prefer the Miro-specific proxy. Also accept standard proxy variables so
    # Render can provide an outbound proxy without changing the application.
    proxy = (
        os.getenv("DISCORD_PROXY", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
        or None
    )
    proxy_auth = None

    if proxy:
        parts = urlsplit(proxy)
        if parts.username:
            proxy_auth = aiohttp.BasicAuth(parts.username, parts.password or "")
        logger.info("Discord outbound proxy configured")

    bot = MiroBot(proxy=proxy, proxy_auth=proxy_auth)
    try:
        bot.http.user_agent = (
            "MiroBot/2.0 DiscordBot "
            "(https://github.com/Malikiyaw/Miro)"
        )
    except Exception:
        pass
    return bot


def get_429_details(exc):
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}

    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None:
        raw = headers.get("Retry-After")
        try:
            retry_after = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            retry_after = None

    is_global = str(headers.get("X-RateLimit-Global", "")).lower() == "true"
    cf_ray = headers.get("CF-Ray", "")
    return retry_after, is_global, cf_ray


async def run_forever():
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not configured")

    start_health_server()
    attempt = 0
    consecutive_429 = 0

    while True:
        bot = build_bot()
        set_state(status="connecting", attempt=attempt + 1, last_error="")

        try:
            logger.info("Starting Discord connection (attempt %d)", attempt + 1)
            await bot.start(token, reconnect=True)
            logger.info("Discord bot stopped cleanly")
            set_state(status="stopped")
            return

        except discord.LoginFailure:
            set_state(status="fatal", last_error="Discord rejected DISCORD_TOKEN")
            logger.exception("Discord rejected DISCORD_TOKEN. Check the Render secret.")
            raise

        except discord.PrivilegedIntentsRequired:
            set_state(status="fatal", last_error="Privileged Discord intents required")
            logger.exception(
                "Discord requires privileged intents. Enable SERVER MEMBERS, "
                "PRESENCE, and MESSAGE CONTENT intents in the Discord Developer Portal."
            )
            raise

        except discord.HTTPException as exc:
            status = getattr(exc, "status", 0)

            if status == 429:
                consecutive_429 += 1
                retry_after, is_global, cf_ray = get_429_details(exc)

                # Discord's Retry-After is authoritative when available. If it
                # is absent, use progressively longer waits instead of repeatedly
                # hitting the same shared Render outbound IP.
                if retry_after is None:
                    retry_after = min(
                        60 * (2 ** min(consecutive_429 - 1, 6)),
                        3600,
                    )

                wait = max(1.0, float(retry_after)) + random.uniform(2, 10)
                scope = "global" if is_global else "API"
                cf_info = f" CF-Ray={cf_ray}" if cf_ray else ""

                logger.warning(
                    "Discord HTTP 429 (%s) during login: Retry-After=%ss, "
                    "consecutive=%d, waiting %.0fs.%s",
                    scope,
                    retry_after,
                    consecutive_429,
                    wait,
                    cf_info,
                )
                logger.warning(
                    "Miro's Render health server is working, but Discord is "
                    "rejecting the HTTP login. This is an API/outbound-IP "
                    "rate-limit, not a bot-command crash. Do not repeatedly redeploy."
                )

                set_state(
                    status="rate_limited",
                    attempt=attempt + 1,
                    last_error=f"Discord HTTP 429 ({scope})",
                    next_retry=time.time() + wait,
                )
                attempt += 1
                await asyncio.sleep(wait)
                continue

            if 500 <= status < 600:
                consecutive_429 = 0
                wait = min(15 * (2 ** min(attempt, 6)), 600) + random.uniform(0, 10)
                logger.warning(
                    "Discord server error %s. Retrying in %.0fs with a fresh session.",
                    status,
                    wait,
                )
                set_state(
                    status="discord_server_error",
                    attempt=attempt + 1,
                    last_error=f"Discord HTTP {status}",
                    next_retry=time.time() + wait,
                )
                attempt += 1
                await asyncio.sleep(wait)
                continue

            set_state(status="fatal", last_error=f"Discord HTTP {status}")
            logger.exception("Fatal Discord HTTP error %s", status)
            raise

        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            consecutive_429 = 0
            wait = min(15 * (2 ** min(attempt, 6)), 600) + random.uniform(0, 10)
            logger.warning(
                "Network error while connecting to Discord: %s. Retrying in %.0fs.",
                exc,
                wait,
            )
            set_state(
                status="network_error",
                attempt=attempt + 1,
                last_error=str(exc)[:300],
                next_retry=time.time() + wait,
            )
            attempt += 1
            await asyncio.sleep(wait)

        except Exception as exc:
            consecutive_429 = 0
            wait = min(30 * (2 ** min(attempt, 6)), 900) + random.uniform(0, 15)
            logger.exception(
                "Unexpected Discord startup error. Retrying in %.0fs with a fresh bot.",
                wait,
            )
            set_state(
                status="startup_error",
                attempt=attempt + 1,
                last_error=str(exc)[:300],
                next_retry=time.time() + wait,
            )
            attempt += 1
            await asyncio.sleep(wait)

        finally:
            try:
                await bot.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(run_forever())

"""
Supabase persistence for per-guild immortal data (free tier, no card).

Render Free wipes ./data on every Manual Deploy. This module makes
Supabase Postgres (500MB free) the source of truth:

  - local ./data/guild_*.json is a cache
  - every dm.save_json -> async push to Supabase (debounced, non-blocking)
  - boot: if ./data empty but Supabase has rows -> pull restores
  - corruption/missing file -> pull single guild from Supabase

Table (run once in Supabase SQL Editor):
  create table if not exists guild_data (
    guild_id text primary key,
    data jsonb not null,
    updated_at timestamptz default now()
  );
  -- allow service_role full access; or disable RLS for simplicity:
  -- alter table guild_data disable row level security;

Env (Render -> Environment):
  SUPABASE_URL=https://<ref>.supabase.co
  SUPABASE_SERVICE_KEY=<service_role key>  # or SUPABASE_KEY / SUPABASE_ANON_KEY
  SUPABASE_TABLE=guild_data                # optional
  SYNC_INTERVAL_SEC=300                    # optional

No extra dependency beyond aiohttp (already in requirements). Uses Supabase
PostgREST: POST /rest/v1/guild_data with Prefer: resolution=merge-duplicates
"""
import os
import json
import time
import asyncio
import glob
from typing import Dict, Any, Optional

from logger import logger

try:
    import aiohttp  # already required
except Exception:  # pragma: no cover
    aiohttp = None

_TABLE = os.getenv("SUPABASE_TABLE", "guild_data")
_DEBOUNCE_SEC = 30

# in-memory debounce: guild_id -> last push ts
_last_push: Dict[str, float] = {}


def _normalize_url(url: str) -> str:
    """Supabase UI shows https://<ref>.supabase.co/rest/v1/ ; we need bare https://<ref>.supabase.co"""
    u = url.strip().rstrip("/")
    # strip trailing /rest/v1 if user pasted Data API URL
    if u.endswith("/rest/v1"):
        u = u[: -len("/rest/v1")].rstrip("/")
    return u

def _cfg():
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    url = _normalize_url(url) if url else url
    key = (os.getenv("SUPABASE_SERVICE_KEY", "")
           or os.getenv("SUPABASE_KEY", "")
           or os.getenv("SUPABASE_ANON_KEY", "")).strip()
    # also support DATABASE_URL naming confusion – if user set SUPABASE_URL incorrectly
    if not url and os.getenv("DATABASE_URL", "").startswith("http"):
        url = _normalize_url(os.getenv("DATABASE_URL").strip().rstrip("/"))
    return url, key


def is_configured() -> bool:
    url, key = _cfg()
    return bool(url and key and aiohttp is not None)


def _headers(key: str) -> Dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


async def push_one(guild_id: int, data: Dict[str, Any]) -> bool:
    """Upsert one guild's JSON to Supabase. Debounced 30s per guild, non-fatal."""
    if not is_configured():
        return False
    gid = str(guild_id)
    now = time.time()
    if now - _last_push.get(gid, 0) < _DEBOUNCE_SEC:
        return False
    _last_push[gid] = now

    url, key = _cfg()
    endpoint = f"{url}/rest/v1/{_TABLE}"
    payload = [{"guild_id": gid, "data": data, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]
    headers = _headers(key)
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    try:
        timeout = aiohttp.ClientTimeout(total=12, connect=6)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(endpoint, headers=headers, data=json.dumps(payload)) as resp:
                if resp.status in (200, 201, 204):
                    logger.debug(f"Supabase push guild {gid} ok ({resp.status})")
                    return True
                body = await resp.text()
                logger.warning(f"Supabase push guild {gid} failed {resp.status}: {body[:400]}")
                return False
    except Exception as e:
        logger.warning(f"Supabase push guild {gid} error: {e}")
        return False


async def pull_one(guild_id: int) -> Optional[Dict[str, Any]]:
    """Fetch one guild's JSON from Supabase, or None."""
    if not is_configured():
        return None
    url, key = _cfg()
    endpoint = f"{url}/rest/v1/{_TABLE}?select=data&guild_id=eq.{guild_id}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    try:
        timeout = aiohttp.ClientTimeout(total=12, connect=6)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(endpoint, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Supabase pull guild {guild_id} {resp.status}")
                    return None
                rows = await resp.json()
                if rows and isinstance(rows, list) and rows[0].get("data") is not None:
                    return rows[0]["data"]
                return None
    except Exception as e:
        logger.warning(f"Supabase pull guild {guild_id} error: {e}")
        return None


async def pull_all(data_dir: str = "data") -> int:
    """Pull every guild row from Supabase into ./data/guild_*.json. Returns count."""
    if not is_configured():
        return 0
    url, key = _cfg()
    endpoint = f"{url}/rest/v1/{_TABLE}?select=guild_id,data"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    try:
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(endpoint, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"Supabase pull_all {resp.status}: {body[:400]}")
                    return 0
                rows = await resp.json()
        if not isinstance(rows, list):
            return 0
        os.makedirs(data_dir, exist_ok=True)
        count = 0
        for row in rows:
            gid = row.get("guild_id")
            data = row.get("data")
            if gid is None or data is None:
                continue
            # data is jsonb -> already dict, but may be string
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    continue
            path = os.path.join(data_dir, f"guild_{gid}.json")
            try:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp, path)
                count += 1
            except Exception as e:
                logger.warning(f"Supabase write local guild_{gid}.json failed: {e}")
        if count:
            logger.info(f"Supabase pull_all restored {count} guild(s) into {data_dir}")
        return count
    except Exception as e:
        logger.warning(f"Supabase pull_all error: {e}")
        return 0


async def push_all(data_dir: str = "data") -> int:
    """Push every local guild_*.json to Supabase (migration). Returns pushed count."""
    if not is_configured():
        return 0
    paths = glob.glob(os.path.join(data_dir, "guild_*.json"))
    if not paths:
        return 0
    pushed = 0
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            # guild_id from filename
            base = os.path.basename(p)
            gid = base.replace("guild_", "").replace(".json", "")
            # avoid debounce for bulk migration – clear entry
            _last_push.pop(gid, None)
            ok = await push_one(int(gid) if gid.isdigit() else gid, data)
            if ok:
                pushed += 1
            await asyncio.sleep(0.15)  # gentle on PostgREST
        except Exception as e:
            logger.warning(f"Supabase push_all {p} failed: {e}")
    if pushed:
        logger.info(f"Supabase push_all migrated {pushed}/{len(paths)} guild(s)")
    return pushed


def pull_if_empty_sync(data_dir: str = "data") -> int:
    """Blocking helper for sync contexts (setup_hook) – runs pull_all if local empty."""
    local = glob.glob(os.path.join(data_dir, "guild_*.json"))
    if local:
        return 0
    # we are in sync context, run async pull via new loop or to_thread
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # cannot run directly – caller should use await pull_all()
        return 0
    try:
        return asyncio.run(pull_all(data_dir))
    except Exception as e:
        logger.warning(f"pull_if_empty_sync failed: {e}")
        return 0


# fire-and-forget helper used by data_manager after save_json
def schedule_push(guild_id: int, data: Dict[str, Any]):
    """Schedule debounced push without blocking. Safe to call from sync code."""
    if not is_configured():
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(push_one(guild_id, data))
    except RuntimeError:
        # no running loop (e.g., during import) – push on next heartbeat
        pass

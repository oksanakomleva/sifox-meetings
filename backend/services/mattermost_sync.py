"""Mattermost ingestion.

Pulls posts from the channels the bot/token is a member of and stores them in
`mm_messages`. Incremental per channel via `sync_state['mattermost:{channel_id}']`
(cursor = last post create_at in ms — Mattermost timestamps are milliseconds).

No-op if MM_TOKEN / MM_SERVER_URL are not configured.
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from config import config
from database import models

logger = logging.getLogger(__name__)

_PER_PAGE = 200
_BOOTSTRAP_MAX_PAGES = 5  # first run: cap history (~1000 newest posts/channel)


def _ts_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


async def _get(client: httpx.AsyncClient, path: str, **params):
    r = await client.get(path, params=params or None)
    r.raise_for_status()
    return r.json()


async def _bot_channels(client: httpx.AsyncClient) -> list[dict]:
    """Channels the token's user is a member of, across all their teams."""
    channels: dict[str, dict] = {}
    teams = await _get(client, "/api/v4/users/me/teams")
    for team in teams:
        try:
            chans = await _get(client, f"/api/v4/users/me/teams/{team['id']}/channels")
        except Exception as e:
            logger.warning("MM: failed to list channels for team %s: %s", team.get("id"), e)
            continue
        for c in chans:
            channels[c["id"]] = c
    return list(channels.values())


async def _resolve_usernames(client: httpx.AsyncClient, user_ids: set[str], cache: dict) -> None:
    missing = [uid for uid in user_ids if uid and uid not in cache]
    for i in range(0, len(missing), 100):
        batch = missing[i:i + 100]
        try:
            r = await client.post("/api/v4/users/ids", json=batch)
            r.raise_for_status()
            for u in r.json():
                cache[u["id"]] = u.get("username")
        except Exception as e:
            logger.warning("MM: failed to resolve usernames: %s", e)
            for uid in batch:
                cache.setdefault(uid, None)


async def _fetch_posts(client: httpx.AsyncClient, channel_id: str, since: int | None) -> list[dict]:
    posts: list[dict] = []
    if since:
        # `since` returns posts created/updated after the timestamp (ms).
        data = await _get(client, f"/api/v4/channels/{channel_id}/posts", since=since, per_page=_PER_PAGE)
        posts.extend(data.get("posts", {}).values())
    else:
        # First run — page through the newest history, bounded.
        for page in range(_BOOTSTRAP_MAX_PAGES):
            data = await _get(client, f"/api/v4/channels/{channel_id}/posts", page=page, per_page=_PER_PAGE)
            order = data.get("order", [])
            ps = data.get("posts", {})
            posts.extend(ps[pid] for pid in order if pid in ps)
            if len(order) < _PER_PAGE:
                break
    return posts


async def _sync_channel(client: httpx.AsyncClient, channel: dict, uname_cache: dict) -> int:
    channel_id = channel["id"]
    channel_name = channel.get("display_name") or channel.get("name")
    source = f"mattermost:{channel_id}"
    state = await models.get_sync_state(source)
    since = int(state["last_cursor"]) if state and state.get("last_cursor") else None

    raw = await _fetch_posts(client, channel_id, since)
    # Keep regular user messages only (skip system join/leave/etc. and empty).
    msgs = [p for p in raw if not p.get("type") and (p.get("message") or "").strip()]
    if not msgs:
        await models.upsert_sync_state(source, datetime.now(timezone.utc),
                                       str(since) if since else None)
        return 0

    await _resolve_usernames(client, {p.get("user_id") for p in msgs}, uname_cache)

    rows = [{
        "id": p["id"],
        "channel_id": channel_id,
        "channel_name": channel_name,
        "user_id": p.get("user_id"),
        "username": uname_cache.get(p.get("user_id")),
        "message": p["message"],
        "created_at": _ts_to_dt(p["create_at"]),
    } for p in msgs]

    await models.insert_mm_messages(rows)
    max_ms = max(p["create_at"] for p in msgs)
    new_cursor = str(max(max_ms, since)) if since else str(max_ms)
    await models.upsert_sync_state(source, datetime.now(timezone.utc), new_cursor)
    return len(rows)


async def sync_mattermost() -> int:
    """Sync all channels the token can see. Returns number of posts ingested."""
    if not (config.MM_TOKEN and config.MM_SERVER_URL):
        return 0
    base = config.MM_SERVER_URL.rstrip("/")
    total = 0
    uname_cache: dict[str, str | None] = {}
    async with httpx.AsyncClient(
        base_url=base,
        headers={"Authorization": f"Bearer {config.MM_TOKEN}"},
        timeout=30.0,
    ) as client:
        try:
            channels = await _bot_channels(client)
        except Exception as e:
            logger.error("MM: failed to list channels: %s", e)
            return 0
        for ch in channels:
            try:
                total += await _sync_channel(client, ch, uname_cache)
            except Exception as e:
                logger.error("MM: channel %s sync failed: %s", ch.get("id"), e)
    logger.info("MM: ingested %d posts from %d channels", total, len(channels))
    return total


async def run_mm_sync_loop() -> None:
    interval = max(60, config.MM_SYNC_MINUTES * 60)
    while True:
        try:
            await sync_mattermost()
        except Exception as e:
            logger.error("MM sync loop error: %s", e)
        await asyncio.sleep(interval)

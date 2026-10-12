"""Accept recorder links from verified users' private Mattermost messages."""
import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from config import config
from database import models
from services.manual_meetings import (
    MeetingAlreadyInProgress, RecorderUnavailable, launch_manual_meeting,
    normalize_meeting_url,
)

logger = logging.getLogger(__name__)
_LINK_RE = re.compile(r"^(?:протоколлер[,!]?\s+)?привязать\s+([A-Za-z0-9_-]{12,64})$", re.I)
_REQUEST_RE = re.compile(r"^(?:протоколлер[,!]?\s+запиши\s+)?(https://\S+)(?:\s+(.+))?$", re.I | re.S)


def parse_command(text: str) -> tuple[str, str, str | None] | None:
    """Return (action, value, title) for a plain link or explicit command."""
    clean = (text or "").strip()
    if len(clean) > 2000:
        return None
    link = _LINK_RE.fullmatch(clean)
    if link:
        return "link", link.group(1), None
    request = _REQUEST_RE.fullmatch(clean)
    if not request:
        return None
    url = request.group(1).rstrip(".,;!")
    try:
        url = normalize_meeting_url(url)
    except ValueError:
        return "invalid_url", url, None
    return "record", url, request.group(2)


async def _reply(client: httpx.AsyncClient, channel_id: str, message: str) -> None:
    response = await client.post(
        "/api/v4/posts", json={"channel_id": channel_id, "message": message},
    )
    response.raise_for_status()


async def _handle_command(
    client: httpx.AsyncClient, channel_id: str, sender_id: str,
    command: tuple[str, str, str | None],
) -> None:
    action, value, title = command
    if action == "link":
        good = await models.claim_mm_link_code(hashlib.sha256(value.encode()).hexdigest(), sender_id)
        await _reply(client, channel_id, (
            "Готово: ваш Mattermost привязан к Sifox. Теперь пришлите мне ссылку Телемоста, и я начну запись."
            if good else "Код не подошёл, истёк или аккаунт уже привязан. Получите новый код в разделе «Встречи» Sifox."
        ))
        return
    if action == "invalid_url":
        await _reply(client, channel_id, "Нужна ссылка на встречу Телемоста или Zoom")
        return
    user = await models.get_mm_recorder_user(sender_id)
    if not user:
        await _reply(client, channel_id, "Сначала привяжите Mattermost в разделе «Встречи» Sifox: получите код и пришлите мне «привязать КОД».")
        return
    if not user["is_active"]:
        await _reply(client, channel_id, "Ваша учётная запись Sifox отключена. Запись не запущена.")
        return
    try:
        meeting, existing = await launch_manual_meeting(value, title, user["id"])
    except MeetingAlreadyInProgress:
        await _reply(client, channel_id, "Эту встречу уже записывает протоколлер. Вторую запись не запускаю.")
        return
    except RecorderUnavailable:
        await _reply(client, channel_id, "Протоколлер перезапускается. Попробуйте ещё раз немного позже.")
        return
    if existing and meeting["status"] in ("done", "transcribing", "analyzing"):
        status = "Эта ссылка недавно уже записывалась; повторно не подключаюсь"
    else:
        status = "Запись этой встречи уже запущена" if existing else "Подключаюсь к встрече и начинаю запись"
    await _reply(client, channel_id,
                 f"{status}: {config.BASE_URL.rstrip('/')}/meetings/{meeting['id']}")


async def _poll_direct_channel(
    client: httpx.AsyncClient, channel: dict, bot_id: str, bootstrap_ms: int,
) -> None:
    channel_id = channel["id"]
    source = f"mm_recorder_commands:{channel_id}"
    state = await models.get_sync_state(source)
    if not state or not state.get("last_cursor"):
        # Existing old DMs are skipped, while the first message in a newly
        # created DM after activation is still processed.
        cursor = bootstrap_ms
        await models.upsert_sync_state(source, datetime.now(timezone.utc), str(cursor))
    else:
        cursor = int(state["last_cursor"])
    last_post_at = channel.get("last_post_at")
    if last_post_at is not None and int(last_post_at) < cursor:
        return
    response = await client.get(
        f"/api/v4/channels/{channel_id}/posts",
        params={"since": max(0, cursor - 1), "per_page": 200},
    )
    response.raise_for_status()
    posts = sorted(response.json().get("posts", {}).values(),
                   key=lambda post: (post.get("create_at", 0), post.get("id", "")))
    for post in posts:
        created = int(post.get("create_at", 0))
        if created < cursor:
            continue
        if post.get("user_id") != bot_id and not post.get("type"):
            command = parse_command(post.get("message", ""))
            if command and not await models.is_mm_recorder_post_processed(post["id"]):
                # A delayed poll or restarted service must not join a room
                # hours after somebody pasted a link.
                if command[0] == "record" and int(time.time() * 1000) - created > 5 * 60 * 1000:
                    await _reply(client, channel_id,
                                 "Эта ссылка пришла больше пяти минут назад. Если встреча ещё идёт, пришлите её снова.")
                else:
                    await _handle_command(client, channel_id, post["user_id"], command)
                await models.mark_mm_recorder_post_processed(post["id"])
        if created > cursor:
            cursor = created
            await models.upsert_sync_state(source, datetime.now(timezone.utc), str(cursor))


async def poll_recorder_messages() -> None:
    token = config.MM_RECORDER_TOKEN or config.MM_TOKEN
    bootstrap = await models.get_sync_state("mm_recorder_commands:bootstrap")
    if not bootstrap or not bootstrap.get("last_cursor"):
        bootstrap_ms = int(time.time() * 1000)
        await models.upsert_sync_state(
            "mm_recorder_commands:bootstrap", datetime.now(timezone.utc), str(bootstrap_ms),
        )
    else:
        bootstrap_ms = int(bootstrap["last_cursor"])
    async with httpx.AsyncClient(
        base_url=config.MM_SERVER_URL.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"}, timeout=10.0,
    ) as client:
        me = await client.get("/api/v4/users/me")
        me.raise_for_status()
        bot_id = me.json()["id"]
        response = await client.get("/api/v4/users/me/channels")
        response.raise_for_status()
        for channel in response.json():
            if channel.get("type") != "D":
                continue
            try:
                await _poll_direct_channel(client, channel, bot_id, bootstrap_ms)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Mattermost recorder DM poll failed (%s)", type(exc).__name__)


async def run_mm_recorder_loop() -> None:
    if not config.MM_RECORDER_COMMANDS_ENABLED:
        return
    if not config.MM_SERVER_URL or not (config.MM_RECORDER_TOKEN or config.MM_TOKEN):
        logger.error("Mattermost recorder commands enabled without server/token")
        return
    while True:
        try:
            await poll_recorder_messages()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Mattermost recorder poll failed (%s)", type(exc).__name__)
        await asyncio.sleep(15)

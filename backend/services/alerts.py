"""Non-blocking incident alerts; failures never interrupt meeting recording."""
import asyncio
import logging
import re

from config import config
from database import models
from services import monitor_state
from services.alert_rules import Incident, detect_incidents
from services.mattermost_alerts import send_direct_message

logger = logging.getLogger(__name__)

_KINDS = {
    "recording_scheduler_stalled": "Планировщик записей перестал отвечать",
    "calendar_sync_stalled": "Обновление календарей не выполняется",
    "meeting_error": "Встреча завершилась с ошибкой",
    "pending_overdue": "Протоколлер не начал запись по расписанию",
    "joining_stalled": "Протоколлер слишком долго подключается",
    "recording_process_missing": "Запись отмечена активной, но процесс записи отсутствует",
    "transcribing_stalled": "Расшифровка встречи слишком долго не завершается",
    "analyzing_stalled": "Обработка встречи слишком долго не завершается",
    "page_crashed": "Протоколлер неожиданно вышел из встречи",
    "left_early": "Протоколлер вышел из встречи раньше времени",
}


def _safe_title(value: str | None) -> str:
    # Calendar titles are untrusted: prevent mentions and markdown/link injection.
    title = re.sub(r"[\r\n\t]+", " ", value or "Без названия")[:150]
    title = title.replace("@", "＠")
    return re.sub(r"([\\`*_~\[\]<>])", r"\\\1", title)


def format_incident(incident: Incident, meeting: dict | None, base_url: str) -> str:
    message = f"⚠️ Sifox: {_KINDS[incident.kind]}"
    if meeting and incident.meeting_id:
        message += f"\nВстреча: {_safe_title(meeting.get('title'))}"
        message += f"\nСсылка: {base_url.rstrip('/')}/meetings/{incident.meeting_id}"
    return message


async def check_and_send_alerts() -> None:
    """Detect and durably queue incidents, then attempt private delivery."""
    from services import recorder

    meetings = await models.get_monitor_meetings()
    by_id = {str(row["id"]): row for row in meetings}
    incidents = detect_incidents(
        meetings,
        set(recorder._active),
        monitor_state.ages(),
        monitor_state.stamps(),
        shutdown_requested=recorder._shutdown_requested,
    )
    for incident in incidents:
        await models.enqueue_monitor_alert(
            incident.key,
            format_incident(incident, by_id.get(incident.meeting_id), config.BASE_URL),
        )
    for pending in await models.get_unsent_monitor_alerts():
        try:
            await send_direct_message(
                config.MM_SERVER_URL, config.ALERT_MM_TOKEN or config.MM_TOKEN,
                config.ALERT_MM_USERNAME, pending["message"],
            )
        except Exception as exc:
            # Do not log response bodies, URLs, or tokens from the MM client.
            logger.warning("Alert delivery failed (%s); will retry", type(exc).__name__)
            return
        await models.mark_monitor_alert_sent(pending["alert_key"])


async def run_alert_loop() -> None:
    if not config.ALERTS_ENABLED:
        return
    if not all((config.MM_SERVER_URL, config.ALERT_MM_TOKEN or config.MM_TOKEN, config.ALERT_MM_USERNAME)):
        logger.error("Alerts enabled but Mattermost destination is incomplete")
        return
    while True:
        try:
            await check_and_send_alerts()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Alert check failed (%s)", type(exc).__name__)
        await asyncio.sleep(60)

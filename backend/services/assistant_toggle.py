"""Pure validation policy for the admin live-assistant meeting toggle."""


class AssistantToggleError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate_assistant_toggle(
    meeting: dict | None,
    enabled: bool,
    *,
    live_assistant_enabled: bool,
    live_assistant_speak: bool,
    live_assistant_all_meetings: bool,
) -> None:
    """Reject toggle states that cannot be applied safely or truthfully."""
    if not meeting:
        raise AssistantToggleError(404, "Встреча не найдена")
    if meeting.get("status") != "pending":
        raise AssistantToggleError(
            409,
            "Ассистента можно настроить только до входа Протоколлера во встречу",
        )
    if live_assistant_all_meetings:
        raise AssistantToggleError(
            409,
            "Сейчас ассистент глобально включён для всех встреч; "
            "индивидуальный переключатель не применяется",
        )
    if enabled and not live_assistant_enabled:
        raise AssistantToggleError(
            409,
            "Живой ассистент выключен в настройках сервиса",
        )
    if enabled and not live_assistant_speak:
        raise AssistantToggleError(
            409,
            "Голосовые ответы выключены в настройках сервиса",
        )
    if enabled and not meeting.get("meeting_url"):
        raise AssistantToggleError(409, "У встречи нет ссылки на Телемост")

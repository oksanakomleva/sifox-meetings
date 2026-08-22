"""Unit tests for the live-assistant pure helpers (no DB / audio / OpenAI)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.qa_engine import (
    _SYSTEM_VOICE,
    build_search_query,
    build_priority_search_query,
    clean_live_transcript,
    contains_assistant_command,
    contains_wake_word,
    format_meeting_metadata,
    make_search_snippet,
    pack_context_with_quotas,
    parse_note_command,
    relevance_score,
    strip_assistant_command,
    strip_wake_word,
    select_scope,
    pack_context,
)

WAKE = "протоколлер"
COMMAND = "подскажи"


class TestWakeWord:
    def test_exact(self):
        assert contains_wake_word("Протоколлер, когда отправили документы?", WAKE)

    def test_mangled_ending(self):
        # ASR often splits/garbles the tail.
        assert contains_wake_word("протокол лер когда отправили", WAKE)
        assert contains_wake_word("протоколер а что по срокам", WAKE)

    def test_case_and_yo(self):
        assert contains_wake_word("ПРОТОКОЛЛЕР привет", WAKE)

    def test_absent(self):
        assert not contains_wake_word("когда отправили документы", WAKE)

    def test_unrelated(self):
        assert not contains_wake_word("обсудим бюджет на квартал", WAKE)

    def test_ordinary_protocol_does_not_trigger(self):
        assert not contains_wake_word("протокол встречи готов", WAKE)


class TestAssistantActivation:
    def test_exact_question_command_activates(self):
        assert contains_assistant_command(
            "Протоколлер, подскажи, что решили по ГПБМ?",
            WAKE,
            COMMAND,
        )

    def test_split_asr_wake_still_activates(self):
        assert contains_assistant_command(
            "протокол лер подскажи какой дедлайн",
            WAKE,
            COMMAND,
        )

    def test_name_alone_does_not_activate(self):
        assert not contains_assistant_command(
            "Кажется, протоколлер сегодня молчит",
            WAKE,
            COMMAND,
        )

    def test_discussion_of_previous_answer_does_not_activate(self):
        assert not contains_assistant_command(
            "Вчера протоколлер подсказывал другую дату",
            WAKE,
            COMMAND,
        )
        assert not contains_assistant_command(
            "Протоколлер ответил на первый вопрос",
            WAKE,
            COMMAND,
        )

    def test_explicit_note_command_still_activates(self):
        assert contains_assistant_command(
            "Протоколлер, запиши отправить договор",
            WAKE,
            COMMAND,
        )

    def test_strips_question_activation(self):
        assert strip_assistant_command(
            "Протоколлер, подскажи, что решили по ГПБМ?",
            WAKE,
            COMMAND,
        ) == "что решили по гпбм"

    def test_preserves_note_intent_when_stripping(self):
        assert strip_assistant_command(
            "Протоколлер, запиши отправить договор",
            WAKE,
            COMMAND,
        ) == "запиши отправить договор"


def test_voice_prompt_uses_archive_for_questions_about_other_meetings():
    assert "[ТЕКУЩАЯ ВСТРЕЧА]" in _SYSTEM_VOICE
    assert "обязательно используй подходящие блоки [АРХИВ]" in _SYSTEM_VOICE
    assert "НЕ отвечай" in _SYSTEM_VOICE
    assert "никогда не является частью названия проекта" in _SYSTEM_VOICE


class TestStripWakeWord:
    def test_strips_prefix(self):
        q = strip_wake_word("Протоколлер когда отправили документы", WAKE)
        assert "отправили документы" in q
        assert "протокол" not in q.lower()

    def test_no_wake_returns_text(self):
        assert strip_wake_word("когда дедлайн", WAKE) == "когда дедлайн"

    def test_wake_only_returns_empty_question(self):
        assert strip_wake_word("Протоколлер", WAKE) == ""


class TestNoteCommand:
    def test_extracts_dictated_note(self):
        assert parse_note_command(
            "запиши в протокол отправить договор до пятницы"
        ) == (True, "отправить договор до пятницы")

    def test_empty_command_is_still_recognized(self):
        assert parse_note_command("запиши, пожалуйста") == (True, "")

    def test_regular_question_is_not_a_note(self):
        assert parse_note_command("что записали в протокол") == (False, "")


class TestSelectScope:
    def test_all_internal_is_full(self):
        assert select_scope(["a@sifox.com", "b@sifox.com"], "sifox.com", False) == "full"

    def test_external_is_meeting_only(self):
        assert select_scope(["a@sifox.com", "guest@gmail.com"], "sifox.com", False) == "meeting_only"

    def test_host_override_forces_full(self):
        assert select_scope(["guest@gmail.com"], "sifox.com", True) == "full"

    def test_unknown_attendees_conservative(self):
        assert select_scope([], "sifox.com", False) == "meeting_only"


class TestPackContext:
    def test_orders_newest_first_within_budget(self):
        items = [
            (0.1, "2026-01-01", "old low"),
            (9.0, "2026-02-01", "feb high"),
            (9.0, "2026-03-01", "mar high"),
        ]
        out = pack_context(items, budget=1000)
        # Both high-rank kept; presented newest-first.
        assert out.index("mar high") < out.index("feb high")

    def test_budget_drops_least_relevant(self):
        items = [
            (9.0, "2026-03-01", "X" * 50),
            (0.1, "2026-01-01", "Y" * 50),
        ]
        out = pack_context(items, budget=60)  # only one line fits
        assert "X" * 50 in out
        assert "Y" * 50 not in out

    def test_oversized_item_does_not_hide_smaller_item(self):
        items = [
            (10.0, "2026-03-01", "X" * 200),
            (9.0, "2026-02-01", "useful"),
        ]
        assert pack_context(items, budget=50) == "useful"


class TestPackContextWithQuotas:
    @staticmethod
    def item(source, rank, line):
        return {
            "source": source,
            "rank": rank,
            "dt": "2026-07-29",
            "line": line,
            "detail": {"source": source, "label": line, "snippet": line},
        }

    def test_guarantees_mattermost_and_email_among_large_meetings(self):
        meetings = [
            self.item("meetings", 1_000 - index, f"[ВСТРЕЧА] общий текст {index} " + "X" * 700)
            for index in range(20)
        ]
        mm = self.item(
            "mattermost",
            900,
            "[MATTERMOST] Сергей Клевицкий будет в отпуске с 10.08 по 23.08",
        )
        email = self.item(
            "email",
            800,
            "[EMAIL] По антиспуфингу согласовали следующий этап",
        )

        context, selected = pack_context_with_quotas(
            [],
            {"meetings": meetings, "mattermost": [mm], "email": [email]},
            budget=2_000,
        )

        assert "отпуске с 10.08 по 23.08" in context
        assert "По антиспуфингу" in context
        assert mm in selected
        assert email in selected


class TestSearchQuery:
    def test_removes_conversational_filler_but_keeps_name(self):
        query = build_search_query(
            "Скажи, пожалуйста, до какого числа в отпуске Сергей Маценов?"
        )
        assert "скажи" not in query
        assert "какого" not in query
        assert "отпуске сергей маценов" in query

    def test_keeps_exact_project_terms(self):
        query = build_search_query("Что говорили по метрикам ГПБ?")
        assert query == "метрикам гпб гпбм газпромбанк"

    def test_keeps_gpbm_and_adds_search_aliases(self):
        query = build_search_query(
            "О чём договорились по метрикам ГПБМ на этой неделе?"
        )
        assert "гпбм" in query
        assert "гпб" in query
        assert "газпромбанк" in query

    def test_priority_query_keeps_exact_gpbm_subject(self):
        assert (
            build_priority_search_query(
                "О чём договорились по метрикам ГПБМ на этой неделе?"
            )
            == "гпбм"
        )

    def test_priority_query_keeps_person_name(self):
        assert (
            build_priority_search_query(
                "Когда уходит в отпуск Сергей Клевицкий?"
            )
            == "сергей клевицкий"
        )

    def test_adds_omantel_alias_for_oman_sync(self):
        query = build_search_query("Как прошел синк по Оману, что там обсудили?")
        assert "оману" in query
        assert "омантел" in query
        assert "встреча" in query
        assert "итоги" in query
        assert "договорились" in query


class TestSearchSnippet:
    def test_centres_excerpt_on_match_in_long_text(self):
        text = f"{'начало ' * 100}важный дедлайн 15 августа {'конец ' * 100}"
        snippet = make_search_snippet(text, "когда важный дедлайн", 120)
        assert "важный дедлайн" in snippet
        assert len(snippet) <= 122

    def test_matches_common_name_inflection_by_prefix(self):
        text = f"{'вводная ' * 100}Отпуск Сергея Клевицкого продлится до пятницы"
        snippet = make_search_snippet(text, "Сергей Клевицкий отпуск", 140)
        assert "Сергея Клевицкого" in snippet

    def test_prefers_short_project_acronym_over_common_early_word(self):
        text = (
            f"{'метрики за неделю без изменений ' * 50}"
            "По ГПБ конверсия составила 42 процента и выросла на 5 пунктов."
        )
        snippet = make_search_snippet(text, "Какие метрики за неделю по ГПБ?", 160)
        assert "ГПБ конверсия" in snippet


def test_relevance_score_prefers_exact_multi_term_match():
    exact = relevance_score(
        "отпуск сергей клибицкий",
        "Сергей Клибицкий будет в отпуске с 10.08 по 23.08",
        0.1,
    )
    generic = relevance_score(
        "отпуск сергей клибицкий",
        "Обсудили общий график отпусков команды",
        100,
    )
    assert exact > generic


def test_relevance_score_prioritizes_exact_subject_over_common_words():
    query = "договорились метрикам гпбм неделе гпб газпромбанк"
    exact_subject = relevance_score(
        query,
        "На встрече ГПБМ обсудили качество распознавания.",
        0.1,
        priority_query="гпбм",
    )
    generic = relevance_score(
        query,
        "На этой неделе договорились обновить общие метрики.",
        100,
        priority_query="гпбм",
    )
    assert exact_subject > generic


def test_clean_live_transcript_removes_subtitle_noise_only():
    transcript = (
        "Продолжение следует. Редактор субтитров А. Иванова "
        "Не забудьте поставить лайк и подписаться на канал! "
        "writing with apple writing with apple writing with apple "
        "Протоколлер, что решили по Оману? Корректор И. Петров"
    )
    cleaned = clean_live_transcript(transcript)
    assert "Продолжение следует" not in cleaned
    assert "Редактор субтитров" not in cleaned
    assert "Корректор" not in cleaned
    assert "подписаться на канал" not in cleaned
    assert "writing with apple" not in cleaned
    assert "Протоколлер, что решили по Оману?" in cleaned


def test_meeting_metadata_contains_stable_current_meeting_facts():
    metadata = format_meeting_metadata(
        {
            "title": "Еженедельный статус",
            "start_time": "2026-07-29T09:30:00+00:00",
        },
        ["USER@SIFOX.COM", "guest@example.com"],
    )
    assert "Еженедельный статус" in metadata
    assert "user@sifox.com" in metadata
    assert "guest@example.com" in metadata

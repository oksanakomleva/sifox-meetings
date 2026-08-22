"""Russian pronunciation regression tests for live-assistant TTS."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.speech_normalizer import normalize_russian_speech_text


def test_year_uses_case_required_by_surrounding_noun():
    assert normalize_russian_speech_text("до 2026 года") == (
        "до две тысячи двадцать шестого года"
    )
    assert normalize_russian_speech_text("в 2026 году") == (
        "в две тысячи двадцать шестом году"
    )
    assert normalize_russian_speech_text("к 2026 году") == (
        "к две тысячи двадцать шестому году"
    )
    assert normalize_russian_speech_text("2026 год") == (
        "две тысячи двадцать шестой год"
    )


def test_date_time_percent_and_regular_numbers_are_spelled_in_russian():
    result = normalize_russian_speech_text(
        "Встреча 22.08.2026 в 10:30, готовность 15%, бюджет 1 250 рублей."
    )

    assert "двадцать второго августа две тысячи двадцать шестого года" in result
    assert "десять часов тридцать минут" in result
    assert "пятнадцать процентов" in result
    assert "одна тысяча двести пятьдесят рублей" in result
    assert not any(character.isdigit() for character in result)


def test_currency_amounts_are_not_split_into_partial_numbers():
    assert normalize_russian_speech_text("$1000 и 1 250 ₽") == (
        "одна тысяча долларов и одна тысяча двести пятьдесят рублей"
    )


def test_written_answer_is_not_required_to_change_for_speech_normalization():
    written = "Результат вырос на 3,5% к 2026 году."
    spoken = normalize_russian_speech_text(written)

    assert written == "Результат вырос на 3,5% к 2026 году."
    assert spoken != written
    assert "три целых пять десятых процента" in spoken
    assert not any(character.isdigit() for character in spoken)

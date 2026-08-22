"""Deterministic Russian number expansion for text-to-speech.

The written assistant answer remains untouched in the audit log.  This module
only prepares a pronunciation-friendly copy for OpenAI TTS or espeak-ng, which
otherwise may switch to English for digit sequences or guess the wrong case.
"""
from __future__ import annotations

import re


_ONES_M = (
    "ноль", "один", "два", "три", "четыре",
    "пять", "шесть", "семь", "восемь", "девять",
)
_ONES_F = (
    "ноль", "одна", "две", "три", "четыре",
    "пять", "шесть", "семь", "восемь", "девять",
)
_TEENS = (
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
    "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
)
_TENS = (
    "", "", "двадцать", "тридцать", "сорок",
    "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто",
)
_HUNDREDS = (
    "", "сто", "двести", "триста", "четыреста",
    "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот",
)
_SCALES = (
    (1_000_000_000_000, ("триллион", "триллиона", "триллионов"), "m"),
    (1_000_000_000, ("миллиард", "миллиарда", "миллиардов"), "m"),
    (1_000_000, ("миллион", "миллиона", "миллионов"), "m"),
    (1_000, ("тысяча", "тысячи", "тысяч"), "f"),
)


def _plural(number: int, forms: tuple[str, str, str]) -> str:
    last_two = abs(number) % 100
    if 11 <= last_two <= 14:
        return forms[2]
    last = last_two % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def _triplet(number: int, gender: str = "m") -> str:
    parts: list[str] = []
    hundreds, remainder = divmod(number, 100)
    if hundreds:
        parts.append(_HUNDREDS[hundreds])
    if 10 <= remainder <= 19:
        parts.append(_TEENS[remainder - 10])
    else:
        tens, ones = divmod(remainder, 10)
        if tens:
            parts.append(_TENS[tens])
        if ones:
            parts.append((_ONES_F if gender == "f" else _ONES_M)[ones])
    return " ".join(parts)


def cardinal_ru(number: int, gender: str = "m") -> str:
    """Spell a non-negative integer in Russian (up to quadrillions)."""
    if number < 0:
        return f"минус {cardinal_ru(-number, gender)}"
    if number == 0:
        return _ONES_M[0]

    parts: list[str] = []
    remainder = number
    for scale, forms, scale_gender in _SCALES:
        amount, remainder = divmod(remainder, scale)
        if amount:
            parts.extend((cardinal_ru(amount, scale_gender), _plural(amount, forms)))
    if remainder:
        parts.append(_triplet(remainder, gender))
    return " ".join(part for part in parts if part)


_ORDINALS = {
    "nom": {
        1: "первый", 2: "второй", 3: "третий", 4: "четвёртый",
        5: "пятый", 6: "шестой", 7: "седьмой", 8: "восьмой", 9: "девятый",
        10: "десятый", 11: "одиннадцатый", 12: "двенадцатый",
        13: "тринадцатый", 14: "четырнадцатый", 15: "пятнадцатый",
        16: "шестнадцатый", 17: "семнадцатый", 18: "восемнадцатый",
        19: "девятнадцатый", 20: "двадцатый", 30: "тридцатый",
        40: "сороковой", 50: "пятидесятый", 60: "шестидесятый",
        70: "семидесятый", 80: "восьмидесятый", 90: "девяностый",
    },
    "gen": {
        1: "первого", 2: "второго", 3: "третьего", 4: "четвёртого",
        5: "пятого", 6: "шестого", 7: "седьмого", 8: "восьмого", 9: "девятого",
        10: "десятого", 11: "одиннадцатого", 12: "двенадцатого",
        13: "тринадцатого", 14: "четырнадцатого", 15: "пятнадцатого",
        16: "шестнадцатого", 17: "семнадцатого", 18: "восемнадцатого",
        19: "девятнадцатого", 20: "двадцатого", 30: "тридцатого",
        40: "сорокового", 50: "пятидесятого", 60: "шестидесятого",
        70: "семидесятого", 80: "восьмидесятого", 90: "девяностого",
    },
    "prep": {
        1: "первом", 2: "втором", 3: "третьем", 4: "четвёртом",
        5: "пятом", 6: "шестом", 7: "седьмом", 8: "восьмом", 9: "девятом",
        10: "десятом", 11: "одиннадцатом", 12: "двенадцатом",
        13: "тринадцатом", 14: "четырнадцатом", 15: "пятнадцатом",
        16: "шестнадцатом", 17: "семнадцатом", 18: "восемнадцатом",
        19: "девятнадцатом", 20: "двадцатом", 30: "тридцатом",
        40: "сороковом", 50: "пятидесятом", 60: "шестидесятом",
        70: "семидесятом", 80: "восьмидесятом", 90: "девяностом",
    },
    "dat": {
        1: "первому", 2: "второму", 3: "третьему", 4: "четвёртому",
        5: "пятому", 6: "шестому", 7: "седьмому", 8: "восьмому", 9: "девятому",
        10: "десятому", 11: "одиннадцатому", 12: "двенадцатому",
        13: "тринадцатому", 14: "четырнадцатому", 15: "пятнадцатому",
        16: "шестнадцатому", 17: "семнадцатому", 18: "восемнадцатому",
        19: "девятнадцатому", 20: "двадцатому", 30: "тридцатому",
        40: "сороковому", 50: "пятидесятому", 60: "шестидесятому",
        70: "семидесятому", 80: "восьмидесятому", 90: "девяностому",
    },
}
_HUNDRED_ORDINALS = {
    "nom": ("", "сотый", "двухсотый", "трёхсотый", "четырёхсотый", "пятисотый", "шестисотый", "семисотый", "восьмисотый", "девятисотый"),
    "gen": ("", "сотого", "двухсотого", "трёхсотого", "четырёхсотого", "пятисотого", "шестисотого", "семисотого", "восьмисотого", "девятисотого"),
    "prep": ("", "сотом", "двухсотом", "трёхсотом", "четырёхсотом", "пятисотом", "шестисотом", "семисотом", "восьмисотом", "девятисотом"),
    "dat": ("", "сотому", "двухсотому", "трёхсотому", "четырёхсотому", "пятисотому", "шестисотому", "семисотому", "восьмисотому", "девятисотому"),
}
_EXACT_THOUSAND_ORDINALS = {
    (1_000, "nom"): "тысячный", (1_000, "gen"): "тысячного", (1_000, "prep"): "тысячном",
    (2_000, "nom"): "двухтысячный", (2_000, "gen"): "двухтысячного", (2_000, "prep"): "двухтысячном",
    (1_000, "dat"): "тысячному", (2_000, "dat"): "двухтысячному",
}


def _ordinal_below_100(number: int, case: str) -> str:
    direct = _ORDINALS[case].get(number)
    if direct:
        return direct
    tens, ones = divmod(number, 10)
    return f"{_TENS[tens]} {_ORDINALS[case][ones]}"


def year_ru(year: int, case: str = "nom") -> str:
    """Spell a year as an ordinal phrase in nominative/genitive/prepositional."""
    exact = _EXACT_THOUSAND_ORDINALS.get((year, case))
    if exact:
        return exact

    last_two = year % 100
    if last_two:
        prefix = cardinal_ru(year - last_two)
        if prefix.startswith("одна тысяча"):
            prefix = prefix[5:]
        return f"{prefix} {_ordinal_below_100(last_two, case)}".strip()

    hundreds = (year % 1_000) // 100
    if hundreds:
        prefix_number = year - hundreds * 100
        prefix = cardinal_ru(prefix_number) if prefix_number else ""
        if prefix.startswith("одна тысяча"):
            prefix = prefix[5:]
        return f"{prefix} {_HUNDRED_ORDINALS[case][hundreds]}".strip()

    # Rare large exact millennia are safer as cardinal words than raw digits.
    return cardinal_ru(year)


_MONTHS = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def normalize_russian_speech_text(text: str) -> str:
    """Expand common numeric forms into pronunciation-friendly Russian text."""
    result = str(text or "")

    def replace_date(match: re.Match[str]) -> str:
        day, month, year = map(int, match.groups())
        if not 1 <= day <= 31 or month not in _MONTHS:
            return match.group(0)
        return f"{_ordinal_below_100(day, 'gen')} {_MONTHS[month]} {year_ru(year, 'gen')} года"

    result = re.sub(
        r"(?<!\d)([0-3]?\d)[./-]([01]?\d)[./-](\d{4})(?!\d)",
        replace_date,
        result,
    )
    # A preposition disambiguates cases that the noun form alone cannot: both
    # «в 2026 году» and «к 2026 году» contain «году», but require different
    # ordinal endings.
    result = re.sub(
        r"\bк\s+(\d{4})\s*(?:году|г\.)",
        lambda m: f"к {year_ru(int(m.group(1)), 'dat')} году",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"\bв\s+(\d{4})\s*(?:году|г\.)",
        lambda m: f"в {year_ru(int(m.group(1)), 'prep')} году",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"\b(до|с|после)\s+(\d{4})\s*(?:года|г\.)",
        lambda m: f"{m.group(1)} {year_ru(int(m.group(2)), 'gen')} года",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"(?<!\d)(\d{4})\s*года",
        lambda m: f"{year_ru(int(m.group(1)), 'gen')} года",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"(?<!\d)(\d{4})\s*году",
        lambda m: f"{year_ru(int(m.group(1)), 'prep')} году",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"(?<!\d)(\d{4})\s*(?:год|г\.)",
        lambda m: f"{year_ru(int(m.group(1)), 'nom')} год",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"(?<!\d)(\d{4})-го(?!\w)",
        lambda m: year_ru(int(m.group(1)), "gen"),
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"(?<!\d)(\d{4})-(?:й|ый)(?!\w)",
        lambda m: year_ru(int(m.group(1)), "nom"),
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"(?<!\d)(\d{4})-м(?!\w)",
        lambda m: year_ru(int(m.group(1)), "prep"),
        result,
        flags=re.IGNORECASE,
    )

    def replace_time(match: re.Match[str]) -> str:
        hours, minutes = map(int, match.groups())
        if hours > 23 or minutes > 59:
            return match.group(0)
        hour_text = f"{cardinal_ru(hours)} {_plural(hours, ('час', 'часа', 'часов'))}"
        if minutes == 0:
            return hour_text
        return f"{hour_text} {cardinal_ru(minutes)} {_plural(minutes, ('минута', 'минуты', 'минут'))}"

    result = re.sub(r"(?<!\d)([0-2]?\d):([0-5]\d)(?!\d)", replace_time, result)

    def decimal_words(whole_raw: str, fraction_digits: str) -> str:
        whole = int(whole_raw)
        fraction = int(fraction_digits)
        denominator_forms = (
            ("десятая", "десятых", "десятых")
            if len(fraction_digits) == 1
            else ("сотая", "сотых", "сотых")
        )
        whole_words = cardinal_ru(whole, "f")
        whole_unit = "целая" if whole % 10 == 1 and whole % 100 != 11 else "целых"
        return (
            f"{whole_words} {whole_unit} {cardinal_ru(fraction, 'f')} "
            f"{_plural(fraction, denominator_forms)}"
        )

    result = re.sub(
        r"(?<!\d)(\d+)[,.](\d{1,2})\s*%",
        lambda m: f"{decimal_words(m.group(1), m.group(2))} процента",
        result,
    )

    def replace_percent(match: re.Match[str]) -> str:
        number = int("".join(match.group(1).split()))
        unit = _plural(number, ("процент", "процента", "процентов"))
        return f"{cardinal_ru(number)} {unit}"

    result = re.sub(
        r"(?<!\d)(\d[\d\s\u00a0]*)\s*%",
        replace_percent,
        result,
    )

    def replace_decimal(match: re.Match[str]) -> str:
        return decimal_words(match.group(1), match.group(2))

    result = re.sub(r"(?<!\d)(\d+)[,.](\d{1,2})(?!\d)", replace_decimal, result)

    currency = {"₽": ("рубль", "рубля", "рублей"), "$": ("доллар", "доллара", "долларов"), "€": ("евро", "евро", "евро")}

    def replace_currency(match: re.Match[str]) -> str:
        symbol, raw_number = match.groups()
        number = int(re.sub(r"[\s\u00a0]", "", raw_number))
        return f"{cardinal_ru(number)} {_plural(number, currency[symbol])}"

    result = re.sub(
        r"([₽$€])\s*(\d{1,3}(?:[\s\u00a0]\d{3})+|\d+)",
        replace_currency,
        result,
    )

    def replace_currency_suffix(match: re.Match[str]) -> str:
        raw_number, symbol = match.groups()
        number = int(re.sub(r"[\s\u00a0]", "", raw_number))
        return f"{cardinal_ru(number)} {_plural(number, currency[symbol])}"

    result = re.sub(
        r"(\d{1,3}(?:[\s\u00a0]\d{3})+|\d+)\s*([₽$€])",
        replace_currency_suffix,
        result,
    )

    def replace_integer(match: re.Match[str]) -> str:
        return cardinal_ru(int(re.sub(r"[\s\u00a0]", "", match.group(0))))

    result = re.sub(
        r"(?<![\w])(?:\d{1,3}(?:[\s\u00a0]\d{3})+|\d+)(?![\w])",
        replace_integer,
        result,
    )
    return re.sub(r"\s+", " ", result).strip()

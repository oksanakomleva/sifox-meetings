"""Byte-range parsing and streaming for meeting audio seeking."""

import pytest

from utils.audio_range import _read_audio_range, parse_audio_range


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bytes=0-99", (0, 99)),
        ("bytes=500-", (500, 999)),
        ("bytes=900-2000", (900, 999)),
        ("bytes=-100", (900, 999)),
        ("bytes=-2000", (0, 999)),
    ],
)
def test_parse_audio_range(header, expected):
    assert parse_audio_range(header, 1000) == expected


@pytest.mark.parametrize(
    "header",
    ["bytes=", "bytes=1000-", "bytes=20-10", "bytes=-0", "bytes=0-1,3-4", "items=0-1"],
)
def test_reject_invalid_or_unavailable_ranges(header):
    with pytest.raises(ValueError):
        parse_audio_range(header, 1000)


def test_stream_only_requested_audio_bytes(tmp_path):
    path = tmp_path / "audio.mp3"
    path.write_bytes(bytes(range(256)) * 600)
    start, end = parse_audio_range("bytes=65000-140000", path.stat().st_size)
    chunks = list(_read_audio_range(str(path), start, end))
    assert b"".join(chunks) == path.read_bytes()[start:end + 1]
    assert len(chunks) == 2

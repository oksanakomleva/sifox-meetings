"""Unit tests for calendar_sync pure functions."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.calendar_sync import (
    _extract_telemost_url,
    _get_attendee_emails,
    _naive_expiry,
)


class TestExtractTelemostUrl:
    def test_url_in_location(self):
        event = {"location": "https://telemost.yandex.ru/j/80807035633285"}
        assert _extract_telemost_url(event) == "https://telemost.yandex.ru/j/80807035633285"

    def test_url_in_description(self):
        event = {"description": "Join: https://telemost.yandex.ru/j/12345 thanks"}
        assert _extract_telemost_url(event) == "https://telemost.yandex.ru/j/12345"

    def test_url_in_conference_data(self):
        event = {
            "conferenceData": {
                "entryPoints": [{"uri": "https://telemost.yandex.ru/j/999"}]
            }
        }
        assert _extract_telemost_url(event) == "https://telemost.yandex.ru/j/999"

    def test_no_url(self):
        assert _extract_telemost_url({"location": "Office"}) is None

    def test_strips_trailing_punctuation(self):
        event = {"description": "Link: https://telemost.yandex.ru/j/123."}
        assert _extract_telemost_url(event) == "https://telemost.yandex.ru/j/123"


class TestGetAttendeeEmails:
    def test_basic(self):
        event = {"attendees": [{"email": "A@SIFOX.com"}, {"email": "b@sifox.com"}]}
        assert _get_attendee_emails(event) == ["a@sifox.com", "b@sifox.com"]

    def test_skips_resources(self):
        event = {"attendees": [
            {"email": "user@x.com"},
            {"email": "room@x.com", "resource": True},
        ]}
        assert _get_attendee_emails(event) == ["user@x.com"]

    def test_no_attendees(self):
        assert _get_attendee_emails({}) == []

    def test_solo_event_uses_organizer(self):
        # No guests, but the organizer (creator) still counts as a participant.
        event = {"organizer": {"email": "me@sifox.com", "self": True}}
        assert _get_attendee_emails(event) == ["me@sifox.com"]

    def test_organizer_creator_deduped_with_attendees(self):
        event = {
            "attendees": [{"email": "me@sifox.com"}, {"email": "guest@x.com"}],
            "organizer": {"email": "ME@sifox.com"},
            "creator": {"email": "other@sifox.com"},
        }
        # me@ already present (deduped), creator appended once.
        assert _get_attendee_emails(event) == ["me@sifox.com", "guest@x.com", "other@sifox.com"]


class TestNaiveExpiry:
    def test_none(self):
        assert _naive_expiry(None) is None

    def test_aware_to_naive(self):
        dt = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        result = _naive_expiry(dt)
        assert result.tzinfo is None
        assert result.year == 2026 and result.hour == 12

    def test_already_naive(self):
        dt = datetime(2026, 1, 1, 12, 0)
        assert _naive_expiry(dt) == dt

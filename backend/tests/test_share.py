"""Unit tests for public share helpers (password hash + signed audio token)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import share as s


class TestPassword:
    def test_hash_verify_roundtrip(self):
        h = s.hash_password("secret123")
        assert s.verify_password("secret123", h)

    def test_wrong_password(self):
        h = s.hash_password("secret123")
        assert not s.verify_password("nope", h)

    def test_hash_is_salted(self):
        assert s.hash_password("x") != s.hash_password("x")

    def test_format(self):
        h = s.hash_password("x")
        parts = h.split("$")
        assert len(parts) == 3 and parts[0].isdigit()

    def test_malformed_stored(self):
        assert not s.verify_password("x", "garbage")


class TestAudioToken:
    def test_roundtrip(self):
        tok = s.make_audio_token("share-abc")
        assert s.verify_audio_token("share-abc", tok)

    def test_wrong_share_token(self):
        tok = s.make_audio_token("share-abc")
        assert not s.verify_audio_token("share-xyz", tok)

    def test_expired(self):
        tok = s.make_audio_token("share-abc", ttl_seconds=-1)
        assert not s.verify_audio_token("share-abc", tok)

    def test_tampered(self):
        tok = s.make_audio_token("share-abc")
        exp = tok.split(".", 1)[0]
        assert not s.verify_audio_token("share-abc", f"{exp}.deadbeef")

    def test_garbage(self):
        assert not s.verify_audio_token("share-abc", "nope")

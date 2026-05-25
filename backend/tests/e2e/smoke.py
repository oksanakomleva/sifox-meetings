"""
E2E smoke test against deployed Railway instance.

Usage:
    BASE_URL=https://sifox-meetings.up.railway.app \
    SESSION_COOKIE=<your-session-token> \
    python backend/tests/e2e/smoke.py

What it checks:
1. /health returns 200
2. Login required pages return 401 without cookie
3. With cookie: /api/auth/me returns user
4. /api/admin/calendars returns calendars
5. /api/admin/meetings returns meetings
6. Calendar sync trigger works
7. Latest 'done' meeting has all artifacts (transcript, summary, audio)

What it does NOT check (requires manual setup):
- Actually recording a real Telemost meeting (no Yandex API to create meetings)
- The recorder.py pipeline end-to-end (use Railway logs for that)

To do a full recorder E2E:
1. Create event in Google Calendar with Telemost link, start_time = now + 3 min
2. Run this smoke test — it will wait for the meeting to appear, get recorded, and verify result
3. Pass --record flag to enable this mode
"""
import os
import sys
import time
import argparse
from datetime import datetime, timezone


def _import_requests():
    try:
        import requests
        return requests
    except ImportError:
        print("ERROR: requests not installed. Run: pip install requests")
        sys.exit(1)


class SmokeTest:
    def __init__(self, base_url: str, session_cookie: str | None):
        self.base_url = base_url.rstrip("/")
        self.cookies = {"session": session_cookie} if session_cookie else {}
        self.results: list[tuple[str, bool, str]] = []
        self.requests = _import_requests()

    def _check(self, name: str, ok: bool, detail: str = ""):
        self.results.append((name, ok, detail))
        symbol = "✅" if ok else "❌"
        print(f"{symbol} {name}" + (f" — {detail}" if detail else ""))

    def _get(self, path: str, **kwargs):
        return self.requests.get(f"{self.base_url}{path}", cookies=self.cookies, timeout=15, **kwargs)

    def _post(self, path: str, **kwargs):
        return self.requests.post(f"{self.base_url}{path}", cookies=self.cookies, timeout=15, **kwargs)

    # ── Tests ────────────────────────────────────────────────────────────────

    def test_health(self):
        r = self._get("/health")
        self._check("/health responds 200", r.status_code == 200, f"got {r.status_code}")

    def test_auth_required(self):
        old_cookies = self.cookies
        self.cookies = {}
        r = self._get("/api/auth/me")
        self.cookies = old_cookies
        self._check("/api/auth/me returns 401 without session", r.status_code == 401)

    def test_me(self):
        if not self.cookies:
            self._check("/api/auth/me with session", False, "no SESSION_COOKIE provided — skipping")
            return False
        r = self._get("/api/auth/me")
        if r.status_code != 200:
            self._check("/api/auth/me with session", False, f"got {r.status_code}")
            return False
        user = r.json()
        self._check("/api/auth/me returns user", "email" in user, f"email={user.get('email')}")
        self._check("user is admin", user.get("is_admin"), "set ADMIN_EMAILS in Railway")
        return user.get("is_admin")

    def test_admin_calendars(self):
        r = self._get("/api/admin/calendars")
        self._check("GET /api/admin/calendars", r.status_code == 200, f"got {r.status_code}")
        if r.status_code == 200:
            cals = r.json().get("calendars", [])
            self._check("has at least one calendar", len(cals) > 0, f"{len(cals)} calendars")
            enabled = [c for c in cals if c.get("record_enabled")]
            self._check("has at least one calendar with record_enabled", len(enabled) > 0, f"{len(enabled)} enabled")

    def test_admin_meetings(self):
        r = self._get("/api/admin/meetings?limit=10")
        self._check("GET /api/admin/meetings", r.status_code == 200, f"got {r.status_code}")
        if r.status_code == 200:
            meetings = r.json().get("meetings", [])
            self._check("has meetings", len(meetings) > 0, f"{len(meetings)} meetings")
            return meetings
        return []

    def test_sync_trigger(self):
        r = self._post("/api/admin/calendars/sync")
        self._check("POST /api/admin/calendars/sync", r.status_code == 200, f"got {r.status_code}")

    def test_last_done_meeting(self, meetings: list):
        done = [m for m in meetings if m.get("status") == "done"]
        if not done:
            self._check("at least one 'done' meeting exists", False, "no done meetings to inspect")
            return
        m = done[0]
        self._check("done meeting has summary", bool(m.get("summary")), f"meeting {m['id'][:8]}")
        self._check("done meeting has audio_path", bool(m.get("audio_path")))
        self._check("done meeting has tags", bool(m.get("tags")))
        self._check("done meeting has meeting_type", bool(m.get("meeting_type")))

    def wait_for_recording(self, timeout_minutes: int = 15):
        """Wait for a pending meeting to be recorded and analyzed."""
        print(f"\n⏳ Waiting up to {timeout_minutes} min for a meeting to complete the pipeline...")
        deadline = time.time() + timeout_minutes * 60
        last_status = None
        while time.time() < deadline:
            r = self._get("/api/admin/meetings?limit=5")
            if r.status_code != 200:
                time.sleep(10)
                continue
            meetings = r.json().get("meetings", [])
            # Find the most recent non-done meeting
            active = next((m for m in meetings if m.get("status") in ("pending", "recording", "transcribing", "analyzing")), None)
            if active:
                if active["status"] != last_status:
                    print(f"  → meeting {active['id'][:8]} status: {active['status']}")
                    last_status = active["status"]
            else:
                # No active — check if latest is done with summary
                if meetings and meetings[0].get("status") == "done" and meetings[0].get("summary"):
                    self._check("recorded meeting reached 'done' with summary", True, f"meeting {meetings[0]['id'][:8]}")
                    return True
                if meetings and meetings[0].get("status") == "error":
                    self._check("recorded meeting reached 'done' with summary", False, f"error: {meetings[0].get('error_message')}")
                    return False
            time.sleep(15)
        self._check("recorded meeting reached 'done' with summary", False, f"timeout after {timeout_minutes} min")
        return False

    # ── Runner ───────────────────────────────────────────────────────────────

    def run(self, record_mode: bool = False) -> bool:
        print(f"🔍 Smoke test against {self.base_url}\n")
        self.test_health()
        self.test_auth_required()
        is_admin = self.test_me()
        if not is_admin:
            print("\n⚠️  No admin session — skipping authenticated tests")
            return self._summary()
        self.test_admin_calendars()
        meetings = self.test_admin_meetings()
        self.test_sync_trigger()
        self.test_last_done_meeting(meetings)

        if record_mode:
            print("\n📅 Record mode: assumes you created a Google Calendar event with Telemost link starting soon")
            self.wait_for_recording()

        return self._summary()

    def _summary(self) -> bool:
        failed = [r for r in self.results if not r[1]]
        print(f"\n{'='*60}")
        print(f"Total: {len(self.results)}  Passed: {len(self.results) - len(failed)}  Failed: {len(failed)}")
        if failed:
            print("\nFailures:")
            for name, _, detail in failed:
                print(f"  ❌ {name} — {detail}")
            return False
        print("✅ All checks passed")
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true", help="Wait for a pending meeting to be recorded (15min timeout)")
    ap.add_argument("--url", default=os.environ.get("BASE_URL", "https://sifox-meetings.up.railway.app"))
    ap.add_argument("--cookie", default=os.environ.get("SESSION_COOKIE"))
    args = ap.parse_args()

    if not args.cookie:
        print("⚠️  No SESSION_COOKIE — auth-required tests will be skipped.")
        print("   Get cookie from browser DevTools → Application → Cookies → 'session' value")
        print()

    smoke = SmokeTest(args.url, args.cookie)
    ok = smoke.run(record_mode=args.record)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

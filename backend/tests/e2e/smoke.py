"""
E2E smoke test against deployed Railway instance.

Usage — with session cookie (browser login):
    SESSION_COOKIE=<value> python backend/tests/e2e/smoke.py

Usage — with test API key (fully automated, no browser):
    python backend/tests/e2e/smoke.py   # reads .env.test automatically

Usage — full automated E2E pipeline (~20 min):
    python backend/tests/e2e/smoke.py --full-e2e

.env.test file (gitignored, create once):
    TEST_API_KEY=your-secret-key   # must match Railway TEST_API_KEY variable
    BASE_URL=https://sifox-meetings.up.railway.app

What it checks:
1. /health returns 200
2. Login required pages return 401 without cookie
3. Admin endpoints: /api/auth/me, /api/admin/calendars, /api/admin/meetings
4. Calendar sync trigger works
5. Latest done meeting has full artifacts (transcript, summary, audio, tags)
6. --full-e2e: creates calendar event + launches Test Speaker, waits for done
"""
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 output on Windows so emoji/Unicode in print() don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8", buffering=1)


def _load_env_test() -> None:
    """Load .env.test from project root if it exists (silently skip if not)."""
    for candidate in [
        Path(__file__).parent.parent.parent.parent / ".env.test",  # project root
        Path(__file__).parent / ".env.test",                       # e2e dir
    ]:
        if candidate.exists():
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        os.environ.setdefault(key.strip(), val.strip())
            break


_load_env_test()


def _import_requests():
    try:
        import requests
        return requests
    except ImportError:
        print("ERROR: requests not installed. Run: pip install requests")
        sys.exit(1)


class SmokeTest:
    def __init__(self, base_url: str, session_cookie: str | None, test_api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.cookies = {"session": session_cookie} if session_cookie else {}
        self.test_api_key = test_api_key
        # Headers sent on every request when using test API key
        self._auth_headers = {"X-Test-Api-Key": test_api_key} if test_api_key else {}
        self.results: list[tuple[str, bool, str]] = []
        self.requests = _import_requests()

    @property
    def has_auth(self) -> bool:
        return bool(self.cookies or self.test_api_key)

    def _check(self, name: str, ok: bool, detail: str = ""):
        self.results.append((name, ok, detail))
        symbol = "✅" if ok else "❌"
        print(f"{symbol} {name}" + (f" — {detail}" if detail else ""))

    def _get(self, path: str, **kwargs):
        return self.requests.get(
            f"{self.base_url}{path}",
            cookies=self.cookies,
            headers=self._auth_headers,
            timeout=15,
            **kwargs,
        )

    def _post(self, path: str, **kwargs):
        return self.requests.post(
            f"{self.base_url}{path}",
            cookies=self.cookies,
            headers=self._auth_headers,
            timeout=15,
            **kwargs,
        )

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
        # When using test API key, /api/auth/me won't work (no real session),
        # but we know we have admin access — skip the /me check gracefully
        if self.test_api_key and not self.cookies:
            self._check("/api/auth/me", True, "skipped — using TEST_API_KEY (no session cookie)")
            return True

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

    # ── Full E2E (automated, no human needed) ────────────────────────────────

    def start_e2e_test(self, *, live_assistant: bool = False) -> dict | None:
        """Call Railway to create calendar event + launch Test Speaker."""
        r = self._post(
            "/api/admin/test/start-e2e",
            json={"live_assistant": live_assistant},
        )
        if r.status_code != 200:
            self._check(
                "POST /api/admin/test/start-e2e",
                False,
                f"got {r.status_code}: {r.text[:200]}",
            )
            return None
        data = r.json()
        self._check(
            "E2E test triggered",
            True,
            f"event_id={data.get('calendar_event_id', '')[:12]}  speaker launches when recording starts",
        )
        return data

    def cleanup_e2e_event(self, calendar_id: str, event_id: str) -> None:
        r = self.requests.delete(
            f"{self.base_url}/api/admin/test/calendar-event/{event_id}",
            cookies=self.cookies,
            headers=self._auth_headers,
            params={"calendar_id": calendar_id},
            timeout=15,
        )
        self._check("Delete test calendar event", r.status_code == 200, f"got {r.status_code}")

    def run_full_e2e(
        self,
        timeout_minutes: int = 20,
        *,
        live_assistant: bool = False,
    ) -> bool:
        """
        Fully automated E2E:
        1. Trigger Railway to create calendar event + schedule Test Speaker
        2. Wait for meeting to reach status=done
        3. Verify transcript, summary, tags
        4. Cleanup calendar event
        """
        print(f"\n🤖 FULL E2E MODE — fully automated, no human needed\n")
        print("Timeline:")
        print("  +0 min  — calendar event created, sync triggered")
        print("  +3 min  — recorder bot joins the meeting")
        print("  +3 min  — Test Speaker joins with mic ON and streams test_audio.wav")
        print("  +5 min  — Test Speaker leaves; recorder gets a graceful E2E finish signal")
        print("  +7 min  — Whisper transcribes, OpenAI analyzes")
        print("  +10 min — status=done, artifacts ready\n")

        e2e_data = self.start_e2e_test(live_assistant=live_assistant)
        if not e2e_data:
            return self._summary()

        calendar_id = e2e_data.get("calendar_id", "")
        event_id = e2e_data.get("calendar_event_id", "")
        meeting_id = e2e_data.get("meeting_id", "")

        if meeting_id:
            print(f"  Tracking meeting_id={meeting_id[:8]}...")
        else:
            print("  WARNING: no meeting_id returned, watching all recent meetings")

        # Wait for the pipeline to complete
        print(f"\n⏳ Waiting up to {timeout_minutes} min for pipeline to complete...")
        deadline = time.time() + timeout_minutes * 60
        last_status = None
        speaker_launched = False
        speaker_job_id = None
        speaker_confirmed = False
        e2e_finish_requested = False
        meeting_url = e2e_data.get("meeting_url", "")

        while time.time() < deadline:
            try:
                # The admin list is ordered by start_time descending. A busy
                # calendar can have dozens of future meetings, so limit=20 can
                # hide the just-started E2E meeting and prevent Test Speaker
                # from ever launching. Fetch a bounded wider window while we
                # still use the direct meeting ID returned by start-e2e.
                r = self._get("/api/admin/meetings?limit=500")
            except Exception as e:
                # Railway may be temporarily busy (Playwright + PulseAudio)
                print(f"  [polling] connection error, retrying in 20s: {type(e).__name__}")
                time.sleep(20)
                continue
            if r.status_code != 200:
                time.sleep(15)
                continue

            meetings = r.json().get("meetings", [])

            # Track specific meeting if we have its ID
            if meeting_id:
                target = next((m for m in meetings if m["id"] == meeting_id), None)
            else:
                target = next(
                    (m for m in meetings if m.get("status") in ("pending", "recording", "transcribing", "analyzing")),
                    None,
                )

            if target:
                status = target.get("status")
                if status != last_status:
                    print(f"  → {target['id'][:8]} status: {status}")
                    last_status = status

                # Join through Telemost as a real second participant. The old
                # direct PulseAudio injection made transcripts pass while nobody
                # actually spoke in the meeting.
                if status == "recording" and not speaker_launched and meeting_url:
                    print("  [speaker] Launching Test Speaker in Telemost...")
                    try:
                        sr = self._post(
                            "/api/admin/test/launch-speaker",
                            json={
                                "meeting_url": meeting_url,
                                "duration_minutes": 2,
                                "audio_profile": (
                                    "live_assistant"
                                    if live_assistant
                                    else "standard"
                                ),
                            },
                        )
                        if sr.status_code == 200:
                            data = sr.json()
                            speaker_job_id = data.get("job_id")
                            speaker_launched = bool(speaker_job_id)
                            print(f"  [speaker] Job started — id={(speaker_job_id or '')[:8]}")
                        else:
                            print(f"  [speaker] WARNING: launch returned {sr.status_code}: {sr.text[:100]}")
                    except Exception as se:
                        print(f"  [speaker] WARNING: could not launch: {se}")

                if speaker_job_id and not e2e_finish_requested:
                    try:
                        speaker_status_response = self._get(
                            f"/api/admin/test/speaker-status/{speaker_job_id}"
                        )
                        if speaker_status_response.status_code == 200:
                            speaker_status = speaker_status_response.json()
                            state = speaker_status.get("status")
                            if (
                                not speaker_confirmed
                                and speaker_status.get("ready")
                                and state in ("speaking", "completed")
                            ):
                                speaker_confirmed = True
                                self._check(
                                    "Test Speaker joined with microphone ON",
                                    True,
                                    f"job {speaker_job_id[:8]} status={state}",
                                )
                            if state == "completed" and speaker_status.get("ready"):
                                if live_assistant:
                                    listener_text = speaker_status.get("stdout") or ""
                                    heard_answer = "маяк" in listener_text.lower()
                                    self._check(
                                        "Remote participant heard Protocaller answer",
                                        heard_answer,
                                        listener_text[-500:],
                                    )
                                    qa_response = self._get(
                                        f"/api/admin/meetings/{meeting_id}/live-qa"
                                    )
                                    qa_items = (
                                        qa_response.json().get("items", [])
                                        if qa_response.status_code == 200
                                        else []
                                    )
                                    latest_qa = qa_items[-1] if qa_items else {}
                                    self._check(
                                        "Wake-word question logged",
                                        bool(qa_items),
                                        (latest_qa.get("question") or "")[:200],
                                    )
                                    self._check(
                                        "Assistant found expected answer",
                                        "маяк" in (latest_qa.get("answer") or "").lower(),
                                        (latest_qa.get("answer") or "")[:300],
                                    )
                                    self._check(
                                        "Assistant voice path reported success",
                                        latest_qa.get("spoken") is True
                                        and not latest_qa.get("error"),
                                        (
                                            latest_qa.get("error")
                                            or f"latency={latest_qa.get('latency_ms')}ms"
                                        ),
                                    )
                                finish_response = self._post(
                                    "/api/admin/test/finish-e2e-recording",
                                    json={"meeting_id": meeting_id},
                                )
                                if finish_response.status_code == 200:
                                    e2e_finish_requested = True
                                    print("  [speaker] Finished speaking; recorder stop requested")
                                elif finish_response.status_code == 409 and status != "recording":
                                    e2e_finish_requested = True
                                else:
                                    print(
                                        "  [speaker] WARNING: finish signal returned "
                                        f"{finish_response.status_code}: {finish_response.text[:150]}"
                                    )
                            elif state == "failed":
                                self._check(
                                    "Test Speaker joined with microphone ON",
                                    False,
                                    (speaker_status.get("error") or "unknown error")[:300],
                                )
                                break
                    except Exception as se:
                        print(f"  [speaker] status unavailable, retrying: {se}")

                if status == "done" and target.get("summary"):
                    if not speaker_confirmed:
                        self._check(
                            "Test Speaker joined with microphone ON",
                            False,
                            "pipeline finished without E2E_SPEAKER_READY",
                        )
                    self._check("Pipeline reached status=done", True, f"meeting {target['id'][:8]}")
                    tlen = target.get("transcript_length") or 0
                    self._check("Transcript not empty", tlen > 0, f"{tlen} chars")
                    self._check("Summary generated", bool(target.get("summary")), "")
                    tags = target.get("tags")
                    self._check(
                        "Tags field is valid",
                        isinstance(tags, list),
                        f"{len(tags or [])} tags",
                    )
                    self._check("Meeting type classified", bool(target.get("meeting_type")), target.get("meeting_type", ""))
                    self._check(
                        "Transcript has content (>50 chars)",
                        tlen > 50,
                        f"{tlen} chars",
                    )
                    break
                elif status == "error":
                    self._check(
                        "Pipeline reached status=done",
                        False,
                        f"error: {target.get('error_message', 'unknown')[:100]}",
                    )
                    break

            time.sleep(20)
        else:
            self._check("Pipeline reached status=done", False, f"timeout after {timeout_minutes} min")

        # Cleanup: delete the test calendar event
        if calendar_id and event_id:
            print("\n🧹 Cleaning up test calendar event...")
            self.cleanup_e2e_event(calendar_id, event_id)

        return self._summary()

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
    ap.add_argument("--full-e2e", action="store_true", help="Fully automated E2E: creates calendar event + launches Test Speaker on Railway (~20min)")
    ap.add_argument(
        "--live-assistant-e2e",
        action="store_true",
        help="E2E wake word → answer → Protocaller voice heard by remote listener",
    )
    ap.add_argument("--url", default=os.environ.get("BASE_URL", "https://sifox-meetings.up.railway.app"))
    ap.add_argument("--cookie", default=os.environ.get("SESSION_COOKIE"))
    ap.add_argument("--api-key", default=os.environ.get("TEST_API_KEY"), help="Test API key (alternative to session cookie)")
    args = ap.parse_args()

    if not args.cookie and not args.api_key:
        print("⚠️  No auth — running public checks only.")
        print("   Option 1: create .env.test with TEST_API_KEY=<key from Railway>")
        print("   Option 2: SESSION_COOKIE=<value from browser DevTools>")
        print()
    elif args.api_key and not args.cookie:
        print(f"[KEY] Using TEST_API_KEY for authentication\n")

    smoke = SmokeTest(args.url, args.cookie, test_api_key=args.api_key)

    if args.full_e2e or args.live_assistant_e2e:
        print(f"[>>] Pre-checks against {args.url}\n")
        smoke.test_health()
        smoke.test_auth_required()
        is_admin = smoke.test_me()
        if not is_admin:
            print("\n❌ Need admin auth for full E2E (set TEST_API_KEY in .env.test)")
            sys.exit(1)
        ok = smoke.run_full_e2e(live_assistant=args.live_assistant_e2e)
    else:
        ok = smoke.run(record_mode=args.record)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

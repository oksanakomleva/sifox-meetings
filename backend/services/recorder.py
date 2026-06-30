"""
Meeting recorder — adapted from telemost-bot.
Joins Telemost meeting as guest, records audio, transcribes, analyses.
One instance per meeting, coordinated by DB status.
"""
import asyncio
import logging
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

from config import config
from database import models
from services.transcriber import transcribe_audio
from services.analyzer import analyze_meeting

logger = logging.getLogger(__name__)

# In-memory registry of active recordings: meeting_id -> task
_active: dict[str, asyncio.Task] = {}

# Set to True on SIGTERM — scheduler stops launching new recordings
_shutdown_requested: bool = False


class EmptyRecordingError(Exception):
    """Raised when a recording has no usable audio/speech (silence). Lets callers
    distinguish a benign 'nobody showed up' from a real failure."""


def request_shutdown() -> None:
    """Signal that the service is shutting down — no new recordings will start."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Shutdown requested — recorder will not start new recordings (%d active)", len(_active))


async def wait_for_idle(timeout: float = 3300) -> bool:
    """
    Wait for all active recordings to finish.
    Returns True if finished cleanly, False if timed out.
    Called during graceful shutdown before the event loop closes.
    """
    if not _active:
        return True
    tasks = list(_active.values())
    logger.info("Graceful shutdown: waiting for %d recording(s) to finish (timeout=%.0fs)…", len(tasks), timeout)
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )
        logger.info("All recordings finished cleanly")
        return True
    except asyncio.TimeoutError:
        logger.warning("Graceful shutdown timed out — %d recording(s) still active", len(_active))
        return False


def find_audio_on_disk(meeting_id: str) -> "Path | None":
    """Locate a meeting's audio on the volume by deterministic name.

    The source WAV is present during transcription — it's deleted only after a
    successful MP3 conversion — so prefer it, then fall back to the MP3 for
    already-converted recordings. Crucially, the DB `audio_path` column isn't
    written until AFTER transcription finishes, so recovery must NOT rely on it
    (that bug marked mid-transcription meetings as "данные не сохранились" even
    though the WAV was sitting right here on the persistent volume)."""
    base = Path(config.AUDIO_DIR)
    for ext in (".wav", ".mp3"):
        p = base / f"{meeting_id}{ext}"
        try:
            if p.exists() and p.stat().st_size > 10_000:
                return p
        except OSError:
            continue
    return None


def spawn_tracked(meeting_id: str, coro, *, name: str) -> bool:
    """Run a coroutine as a background task that graceful shutdown will wait for
    (registered in _active, exactly like a live recording). Returns False — and
    closes the coroutine — if this meeting is already being processed."""
    if meeting_id in _active:
        coro.close()
        return False
    task = asyncio.create_task(coro, name=name)
    _active[meeting_id] = task
    task.add_done_callback(lambda _: _active.pop(meeting_id, None))
    return True


async def recover_interrupted_meetings() -> None:
    """
    Called on startup: re-queue recordings that were interrupted by a previous deploy.
    - status='recording'    → reset to 'pending' (bot re-joins if meeting still ongoing)
    - status='transcribing' → re-run the full pipeline from the WAV/MP3 on the volume
    - status='analyzing'    → re-run analysis from the transcript in the DB

    Resumed work is registered in _active so the NEXT graceful shutdown waits for
    it too (otherwise a restart mid-recovery would silently kill it).
    """
    from database.connection import get_pool
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Reset stuck recordings to pending
        rec_result = await conn.execute(
            """
            UPDATE meetings
               SET status = 'pending', start_time = NOW(), error_message = NULL, updated_at = NOW()
             WHERE status = 'recording'
            """
        )
        rec_count = int(rec_result.split()[-1])

        # Find meetings stuck mid-processing
        stuck = await conn.fetch(
            "SELECT id, title, status, transcript FROM meetings WHERE status IN ('transcribing', 'analyzing')"
        )

    if rec_count:
        logger.warning("Reset %d interrupted recordings back to pending", rec_count)

    for row in stuck:
        mid = str(row["id"])
        title = row["title"] or mid[:8]
        status = row["status"]
        transcript = row["transcript"]

        # Analysis step: the transcript is already saved — just re-run analysis.
        if status == "analyzing" and transcript:
            logger.info("Recovering analysis for '%s' (%s)", title, mid[:8])
            spawn_tracked(mid, _recover_analyze(mid, transcript), name=f"reanalyze-{mid[:8]}")
            continue

        # Transcription step (or analyzing without a saved transcript): the source
        # audio is on the volume as {id}.wav/.mp3 even though the DB audio_path is
        # still NULL. Find it by name and resume the full pipeline.
        audio = find_audio_on_disk(mid)
        if audio is not None:
            logger.info("Recovering transcription for '%s' (%s) from %s", title, mid[:8], audio.name)
            spawn_tracked(mid, _recover_pipeline(mid, audio), name=f"recover-{mid[:8]}")
            continue

        # Genuinely nothing to resume from (no audio on disk, no transcript).
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE meetings SET status='error', error_message='Прервано при деплое, данные не сохранились', updated_at=NOW() WHERE id=$1",
                mid,
            )
        logger.warning("Meeting '%s' (%s) stuck in %s without recoverable data — marked error", title, mid[:8], status)


async def _finalize_no_show(meeting_id: str) -> None:
    """Mark a meeting nobody joined as 'no_show' and drop its empty recording.
    Deletes the file regardless of size (find_audio_on_disk skips tiny files)."""
    await models.mark_no_show(meeting_id)
    base = Path(config.AUDIO_DIR)
    for ext in (".wav", ".mp3"):
        p = base / f"{meeting_id}{ext}"
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Could not delete empty recording %s: %s", p.name, e)


async def _recover_pipeline(meeting_id: str, audio_path: "Path") -> None:
    """Resume transcription → analysis from an existing audio file on the volume.
    transcribe_and_analyze raises on failure, so mark the meeting 'error' here."""
    try:
        await transcribe_and_analyze(meeting_id, audio_path)
    except EmptyRecordingError:
        # Recovered audio has no usable speech — nothing to show.
        logger.info("Recovery %s: empty recording — marking no_show", meeting_id[:8])
        await _finalize_no_show(meeting_id)
    except Exception as e:
        logger.error("Recovery pipeline failed for %s: %s", meeting_id[:8], e)
        await models.update_meeting_status(meeting_id, "error", f"Ошибка при восстановлении: {str(e)[:400]}")


async def _recover_analyze(meeting_id: str, transcript_text: str) -> None:
    """Resume pipeline from analysis step after interrupted deploy."""
    try:
        await models.update_meeting_status(meeting_id, "analyzing")
        _m = await models.get_meeting(meeting_id)
        analysis = await analyze_meeting(
            transcript_text, _m.get("title") if _m else None
        )
        await models.save_analysis(
            meeting_id,
            summary=analysis["summary"],
            tags=analysis["tags"],
            topic=analysis["topic"],
            meeting_type=analysis["meeting_type"],
        )
        await models.update_meeting_status(meeting_id, "done")
        logger.info("Recovery analysis complete for %s", meeting_id[:8])
    except Exception as e:
        logger.error("Recovery analysis failed for %s: %s", meeting_id[:8], e)
        await models.update_meeting_status(meeting_id, "error", f"Ошибка анализа при восстановлении: {str(e)[:400]}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def start_recording(meeting_id: str) -> None:
    """Claim and record a meeting. No-op if already taken."""
    if meeting_id in _active:
        logger.info("Meeting %s already recording", meeting_id)
        return

    claimed = await models.claim_meeting_for_recording(meeting_id)
    if not claimed:
        # Either claimed by another worker, or a duplicate calendar event for a
        # Telemost room that is already being recorded. Mark duplicates so they
        # don't linger as pending and later join an already-finished meeting.
        if await models.mark_duplicate_if_sibling_active(meeting_id):
            logger.info(
                "Meeting %s is a duplicate of an active/recent recording for the "
                "same Telemost room — skipped", meeting_id[:8],
            )
        else:
            logger.info("Meeting %s already claimed by another worker", meeting_id)
        return

    task = asyncio.create_task(
        _record_pipeline(meeting_id),
        name=f"record-{meeting_id[:8]}",
    )
    _active[meeting_id] = task
    task.add_done_callback(lambda _: _active.pop(meeting_id, None))


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def _record_pipeline(meeting_id: str) -> None:
    meeting = await models.get_meeting(meeting_id)
    if not meeting:
        return

    url = meeting["meeting_url"]
    audio_filename = f"{meeting_id}.wav"
    audio_path = Path(config.AUDIO_DIR) / audio_filename
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    sink_name = f"meet_{meeting_id[:8]}"
    botmic_name: str | None = None   # virtual mic for voice answers (Phase 3)
    had_participants = False
    audio_proc: AudioCapture | None = None
    browser = None
    pw = None
    speak_enabled = config.LIVE_ASSISTANT_ENABLED and config.LIVE_ASSISTANT_SPEAK

    try:
        # 1. PulseAudio sink — one per meeting. The browser is bound to THIS sink
        # per-process via PULSE_SINK below, which takes precedence over the global
        # default — so concurrent recordings never share a sink and their audio
        # can't mix. (Previously routing relied ONLY on the global default sink,
        # which raced when several meetings recorded at once → overlapping audio.)
        # set_default_sink stays as a fallback in case PULSE_SINK isn't honored.
        await _create_pulse_sink(sink_name)
        await asyncio.sleep(0.5)
        await _set_default_sink(sink_name)

        # 1b. Virtual microphone for the bot (Phase 3, voice answers). Its monitor
        # becomes the default capture source BEFORE Chromium launches, so the bot's
        # mic = this sink; paplay-ing TTS into it transmits to the meeting. Best
        # effort — on any failure we just disable voice and keep recording.
        if speak_enabled:
            try:
                botmic_name = f"botmic_{meeting_id[:8]}"
                await _create_pulse_sink(botmic_name)
                await _set_default_source(f"{botmic_name}.monitor")
            except Exception as e:
                logger.warning("Bot mic setup failed (%s) — voice disabled: %s", meeting_id[:8], e)
                botmic_name = None

        # 2. Browser — check Xvfb is alive, then launch with timeout
        display = os.environ.get("DISPLAY", ":99")
        pulse = os.environ.get("PULSE_SERVER", "unix:/tmp/pulse.sock")
        logger.info("Launching browser: DISPLAY=%s PULSE_SERVER=%s", display, pulse)

        # Quick Xvfb sanity check (non-fatal if xdpyinfo is missing)
        try:
            xdpy = await asyncio.create_subprocess_exec(
                "xdpyinfo", "-display", display,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                ret = await asyncio.wait_for(xdpy.wait(), timeout=5)
                if ret != 0:
                    raise RuntimeError(f"Xvfb display {display} not responding (xdpyinfo exit {ret})")
            except asyncio.TimeoutError:
                xdpy.kill()
                raise RuntimeError(f"Xvfb check timed out — display {display} unavailable")
        except FileNotFoundError:
            logger.warning("xdpyinfo not found — skipping Xvfb check (install x11-utils to enable)")

        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        try:
            browser = await asyncio.wait_for(
                pw.chromium.launch(
                    headless=False,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--autoplay-policy=no-user-gesture-required",
                        "--use-fake-ui-for-media-stream",
                    ],
                    env={**os.environ, "DISPLAY": display, "PULSE_SERVER": pulse, "PULSE_SINK": sink_name},
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("chromium.launch() timed out after 30s — Xvfb/Chromium issue")
        logger.info("Browser launched OK")
        page = await browser.new_page()

        # 3. Join meeting
        participants = await _join_meeting(page, url)
        logger.info("Joined meeting %s, participants: %s", meeting_id[:8], participants)

        # 3b. Turn the bot mic on (best effort) so voice answers are audible.
        if botmic_name:
            try:
                await _enable_bot_mic(page)
            except Exception as e:
                logger.warning("Bot mic enable failed (%s): %s", meeting_id[:8], e)

        # 4. Start audio capture
        await models.update_meeting_status(meeting_id, "recording")
        audio_proc = await _start_audio_capture(str(audio_path), sink_name)

        # 5. Speaker timeline
        speaker_timeline: list[tuple[float, str]] = []
        t0 = time.monotonic()

        async def track_speakers():
            dumped = False
            while True:
                await asyncio.sleep(1)
                try:
                    if not dumped:
                        # One-shot: log a sample speaker tile so we can confirm the
                        # (obfuscated) DOM structure and that avatar stripping works.
                        try:
                            sample = await page.evaluate(
                                "() => { const e = document.querySelector("
                                "\"div[class*='rootStroke'], div[class*='speaking']\");"
                                " return e ? e.outerHTML.slice(0, 1500) : null; }"
                            )
                            if sample:
                                dumped = True
                                logger.info("Speaker tile DOM sample %s: %s", meeting_id[:8], sample)
                        except Exception:
                            pass
                    speakers = await _get_active_speakers(page)
                    for sp in speakers:
                        t = time.monotonic() - t0
                        if not speaker_timeline or speaker_timeline[-1][1] != sp:
                            speaker_timeline.append((t, sp))
                except Exception:
                    pass

        tracker = asyncio.create_task(track_speakers())

        # 5b. Live in-meeting assistant (isolated, opt-in via flag). Failures here
        # never affect the recording — run_live_assistant swallows its own errors.
        live_task = None
        if config.LIVE_ASSISTANT_ENABLED:
            from services.live_assistant import run_live_assistant, speak_text
            speak_cb = None
            if botmic_name:
                _mic = botmic_name
                async def speak_cb(text: str):
                    await speak_text(text, _mic)
            live_task = asyncio.create_task(
                run_live_assistant(meeting_id, sink_name, speak=speak_cb),
                name=f"live-{meeting_id[:8]}",
            )

        # 6. Wait for meeting end
        had_participants = await _wait_for_meeting_end(page, participants, meeting.get("start_time"))
        tracker.cancel()
        if live_task:
            live_task.cancel()

        # 7. Stop recording
        end_time = datetime.now(timezone.utc)
        await _stop_audio_capture(audio_proc)
        audio_proc = None

        await browser.close()
        browser = None
        await pw.stop()
        pw = None

        # 8–9. Transcribe → store → MP3 → analyze (shared with extension uploads)
        await transcribe_and_analyze(
            meeting_id,
            audio_path,
            speaker_timeline=speaker_timeline,
            participants=participants,
            end_time=end_time,
        )

    except asyncio.CancelledError:
        logger.info("Recording %s cancelled", meeting_id[:8])
        raise
    except EmptyRecordingError as e:
        if had_participants:
            # People were here but no speech was captured — a real failure worth seeing.
            logger.warning("Recording %s empty despite participants: %s", meeting_id[:8], e)
            await models.update_meeting_status(meeting_id, "error", "Пустая запись: речь не распознана")
        else:
            # Nobody showed up (cancelled / no-show) — benign, not an error.
            logger.info("Recording %s: nobody joined — marking no_show", meeting_id[:8])
            await _finalize_no_show(meeting_id)
    except Exception as e:
        logger.error("Recording %s failed: %s", meeting_id[:8], e, exc_info=True)
        await models.update_meeting_status(meeting_id, "error", str(e)[:500])
    finally:
        if audio_proc and audio_proc.returncode is None:
            await _stop_audio_capture(audio_proc)
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass
        await _delete_pulse_sink(sink_name)
        if botmic_name:
            try:
                await _delete_pulse_sink(botmic_name)
            except Exception:
                pass


# ── Shared post-capture pipeline ──────────────────────────────────────────────

async def transcribe_and_analyze(
    meeting_id: str,
    audio_path: Path,
    *,
    speaker_timeline: list | None = None,
    participants: set[str] | list[str] | None = None,
    end_time: datetime | None = None,
) -> None:
    """Transcribe an audio file, store the transcript, convert to MP3, run AI
    analysis, and finalize the meeting (status='done').

    Shared by the live recorder (WAV + speaker timeline) and by browser-extension
    uploads (webm/opus, no speaker data). Raises on failure — the caller is
    responsible for marking the meeting 'error'.
    """
    participants = list(participants or [])

    await models.update_meeting_status(meeting_id, "transcribing")

    if not audio_path.exists() or audio_path.stat().st_size < 10_000:
        raise EmptyRecordingError(f"Audio file missing or too small: {audio_path}")

    meeting = await models.get_meeting(meeting_id)

    # 1. Transcribe (faster-whisper decodes any format via ffmpeg)
    segments = await transcribe_audio(str(audio_path))
    if not segments:
        raise EmptyRecordingError("Empty transcription")

    effective_tl = _effective_speaker_timeline(speaker_timeline or [], set(participants))
    transcript_text = _build_transcript(segments, effective_tl)
    await models.save_transcript(meeting_id, transcript_text)

    # 2. Convert → MP3 (5x smaller — keep for download, drop source)
    mp3_filename = f"{meeting_id}.mp3"
    mp3_path = Path(config.AUDIO_DIR) / mp3_filename
    try:
        src_size = audio_path.stat().st_size
        await _convert_to_mp3(audio_path, mp3_path)
        stored_filename = mp3_filename
        stored_size = mp3_path.stat().st_size
        try:
            audio_path.unlink()
        except FileNotFoundError:
            pass
        logger.info(
            "Converted %s → %s (%d → %d bytes, %.0f%% smaller)",
            audio_path.name, mp3_path.name, src_size, stored_size,
            100 * (1 - stored_size / max(src_size, 1)),
        )
    except Exception as e:
        # Conversion failed — keep the source so audio is at least preserved
        logger.error("MP3 conversion failed for %s: %s — keeping source", meeting_id[:8], e)
        stored_filename = audio_path.name
        stored_size = audio_path.stat().st_size

    await models.save_meeting_audio(meeting_id, stored_filename, stored_size)

    # 3. Participants (none for uploads)
    for p in participants:
        if p != "Protocaller":
            await models.upsert_participant(meeting_id, p)
    await models.resolve_participants_by_email(meeting_id)

    # 4. Analyze
    await models.update_meeting_status(meeting_id, "analyzing")
    analysis = await analyze_meeting(transcript_text, meeting.get("title") if meeting else None)
    await models.save_analysis(
        meeting_id,
        summary=analysis["summary"],
        tags=analysis["tags"],
        topic=analysis["topic"],
        meeting_type=analysis["meeting_type"],
    )

    # 5. End time
    from database.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET end_time = $1 WHERE id = $2",
            end_time or datetime.now(timezone.utc), meeting_id,
        )

    logger.info("Meeting %s done", meeting_id[:8])


# ── Meeting join ──────────────────────────────────────────────────────────────

async def _join_meeting(page, url: str) -> set[str]:
    debug_dir = Path("/tmp/recorder-debug")
    debug_dir.mkdir(exist_ok=True)

    async def snap(name: str):
        try:
            await page.screenshot(path=str(debug_dir / f"{name}.png"), full_page=True)
            logger.info("Screenshot saved: %s.png", name)
        except Exception as e:
            logger.warning("Screenshot %s failed: %s", name, e)

    await page.goto(url, timeout=30_000)
    await page.wait_for_timeout(3000)
    await snap("1-loaded")
    logger.info("Page URL after load: %s", page.url)
    logger.info("Page title: %s", await page.title())

    # Fill name
    name_filled = False
    for sel in [
        "input[placeholder*='имя']",
        "input[placeholder*='name']",
        "input[name='name']",
        "input[type='text']",
    ]:
        try:
            inp = page.locator(sel).first
            if await inp.is_visible(timeout=1500):
                await inp.fill("Protocaller", timeout=3000)
                logger.info("Name filled via selector: %s", sel)
                name_filled = True
                break
        except Exception:
            continue
    if not name_filled:
        logger.warning("Could not find name input")

    await page.wait_for_timeout(500)

    # Telemost has mic/cam OFF by default — don't click them, it triggers
    # permission errors and modals that block the join button.

    # Dismiss any modal/popup (e.g. "Понятно", "Закрыть") that might block join
    for selector in [
        "button:has-text('Понятно')",
        "button:has-text('OK')",
        "button:has-text('Закрыть')",
        "button[aria-label*='Закрыть' i]",
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=500):
                await btn.click()
                logger.info("Dismissed modal: %s", selector)
                await page.wait_for_timeout(500)
        except Exception:
            pass

    await snap("2-before-join")

    # Click join button — try has-text, fallback to clicking by force
    joined = False
    for selector in [
        "button:has-text('Подключиться')",
        "button:has-text('Присоединиться')",
        "button:has-text('Войти')",
        "button:has-text('Join')",
        "button[data-testid='join-button']",
    ]:
        try:
            btn = page.locator(selector).first
            count = await btn.count()
            if count == 0:
                continue
            # Force click even if covered (force=True bypasses visibility check)
            await btn.click(force=True, timeout=3000)
            logger.info("Clicked join button: %s", selector)
            joined = True
            break
        except Exception as e:
            logger.info("Selector %s failed: %s", selector, e)
            continue

    if not joined:
        logger.warning("Could not find join button")
        try:
            buttons = await page.locator("button").all()
            for b in buttons[:30]:
                txt = (await b.text_content() or "").strip()
                aria = await b.get_attribute("aria-label") or ""
                if txt or aria:
                    logger.info("  Button: text=%r aria=%r", txt[:50], aria[:50])
        except Exception:
            pass

    await page.wait_for_timeout(7000)
    await snap("3-after-join")
    logger.info("Page URL after join: %s", page.url)

    return await _get_participant_names(page)


# ── Participant detection ─────────────────────────────────────────────────────

_UI_NOISE = {
    "protocaller", "подключиться", "войти", "join", "ваше имя",
    "your name", "имя", "name", "микрофон", "камера", "mic", "camera",
    "mute", "unmute", "выйти", "leave", "отключить", "включить",
}


def _is_real_name(text: str) -> bool:
    """Return True if text looks like a real participant name."""
    t = text.strip()
    if len(t) < 2 or len(t) > 60:
        return False
    lower = t.lower()
    # Reject if contains any UI noise phrase
    for noise in _UI_NOISE:
        if noise in lower:
            return False
    # Reject pure digits / punctuation
    if re.match(r'^[\d\s\W]+$', t):
        return False
    return True


async def _get_participant_names(page) -> set[str]:
    try:
        # Try specific participant list selectors first
        for selector in [
            "div[class*='ParticipantName']",
            "div[class*='participant-name']",
            "span[class*='participant-name']",
            "div[class*='MemberName']",
            "div[data-testid*='participant'] span",
        ]:
            elements = await page.locator(selector).all()
            if elements:
                names = set()
                for el in elements:
                    text = (await el.text_content() or "").strip()
                    if _is_real_name(text) and text != "Protocaller":
                        names.add(text)
                if names:
                    return names

        # Fallback: broader selector with strict filtering
        elements = await page.locator(
            "div[class*='participant'], div[class*='member']"
        ).all()
        names = set()
        for el in elements:
            text = (await el.text_content() or "").strip()
            if _is_real_name(text) and text != "Protocaller":
                names.add(text)
        return names
    except Exception:
        return set()


async def _get_active_speakers(page) -> list[str]:
    try:
        elements = await page.locator("div[class*='rootStroke'], div[class*='speaking']").all()
        speakers = []
        for el in elements:
            name = await el.get_attribute("data-name") or await el.text_content() or ""
            name = name.strip()
            if name and name != "Protocaller":
                speakers.append(name)
        return speakers
    except Exception:
        return []


# Candidate selectors for the Telemost in-call microphone toggle. Telemost uses
# obfuscated class names, so this is a best guess — refined from the toolbar DOM
# dump logged below. Failure to find it just means the answer isn't heard.
_MIC_BUTTON_SELECTORS = [
    "button[aria-label*='икрофон']",
    "button[aria-label*='icrophone']",
    "button[data-testid*='mic']",
    "button[title*='икрофон']",
    "[role='button'][aria-label*='икрофон']",
]


async def _enable_bot_mic(page) -> bool:
    """Best-effort: turn the bot's microphone ON after joining (Telemost joins
    muted). Also dumps the control bar DOM once so we can find the real selector
    from logs. Returns True if a control was clicked."""
    try:
        toolbar = await page.evaluate(
            "() => { const b = [...document.querySelectorAll(\"button,[role='button']\")]"
            ".map(e => (e.getAttribute('aria-label')||e.getAttribute('title')||'').trim())"
            ".filter(Boolean); return b.slice(0, 40); }"
        )
        logger.info("Telemost controls (aria/title labels): %s", toolbar)
    except Exception:
        pass
    for sel in _MIC_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=2000)
                logger.info("Bot mic: clicked %s", sel)
                return True
        except Exception:
            continue
    logger.warning("Bot mic: no known toggle selector matched — answer won't be heard")
    return False


async def _wait_for_meeting_end(page, initial_participants: set[str], scheduled_start=None) -> bool:
    """Block until the meeting ends. Returns whether any real participant (besides
    Protocaller) was ever present — False means nobody showed up (no_show)."""
    meeting_started = len(initial_participants) > 0
    empty_polls = 0
    deadline = time.monotonic() + config.MAX_RECORDING_HOURS * 3600
    # Grace period after scheduled start — wait this long for someone to arrive
    # before giving up on a meeting that never started
    GRACE_MINUTES = 10

    while True:
        await asyncio.sleep(config.PARTICIPANT_POLL_INTERVAL)

        # Hard deadline
        if time.monotonic() > deadline:
            logger.info("Max recording time reached — stopping")
            return meeting_started

        try:
            current_url = page.url
            if "telemost" not in current_url.lower():
                logger.info("URL changed — meeting ended")
                return meeting_started
        except Exception:
            logger.info("Page crashed — meeting ended")
            return meeting_started

        new_names = await _get_participant_names(page)
        others = [n for n in new_names if n != "Protocaller"]

        if others:
            meeting_started = True
            empty_polls = 0
            continue

        # No one present. Decide whether to end or keep waiting.
        if not meeting_started and scheduled_start is not None:
            now = datetime.now(timezone.utc)
            # If we're still before scheduled start + grace, keep waiting
            grace_until = scheduled_start + timedelta(minutes=GRACE_MINUTES)
            if now < grace_until:
                logger.info(
                    "Empty but within grace period (until %s, now %s) — waiting",
                    grace_until.isoformat(), now.isoformat(),
                )
                continue

        empty_polls += 1
        logger.info("Empty poll %d/%d (started=%s)", empty_polls, config.EMPTY_POLLS_TO_END, meeting_started)
        if empty_polls >= config.EMPTY_POLLS_TO_END:
            logger.info("Meeting ended (no participants)")
            return meeting_started


# ── Audio capture ─────────────────────────────────────────────────────────────

class AudioCapture:
    """Holds parec and ffmpeg subprocesses so we can signal them directly.

    Previously we used create_subprocess_shell("parec | ffmpeg"), which spawned
    a shell that forwarded signals unreliably — ffmpeg often kept running after
    the shell exited and wrote silence to the WAV file for days, filling the
    volume. Running both processes ourselves lets us send SIGINT to each one.
    """

    def __init__(
        self,
        parec: asyncio.subprocess.Process,
        ffmpeg: asyncio.subprocess.Process,
        output_path: str,
    ) -> None:
        self.parec = parec
        self.ffmpeg = ffmpeg
        self.output_path = output_path

    @property
    def returncode(self) -> int | None:
        """None while either process is still running — keeps API compatible
        with the previous `asyncio.subprocess.Process` return type."""
        if self.parec.returncode is None or self.ffmpeg.returncode is None:
            return None
        return self.ffmpeg.returncode


async def _start_audio_capture(output_path: str, sink_name: str) -> AudioCapture:
    """Spawn parec and ffmpeg connected by a raw OS pipe.

    We can't pass parec.stdout (an asyncio StreamReader) to ffmpeg's stdin —
    asyncio tries to fileno() it and fails. So we open the pipe ourselves with
    os.pipe(), hand the write end to parec and the read end to ffmpeg, and
    close our copies in the parent so the OS knows the pipe has exactly one
    writer and one reader (clean EOF propagation when parec exits).
    """
    monitor = f"{sink_name}.monitor"
    read_fd, write_fd = os.pipe()

    try:
        parec = await asyncio.create_subprocess_exec(
            "parec",
            f"--device={monitor}",
            "--format=s16le",
            "--rate=16000",
            "--channels=1",
            stdout=write_fd,
            stderr=asyncio.subprocess.DEVNULL,
        )
    finally:
        # Parent no longer needs the write end; parec inherited it
        os.close(write_fd)

    try:
        ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-f", "s16le",
            "-ar", "16000",
            "-ac", "1",
            "-i", "pipe:0",
            "-acodec", "pcm_s16le",
            output_path,
            stdin=read_fd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    finally:
        # Parent no longer needs the read end; ffmpeg inherited it
        os.close(read_fd)

    logger.info(
        "Audio capture started: parec pid=%s → ffmpeg pid=%s → %s",
        parec.pid, ffmpeg.pid, output_path,
    )
    return AudioCapture(parec, ffmpeg, output_path)


async def _kill_if_alive(proc: asyncio.subprocess.Process, name: str) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=3.0)
        logger.warning("%s killed (pid=%s)", name, proc.pid)
    except (ProcessLookupError, OSError):
        pass
    except asyncio.TimeoutError:
        logger.error("%s did not die after SIGKILL (pid=%s)", name, proc.pid)


async def _stop_audio_capture(cap: AudioCapture) -> None:
    """Stop parec first so ffmpeg gets EOF on stdin and finalizes the WAV
    header cleanly. Then wait for ffmpeg. Hard-kill anything still alive."""
    # 1. SIGINT parec — it stops capturing and closes its stdout
    if cap.parec.returncode is None:
        try:
            cap.parec.send_signal(signal.SIGINT)
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(cap.parec.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("parec didn't exit on SIGINT — killing (pid=%s)", cap.parec.pid)
            await _kill_if_alive(cap.parec, "parec")

    # 2. ffmpeg should now see EOF on stdin and exit on its own.
    #    SIGINT as a hint, then wait, then SIGKILL.
    if cap.ffmpeg.returncode is None:
        try:
            cap.ffmpeg.send_signal(signal.SIGINT)
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(cap.ffmpeg.wait(), timeout=8.0)
        except asyncio.TimeoutError:
            logger.warning("ffmpeg didn't exit on SIGINT — killing (pid=%s)", cap.ffmpeg.pid)
            await _kill_if_alive(cap.ffmpeg, "ffmpeg")

    # 3. Belt-and-suspenders: make sure neither is alive
    await _kill_if_alive(cap.parec, "parec")
    await _kill_if_alive(cap.ffmpeg, "ffmpeg")

    logger.info(
        "Audio capture stopped: parec rc=%s, ffmpeg rc=%s",
        cap.parec.returncode, cap.ffmpeg.returncode,
    )


async def _convert_to_mp3(src_path: Path, mp3_path: Path, *, mono: bool = True) -> None:
    """Convert any ffmpeg-readable audio → MP3 (libmp3lame, 64 kbps).

    Used for live recordings (WAV input) and for browser-extension uploads
    (webm/opus input) — ffmpeg decodes either. ~28 MB/hour for speech.
    mono=True downmixes to one channel; calls keep stereo (mono=False) so the
    two parties stay separable. Raises RuntimeError on failure/empty output.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i", str(src_path),
        "-codec:a", "libmp3lame",
        "-b:a", "64k",
        *(("-ac", "1") if mono else ()),
        str(mp3_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("ffmpeg →mp3 conversion timed out after 10 min")

    if proc.returncode != 0:
        tail = (stderr.decode(errors="replace") if stderr else "")[-500:]
        raise RuntimeError(f"ffmpeg exited {proc.returncode}: {tail}")

    if not mp3_path.exists() or mp3_path.stat().st_size < 1000:
        raise RuntimeError(f"MP3 output missing or too small: {mp3_path}")


# ── PulseAudio ────────────────────────────────────────────────────────────────

async def _create_pulse_sink(name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "pactl", "load-module", "module-null-sink",
        f"sink_name={name}", f"sink_properties=device.description={name}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def _set_default_sink(name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "pactl", "set-default-sink", name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def _set_default_source(name: str) -> None:
    """Make `name` the default capture source — Chromium then uses it as the
    bot's microphone (live assistant voice answers, Phase 3)."""
    proc = await asyncio.create_subprocess_exec(
        "pactl", "set-default-source", name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def _delete_pulse_sink(name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "pactl", "unload-module", f"sink_name={name}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


# ── Transcript building ───────────────────────────────────────────────────────

def _effective_speaker_timeline(
    timeline: list[tuple[float, str]],
    participants: set[str],
) -> list[tuple[float, str]]:
    if timeline:
        return timeline
    others = [p for p in participants if p != "Protocaller"]
    if len(others) == 1:
        return [(0.0, others[0])]
    return []


def _speaker_for_segment(
    start: float,
    end: float,
    timeline: list[tuple[float, str]],
) -> str:
    """Speaker covering the LARGEST share of [start, end] per the timeline.

    The active-speaker timeline is polled ~once per second, so it lags reality
    by up to ~1s; a Whisper segment can also span a real speaker change. Picking
    the speaker with the most overlap over the whole segment is far more robust
    than reading the instantaneous speaker at seg.start. Falls back to 'Участник'.
    """
    if not timeline:
        return "Участник"

    durations: dict[str, float] = {}
    # Time before the first recorded speaker event has no known speaker.
    first_ts = timeline[0][0]
    if start < first_ts:
        durations["Участник"] = max(0.0, min(end, first_ts) - start)

    n = len(timeline)
    for i in range(n):
        ts, name = timeline[i]
        seg_from = max(start, ts)
        seg_to = end if i == n - 1 else min(end, timeline[i + 1][0])
        if seg_to > seg_from:
            durations[name] = durations.get(name, 0.0) + (seg_to - seg_from)

    if not durations:
        # Zero-length segment — fall back to the instantaneous speaker at start.
        speaker = "Участник"
        for ts, name in reversed(timeline):
            if start >= ts:
                speaker = name
                break
        return speaker

    return max(durations, key=durations.get)


def _build_transcript(segments, speaker_timeline: list[tuple[float, str]]) -> str:
    """
    Build transcript merging consecutive segments from the same speaker
    if the gap between them is less than PAUSE_THRESHOLD seconds.
    A new block starts when: speaker changes, there is a long pause, OR the
    current block already spans MAX_BLOCK_SECONDS (so a long monologue — e.g. an
    upload with no speaker timeline — still breaks into readable paragraphs).
    """
    PAUSE_THRESHOLD = 4.0     # seconds — gap that starts a new paragraph
    MAX_BLOCK_SECONDS = 45.0  # cap on one block so long monologues still split

    if not segments:
        return ""

    # Label every Whisper segment by majority overlap with the speaker timeline
    # (robust to the ~1s polling lag and to segments spanning a speaker change),
    # not by the instantaneous speaker at seg.start.
    labeled: list[tuple[object, str]] = [
        (seg, _speaker_for_segment(seg.start, seg.end, speaker_timeline))
        for seg in segments
    ]

    # Merge consecutive same-speaker segments with short gaps into one block
    blocks: list[tuple[float, str, str]] = []  # (start_time, speaker, text)
    curr_speaker = labeled[0][1]
    curr_start   = labeled[0][0].start
    curr_texts   = [labeled[0][0].text.strip()]
    prev_end     = labeled[0][0].end

    for seg, speaker in labeled[1:]:
        gap = seg.start - prev_end
        block_len = seg.start - curr_start
        if speaker == curr_speaker and gap < PAUSE_THRESHOLD and block_len < MAX_BLOCK_SECONDS:
            # Same speaker, short gap, block not too long — append to current block
            curr_texts.append(seg.text.strip())
        else:
            # Speaker changed, long pause, or block hit the length cap — flush it
            blocks.append((curr_start, curr_speaker, " ".join(curr_texts)))
            curr_speaker = speaker
            curr_start   = seg.start
            curr_texts   = [seg.text.strip()]
        prev_end = seg.end

    blocks.append((curr_start, curr_speaker, " ".join(curr_texts)))

    return "\n".join(
        f"[{_fmt_time(start)}] {speaker}: {text}"
        for start, speaker, text in blocks
    )


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ── Scheduler: start pending meetings ────────────────────────────────────────

async def run_recording_scheduler() -> None:
    """Background loop: pick up pending meetings and start recording."""
    while True:
        if not _shutdown_requested:
            try:
                pending = await models.get_pending_meetings_to_start(
                    within_minutes=config.JOIN_BEFORE_MINUTES + 1
                )
                for meeting in pending:
                    mid = str(meeting["id"])
                    if mid not in _active:
                        logger.info(
                            "Scheduling recording for meeting %s at %s",
                            mid[:8], meeting.get("start_time"),
                        )
                        await start_recording(mid)
            except Exception as e:
                logger.error("Scheduler error: %s", e)
        await asyncio.sleep(30)

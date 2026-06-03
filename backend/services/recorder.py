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


async def recover_interrupted_meetings() -> None:
    """
    Called on startup: re-queue recordings that were interrupted by a previous deploy.
    - status='recording' → reset to 'pending' (bot will re-join if meeting still ongoing)
    - status='transcribing' → re-run transcription if audio file exists
    - status='analyzing' → re-run analysis if transcript exists in DB
    """
    from database.connection import get_pool
    from config import config as _config
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
            "SELECT id, title, status, audio_path, transcript FROM meetings WHERE status IN ('transcribing', 'analyzing')"
        )

    if rec_count:
        logger.warning("Reset %d interrupted recordings back to pending", rec_count)

    for row in stuck:
        mid = str(row["id"])
        title = row["title"] or mid[:8]
        status = row["status"]
        audio_path = row["audio_path"]
        transcript = row["transcript"]

        if status == "transcribing" and audio_path:
            full_path = Path(_config.AUDIO_DIR) / audio_path
            if full_path.exists() and full_path.stat().st_size > 10_000:
                logger.info("Recovering transcription for '%s' (%s)", title, mid[:8])
                asyncio.create_task(_recover_transcribe(mid, str(full_path)), name=f"recover-{mid[:8]}")
                continue
        if status == "analyzing" and transcript:
            logger.info("Recovering analysis for '%s' (%s)", title, mid[:8])
            asyncio.create_task(_recover_analyze(mid, transcript), name=f"reanalyze-{mid[:8]}")
            continue

        # Audio/transcript missing — reset to error
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE meetings SET status='error', error_message='Прервано при деплое, данные не сохранились', updated_at=NOW() WHERE id=$1",
                mid,
            )
        logger.warning("Meeting '%s' (%s) stuck in %s without data — marked error", title, mid[:8], status)


async def _recover_transcribe(meeting_id: str, audio_file: str) -> None:
    """Resume pipeline from transcription step after interrupted deploy."""
    try:
        await models.update_meeting_status(meeting_id, "transcribing")
        segments = await transcribe_audio(audio_file)
        if not segments:
            raise RuntimeError("Empty transcription on recovery")
        transcript_text = _build_transcript(segments, [])
        audio_filename = Path(audio_file).name
        audio_size = Path(audio_file).stat().st_size
        await models.save_transcript(meeting_id, transcript_text)
        await models.save_meeting_audio(meeting_id, audio_filename, audio_size)
        await models.resolve_participants_by_email(meeting_id)
        await _recover_analyze(meeting_id, transcript_text)
    except Exception as e:
        logger.error("Recovery transcription failed for %s: %s", meeting_id[:8], e)
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
    audio_proc: AudioCapture | None = None
    browser = None
    pw = None

    try:
        # 1. PulseAudio sink
        await _create_pulse_sink(sink_name)
        await asyncio.sleep(0.5)
        await _set_default_sink(sink_name)

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
                    env={**os.environ, "DISPLAY": display, "PULSE_SERVER": pulse},
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

        # 6. Wait for meeting end
        await _wait_for_meeting_end(page, participants, meeting.get("start_time"))
        tracker.cancel()

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
        raise RuntimeError(f"Audio file missing or too small: {audio_path}")

    meeting = await models.get_meeting(meeting_id)

    # 1. Transcribe (faster-whisper decodes any format via ffmpeg)
    segments = await transcribe_audio(str(audio_path))
    if not segments:
        raise RuntimeError("Empty transcription")

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


async def _wait_for_meeting_end(page, initial_participants: set[str], scheduled_start=None) -> None:
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
            return

        try:
            current_url = page.url
            if "telemost" not in current_url.lower():
                logger.info("URL changed — meeting ended")
                return
        except Exception:
            logger.info("Page crashed — meeting ended")
            return

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
            return


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


async def _convert_to_mp3(src_path: Path, mp3_path: Path) -> None:
    """Convert any ffmpeg-readable audio → MP3 (libmp3lame, 64 kbps mono).

    Used for live recordings (WAV input) and for browser-extension uploads
    (webm/opus input) — ffmpeg decodes either. ~28 MB/hour for speech.
    Raises RuntimeError if ffmpeg fails or the output is missing/empty.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i", str(src_path),
        "-codec:a", "libmp3lame",
        "-b:a", "64k",
        "-ac", "1",
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
    A new block starts when: speaker changes OR there is a long pause.
    """
    PAUSE_THRESHOLD = 4.0  # seconds — gap that starts a new paragraph

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
        if speaker == curr_speaker and gap < PAUSE_THRESHOLD:
            # Same speaker, short gap — append to current block
            curr_texts.append(seg.text.strip())
        else:
            # Speaker changed or long pause — flush current block
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

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
from services import fsio
from services.transcriber import transcribe_audio
from services.analyzer import analyze_meeting

logger = logging.getLogger(__name__)

# In-memory registry of active recordings: meeting_id -> task
_active: dict[str, asyncio.Task] = {}
# Test-only graceful finish signals. The E2E speaker is independently verified
# through its job status, so the recorder need not rely on Telemost's obfuscated
# participant-name DOM to notice that the test participant came and left.
_e2e_finish_requested: set[str] = set()

# Set to True on SIGTERM — scheduler stops launching new recordings
_shutdown_requested: bool = False


def _find_pids_with_environment(
    entry: str,
    proc_root: Path = Path("/proc"),
) -> list[int]:
    """Find Linux processes carrying one exact environment entry.

    Every Chromium child inherits ``PULSE_SINK=meet_<id>`` from its recorder
    launch. That gives us a meeting-specific cleanup key without killing
    browsers belonging to other, healthy recordings.
    """
    expected = entry.encode()
    result: list[int] = []
    try:
        children = list(proc_root.iterdir())
    except OSError:
        return result
    for child in children:
        if not child.name.isdigit():
            continue
        try:
            values = (child / "environ").read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        if expected in values:
            result.append(int(child.name))
    return result


def _collect_runtime_snapshot(proc_root: Path = Path("/proc")) -> str:
    """Collect small, non-sensitive resource diagnostics after a browser stall."""
    parts: list[str] = []
    for label, path in (
        ("load", proc_root / "loadavg"),
        ("memory.current", Path("/sys/fs/cgroup/memory.current")),
        ("memory.max", Path("/sys/fs/cgroup/memory.max")),
        ("memory.events", Path("/sys/fs/cgroup/memory.events")),
        ("pids.current", Path("/sys/fs/cgroup/pids.current")),
        ("pids.max", Path("/sys/fs/cgroup/pids.max")),
    ):
        try:
            value = path.read_text(encoding="utf-8").strip().replace("\n", ",")
        except (OSError, PermissionError):
            continue
        parts.append(f"{label}={value}")

    process_counts: dict[str, int] = {}
    try:
        children = list(proc_root.iterdir())
    except OSError:
        children = []
    for child in children:
        if not child.name.isdigit():
            continue
        try:
            name = (child / "comm").read_text(encoding="utf-8").strip()
        except (OSError, PermissionError):
            continue
        if any(token in name.lower() for token in ("chrome", "chromium", "ffmpeg", "parec")):
            process_counts[name] = process_counts.get(name, 0) + 1
    if process_counts:
        parts.append(
            "processes=" + ",".join(
                f"{name}:{count}" for name, count in sorted(process_counts.items())
            )
        )
    try:
        parts.append(f"self.fds={len(list((proc_root / 'self' / 'fd').iterdir()))}")
    except OSError:
        pass
    return " ".join(parts) or "runtime metrics unavailable"


async def _terminate_stuck_browser(sink_name: str) -> None:
    """Terminate only Chromium processes launched for one meeting.

    Playwright occasionally cannot close a wedged renderer. Leaving that tree
    alive consumes memory/Xvfb resources and makes later recorder launches more
    likely to fail, so fall back to TERM then KILL after bounded normal cleanup.
    """
    entry = f"PULSE_SINK={sink_name}"
    try:
        pids = await asyncio.wait_for(
            asyncio.to_thread(_find_pids_with_environment, entry), timeout=2
        )
    except Exception as exc:
        logger.warning("Could not locate stuck browser for %s: %s", sink_name, exc)
        return
    if not pids:
        return
    logger.warning("Force-cleaning stuck browser for %s (pids=%s)", sink_name, pids)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    await asyncio.sleep(1)
    survivors = await asyncio.to_thread(_find_pids_with_environment, entry)
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


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


async def find_audio_on_disk(meeting_id: str) -> "Path | None":
    """Locate a meeting's audio on the volume by deterministic name.

    The source WAV is present during transcription — it's deleted only after a
    successful MP3 conversion — so prefer it, then fall back to the MP3 for
    already-converted recordings. Crucially, the DB `audio_path` column isn't
    written until AFTER transcription finishes, so recovery must NOT rely on it
    (that bug marked mid-transcription meetings as "данные не сохранились" even
    though the WAV was sitting right here on the persistent volume)."""
    base = Path(config.AUDIO_DIR)
    for ext in (".wav", ".webm", ".ogg", ".opus", ".m4a", ".mp4", ".mp3"):
        p = base / f"{meeting_id}{ext}"
        if await fsio.size(p) > 10_000:
            return p
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


def request_e2e_finish(meeting_id: str) -> bool:
    """Ask an active E2E recording to finish through its normal processing path."""
    if meeting_id not in _active:
        return False
    _e2e_finish_requested.add(meeting_id)
    return True


async def recover_interrupted_meetings() -> None:
    """
    Called on startup: re-queue recordings that were interrupted by a previous deploy.
    - status='joining'/'recording' → salvage a partial WAV if one survived on
                              disk, else reset to pending (bot re-joins if the
                              meeting is still ongoing)
    - status='transcribing' → re-run the full pipeline from the WAV/MP3 on the volume
    - status='analyzing'    → re-run analysis from the transcript in the DB

    Resumed work is registered in _active so the NEXT graceful shutdown waits for
    it too (otherwise a restart mid-recovery would silently kill it).
    """
    from database.connection import get_pool
    pool = await get_pool()

    async with pool.acquire() as conn:
        recording = await conn.fetch(
            "SELECT id, title FROM meetings WHERE status IN ('joining', 'recording')"
        )
        # Find meetings stuck mid-processing
        stuck = await conn.fetch(
            "SELECT id, title, status, transcript FROM meetings WHERE status IN ('transcribing', 'analyzing')"
        )
        abandoned_uploads = await conn.fetch(
            """
            SELECT id
            FROM meetings
            WHERE status = 'uploading'
              AND updated_at < NOW() - INTERVAL '24 hours'
            """
        )

    for row in abandoned_uploads:
        mid = str(row["id"])
        await fsio.unlink_quiet(Path(config.AUDIO_DIR) / f"{mid}.webm.part")
        await models.update_meeting_status(
            mid, "error", "Незавершённая загрузка устарела"
        )
    if abandoned_uploads:
        logger.warning("Cleaned up %d abandoned browser uploads", len(abandoned_uploads))

    # Interrupted recordings: if a partial WAV survived on the volume, SALVAGE it
    # (transcribe what we have) rather than resetting to 'pending'. A blind reset
    # sets start_time=NOW(), so the bot re-joins the (often already-finished)
    # meeting, OVERWRITES the partial with an empty re-record, then deletes it via
    # no_show — that destroyed real recordings on restart (2026-07-22). Only reset
    # meetings with nothing to lose (no usable partial on disk).
    reset_ids = []
    for row in recording:
        mid = str(row["id"])
        title = row["title"] or mid[:8]
        audio = await find_audio_on_disk(mid)
        if audio is not None:
            logger.warning(
                "Interrupted recording '%s' (%s) has a partial on disk (%s) — salvaging, not re-recording",
                title, mid[:8], audio.name,
            )
            spawn_tracked(mid, _recover_pipeline(mid, audio), name=f"salvage-{mid[:8]}")
        else:
            reset_ids.append(row["id"])

    if reset_ids:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE meetings
                   SET status = 'pending', start_time = NOW(), error_message = NULL, updated_at = NOW()
                 WHERE id = ANY($1::uuid[])
                """,
                reset_ids,
            )
        logger.warning("Reset %d interrupted recordings (no partial audio) back to pending", len(reset_ids))

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
        audio = await find_audio_on_disk(mid)
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
    for ext in (".wav", ".webm", ".ogg", ".opus", ".m4a", ".mp4", ".mp3"):
        await fsio.unlink_quiet(base / f"{meeting_id}{ext}")


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
            transcript_text,
            _m.get("title") if _m else None,
            meeting_id=meeting_id,
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
    await fsio.mkdir_p(audio_path.parent)

    sink_name = f"meet_{meeting_id[:8]}"
    sink_module: int | None = None   # pulse module index, for reliable unload
    botmic_name: str | None = None   # virtual mic for voice answers (Phase 3)
    botmic_module: int | None = None
    botmic_source_name: str | None = None
    botmic_source_module: int | None = None
    had_participants = False
    audio_proc: AudioCapture | None = None
    pin_task: "asyncio.Task | None" = None
    tracker: "asyncio.Task | None" = None
    browser = None
    pw = None
    live_task: "asyncio.Task | None" = None
    assistant_enabled = config.LIVE_ASSISTANT_ENABLED and (
        config.LIVE_ASSISTANT_ALL_MEETINGS
        or bool(meeting.get("assistant_enabled"))
    )
    speak_enabled = assistant_enabled and config.LIVE_ASSISTANT_SPEAK

    try:
        startup_deadline = (
            time.monotonic() + max(30, config.RECORDER_STARTUP_TIMEOUT_SEC)
        )

        async def startup_step(factory, label: str, step_timeout: float):
            """Run one admission step within both its own and the total deadline."""
            remaining = startup_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Протоколлер не подключился: превышено время подключения"
                )
            try:
                return await asyncio.wait_for(
                    factory(), timeout=min(step_timeout, remaining)
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"Протоколлер не подключился: завис этап «{label}»"
                ) from exc

        # 1. PulseAudio sink — one per meeting. The browser is launched with
        # PULSE_SINK=this sink, and a background loop (_audio_pin_loop) force-moves
        # its audio stream onto this sink. We deliberately DON'T point the global
        # default sink at a meeting (as before): Chromium doesn't always honour
        # PULSE_SINK, and a shared mutable default raced under concurrency — a
        # browser's audio could land on another meeting's sink and bleed into its
        # recording. Unpinned streams fall back to the throwaway default_sink
        # (recorded by nobody); the pin loop then routes them to the right sink.
        sink_module = await startup_step(
            lambda: _create_pulse_sink(sink_name), "подготовка аудио", 15
        )
        await asyncio.sleep(0.5)

        # 1b. Virtual microphone for the bot (Phase 3, voice answers). A remapped
        # source over this sink's monitor becomes Chromium's capture device before
        # launch; paplay-ing TTS into the sink then transmits it to the meeting.
        # Best effort — on any failure we disable voice and keep recording.
        if speak_enabled:
            try:
                botmic_name = f"botmic_{meeting_id[:8]}"
                botmic_module = await startup_step(
                    lambda: _create_pulse_sink(botmic_name),
                    "подготовка микрофона",
                    15,
                )
                if botmic_module is None:
                    raise RuntimeError("could not create bot microphone sink")
                botmic_source_name = f"botmic_source_{meeting_id[:8]}"
                botmic_source_module = await startup_step(
                    lambda: _create_pulse_source(
                        botmic_source_name,
                        f"{botmic_name}.monitor",
                    ),
                    "подготовка микрофона",
                    15,
                )
            except Exception as e:
                logger.warning("Bot mic setup failed (%s) — voice disabled: %s", meeting_id[:8], e)
                if botmic_module is not None and botmic_name:
                    try:
                        await asyncio.wait_for(
                            _delete_pulse_sink(botmic_name, botmic_module),
                            timeout=10,
                        )
                    except Exception as cleanup_exc:
                        logger.warning(
                            "Bot mic rollback failed (%s): %s",
                            meeting_id[:8],
                            cleanup_exc,
                        )
                botmic_name = None
                botmic_module = None
                botmic_source_name = None
                botmic_source_module = None

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
        pw = await startup_step(
            lambda: async_playwright().start(), "запуск управления браузером", 20
        )
        chromium_args = [
            "--disable-dev-shm-usage",
            "--autoplay-policy=no-user-gesture-required",
            "--use-fake-ui-for-media-stream",
        ]
        if config.CHROMIUM_DISABLE_SANDBOX:
            logger.warning("Chromium sandbox explicitly disabled by configuration")
            chromium_args.extend(["--no-sandbox", "--disable-setuid-sandbox"])
        browser_env = {
            **os.environ,
            "DISPLAY": display,
            "PULSE_SERVER": pulse,
            "PULSE_SINK": sink_name,
        }
        if botmic_source_name:
            # Bind this Chromium instance to its own virtual microphone.
            # Relying only on PulseAudio's process-global default source
            # races when two meetings start at the same time.
            browser_env["PULSE_SOURCE"] = botmic_source_name
        browser = await startup_step(
            lambda: pw.chromium.launch(
                headless=False,
                args=chromium_args,
                env=browser_env,
            ),
            "запуск браузера",
            30,
        )
        logger.info("Browser launched OK")
        context = await startup_step(
            lambda: browser.new_context(permissions=["microphone"]),
            "создание профиля браузера",
            20,
        )
        page = await startup_step(
            context.new_page, "открытие страницы встречи", 20
        )

        # 3. Join meeting
        participants = await startup_step(
            lambda: _join_meeting(
                page,
                url,
                scheduled_start=meeting.get("start_time"),
            ),
            "вход во встречу",
            max(30, config.RECORDER_STARTUP_TIMEOUT_SEC),
        )
        logger.info("Joined meeting %s, participants: %s", meeting_id[:8], participants)

        # 4. Start audio capture
        audio_proc = await startup_step(
            lambda: _start_audio_capture(str(audio_path), sink_name),
            "запуск аудиозаписи",
            20,
        )
        await startup_step(
            lambda: _confirm_audio_capture_started(audio_proc, audio_path),
            "проверка аудиозаписи",
            max(1, config.AUDIO_START_CONFIRM_TIMEOUT_SEC) + 1,
        )
        # "Запись" is now factual: Telemost admitted the bot and the audio file
        # is demonstrably growing.
        await models.update_meeting_status(meeting_id, "recording")
        # Keep this browser's audio pinned to its own sink (Chromium may ignore
        # PULSE_SINK / land on the default → would bleed across concurrent meetings).
        pin_task = asyncio.create_task(
            _audio_pin_loop(
                sink_name,
                botmic_source_name,
            )
        )

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
        if assistant_enabled:
            from services.live_assistant import run_live_assistant, speak_text
            speak_cb = None
            if botmic_name and botmic_source_name:
                _mic = botmic_name
                _source = botmic_source_name

                async def speak_cb(text: str):
                    if not await _set_bot_mic(page, enabled=True):
                        raise RuntimeError("Telemost microphone could not be enabled")
                    try:
                        pinned_streams = 0
                        for _ in range(6):
                            pinned_streams = await _pin_browser_microphone(_source)
                            if pinned_streams:
                                break
                            await asyncio.sleep(0.5)
                        if not pinned_streams:
                            raise RuntimeError(
                                "Chromium microphone capture stream was not found"
                            )
                        # Give Telemost time to publish the newly unmuted WebRTC
                        # track before feeding it a short TTS sentence.
                        await asyncio.sleep(1)
                        if not await speak_text(text, _mic):
                            raise RuntimeError("TTS playback into the bot microphone failed")
                        # Let Chromium flush the final encoded audio frames before
                        # muting the track again.
                        await asyncio.sleep(0.75)
                    finally:
                        if not await _set_bot_mic(page, enabled=False):
                            logger.warning(
                                "Bot mic could not be muted after speaking (%s)",
                                meeting_id[:8],
                            )

            live_task = asyncio.create_task(
                run_live_assistant(meeting_id, sink_name, speak=speak_cb),
                name=f"live-{meeting_id[:8]}",
            )

        # 6. Wait for meeting end
        had_participants = await _wait_for_meeting_end(
            page,
            participants,
            meeting.get("start_time"),
            meeting_id=meeting_id,
        )
        tracker.cancel()
        if live_task:
            live_task.cancel()
            await asyncio.gather(live_task, return_exceptions=True)
            live_task = None

        # 7. Stop recording
        end_time = datetime.now(timezone.utc)
        await _stop_audio_capture(audio_proc)
        audio_proc = None

        try:
            await asyncio.wait_for(browser.close(), timeout=15)
        except Exception as exc:
            logger.warning("Browser close timed out for %s: %s", meeting_id[:8], exc)
        else:
            browser = None
        try:
            await asyncio.wait_for(pw.stop(), timeout=15)
        except Exception as exc:
            logger.warning("Playwright stop timed out for %s: %s", meeting_id[:8], exc)
        else:
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
        try:
            snapshot = await asyncio.wait_for(
                asyncio.to_thread(_collect_runtime_snapshot), timeout=2
            )
            logger.error("Recorder runtime at failure (%s): %s", meeting_id[:8], snapshot)
        except Exception as snapshot_exc:
            logger.warning(
                "Recorder runtime snapshot failed (%s): %s",
                meeting_id[:8],
                snapshot_exc,
            )
        await models.update_meeting_status(meeting_id, "error", str(e)[:500])
    finally:
        browser_cleanup_failed = False
        if audio_proc and audio_proc.returncode is None:
            await _stop_audio_capture(audio_proc)
        if browser:
            try:
                await asyncio.wait_for(browser.close(), timeout=15)
            except Exception as exc:
                browser_cleanup_failed = True
                logger.warning(
                    "Final browser close failed for %s: %s", meeting_id[:8], exc
                )
        if pw:
            try:
                await asyncio.wait_for(pw.stop(), timeout=15)
            except Exception as exc:
                browser_cleanup_failed = True
                logger.warning(
                    "Final Playwright stop failed for %s: %s", meeting_id[:8], exc
                )
        if browser_cleanup_failed:
            await _terminate_stuck_browser(sink_name)
        if tracker:
            tracker.cancel()
            await asyncio.gather(tracker, return_exceptions=True)
        if pin_task:
            pin_task.cancel()
            await asyncio.gather(pin_task, return_exceptions=True)
        if live_task:
            live_task.cancel()
            await asyncio.gather(live_task, return_exceptions=True)
        try:
            await asyncio.wait_for(
                _delete_pulse_sink(sink_name, sink_module), timeout=10
            )
        except Exception as exc:
            logger.warning("Pulse sink cleanup failed for %s: %s", sink_name, exc)
        if botmic_source_module is not None:
            try:
                await asyncio.wait_for(
                    _unload_pulse_module(
                        botmic_source_module,
                        botmic_source_name or "bot microphone source",
                    ),
                    timeout=10,
                )
            except Exception as exc:
                logger.warning("Bot microphone cleanup failed: %s", exc)
        if botmic_name:
            try:
                await asyncio.wait_for(
                    _delete_pulse_sink(botmic_name, botmic_module), timeout=10
                )
            except Exception as exc:
                logger.warning("Bot microphone sink cleanup failed: %s", exc)
        _e2e_finish_requested.discard(meeting_id)


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

    if await fsio.size(audio_path) < 10_000:
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
        src_size = await fsio.size(audio_path)
        await _convert_to_mp3(audio_path, mp3_path)
        stored_filename = mp3_filename
        stored_size = await fsio.size(mp3_path)
        logger.info(
            "Converted %s → %s (%d → %d bytes, %.0f%% smaller)",
            audio_path.name, mp3_path.name, src_size, stored_size,
            100 * (1 - stored_size / max(src_size, 1)),
        )
    except Exception as e:
        # Conversion failed — keep the source so audio is at least preserved
        logger.error("MP3 conversion failed for %s: %s — keeping source", meeting_id[:8], e)
        stored_filename = audio_path.name
        stored_size = await fsio.size(audio_path)
    else:
        # Conversion + metadata OK — drop the (large) source WAV. Best effort:
        # a slow/failed delete must not undo a successful conversion.
        await fsio.unlink_quiet(audio_path)

    await models.save_meeting_audio(meeting_id, stored_filename, stored_size)

    # 3. Participants (none for uploads)
    for p in participants:
        if p != "Protocaller":
            await models.upsert_participant(meeting_id, p)
    await models.resolve_participants_by_email(meeting_id)

    # 4. Analyze
    await models.update_meeting_status(meeting_id, "analyzing")
    analysis = await analyze_meeting(
        transcript_text,
        meeting.get("title") if meeting else None,
        meeting_id=meeting_id,
    )
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

_JOIN_BUTTON_SELECTORS = (
    "button[data-testid='join-button']",
    "button:has-text('Подключиться')",
    "button:has-text('Присоединиться')",
    "button:has-text('Join')",
)


def _is_join_confirmed(state: dict) -> bool:
    """Interpret a bounded Telemost DOM probe after clicking Join.

    Do not rely on the Leave button alone: Telemost can briefly keep stale
    pre-join controls mounted while the participant is already visible to
    others. Conversely, a mic control by itself also exists on the pre-join
    screen and is never sufficient proof.
    """
    if state.get("has_waiting_room"):
        return False
    if state.get("has_leave"):
        return True
    in_call_signals = int(state.get("in_call_signal_count") or 0)
    if in_call_signals >= 2:
        return True
    return bool(
        state.get("has_mic")
        and not state.get("has_join")
        and not state.get("has_name_input")
        and in_call_signals >= 1
    )


def _join_wait_seconds(
    scheduled_start: datetime | None,
    *,
    now: datetime | None = None,
) -> float:
    """Bounded wait covering both UI lag and admission around meeting start."""
    wait = float(max(30, config.TELEMOST_JOIN_TIMEOUT_SEC))
    if scheduled_start is not None:
        current = now or datetime.now(timezone.utc)
        start = scheduled_start
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        seconds_until_start = (start - current).total_seconds()
        wait = max(
            wait,
            seconds_until_start + config.TELEMOST_JOIN_GRACE_AFTER_START_SEC,
        )
    return min(wait, float(max(30, config.TELEMOST_JOIN_MAX_WAIT_SEC)))


async def _telemost_call_state(page) -> dict:
    """Probe only the visible controls needed to prove that the call was joined."""
    return await page.evaluate(
        """() => {
          const visible = e => {
            const r = e.getBoundingClientRect();
            const s = getComputedStyle(e);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' &&
              s.display !== 'none';
          };
          const controls = [...document.querySelectorAll("button,[role='button']")]
            .filter(visible)
            .map(e => (`${e.getAttribute('aria-label') || ''} ` +
              `${e.getAttribute('title') || ''} ${e.textContent || ''}`).toLowerCase())
            .slice(0, 80);
          const inputs = [...document.querySelectorAll(
            "input[placeholder*='имя' i],input[placeholder*='name' i]," +
            "input[name='name'],input[type='text']"
          )].filter(visible);
          const pageText = (document.body.innerText || '').toLowerCase().slice(0, 5000);
          const inCallSignals = [
            controls.some(x => x.includes('участник') || x.includes('participants')),
            controls.some(x => x.includes('открыть чат') || x.includes('open chat')),
            controls.some(x => x.includes('поднять руку') || x.includes('raise hand')),
            controls.some(x =>
              x.includes('демонстрац') || x.includes('share screen')
            ),
            controls.some(x =>
              x.includes('пригласить') || x.includes('invite') ||
              x.includes('копировать ссылку') || x.includes('copy link')
            )
          ].filter(Boolean).length;
          return {
            has_leave: controls.some(x =>
              x.includes('выйти') || x.includes('покинуть') ||
              x.includes('leave') || x.includes('hang up')
            ),
            has_mic: controls.some(x =>
              x.includes('микроф') || x.includes('microphone') ||
              x.includes('mic-button')
            ),
            has_join: controls.some(x =>
              x.includes('подключиться') || x.includes('присоединиться') ||
              x.trim() === 'join'
            ),
            has_name_input: inputs.length > 0,
            has_waiting_room:
              pageText.includes('комната ожидания') ||
              pageText.includes('ожидайте разрешения') ||
              pageText.includes('организатор допустит') ||
              pageText.includes('waiting room') ||
              pageText.includes('wait for the host'),
            in_call_signal_count: inCallSignals,
            labels: controls.slice(0, 25)
          };
        }"""
    )


async def _click_visible_join_button(page, *, retry: bool = False) -> str | None:
    """Click a real visible meeting-join control, never the account Login button."""
    for selector in _JOIN_BUTTON_SELECTORS:
        try:
            buttons = page.locator(selector)
            count = await buttons.count()
            for index in range(min(count, 5)):
                button = buttons.nth(index)
                if not await button.is_visible():
                    continue
                await button.click(force=True, timeout=3_000)
                logger.info(
                    "%s Telemost join via %s",
                    "Retried" if retry else "Clicked",
                    selector,
                )
                return selector
        except Exception as exc:
            logger.info("Join selector %s failed: %s", selector, exc)
    return None


async def _join_meeting(
    page,
    url: str,
    *,
    scheduled_start: datetime | None = None,
) -> set[str]:
    debug_dir = Path("/tmp/recorder-debug")
    debug_dir.mkdir(exist_ok=True)

    async def snap(name: str):
        if not config.TELEMOST_DEBUG_SCREENSHOTS:
            return
        try:
            await asyncio.wait_for(
                page.screenshot(
                    path=str(debug_dir / f"{name}.png"),
                    full_page=True,
                    timeout=max(
                        1, config.TELEMOST_DEBUG_SCREENSHOT_TIMEOUT_SEC
                    ) * 1000,
                ),
                timeout=max(1, config.TELEMOST_DEBUG_SCREENSHOT_TIMEOUT_SEC) + 1,
            )
            logger.info("Screenshot saved: %s.png", name)
        except Exception as e:
            logger.warning("Screenshot %s failed: %s", name, e)

    await page.goto(url, timeout=30_000)
    await page.wait_for_timeout(3000)
    await snap("1-loaded")
    logger.info("Page URL after load: %s", page.url)
    if config.TELEMOST_DEBUG_SCREENSHOTS:
        try:
            page_title = await asyncio.wait_for(page.title(), timeout=5)
        except Exception as exc:
            page_title = f"<unavailable: {type(exc).__name__}>"
        logger.info("Page title: %s", page_title)

    # Fresh anonymous sessions sometimes show an app handoff page first.
    try:
        continue_buttons = page.locator(
            "button:has-text('Продолжить в браузере')"
        )
        for index in range(min(await continue_buttons.count(), 3)):
            button = continue_buttons.nth(index)
            if await button.is_visible():
                await button.click(force=True, timeout=3_000)
                logger.info("Continued to Telemost in browser")
                await page.wait_for_timeout(1_000)
                break
    except Exception:
        pass

    # Fill name
    name_filled = False
    for sel in [
        "input[placeholder*='имя']",
        "input[placeholder*='name']",
        "input[name='name']",
        "input[type='text']",
    ]:
        try:
            inputs = page.locator(sel)
            for index in range(min(await inputs.count(), 5)):
                inp = inputs.nth(index)
                if await inp.is_visible(timeout=1500):
                    await inp.fill("Protocaller", timeout=3000)
                    logger.info("Name filled via selector: %s", sel)
                    name_filled = True
                    break
            if name_filled:
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

    clicked_selector = await _click_visible_join_button(page)

    if not clicked_selector:
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

    # A successful click is not proof that Telemost admitted the guest. It may
    # publish the participant remotely before its own DOM switches to the call.
    # Keep the browser alive through the scheduled start/grace window and retry
    # only a real, visible meeting-join control (never the header account login).
    state: dict = {}
    wait_seconds = _join_wait_seconds(scheduled_start)
    deadline = time.monotonic() + wait_seconds
    last_click_at = time.monotonic() if clicked_selector else 0.0
    probe = 0
    waiting_logged = False
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        await page.wait_for_timeout(min(3_000, max(250, int(remaining * 1_000))))
        await _dismiss_media_modals(page)
        probe += 1
        try:
            state = await _telemost_call_state(page)
        except Exception as exc:
            state = {"probe_error": f"{type(exc).__name__}: {exc}"}
        logger.info("Telemost post-join probe %d: %s", probe, state)
        if _is_join_confirmed(state):
            break

        if state.get("has_waiting_room"):
            if not waiting_logged:
                logger.info(
                    "Telemost guest is waiting for organizer admission "
                    "(up to %.0fs total)",
                    wait_seconds,
                )
                waiting_logged = True
            continue

        if (
            time.monotonic() - last_click_at
            >= max(3, config.TELEMOST_JOIN_RETRY_SEC)
        ):
            retried = await _click_visible_join_button(page, retry=True)
            if retried:
                last_click_at = time.monotonic()
    else:
        await snap("3-join-failed")
        raise RuntimeError(
            f"Telemost join was not confirmed after {wait_seconds:.0f}s; "
            "visible state: "
            f"{str(state)[:1000]}"
        )

    await snap("3-after-join")
    logger.info("Page URL after verified join: %s", page.url)

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


def _mic_control_state(label: str) -> str:
    """Interpret a Telemost mic control. Labels describe the available action."""
    normalized = f" {(label or '').lower()} "
    if any(term in normalized for term in ("включить", "unmute", "turn on", "enable")):
        return "off"
    if any(
        term in normalized
        for term in ("выключить", "mute microphone", "turn off", "disable")
    ):
        return "on"
    if any(token in normalized for token in (" muted", " off", " disabled", " inactive")):
        return "off"
    if any(token in normalized for token in (" unmuted", " on", " enabled", " active")):
        return "on"
    return "unknown"


async def _dismiss_media_modals(page) -> None:
    for selector in [
        "button:has-text('Понятно')",
        "button:has-text('Хорошо')",
        "button:has-text('Продолжить')",
        "button:has-text('Закрыть')",
        "button:has-text('OK')",
        "button:has-text('Got it')",
        "button:has-text('Continue')",
        "button[aria-label*='Закрыть' i]",
        "button[aria-label*='Close' i]",
    ]:
        try:
            button = page.locator(selector).last
            if await button.count() and await button.is_visible():
                await button.click(force=True, timeout=2_000)
                await page.wait_for_timeout(500)
                logger.info("Dismissed media modal via %s", selector)
                return
        except Exception:
            continue


async def _set_bot_mic(page, *, enabled: bool) -> bool:
    """Set the in-call Telemost microphone to a verified state.

    Telemost can leave both pre-join and in-call controls in the DOM. Prefer a
    control whose nearby DOM also contains the Leave button, then verify that
    the action label flips after the click. Never report success after a blind
    click: a false positive here means participants hear silence.
    """
    try:
        toolbar = await page.evaluate(
            "() => { const b = [...document.querySelectorAll(\"button,[role='button']\")]"
            ".map(e => (e.getAttribute('aria-label')||e.getAttribute('title')||'').trim())"
            ".filter(Boolean); return b.slice(0, 40); }"
        )
        logger.info("Telemost controls (aria/title labels): %s", toolbar)
    except Exception:
        pass

    mic_terms = ("микроф", "microphone", " mic")
    clicked_opaque = False
    desired = "on" if enabled else "off"
    opposite = "off" if enabled else "on"
    for _ in range(4):
        await _dismiss_media_modals(page)
        controls = page.locator("button,[role='button']")
        candidates: list[tuple[int, int, object, str]] = []
        for index in range(await controls.count()):
            control = controls.nth(index)
            try:
                if not await control.is_visible():
                    continue
                label = " ".join(filter(None, [
                    await control.get_attribute("aria-label"),
                    await control.get_attribute("title"),
                    await control.get_attribute("data-testid"),
                    await control.get_attribute("data-state"),
                    await control.get_attribute("data-status"),
                    await control.get_attribute("aria-pressed"),
                    await control.get_attribute("class"),
                    (await control.text_content() or "")[:100],
                ])).strip()
                if not any(term in f" {label.lower()}" for term in mic_terms):
                    continue
                in_call = await control.evaluate(
                    """e => {
                      let p = e;
                      for (let i = 0; p && i < 7; i++, p = p.parentElement) {
                        const labels = [...p.querySelectorAll('button,[role="button"]')]
                          .map(x => `${x.getAttribute('aria-label') || ''} ${x.getAttribute('title') || ''}`.toLowerCase());
                        if (labels.some(x => x.includes('выйти') || x.includes('leave'))) return true;
                      }
                      return false;
                    }"""
                )
                box = await control.bounding_box()
                viewport = page.viewport_size or {"height": 0}
                lower_toolbar = bool(
                    box
                    and viewport.get("height")
                    and box["y"] >= viewport["height"] * 0.45
                )
                score = (100 if in_call else 0) + (10 if lower_toolbar else 0)
                candidates.append((score, index, control, label))
            except Exception:
                continue

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        opaque = None
        for score, _index, control, label in candidates:
            state = _mic_control_state(label)
            if state == desired:
                logger.info("Bot mic verified %s (%r, score=%d)", desired, label, score)
                return True
            if state == "unknown" and opaque is None:
                opaque = (score, control, label)
            if state != opposite:
                continue
            try:
                # Telemost can briefly leave an invisible overlay over the
                # in-call toolbar. The same forced click is already used by the
                # proven E2E join path and targets only a visible mic control.
                await control.click(force=True, timeout=3_000)
                logger.info(
                    "Bot mic switching %s via %r (score=%d)",
                    desired,
                    label,
                    score,
                )
                await page.wait_for_timeout(1_500)
                # Telemost replaces the toolbar node after a click, so don't
                # inspect the stale locator. Re-scan the live DOM next pass.
                break
            except Exception:
                continue
        else:
            if opaque is not None and not clicked_opaque:
                score, control, label = opaque
                try:
                    # Some Telemost builds expose only data-testid="mic-button"
                    # and no state. The caller invokes this setter only for a
                    # real state transition (muted→speaking or speaking→muted),
                    # so one guarded fallback click is deterministic.
                    await control.click(force=True, timeout=3_000)
                    logger.warning(
                        "Bot mic state opaque; one fallback click for %s via %r "
                        "(score=%d)",
                        desired,
                        label,
                        score,
                    )
                    await page.wait_for_timeout(1_500)
                    clicked_opaque = True
                    continue
                except Exception:
                    pass
            if opaque is not None and clicked_opaque:
                logger.warning(
                    "Bot mic remained opaque after one click; treating state as %s",
                    desired,
                )
                return True
            break

    logger.warning("Bot mic: could not verify state=%s", "ON" if enabled else "OFF")
    return False


async def _wait_for_meeting_end(
    page,
    initial_participants: set[str],
    scheduled_start=None,
    *,
    meeting_id: str | None = None,
) -> bool:
    """Block until the meeting ends. Returns whether any real participant (besides
    Protocaller) was ever present — False means nobody showed up (no_show)."""
    meeting_started = len(initial_participants) > 0
    empty_polls = 0
    deadline = time.monotonic() + config.MAX_RECORDING_HOURS * 3600
    # Grace period after scheduled start — wait this long for someone to arrive
    # before giving up on a meeting that never started
    GRACE_MINUTES = 10

    while True:
        if meeting_id and meeting_id in _e2e_finish_requested:
            _e2e_finish_requested.discard(meeting_id)
            logger.info("E2E finish requested for %s after verified speaker exit", meeting_id[:8])
            return True
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
    parec: asyncio.subprocess.Process | None = None

    try:
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
    except BaseException:
        # No child inherited the read end if parec itself could not be spawned.
        os.close(read_fd)
        raise

    try:
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
        except BaseException:
            # If ffmpeg cannot be spawned (including cancellation by the startup
            # deadline), do not leave parec running and holding the pipe forever.
            await _kill_if_alive(parec, "parec")
            raise
    finally:
        # Parent no longer needs the read end; ffmpeg inherited it
        os.close(read_fd)

    logger.info(
        "Audio capture started: parec pid=%s → ffmpeg pid=%s → %s",
        parec.pid, ffmpeg.pid, output_path,
    )
    return AudioCapture(parec, ffmpeg, output_path)


async def _confirm_audio_capture_started(
    cap: AudioCapture,
    audio_path: Path,
) -> None:
    """Require live subprocesses and a growing WAV before showing "Запись".

    Merely spawning parec/ffmpeg is not enough: either process can exit before
    producing useful bytes. PulseAudio continuously emits PCM (including
    silence), so a healthy capture grows within a few seconds even before
    anybody speaks.
    """
    timeout = max(1, config.AUDIO_START_CONFIRM_TIMEOUT_SEC)
    deadline = time.monotonic() + timeout
    previous_size = await fsio.size(audio_path)
    while time.monotonic() < deadline:
        if cap.parec.returncode is not None or cap.ffmpeg.returncode is not None:
            raise RuntimeError(
                "Аудиозапись не запустилась: процесс захвата завершился"
            )
        size = await fsio.size(audio_path)
        if previous_size > 10_000 and size > previous_size:
            return
        previous_size = size
        await asyncio.sleep(0.5)
    raise RuntimeError(
        f"Аудиозапись не запустилась: файл не растёт за {timeout} с"
    )


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

    if await fsio.size(mp3_path) < 1000:
        raise RuntimeError(f"MP3 output missing or too small: {mp3_path}")


# ── PulseAudio ────────────────────────────────────────────────────────────────

async def _create_pulse_sink(name: str) -> int | None:
    """Load a null-sink and return its module index (needed to unload it later —
    unloading is what stops PulseAudio leaking file descriptors across recordings)."""
    proc = await asyncio.create_subprocess_exec(
        "pactl", "load-module", "module-null-sink",
        f"sink_name={name}", f"sink_properties=device.description={name}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return int(out.decode().strip())
    except (ValueError, AttributeError):
        return None


async def _create_pulse_source(name: str, master_monitor: str) -> int:
    """Expose a sink monitor as a regular named microphone source.

    Chromium can capture a monitor-class Pulse source, but Telemost may refuse
    to unmute it as a microphone. A remapped source presents the same PCM as a
    normal WebRTC input device while the null sink stays the TTS target.
    """
    proc = await asyncio.create_subprocess_exec(
        "pactl",
        "load-module",
        "module-remap-source",
        f"master={master_monitor}",
        f"source_name={name}",
        f"source_properties=device.description={name}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-500:] if stderr else ""
        raise RuntimeError(
            f"could not create remapped microphone source ({proc.returncode}): {tail}"
        )
    try:
        return int(stdout.decode().strip())
    except (ValueError, AttributeError) as exc:
        raise RuntimeError("remapped microphone source returned no module id") from exc


async def _set_default_sink(name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "pactl", "set-default-sink", name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def _pin_browser_audio(sink_name: str) -> None:
    """Force this meeting's browser audio onto its own sink.

    Chromium doesn't reliably honour the PULSE_SINK env and can land on the global
    default — so with concurrent meetings one browser's audio could end up on
    another meeting's sink and bleed into its recording. Each recording browser is
    launched with a unique PULSE_SINK env; we match sink-inputs to this meeting via
    the owning process's environ and move them onto the correct sink."""
    try:
        needle = f"PULSE_SINK={sink_name}".encode()
        mypids: set[str] = set()
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/environ", "rb") as fh:
                    if needle in fh.read():
                        mypids.add(pid)
            except OSError:
                continue
        if not mypids:
            return

        proc = await asyncio.create_subprocess_exec(
            "pactl", "list", "sink-inputs",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        idx: str | None = None
        to_move: list[str] = []
        for raw in out.decode(errors="replace").splitlines():
            s = raw.strip()
            if s.startswith("Sink Input #"):
                idx = s.split("#", 1)[1].strip()
            elif idx and "application.process.id" in s and '"' in s:
                if s.split('"')[1] in mypids:
                    to_move.append(idx)
                idx = None
        for i in to_move:
            mv = await asyncio.create_subprocess_exec(
                "pactl", "move-sink-input", i, sink_name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await mv.wait()
    except Exception as e:  # noqa: BLE001 — best effort, never break recording
        logger.debug("pin audio %s: %s", sink_name, e)


async def _pin_browser_microphone(source_name: str) -> int:
    """Force Chromium's WebRTC capture stream onto its per-meeting bot mic."""
    try:
        sources = await asyncio.create_subprocess_exec(
            "pactl",
            "list",
            "sources",
            "short",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        sources_stdout, _ = await sources.communicate()
        target_source_index: str | None = None
        for raw in sources_stdout.decode(errors="replace").splitlines():
            columns = raw.split()
            if len(columns) >= 2 and columns[1] == source_name:
                target_source_index = columns[0]
                break

        needle = f"PULSE_SOURCE={source_name}".encode()
        mypids: set[str] = set()
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/environ", "rb") as source:
                    if needle in source.read():
                        mypids.add(pid)
            except OSError:
                continue

        proc = await asyncio.create_subprocess_exec(
            "pactl",
            "list",
            "source-outputs",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        current: str | None = None
        already_pinned: set[str] = set()
        owned_outputs: set[str] = set()
        for raw in stdout.decode(errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("Source Output #"):
                current = line.split("#", 1)[1].strip()
            elif current and line.startswith("Source:"):
                if (
                    target_source_index is not None
                    and line.split(":", 1)[1].strip() == target_source_index
                ):
                    already_pinned.add(current)
            elif current and "application.process.id" in line and '"' in line:
                if line.split('"')[1] in mypids:
                    owned_outputs.add(current)
        pinned = len(already_pinned)
        for source_output in owned_outputs - already_pinned:
            move = await asyncio.create_subprocess_exec(
                "pactl",
                "move-source-output",
                source_output,
                source_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await move.wait()
            if move.returncode != 0:
                logger.warning(
                    "Could not pin source-output %s to %s",
                    source_output,
                    source_name,
                )
            else:
                pinned += 1
        return pinned
    except Exception as exc:
        logger.debug("pin microphone %s: %s", source_name, exc)
        return 0


async def _audio_pin_loop(
    sink_name: str,
    source_name: str | None = None,
) -> None:
    """Re-pin the browser's audio periodically — the audio stream appears a few
    seconds after joining and Chromium may recreate it mid-meeting."""
    try:
        while True:
            await _pin_browser_audio(sink_name)
            if source_name:
                await _pin_browser_microphone(source_name)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass


async def _find_sink_module(sink_name: str) -> int | None:
    """Resolve a loaded null-sink's module index by its sink_name (fallback when
    the index wasn't captured at load time)."""
    proc = await asyncio.create_subprocess_exec(
        "pactl", "list", "modules", "short",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    for line in out.decode(errors="replace").splitlines():
        if "module-null-sink" in line and f"sink_name={sink_name}" in line:
            try:
                return int(line.split()[0])
            except (ValueError, IndexError):
                continue
    return None


async def _delete_pulse_sink(name: str, module_id: int | None = None) -> None:
    """Unload the meeting's null-sink module. `pactl unload-module` takes a module
    INDEX (or name) — NOT `sink_name=…` (that silently failed and leaked FDs until
    PulseAudio hit its open-file limit and stopped creating sinks). Prefer the
    index captured at load; fall back to resolving it by sink_name."""
    if module_id is None:
        module_id = await _find_sink_module(name)
    if module_id is None:
        logger.warning("Pulse module for sink %s not found — not unloaded", name)
        return
    proc = await asyncio.create_subprocess_exec(
        "pactl", "unload-module", str(module_id),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def _unload_pulse_module(module_id: int, label: str) -> None:
    """Unload a PulseAudio module whose creation returned an exact index."""
    proc = await asyncio.create_subprocess_exec(
        "pactl",
        "unload-module",
        str(module_id),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "Pulse module %s (%s) could not be unloaded: %s",
            module_id,
            label,
            stderr.decode(errors="replace")[-300:] if stderr else "",
        )


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

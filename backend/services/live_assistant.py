"""Live in-meeting assistant: listen for the wake word, answer the question.

Runs ONLY when config.LIVE_ASSISTANT_ENABLED. Fully isolated: a failure here is
logged and never propagates to the recording pipeline. Taps the room audio with
a SECOND `parec` on the sink monitor (multiple readers allowed), so the primary
WAV capture is untouched.

Phase 0–2 (this module): listen → wake word → question → answer (saved to
live_qa + logged). Speaking the answer into the meeting (TTS) is Phase 3 and is
injected via the optional `speak` callback.
"""
import asyncio
import logging
import math
import sys
import time
from array import array
from collections import deque
from datetime import datetime, timezone
from typing import Awaitable, Callable

from config import config
from database import models
from services import public_info, qa_engine
from services.transcriber import transcribe_openai_pcm, transcribe_pcm

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_BYTES_PER_SEC = _SAMPLE_RATE * 2  # s16le mono
_LIVE_TRANSCRIPT_MAX_CHARS = 20_000
_COOLDOWN_SEC = 3.0  # ignore wake right after answering
_diagnostics: dict[str, dict] = {}
_MAX_DIAGNOSTICS = 100


def _diagnostic_update(meeting_id: str, **values) -> None:
    if meeting_id not in _diagnostics and len(_diagnostics) >= _MAX_DIAGNOSTICS:
        oldest = next(iter(_diagnostics))
        _diagnostics.pop(oldest, None)
    current = _diagnostics.setdefault(
        meeting_id,
        {
            "status": "starting",
            "bytes_received": 0,
            "windows_transcribed": 0,
            "silent_windows_skipped": 0,
            "last_rms": 0,
            "last_text": "",
            "last_error": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    current.update(values)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()


def get_live_diagnostic(meeting_id: str) -> dict | None:
    """Return a copy of bounded, admin-only runtime diagnostics."""
    item = _diagnostics.get(meeting_id)
    return dict(item) if item else None


class RollingPCMBuffer:
    """Bounded 16 kHz mono PCM history used for meeting-only answers."""

    def __init__(self, seconds: int):
        self.max_bytes = max(1, seconds) * _BYTES_PER_SEC
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self.total_bytes = 0

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._chunks.append(chunk)
        self._size += len(chunk)
        self.total_bytes += len(chunk)
        while self._size > self.max_bytes and self._chunks:
            overflow = self._size - self.max_bytes
            first = self._chunks[0]
            if len(first) <= overflow:
                self._chunks.popleft()
                self._size -= len(first)
            else:
                self._chunks[0] = first[overflow:]
                self._size -= overflow

    def tail(self, seconds: int | None = None) -> bytes:
        wanted = self._size if seconds is None else max(1, seconds) * _BYTES_PER_SEC
        selected: list[bytes] = []
        total = 0
        for chunk in reversed(self._chunks):
            selected.append(chunk)
            total += len(chunk)
            if total >= wanted:
                break
        return b"".join(reversed(selected))[-wanted:]

    def range(self, start_offset: int, max_bytes: int) -> bytes:
        """Return retained PCM starting at an absolute stream byte offset."""
        retained_start = self.total_bytes - self._size
        relative_start = max(0, start_offset - retained_start)
        data = b"".join(self._chunks)
        return data[relative_start:relative_start + max(0, max_bytes)]

    def __len__(self) -> int:
        return self._size


def merge_live_transcript(previous: str, current: str) -> str:
    """Merge overlapping STT windows without repeating their shared words."""
    left = (previous or "").split()
    right = (current or "").split()
    overlap = 0
    for size in range(min(len(left), len(right), 20), 0, -1):
        if [w.lower().strip(".,!?") for w in left[-size:]] == [
            w.lower().strip(".,!?") for w in right[:size]
        ]:
            overlap = size
            break
    merged = " ".join(left + right[overlap:]).strip()
    return merged[-_LIVE_TRANSCRIPT_MAX_CHARS:]


def pcm_rms(pcm: bytes) -> int:
    """Return the RMS level of little-endian signed 16-bit mono PCM."""
    if len(pcm) < 2:
        return 0
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return int(math.sqrt(mean_square))


def trailing_pcm_is_silent(
    pcm: bytes,
    seconds: float,
    min_rms: int,
) -> bool:
    """Whether the end of a PCM buffer contains only near-silence."""
    wanted = max(2, int(seconds * _BYTES_PER_SEC))
    wanted -= wanted % 2
    tail = pcm[-wanted:]
    return len(tail) >= wanted and pcm_rms(tail) < min_rms


def pcm_contains_speech(pcm: bytes, min_rms: int) -> bool:
    """Detect any voiced quarter-second inside a longer mostly-silent tail."""
    chunk_bytes = _BYTES_PER_SEC // 4
    return any(
        pcm_rms(pcm[offset:offset + chunk_bytes]) >= min_rms
        for offset in range(0, len(pcm), chunk_bytes)
    )


async def capture_question_audio(
    rolling: RollingPCMBuffer,
    reader_task,
    question_start: int,
    window_end_offset: int,
    wake_text: str,
    max_bytes: int,
) -> bytes:
    """Capture until speech ends instead of always waiting for the hard limit."""
    max_end = question_start + max_bytes
    wake_question = qa_engine.strip_assistant_command(
        wake_text,
        config.LIVE_WAKE_WORD,
        config.LIVE_WAKE_COMMAND,
    )
    is_empty_note, note_body = qa_engine.parse_note_command(wake_question)
    needs_followup = not wake_question.strip() or (
        is_empty_note and not note_body
    )
    min_wait = (
        config.LIVE_QUESTION_WAKE_ONLY_WAIT_SEC
        if needs_followup
        else config.LIVE_QUESTION_MIN_WAIT_SEC
    )

    while not reader_task.done():
        current_end = min(rolling.total_bytes, max_end)
        available_bytes = max(0, current_end - question_start)
        audio = rolling.range(question_start, available_bytes)
        after_window_sec = max(
            0.0,
            (current_end - window_end_offset) / _BYTES_PER_SEC,
        )
        if (
            after_window_sec >= min_wait
            and trailing_pcm_is_silent(
                audio,
                config.LIVE_QUESTION_SILENCE_SEC,
                config.LIVE_MIN_RMS,
            )
        ):
            return audio
        if rolling.total_bytes >= max_end:
            break
        await asyncio.sleep(0.1)

    available_bytes = min(max_bytes, max(0, rolling.total_bytes - question_start))
    return rolling.range(question_start, available_bytes)


async def _audio_reader(
    stream,
    rolling: RollingPCMBuffer,
    meeting_id: str,
) -> None:
    """Continuously drain parec directly into history.

    Cloud STT may take longer than LIVE_POLL_SEC. A bounded queue here used to
    overflow and splice non-contiguous chunks together, making clear speech look
    like noise. Keeping the rolling buffer current lets STT always sample one
    continuous recent window, regardless of request latency.
    """
    while True:
        chunk = await stream.read(_BYTES_PER_SEC)
        if not chunk:
            return
        rolling.append(chunk)
        _diagnostic_update(meeting_id, bytes_received=rolling.total_bytes)


async def transcribe_question(pcm: bytes) -> str:
    """Use cloud STT for proper nouns/latency, falling back to isolated local STT."""
    if config.LIVE_QUESTION_STT.lower() == "openai":
        try:
            return await transcribe_openai_pcm(
                pcm,
                config.LIVE_QUESTION_STT_MODEL,
                prompt=(
                    f"{config.LIVE_WAKE_WORD}, {config.LIVE_WAKE_COMMAND}. "
                    f"{config.LIVE_WAKE_WORD}, запиши."
                ),
            )
        except Exception as exc:
            logger.warning("OpenAI question STT failed; using local model: %s", exc)
    return await transcribe_pcm(pcm, config.LIVE_QUESTION_MODEL, beam_size=3)


async def transcribe_wake_window(pcm: bytes) -> str:
    """Recognize a wake-word window accurately, with an isolated local fallback."""
    if config.LIVE_WAKE_STT.lower() == "openai":
        try:
            return await transcribe_openai_pcm(
                pcm,
                config.LIVE_WAKE_STT_MODEL,
                prompt=(
                    f"{config.LIVE_WAKE_WORD}, {config.LIVE_WAKE_COMMAND}. "
                    f"{config.LIVE_WAKE_WORD}, запиши."
                ),
            )
        except Exception as exc:
            logger.warning("OpenAI wake STT failed; using local model: %s", exc)
    return await transcribe_pcm(pcm, config.LIVE_WAKE_MODEL, beam_size=1)


async def run_live_assistant(
    meeting_id: str,
    sink_name: str,
    *,
    speak: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Listen on the meeting sink and answer wake-word questions until cancelled."""
    proc = None
    reader_task = None
    _diagnostic_update(meeting_id, status="starting", sink=f"{sink_name}.monitor")
    try:
        proc = await asyncio.create_subprocess_exec(
            "parec",
            f"--device={sink_name}.monitor",
            "--format=s16le", "--rate=16000", "--channels=1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        _diagnostic_update(meeting_id, status="listening", parec_pid=proc.pid)
        logger.info("Live assistant listening on %s.monitor (meeting %s)", sink_name, meeting_id[:8])

        window_bytes = max(1, config.LIVE_WINDOW_SEC) * _BYTES_PER_SEC
        poll_bytes = max(1, config.LIVE_POLL_SEC) * _BYTES_PER_SEC
        question_bytes = max(1, config.LIVE_QUESTION_MAX_SEC) * _BYTES_PER_SEC

        rolling = RollingPCMBuffer(max(1, config.LIVE_BUFFER_MIN) * 60)
        reader_task = asyncio.create_task(
            _audio_reader(proc.stdout, rolling, meeting_id),
            name=f"live-audio-{meeting_id[:8]}",
        )
        last_polled_bytes = 0
        live_transcript = ""
        mute_until = 0.0

        while True:
            if reader_task.done():
                _diagnostic_update(
                    meeting_id,
                    status="audio_source_ended",
                    parec_returncode=proc.returncode,
                )
                break
            if (
                len(rolling) < window_bytes
                or rolling.total_bytes - last_polled_bytes < poll_bytes
            ):
                await asyncio.sleep(0.1)
                continue

            segment = rolling.tail(config.LIVE_WINDOW_SEC)
            window_end_offset = rolling.total_bytes
            last_polled_bytes = window_end_offset

            if time.monotonic() < mute_until:
                continue

            rms = pcm_rms(segment)
            if rms < config.LIVE_MIN_RMS:
                _diagnostic_update(
                    meeting_id,
                    last_rms=rms,
                    last_text="",
                    silent_windows_skipped=(
                        _diagnostics[meeting_id]["silent_windows_skipped"] + 1
                    ),
                )
                continue

            try:
                raw_text = await transcribe_wake_window(segment)
            except Exception as e:
                _diagnostic_update(
                    meeting_id,
                    windows_transcribed=_diagnostics[meeting_id]["windows_transcribed"] + 1,
                    last_error=f"{type(e).__name__}: {e}"[:500],
                )
                logger.warning("Live wake STT failed (%s): %s", meeting_id[:8], e)
                continue
            text = qa_engine.clean_live_transcript(raw_text)
            _diagnostic_update(
                meeting_id,
                windows_transcribed=_diagnostics[meeting_id]["windows_transcribed"] + 1,
                last_rms=rms,
                last_error=None,
                last_text=text[-300:] if text else "",
            )
            if not text:
                continue

            # Accumulate a rolling live transcript (for meeting_only scope).
            live_transcript = merge_live_transcript(live_transcript, text)

            if not qa_engine.contains_assistant_command(
                text,
                config.LIVE_WAKE_WORD,
                config.LIVE_WAKE_COMMAND,
            ):
                continue

            logger.info("Live assistant wake detected (%s): %r", meeting_id[:8], text)
            _diagnostic_update(meeting_id, status="wake_detected")
            # Capture the question: this window (has wake word + maybe start of
            # question) plus the following audio up to LIVE_QUESTION_MAX_SEC.
            question_start = window_end_offset - len(segment)
            question_audio = await capture_question_audio(
                rolling,
                reader_task,
                question_start,
                window_end_offset,
                text,
                question_bytes,
            )
            _diagnostic_update(
                meeting_id,
                question_audio_sec=round(
                    len(question_audio) / _BYTES_PER_SEC,
                    2,
                ),
            )

            await _handle_question(
                meeting_id,
                question_audio,
                live_transcript,
                speak,
                wake_text=text,
                wake_window_bytes=len(segment),
            )
            _diagnostic_update(meeting_id, status="listening")
            mute_until = time.monotonic() + _COOLDOWN_SEC

    except asyncio.CancelledError:
        _diagnostic_update(meeting_id, status="cancelled")
        raise
    except Exception as e:
        _diagnostic_update(
            meeting_id,
            status="error",
            last_error=f"{type(e).__name__}: {e}"[:500],
        )
        logger.error("Live assistant crashed (%s): %s", meeting_id[:8], e, exc_info=True)
    finally:
        if reader_task:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        _diagnostic_update(
            meeting_id,
            parec_returncode=proc.returncode if proc else None,
            ended_at=datetime.now(timezone.utc).isoformat(),
        )


async def speak_text(text: str, sink_name: str) -> bool:
    """Synthesize `text` and play it into `sink_name` (the bot's mic sink), so
    Telemost transmits it to participants. OpenAI TTS with an espeak-ng fallback.
    Returns True only when synthesis and PulseAudio playback both succeed."""
    import inspect
    import os
    import tempfile
    wav_path = None
    try:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_")
        os.close(fd)
        ok = False
        if config.LIVE_TTS == "openai":
            try:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
                resp = await asyncio.wait_for(
                    client.audio.speech.create(
                        model=config.LIVE_TTS_MODEL,
                        voice=config.LIVE_TTS_VOICE,
                        input=text,
                        response_format="wav",
                    ),
                    timeout=45,
                )
                audio = resp.read()
                if inspect.isawaitable(audio):
                    audio = await audio
                with open(wav_path, "wb") as f:
                    f.write(audio)
                ok = os.path.getsize(wav_path) > 1_000
            except Exception as e:
                logger.warning("OpenAI TTS failed, falling back to espeak: %s", e)
        if not ok:  # espeak-ng fallback (offline, already in the image)
            p = await asyncio.create_subprocess_exec(
                "espeak-ng", "-v", "ru", "-s", "160", "-w", wav_path, text,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(p.wait(), timeout=30)
            if p.returncode != 0 or os.path.getsize(wav_path) <= 1_000:
                raise RuntimeError(f"espeak-ng failed with exit code {p.returncode}")
        # Play into the bot's mic sink.
        p = await asyncio.create_subprocess_exec(
            "paplay", f"--device={sink_name}", wav_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(p.communicate(), timeout=120)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
            raise RuntimeError("paplay timed out")
        if p.returncode != 0:
            tail = stderr.decode(errors="replace")[-500:] if stderr else ""
            raise RuntimeError(f"paplay exited {p.returncode}: {tail}")
        logger.info("Live assistant spoke answer into %s (%d chars)", sink_name, len(text))
        return True
    except Exception as e:
        logger.error("speak_text failed: %s", e)
        return False
    finally:
        if wav_path:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


async def _handle_question(
    meeting_id: str,
    audio: bytes,
    live_transcript: str,
    speak: Callable[[str], Awaitable[None]] | None,
    *,
    wake_text: str = "",
    wake_window_bytes: int = 0,
) -> None:
    started = time.monotonic()
    question = ""
    answer = None
    scope = "unknown"
    sources: list[str] = []
    source_details: list[dict] = []
    search_query = ""
    saved = False
    try:
        # For a short question already complete inside the validated wake window,
        # reuse that cloud transcription instead of uploading the same audio a
        # second time. If speech continued after the window, transcribe the full
        # captured question for accuracy.
        wake_question = qa_engine.strip_assistant_command(
            wake_text,
            config.LIVE_WAKE_WORD,
            config.LIVE_WAKE_COMMAND,
        )
        followup_audio = (
            audio[wake_window_bytes:]
            if wake_window_bytes > 0 and wake_window_bytes <= len(audio)
            else audio
        )
        reuse_wake_text = bool(
            wake_question
            and not pcm_contains_speech(followup_audio, config.LIVE_MIN_RMS)
        )
        stt_started = time.monotonic()
        raw = wake_text if reuse_wake_text else await transcribe_question(audio)
        _diagnostic_update(
            meeting_id,
            question_stt_ms=int((time.monotonic() - stt_started) * 1_000),
            question_stt_reused=reuse_wake_text,
        )
        question = qa_engine.strip_assistant_command(
            raw,
            config.LIVE_WAKE_WORD,
            config.LIVE_WAKE_COMMAND,
        )
        if not question or len(question) < 3:
            logger.info(
                "Live assistant: empty question after wake (%s) — skipping",
                meeting_id[:8],
            )
            return

        is_note, note_text = qa_engine.parse_note_command(question)
        if is_note:
            scope = "note"
            if note_text:
                note_text = note_text[:2_000]
                await models.save_live_note(meeting_id, note_text)
                answer = "Записал. Заметка попадёт в итоговый протокол."
                sources = ["meeting_note"]
                source_details = [{
                    "source": "meeting_note",
                    "label": "Продиктованная заметка",
                    "snippet": note_text,
                    "used": True,
                }]
            else:
                answer = "Что именно записать?"
            await _speak_and_save_answer(
                meeting_id,
                question,
                answer,
                scope,
                sources,
                source_details,
                search_query,
                started,
                speak,
            )
            saved = True
            return

        meeting = await models.get_meeting(meeting_id)
        route = public_info.classify_public_question(question)
        if route == "ambiguous":
            scope = "ambiguous"
            answer = (
                "Уточните, пожалуйста: искать ответ в рабочих данных "
                "или в публичных источниках?"
            )
            await _speak_and_save_answer(
                meeting_id,
                question,
                answer,
                scope,
                sources,
                source_details,
                search_query,
                started,
                speak,
            )
            saved = True
            return

        if route == "public":
            scope = "public"
            sources = ["web"]
            public_enabled = bool(
                config.LIVE_PUBLIC_INFO_ENABLED
                and meeting
                and meeting.get("assistant_public_info_enabled")
            )
            public_error = None
            if not public_enabled:
                answer = "Публичные источники для этой встречи выключены."
            else:
                try:
                    answer, source_details = (
                        await public_info.answer_public_question(question)
                    )
                except Exception as exc:
                    public_error = f"{type(exc).__name__}: {exc}"[:1_000]
                    logger.exception(
                        "Public live answer failed (%s)",
                        meeting_id[:8],
                    )
                    answer = (
                        "Сейчас не удалось получить актуальные публичные данные. "
                        "Попробуйте повторить вопрос чуть позже."
                    )
            await _speak_and_save_answer(
                meeting_id,
                question,
                answer,
                scope,
                sources,
                source_details,
                search_query,
                started,
                speak,
                initial_error=public_error,
            )
            saved = True
            return

        search_query = qa_engine.build_search_query(question)
        # Scope: external attendees → meeting_only, unless host opted into full.
        attendees, full_override = await asyncio.gather(
            models.get_meeting_attendee_emails(meeting_id),
            models.get_meeting_full_access(meeting_id),
        )
        scope = qa_engine.select_scope(
            attendees,
            config.ALLOWED_DOMAIN,
            full_override,
        )
        meeting_metadata = qa_engine.format_meeting_metadata(meeting, attendees)

        # The overlapping wake windows already maintain a rolling transcript.
        # Re-transcribing up to three minutes here made every meeting-only answer
        # wait on another large STT request. Use the accumulated text directly.
        context_transcript = live_transcript

        answer, sources, source_details, search_query = (
            await qa_engine.answer_question(
                question,
                scope=scope,
                live_transcript=context_transcript,
                meeting_metadata=meeting_metadata,
                budget=config.LIVE_CONTEXT_MAX_CHARS,
            )
        )
        logger.info(
            "Live assistant Q (%s, scope=%s): %r → A: %r",
            meeting_id[:8],
            scope,
            question,
            answer,
        )

        await _speak_and_save_answer(
            meeting_id,
            question,
            answer,
            scope,
            sources,
            source_details,
            search_query,
            started,
            speak,
        )
        saved = True
    except Exception as e:
        logger.error("Live assistant question handling failed (%s): %s", meeting_id[:8], e, exc_info=True)
        if question and not saved:
            try:
                await models.save_live_qa(
                    meeting_id,
                    question,
                    answer,
                    scope,
                    sources,
                    spoken=False,
                    latency_ms=int((time.monotonic() - started) * 1_000),
                    error=f"{type(e).__name__}: {e}"[:1_000],
                    search_query=search_query,
                    source_details=source_details,
                )
            except Exception:
                logger.exception(
                    "Could not persist failed live-assistant question (%s)",
                    meeting_id[:8],
                )


async def _speak_and_save_answer(
    meeting_id: str,
    question: str,
    answer: str | None,
    scope: str,
    sources: list[str],
    source_details: list[dict],
    search_query: str,
    started: float,
    speak: Callable[[str], Awaitable[None]] | None,
    *,
    initial_error: str | None = None,
) -> None:
    """Deliver one answer and persist the same auditable result for every route."""
    spoken = False
    error = initial_error
    _diagnostic_update(
        meeting_id,
        answer_ready_ms=int((time.monotonic() - started) * 1_000),
    )
    if answer and speak is not None:
        try:
            await speak(answer)
            spoken = True
        except Exception as exc:
            speech_error = f"{type(exc).__name__}: {exc}"
            error = f"{error}; {speech_error}" if error else speech_error
            logger.error(
                "Live assistant speech failed (%s): %s",
                meeting_id[:8],
                speech_error,
            )
    await models.save_live_qa(
        meeting_id,
        question,
        answer,
        scope,
        sources,
        spoken=spoken,
        latency_ms=int((time.monotonic() - started) * 1_000),
        error=error,
        search_query=search_query,
        source_details=source_details,
    )
    _diagnostic_update(
        meeting_id,
        total_latency_ms=int((time.monotonic() - started) * 1_000),
    )

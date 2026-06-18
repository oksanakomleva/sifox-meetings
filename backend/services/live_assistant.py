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
import time
from typing import Awaitable, Callable

from config import config
from database import models
from services import qa_engine
from services.transcriber import transcribe_pcm

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_BYTES_PER_SEC = _SAMPLE_RATE * 2  # s16le mono
_LIVE_TRANSCRIPT_MAX_CHARS = 20_000
_COOLDOWN_SEC = 3.0  # ignore wake right after answering


async def run_live_assistant(
    meeting_id: str,
    sink_name: str,
    *,
    speak: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Listen on the meeting sink and answer wake-word questions until cancelled."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "parec",
            f"--device={sink_name}.monitor",
            "--format=s16le", "--rate=16000", "--channels=1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("Live assistant listening on %s.monitor (meeting %s)", sink_name, meeting_id[:8])

        window_bytes = max(1, config.LIVE_WINDOW_SEC) * _BYTES_PER_SEC
        question_bytes = max(1, config.LIVE_QUESTION_MAX_SEC) * _BYTES_PER_SEC

        window = bytearray()
        live_transcript = ""
        mute_until = 0.0

        while True:
            chunk = await proc.stdout.read(_BYTES_PER_SEC)  # ~1s
            if not chunk:
                break
            window.extend(chunk)
            if len(window) < window_bytes:
                continue

            segment = bytes(window)
            window = bytearray()

            if time.monotonic() < mute_until:
                continue

            try:
                text = await transcribe_pcm(segment, config.LIVE_WAKE_MODEL, beam_size=1)
            except Exception as e:
                logger.warning("Live wake STT failed (%s): %s", meeting_id[:8], e)
                continue
            if not text:
                continue

            # Accumulate a rolling live transcript (for meeting_only scope).
            live_transcript = (live_transcript + " " + text)[-_LIVE_TRANSCRIPT_MAX_CHARS:]

            if not qa_engine.contains_wake_word(text, config.LIVE_WAKE_WORD):
                continue

            logger.info("Live assistant wake detected (%s): %r", meeting_id[:8], text)
            # Capture the question: this window (has wake word + maybe start of
            # question) plus the following audio up to LIVE_QUESTION_MAX_SEC.
            qbuf = bytearray(segment)
            while len(qbuf) < question_bytes:
                more = await proc.stdout.read(_BYTES_PER_SEC)
                if not more:
                    break
                qbuf.extend(more)

            await _handle_question(meeting_id, bytes(qbuf), live_transcript, speak)
            mute_until = time.monotonic() + _COOLDOWN_SEC

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("Live assistant crashed (%s): %s", meeting_id[:8], e, exc_info=True)
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass


async def speak_text(text: str, sink_name: str) -> None:
    """Synthesize `text` and play it into `sink_name` (the bot's mic sink), so
    Telemost transmits it to participants. OpenAI TTS with an espeak-ng fallback.
    Best-effort: any failure is logged, never raised."""
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
                resp = await client.audio.speech.create(
                    model=config.LIVE_TTS_MODEL, voice=config.LIVE_TTS_VOICE,
                    input=text, response_format="wav",
                )
                with open(wav_path, "wb") as f:
                    f.write(resp.read())
                ok = True
            except Exception as e:
                logger.warning("OpenAI TTS failed, falling back to espeak: %s", e)
        if not ok:  # espeak-ng fallback (offline, already in the image)
            p = await asyncio.create_subprocess_exec(
                "espeak-ng", "-v", "ru", "-s", "160", "-w", wav_path, text,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await p.wait()
        # Play into the bot's mic sink.
        p = await asyncio.create_subprocess_exec(
            "paplay", f"--device={sink_name}", wav_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await p.wait()
        logger.info("Live assistant spoke answer into %s (%d chars)", sink_name, len(text))
    except Exception as e:
        logger.error("speak_text failed: %s", e)
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
) -> None:
    try:
        raw = await transcribe_pcm(audio, config.LIVE_QUESTION_MODEL, beam_size=3)
        question = qa_engine.strip_wake_word(raw, config.LIVE_WAKE_WORD)
        if not question or len(question) < 3:
            logger.info("Live assistant: empty question after wake (%s) — skipping", meeting_id[:8])
            return

        # Scope: external attendees → meeting_only, unless host opted into full.
        attendees = await models.get_meeting_attendee_emails(meeting_id)
        full_override = await models.get_meeting_full_access(meeting_id)
        scope = qa_engine.select_scope(attendees, config.ALLOWED_DOMAIN, full_override)

        answer, sources = await qa_engine.answer_question(
            question, scope=scope, live_transcript=live_transcript,
            budget=config.LIVE_CONTEXT_MAX_CHARS,
        )
        logger.info("Live assistant Q (%s, scope=%s): %r → A: %r", meeting_id[:8], scope, question, answer)
        await models.save_live_qa(meeting_id, question, answer, scope, sources)

        if answer and speak is not None:
            await speak(answer)  # Phase 3: voice into the meeting
    except Exception as e:
        logger.error("Live assistant question handling failed (%s): %s", meeting_id[:8], e, exc_info=True)

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
from collections import deque
from typing import Awaitable, Callable

from config import config
from database import models
from services import qa_engine
from services.transcriber import transcribe_openai_pcm, transcribe_pcm

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_BYTES_PER_SEC = _SAMPLE_RATE * 2  # s16le mono
_LIVE_TRANSCRIPT_MAX_CHARS = 20_000
_COOLDOWN_SEC = 3.0  # ignore wake right after answering


class RollingPCMBuffer:
    """Bounded 16 kHz mono PCM history used for meeting-only answers."""

    def __init__(self, seconds: int):
        self.max_bytes = max(1, seconds) * _BYTES_PER_SEC
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._chunks.append(chunk)
        self._size += len(chunk)
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


async def _audio_reader(stream, queue: asyncio.Queue[bytes]) -> None:
    """Continuously drain parec so STT/LLM latency cannot block room capture."""
    try:
        while True:
            chunk = await stream.read(_BYTES_PER_SEC)
            if not chunk:
                return
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(chunk)
    finally:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(b"")


async def transcribe_question(pcm: bytes) -> str:
    """Use cloud STT for proper nouns/latency, falling back to isolated local STT."""
    if config.LIVE_QUESTION_STT.lower() == "openai":
        try:
            return await transcribe_openai_pcm(
                pcm,
                config.LIVE_QUESTION_STT_MODEL,
                prompt=config.LIVE_WAKE_WORD,
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
                prompt=config.LIVE_WAKE_WORD,
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
        poll_bytes = max(1, config.LIVE_POLL_SEC) * _BYTES_PER_SEC
        question_bytes = max(1, config.LIVE_QUESTION_MAX_SEC) * _BYTES_PER_SEC

        rolling = RollingPCMBuffer(max(1, config.LIVE_BUFFER_MIN) * 60)
        audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
        reader_task = asyncio.create_task(
            _audio_reader(proc.stdout, audio_queue),
            name=f"live-audio-{meeting_id[:8]}",
        )
        bytes_since_poll = 0
        live_transcript = ""
        mute_until = 0.0

        while True:
            chunk = await audio_queue.get()
            if not chunk:
                break
            rolling.append(chunk)
            bytes_since_poll += len(chunk)
            if len(rolling) < window_bytes or bytes_since_poll < poll_bytes:
                continue

            segment = rolling.tail(config.LIVE_WINDOW_SEC)
            bytes_since_poll = 0

            if time.monotonic() < mute_until:
                continue

            try:
                text = await transcribe_wake_window(segment)
            except Exception as e:
                logger.warning("Live wake STT failed (%s): %s", meeting_id[:8], e)
                continue
            if not text:
                continue

            # Accumulate a rolling live transcript (for meeting_only scope).
            live_transcript = merge_live_transcript(live_transcript, text)

            if not qa_engine.contains_wake_word(text, config.LIVE_WAKE_WORD):
                continue

            logger.info("Live assistant wake detected (%s): %r", meeting_id[:8], text)
            # Capture the question: this window (has wake word + maybe start of
            # question) plus the following audio up to LIVE_QUESTION_MAX_SEC.
            qbuf = bytearray(segment)
            while len(qbuf) < question_bytes:
                more = await audio_queue.get()
                if not more:
                    break
                qbuf.extend(more)
                rolling.append(more)

            await _handle_question(
                meeting_id,
                bytes(qbuf),
                live_transcript,
                rolling.tail(config.LIVE_CONTEXT_AUDIO_SEC),
                speak,
            )
            mute_until = time.monotonic() + _COOLDOWN_SEC

    except asyncio.CancelledError:
        raise
    except Exception as e:
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
    recent_audio: bytes,
    speak: Callable[[str], Awaitable[None]] | None,
) -> None:
    started = time.monotonic()
    question = ""
    answer = None
    scope = "unknown"
    sources: list[str] = []
    saved = False
    try:
        raw = await transcribe_question(audio)
        question = qa_engine.strip_wake_word(raw, config.LIVE_WAKE_WORD)
        if not question or len(question) < 3:
            logger.info("Live assistant: empty question after wake (%s) — skipping", meeting_id[:8])
            return

        # Scope: external attendees → meeting_only, unless host opted into full.
        attendees = await models.get_meeting_attendee_emails(meeting_id)
        full_override = await models.get_meeting_full_access(meeting_id)
        scope = qa_engine.select_scope(attendees, config.ALLOWED_DOMAIN, full_override)

        context_transcript = live_transcript
        if scope == "meeting_only" and recent_audio:
            accurate_context = await transcribe_question(recent_audio)
            if accurate_context:
                context_transcript = accurate_context

        answer, sources = await qa_engine.answer_question(
            question, scope=scope, live_transcript=context_transcript,
            budget=config.LIVE_CONTEXT_MAX_CHARS,
        )
        logger.info("Live assistant Q (%s, scope=%s): %r → A: %r", meeting_id[:8], scope, question, answer)

        spoken = False
        speech_error = None
        if answer and speak is not None:
            try:
                await speak(answer)  # Phase 3: voice into the meeting
                spoken = True
            except Exception as exc:
                speech_error = f"{type(exc).__name__}: {exc}"
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
            error=speech_error,
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
                )
            except Exception:
                logger.exception(
                    "Could not persist failed live-assistant question (%s)",
                    meeting_id[:8],
                )

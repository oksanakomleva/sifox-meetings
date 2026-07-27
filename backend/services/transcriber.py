"""faster-whisper transcription — same approach as telemost-bot."""
import asyncio
import io
import json
import logging
import os
import sys
import uuid
import wave
from contextlib import suppress
from dataclasses import dataclass

from config import config

logger = logging.getLogger(__name__)

_transcribe_semaphore = asyncio.Semaphore(1)  # one at a time to prevent OOM
_openai_stt_client = None

# Post-meeting transcription runs faster-whisper in a SEPARATE PROCESS
# (transcribe_worker.py). A native hang/deadlock in CTranslate2 during inference
# used to hold the GIL and freeze the whole event loop → total service outage
# (2026-07-22). A subprocess cannot do that, and we can kill it on timeout.
# Generous cap: medium/int8 runs several× faster than realtime, so this covers
# multi-hour recordings while still bounding a truly wedged worker.
_WORKER_TIMEOUT = 7200  # seconds


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


async def transcribe_audio(audio_path: str) -> list[TranscriptSegment]:
    """Transcribe a file with faster-whisper in an isolated, killable subprocess."""
    async with _transcribe_semaphore:
        # Free cached tiny/small models before loading the much larger post-call
        # model. The semaphore alone prevents concurrent inference but would
        # still leave both workers' model memory resident and risk an OOM.
        await _live_worker.stop()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "services.transcribe_worker",
            audio_path, config.WHISPER_MODEL, "ru", "5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "HF_HUB_DISABLE_PROGRESS_BARS": "1"},
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_WORKER_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"Whisper worker timed out after {_WORKER_TIMEOUT}s — killed (audio: {audio_path})"
            )

        if proc.returncode != 0:
            tail = (stderr.decode(errors="replace") if stderr else "")[-800:]
            raise RuntimeError(f"Whisper worker exited {proc.returncode}: {tail}")

        # The worker prints one JSON object as its LAST stdout line.
        out = stdout.decode(errors="replace").strip()
        last = out.splitlines()[-1] if out else ""
        try:
            data = json.loads(last)
        except Exception as e:
            raise RuntimeError(
                f"Whisper worker returned unparseable output ({e}); got: {out[:300]!r}"
            )
        result = [
            TranscriptSegment(start=s["start"], end=s["end"], text=s["text"])
            for s in data.get("segments", [])
        ]
        logger.info(
            "Transcribed %s: %d segments, lang=%s",
            audio_path, len(result), data.get("language"),
        )
        return result


# ── Live assistant: isolated persistent small-model worker ────────────────────


class _LiveSTTWorker:
    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr_task: asyncio.Task | None = None

    async def _start(self) -> None:
        if self.proc and self.proc.returncode is None:
            return
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "services.live_transcribe_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "HF_HUB_DISABLE_PROGRESS_BARS": "1"},
        )
        self.stderr_task = asyncio.create_task(
            self._drain_stderr(),
            name="live-stt-stderr",
        )
        logger.info("Started isolated live-STT worker pid=%s", self.proc.pid)

    async def _drain_stderr(self) -> None:
        proc = self.proc
        if not proc or not proc.stderr:
            return
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                return
            logger.warning(
                "live-STT worker: %s",
                raw.decode(errors="replace").rstrip()[-800:],
            )

    async def stop(self) -> None:
        proc, self.proc = self.proc, None
        stderr_task, self.stderr_task = self.stderr_task, None
        if proc and proc.returncode is None:
            proc.kill()
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
        if stderr_task:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

    async def transcribe(
        self,
        pcm: bytes,
        model_size: str,
        beam_size: int,
        timeout: float,
    ) -> str:
        await self._start()
        assert self.proc and self.proc.stdin and self.proc.stdout
        request_id = uuid.uuid4().hex
        header = json.dumps(
            {
                "id": request_id,
                "bytes": len(pcm),
                "model": model_size,
                "beam_size": beam_size,
            }
        ).encode() + b"\n"
        try:
            self.proc.stdin.write(header)
            self.proc.stdin.write(pcm)
            await self.proc.stdin.drain()
            raw = await asyncio.wait_for(self.proc.stdout.readline(), timeout=timeout)
            if not raw:
                raise RuntimeError(
                    f"live-STT worker exited unexpectedly ({self.proc.returncode})"
                )
            response = json.loads(raw)
            if response.get("id") != request_id:
                raise RuntimeError("live-STT worker protocol desynchronized")
            if response.get("error"):
                raise RuntimeError(response["error"])
            return str(response.get("text") or "")
        except asyncio.CancelledError:
            # The unread response would desynchronize the next request.
            await self.stop()
            raise
        except Exception:
            await self.stop()
            raise


_live_worker = _LiveSTTWorker()


async def transcribe_pcm(pcm: bytes, model_size: str, *, beam_size: int = 1) -> str:
    """Transcribe raw PCM outside the web process with bounded queue/runtime."""
    queue_timeout = (
        config.LIVE_STT_TIMEOUT_SEC
        if beam_size > 1
        else config.LIVE_STT_QUEUE_TIMEOUT_SEC
    )
    try:
        await asyncio.wait_for(_transcribe_semaphore.acquire(), timeout=queue_timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError("live-STT queue is busy; dropping stale audio") from exc
    try:
        return await _live_worker.transcribe(
            pcm,
            model_size,
            beam_size,
            timeout=config.LIVE_STT_TIMEOUT_SEC,
        )
    finally:
        _transcribe_semaphore.release()


def _pcm_wav(pcm: bytes) -> io.BytesIO:
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(pcm)
    target.seek(0)
    target.name = "live-audio.wav"
    return target


async def transcribe_openai_pcm(
    pcm: bytes,
    model: str,
    *,
    prompt: str | None = None,
) -> str:
    """Transcribe a short PCM window using the shared cloud STT client."""
    global _openai_stt_client
    if _openai_stt_client is None:
        from openai import AsyncOpenAI

        _openai_stt_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

    kwargs = {
        "model": model,
        "file": _pcm_wav(pcm),
        "language": "ru",
        "response_format": "text",
    }
    if prompt and model == "whisper-1":
        kwargs["prompt"] = prompt
    response = await asyncio.wait_for(
        _openai_stt_client.audio.transcriptions.create(**kwargs),
        timeout=config.LIVE_STT_TIMEOUT_SEC,
    )
    if isinstance(response, str):
        return response.strip()
    return str(getattr(response, "text", response) or "").strip()


async def close_live_transcriber() -> None:
    global _openai_stt_client
    await _live_worker.stop()
    client, _openai_stt_client = _openai_stt_client, None
    if client is not None:
        await client.close()

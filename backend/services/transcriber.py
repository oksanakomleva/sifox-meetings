"""faster-whisper transcription — same approach as telemost-bot."""
import asyncio
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from config import config

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
_transcribe_semaphore = asyncio.Semaphore(1)  # one at a time to prevent OOM

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


# ── Live assistant: cached small models + raw-PCM transcription ────────────────
# Used only by the live in-meeting assistant. Models are cached (unlike the
# post-meeting path which loads+frees) because the wake-word loop runs every few
# seconds. Routed through the SAME semaphore/executor so we never run two whisper
# inferences at once (OOM guard).

_model_cache: dict[str, "object"] = {}


def _get_cached_model(size: str):
    from faster_whisper import WhisperModel
    m = _model_cache.get(size)
    if m is None:
        logger.info("Loading cached Whisper '%s' for live assistant", size)
        m = WhisperModel(size, device="cpu", compute_type="int8")
        _model_cache[size] = m
    return m


def _transcribe_pcm_sync(pcm: bytes, model_size: str, beam_size: int) -> str:
    import numpy as np
    if not pcm:
        return ""
    model = _get_cached_model(model_size)
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _info = model.transcribe(
        audio, language="ru", beam_size=beam_size, vad_filter=True,
    )
    return " ".join(s.text.strip() for s in segments if s.text.strip()).strip()


async def transcribe_pcm(pcm: bytes, model_size: str, *, beam_size: int = 1) -> str:
    """Transcribe raw 16 kHz mono s16le PCM with a cached model. Returns plain text."""
    async with _transcribe_semaphore:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, _transcribe_pcm_sync, pcm, model_size, beam_size)

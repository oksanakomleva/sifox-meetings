"""faster-whisper transcription — same approach as telemost-bot."""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from config import config

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
_transcribe_semaphore = asyncio.Semaphore(1)  # one at a time to prevent OOM


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


def _transcribe_sync(audio_path: str) -> list[TranscriptSegment]:
    from faster_whisper import WhisperModel

    logger.info("Loading Whisper %s for %s", config.WHISPER_MODEL, audio_path)
    model = WhisperModel(
        config.WHISPER_MODEL,
        device="cpu",
        compute_type="int8",
    )
    segments, info = model.transcribe(
        audio_path,
        language="ru",
        beam_size=5,
        vad_filter=True,
    )
    result = [
        TranscriptSegment(start=s.start, end=s.end, text=s.text.strip())
        for s in segments
        if s.text.strip()
    ]
    del model
    logger.info(
        "Transcribed %s: %d segments, lang=%s",
        audio_path, len(result), info.language,
    )
    return result


async def transcribe_audio(audio_path: str) -> list[TranscriptSegment]:
    async with _transcribe_semaphore:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, _transcribe_sync, audio_path)


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

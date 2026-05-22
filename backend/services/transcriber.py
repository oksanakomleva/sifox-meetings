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

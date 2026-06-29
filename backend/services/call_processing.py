"""Transcribe + analyze an imported phone call, reusing the meeting pipeline.

A downloaded rec.megafon.ru recording on disk → transcript → mp3 → AI analysis
(summary / tasks / reminders / tags) → status='done'. Mirrors
recorder.transcribe_and_analyze but writes to the `calls` table.
"""
import logging
from pathlib import Path

from config import config
from database import models
from services.analyzer import analyze_call
from services.transcriber import transcribe_audio

logger = logging.getLogger(__name__)


async def process_call(call_id: str, audio_path: Path) -> None:
    """Transcribe the call audio, convert to mp3, run AI analysis, finalize.
    Marks the call 'error' on failure (caller need not handle exceptions)."""
    # Imported lazily — recorder pulls in Playwright/heavy deps at import time.
    from services.recorder import _build_transcript, _convert_to_mp3

    try:
        await models.update_call_status(call_id, "transcribing")
        if not audio_path.exists() or audio_path.stat().st_size < 1000:
            raise RuntimeError(f"Call audio missing or too small: {audio_path}")

        # 1. Transcribe. Calls have no speaker timeline (mono) → flat labelling.
        # TODO: if the recording is stereo (channel = party), split channels for
        # real "Вы"/"Собеседник" diarization.
        segments = await transcribe_audio(str(audio_path))
        transcript_text = _build_transcript(segments, []) if segments else ""
        await models.save_call_transcript(call_id, transcript_text)

        # 2. Convert → mp3, drop the source.
        mp3_path = Path(config.AUDIO_DIR) / f"{call_id}.mp3"
        try:
            await _convert_to_mp3(audio_path, mp3_path)
            stored_name, stored_size = mp3_path.name, mp3_path.stat().st_size
            try:
                audio_path.unlink()
            except FileNotFoundError:
                pass
        except Exception as e:  # noqa: BLE001 — keep source if conversion fails
            logger.error("Call %s mp3 conversion failed: %s — keeping source", call_id[:8], e)
            stored_name, stored_size = audio_path.name, audio_path.stat().st_size
        await models.save_call_audio(call_id, stored_name, stored_size)

        # 3. Analyze (skip if nothing was said).
        if not transcript_text:
            await models.save_call_analysis(call_id, summary="Запись без распознанной речи.")
            return
        await models.update_call_status(call_id, "analyzing")
        a = await analyze_call(transcript_text)
        await models.save_call_analysis(
            call_id,
            title=a.get("title") or None,
            summary=a.get("summary"),
            tasks=a.get("tasks"),
            reminders=a.get("reminders"),
            tags=a.get("tags"),
        )
        logger.info("Call %s processed (done)", call_id[:8])
    except Exception as e:  # noqa: BLE001
        logger.error("Call %s processing failed: %s", call_id[:8], e, exc_info=True)
        await models.update_call_status(call_id, "error", str(e)[:500])

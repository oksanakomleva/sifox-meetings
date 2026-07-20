"""Transcribe + analyze an imported phone call, reusing the meeting pipeline.

A downloaded rec.megafon.ru recording on disk → transcript → mp3 → AI analysis
(summary / tasks / reminders / tags) → status='done'.

Call recordings are stereo (one party per channel), so we split the channels and
transcribe each separately to label "Вы" vs "Собеседник". Mono recordings fall
back to a flat transcript.
"""
import asyncio
import logging
from pathlib import Path

from config import config
from database import models
from services import fsio
from services.analyzer import analyze_call
from services.transcriber import transcribe_audio

logger = logging.getLogger(__name__)


async def _probe_channels(path: Path) -> int:
    """Number of audio channels (ffprobe). Defaults to 1 on any error."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=channels", "-of", "csv=p=0", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return int(out.decode().strip().split()[0])
    except Exception:  # noqa: BLE001
        return 1


async def _extract_channel(src: Path, channel: int, dest: Path) -> None:
    """Extract one channel of `src` to a mono wav at `dest`."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(src), "-af", f"pan=mono|c0=c{channel}",
        "-ar", "16000", "-ac", "1", str(dest),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        tail = (err.decode(errors="replace") if err else "")[-300:]
        raise RuntimeError(f"channel {channel} extract failed: {tail}")


def _build_call_transcript(you_segs, other_segs) -> str:
    """Merge two channels' segments into one timecoded, speaker-labelled script."""
    from services.recorder import _fmt_time

    PAUSE_THRESHOLD = 4.0
    MAX_BLOCK_SECONDS = 45.0
    labeled = [(s, "Вы") for s in you_segs if s.text.strip()]
    labeled += [(s, "Собеседник") for s in other_segs if s.text.strip()]
    if not labeled:
        return ""
    labeled.sort(key=lambda x: x[0].start)

    blocks: list[tuple[float, str, str]] = []
    cs, cstart, ctexts, pend = labeled[0][1], labeled[0][0].start, [labeled[0][0].text.strip()], labeled[0][0].end
    for seg, sp in labeled[1:]:
        gap, blen = seg.start - pend, seg.start - cstart
        if sp == cs and gap < PAUSE_THRESHOLD and blen < MAX_BLOCK_SECONDS:
            ctexts.append(seg.text.strip())
        else:
            blocks.append((cstart, cs, " ".join(ctexts)))
            cs, cstart, ctexts = sp, seg.start, [seg.text.strip()]
        pend = seg.end
    blocks.append((cstart, cs, " ".join(ctexts)))
    return "\n".join(f"[{_fmt_time(st)}] {sp}: {tx}" for st, sp, tx in blocks)


async def _transcribe_stereo(audio_path: Path, call_id: str) -> str:
    """Split channels, transcribe each, build a diarized transcript."""
    you_ch = 0 if config.MEGAFON_YOU_CHANNEL not in (0, 1) else config.MEGAFON_YOU_CHANNEL
    other_ch = 1 - you_ch
    base = Path(config.AUDIO_DIR)
    you_wav = base / f"{call_id}.you.wav"
    other_wav = base / f"{call_id}.other.wav"
    try:
        await _extract_channel(audio_path, you_ch, you_wav)
        await _extract_channel(audio_path, other_ch, other_wav)
        you_segs = await transcribe_audio(str(you_wav))
        other_segs = await transcribe_audio(str(other_wav))
        return _build_call_transcript(you_segs, other_segs)
    finally:
        for p in (you_wav, other_wav):
            await fsio.unlink_quiet(p)


async def process_call(call_id: str, audio_path: Path) -> None:
    """Transcribe the call audio, convert to mp3, run AI analysis, finalize.
    Marks the call 'error' on failure (caller need not handle exceptions)."""
    from services.recorder import _build_transcript, _convert_to_mp3

    try:
        await models.update_call_status(call_id, "transcribing")
        if await fsio.size(audio_path) < 1000:
            raise RuntimeError(f"Call audio missing or too small: {audio_path}")

        # 1. Transcribe. Stereo → split channels → "Вы"/"Собеседник"; mono → flat.
        channels = await _probe_channels(audio_path)
        if channels >= 2:
            transcript_text = await _transcribe_stereo(audio_path, call_id)
        else:
            segments = await transcribe_audio(str(audio_path))
            transcript_text = _build_transcript(segments, []) if segments else ""
        await models.save_call_transcript(call_id, transcript_text)

        # 2. Convert → mp3 (keep stereo so the two parties stay separable), drop source.
        mp3_path = Path(config.AUDIO_DIR) / f"{call_id}.mp3"
        try:
            await _convert_to_mp3(audio_path, mp3_path, mono=False)
            stored_name, stored_size = mp3_path.name, await fsio.size(mp3_path)
            await fsio.unlink_quiet(audio_path)
        except Exception as e:  # noqa: BLE001 — keep source if conversion fails
            logger.error("Call %s mp3 conversion failed: %s — keeping source", call_id[:8], e)
            stored_name, stored_size = audio_path.name, await fsio.size(audio_path)
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
        logger.info("Call %s processed (done, %d channels)", call_id[:8], channels)
    except Exception as e:  # noqa: BLE001
        logger.error("Call %s processing failed: %s", call_id[:8], e, exc_info=True)
        await models.update_call_status(call_id, "error", str(e)[:500])

"""Standalone faster-whisper worker — runs as a SEPARATE PROCESS.

Isolating CTranslate2 inference in its own process means a native hang/deadlock
during transcription cannot hold the main service's GIL and freeze its event
loop — that is what caused the 2026-07-22 total outage (the whole service went
unresponsive while a transcription wedged). The parent runs this with a hard
timeout and kills it if it wedges, so a bad transcription can never take the
service down.

Invoked as:  python -m services.transcribe_worker <audio_path> <model> <lang> <beam_size>
Optional prompt/hotwords are read as a JSON object from stdin so participant
names are not exposed in the operating-system process list.
Emits ONE JSON object on the LAST stdout line:
  {"segments": [{"start": float, "end": float, "text": str}, ...], "language": "ru"}
faster-whisper's own logs go to stderr; the parent reads them only on failure.
"""
import json
import sys


def main() -> int:
    if len(sys.argv) < 5:
        sys.stderr.write("usage: transcribe_worker <audio_path> <model> <lang> <beam_size>\n")
        return 2
    audio_path = sys.argv[1]
    model_name = sys.argv[2]
    language = sys.argv[3]
    beam_size = int(sys.argv[4])
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except (TypeError, ValueError):
        request = {}
    prompt = str(request.get("prompt") or "").strip()
    hotwords = str(request.get("hotwords") or "").strip()

    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=beam_size,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
        word_timestamps=True,
        initial_prompt=prompt or None,
        hotwords=hotwords or None,
        hallucination_silence_threshold=2.0,
    )
    result = []
    word_probabilities = []
    for s in segments:
        if not s.text.strip():
            continue
        words = []
        for w in (s.words or []):
            words.append({
                "start": w.start,
                "end": w.end,
                "word": w.word,
                "probability": w.probability,
            })
            if w.probability is not None:
                word_probabilities.append(w.probability)
        result.append({
            "start": s.start,
            "end": s.end,
            "text": s.text.strip(),
            "words": words,
            "avg_logprob": s.avg_logprob,
            "no_speech_prob": s.no_speech_prob,
            "compression_ratio": s.compression_ratio,
        })
    duration = float(getattr(info, "duration", 0.0) or 0.0)
    speech_duration = float(getattr(info, "duration_after_vad", 0.0) or 0.0)
    avg_logprobs = [s["avg_logprob"] for s in result if s["avg_logprob"] is not None]
    diagnostics = {
        "model": model_name,
        "language": info.language,
        "duration_sec": round(duration, 2),
        "speech_sec": round(speech_duration, 2),
        "speech_ratio": round(speech_duration / duration, 4) if duration else None,
        "segment_count": len(result),
        "word_count": sum(len(s["words"]) for s in result),
        "avg_logprob": round(sum(avg_logprobs) / len(avg_logprobs), 4) if avg_logprobs else None,
        "avg_word_probability": round(sum(word_probabilities) / len(word_probabilities), 4) if word_probabilities else None,
        "low_confidence_word_ratio": round(
            sum(1 for p in word_probabilities if p < 0.5) / len(word_probabilities), 4
        ) if word_probabilities else None,
    }
    # Leading newline guarantees the JSON is the LAST line even if a library
    # leaked anything to stdout earlier.
    sys.stdout.write("\n" + json.dumps({
        "segments": result,
        "language": info.language,
        "diagnostics": diagnostics,
    }))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

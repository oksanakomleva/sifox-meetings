"""Standalone faster-whisper worker — runs as a SEPARATE PROCESS.

Isolating CTranslate2 inference in its own process means a native hang/deadlock
during transcription cannot hold the main service's GIL and freeze its event
loop — that is what caused the 2026-07-22 total outage (the whole service went
unresponsive while a transcription wedged). The parent runs this with a hard
timeout and kills it if it wedges, so a bad transcription can never take the
service down.

Invoked as:  python -m services.transcribe_worker <audio_path> <model> <lang> <beam_size>
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

    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=beam_size,
        vad_filter=True,
    )
    result = [
        {"start": s.start, "end": s.end, "text": s.text.strip()}
        for s in segments
        if s.text.strip()
    ]
    # Leading newline guarantees the JSON is the LAST line even if a library
    # leaked anything to stdout earlier.
    sys.stdout.write("\n" + json.dumps({"segments": result, "language": info.language}))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

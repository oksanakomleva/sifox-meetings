"""Persistent isolated faster-whisper worker for the live assistant.

The native CTranslate2 runtime must not execute inside the web process: a native
hang can hold the GIL and make health checks and every HTTP request stop. The
parent sends newline-delimited JSON headers followed by exact raw-PCM payloads;
this worker returns one JSON line per request and can be killed on a timeout.
"""
import json
import sys


_models: dict[str, object] = {}


def _model(size: str):
    from faster_whisper import WhisperModel

    model = _models.get(size)
    if model is None:
        model = WhisperModel(size, device="cpu", compute_type="int8")
        _models[size] = model
    return model


def _read_exact(stream, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError(f"expected {size} PCM bytes, received {size - remaining}")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _transcribe(pcm: bytes, model_size: str, beam_size: int) -> str:
    import numpy as np

    if not pcm:
        return ""
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _info = _model(model_size).transcribe(
        audio,
        language="ru",
        beam_size=beam_size,
        vad_filter=True,
    )
    return " ".join(
        segment.text.strip() for segment in segments if segment.text.strip()
    ).strip()


def main() -> None:
    source = sys.stdin.buffer
    target = sys.stdout
    while True:
        header = source.readline()
        if not header:
            return
        request_id = None
        try:
            request = json.loads(header)
            request_id = request.get("id")
            size = int(request["bytes"])
            if size < 0 or size > 64 * 1024 * 1024:
                raise ValueError(f"invalid PCM size: {size}")
            pcm = _read_exact(source, size)
            text = _transcribe(
                pcm,
                str(request["model"]),
                int(request.get("beam_size", 1)),
            )
            response = {"id": request_id, "text": text}
        except Exception as exc:  # parent receives a bounded, serializable error
            response = {
                "id": request_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
        target.write(json.dumps(response, ensure_ascii=False) + "\n")
        target.flush()


if __name__ == "__main__":
    main()

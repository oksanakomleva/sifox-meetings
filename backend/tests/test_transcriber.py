"""Unit tests for the isolated post-meeting transcription protocol."""
import asyncio
import json
from unittest.mock import AsyncMock

from services import transcriber


class _WorkerProcess:
    def __init__(self, payload):
        self.returncode = 0
        self.payload = payload
        self.input = None

    async def communicate(self, input=None):
        self.input = input
        return json.dumps(self.payload).encode(), b""


def test_transcribe_audio_parses_words_metrics_and_sends_hints_via_stdin(monkeypatch):
    payload = {
        "language": "ru",
        "diagnostics": {"speech_ratio": 0.42, "word_count": 1},
        "segments": [{
            "start": 1.0,
            "end": 2.0,
            "text": "Привет",
            "avg_logprob": -0.2,
            "no_speech_prob": 0.01,
            "compression_ratio": 1.1,
            "words": [{
                "start": 1.0,
                "end": 2.0,
                "word": " Привет",
                "probability": 0.95,
            }],
        }],
    }
    process = _WorkerProcess(payload)

    async def create(*args, **kwargs):
        assert kwargs["stdin"] == asyncio.subprocess.PIPE
        return process

    monkeypatch.setattr(transcriber.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(transcriber._live_worker, "stop", AsyncMock())
    diagnostics = {}

    result = asyncio.run(transcriber.transcribe_audio(
        "meeting.wav",
        prompt="Название встречи: ГПБМ",
        hotwords="ГПБМ",
        diagnostics=diagnostics,
    ))

    request = json.loads(process.input)
    assert request == {
        "prompt": "Название встречи: ГПБМ",
        "hotwords": "ГПБМ",
    }
    assert result[0].text == "Привет"
    assert result[0].words[0].word == " Привет"
    assert result[0].words[0].probability == 0.95
    assert diagnostics == {"speech_ratio": 0.42, "word_count": 1}

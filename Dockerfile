FROM python:3.11-slim AS base

# ── System deps ───────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Xvfb + PulseAudio for browser recording
    xvfb \
    x11-utils \
    pulseaudio \
    pulseaudio-utils \
    # Chromium deps
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libxkbcommon0 \
    libpango-1.0-0 libcairo2 libgtk-3-0 libdrm2 \
    # libasound2 renamed in Debian Trixie
    libasound2t64 \
    # Audio capture — ffmpeg only; parec is part of pulseaudio-utils
    ffmpeg \
    # TTS engine for E2E test audio generation
    espeak-ng \
    # Build tools
    curl ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js for frontend build ────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps ───────────────────────────────────────────────────────────────
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright + Chromium
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium

# Pre-download Whisper models (avoids a slow runtime download on first use).
# medium = post-meeting transcription; tiny/small = live in-meeting assistant
# (continuous wake-word + question STT) — downloading these mid-meeting would
# block the listen loop and miss the wake word.
ARG WHISPER_MODEL=medium
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from faster_whisper import WhisperModel; [WhisperModel(m, device='cpu', compute_type='int8') for m in ['${WHISPER_MODEL}', 'tiny', 'small']]" || true

# ── Frontend build ────────────────────────────────────────────────────────────
COPY frontend/package.json frontend/package.json
WORKDIR /app/frontend
RUN npm install
COPY frontend/ .
RUN npm run build

# ── Backend ───────────────────────────────────────────────────────────────────
WORKDIR /app
COPY backend/ backend/

# Browser extension source — served as a downloadable .zip from the web app
# (GET /api/extension/download zips this folder on the fly).
COPY extension/ extension/

# Generate test audio for E2E tests (espeak-ng Russian TTS → WAV)
# The Test Speaker bot streams this file as a fake microphone in Telemost meetings
RUN espeak-ng -v ru -s 120 -p 50 \
    "Добрый день. Это автоматический тест системы записи встреч. Пункт первый: техническая готовность. Пункт второй: проверка качества звука. Пункт третий: интеграция с искусственным интеллектом. Тест успешно пройден. Запись завершена." \
    -w /app/backend/tests/e2e/test_audio.wav \
    && echo "✅ test_audio.wav generated"

# Live-assistant E2E: enough leading silence for both browsers to join, then a
# deterministic fact + wake-word question. Pad beyond the test duration so the
# fake microphone never loops and triggers the assistant twice.
RUN espeak-ng -v ru -s 115 -p 50 \
    "Проект называется Мега. Проект называется Мега. Протоколлер, как называется проект? Протоколлер, скажи название проекта." \
    -w /tmp/live_assistant_question.wav \
    && ffmpeg -y -i /tmp/live_assistant_question.wav \
       -af "adelay=35000,apad=pad_dur=150" -t 150 \
       /app/backend/tests/e2e/live_assistant_test_audio.wav \
       >/dev/null 2>&1 \
    && rm /tmp/live_assistant_question.wav \
    && echo "✅ live_assistant_test_audio.wav generated"

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# Uvicorn, Chromium and user-controlled media processing must not run as root.
# The entrypoint starts the display/audio daemons first, prepares the mounted
# volume, then drops to this account via gosu.
RUN groupadd --system appuser \
    && useradd --system --gid appuser --home-dir /app --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

ENV DISPLAY=:99
ENV PULSE_SERVER=unix:/tmp/pulse.sock
ENV PYTHONPATH=/app/backend

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]

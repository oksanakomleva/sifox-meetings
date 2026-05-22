FROM python:3.11-slim AS base

# ── System deps ───────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Xvfb + PulseAudio for browser recording
    xvfb \
    pulseaudio \
    pulseaudio-utils \
    # Chromium deps
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libxkbcommon0 \
    libpango-1.0-0 libcairo2 libasound2 libgtk-3-0 libdrm2 \
    # Audio capture
    parec ffmpeg \
    # Build tools
    curl ca-certificates \
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
RUN playwright install chromium --with-deps

# Pre-download Whisper model (avoids timeout on first run)
ARG WHISPER_MODEL=medium
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')" || true

# ── Frontend build ────────────────────────────────────────────────────────────
COPY frontend/package.json frontend/package.json
WORKDIR /app/frontend
RUN npm install
COPY frontend/ .
RUN npm run build

# ── Backend ───────────────────────────────────────────────────────────────────
WORKDIR /app
COPY backend/ backend/

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

ENV DISPLAY=:99
ENV PULSE_SERVER=unix:/tmp/pulse.sock
ENV PYTHONPATH=/app/backend

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]

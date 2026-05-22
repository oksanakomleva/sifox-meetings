#!/bin/bash
set -e

# ── Xvfb ─────────────────────────────────────────────────────────────────────
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
export DISPLAY=:99
sleep 1

# ── PulseAudio ────────────────────────────────────────────────────────────────
pulseaudio --start \
  --exit-idle-time=-1 \
  --daemonize=false \
  --load="module-native-protocol-unix socket=/tmp/pulse.sock auth-anonymous=1" \
  --log-target=stderr &

export PULSE_SERVER=unix:/tmp/pulse.sock
sleep 1

echo "Xvfb and PulseAudio started"

# ── App ───────────────────────────────────────────────────────────────────────
cd /app
exec python -m uvicorn backend.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --log-level info

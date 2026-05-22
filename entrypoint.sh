#!/bin/bash
set -e

# ── Xvfb ─────────────────────────────────────────────────────────────────────
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
export DISPLAY=:99
sleep 1

# ── PulseAudio ────────────────────────────────────────────────────────────────
# Unset PULSE_SERVER before starting daemon — otherwise PulseAudio sees it
# and refuses to start thinking a server is already configured
unset PULSE_SERVER

pulseaudio \
  --system \
  -n \
  --exit-idle-time=-1 \
  --daemonize=no \
  --log-target=stderr \
  --load="module-native-protocol-unix socket=/tmp/pulse.sock auth-anonymous=1" \
  --load="module-null-sink sink_name=default_sink" &

export PULSE_SERVER=unix:/tmp/pulse.sock
sleep 2

echo "Xvfb and PulseAudio started"

# ── App ───────────────────────────────────────────────────────────────────────
cd /app
exec python -m uvicorn backend.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --log-level info

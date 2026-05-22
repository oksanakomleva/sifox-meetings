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

# Provide a writable runtime path so PulseAudio doesn't try to use root's homedir
mkdir -p /tmp/pulse-runtime
chmod 700 /tmp/pulse-runtime
export PULSE_RUNTIME_PATH=/tmp/pulse-runtime

# Start PulseAudio in a subshell so HOME=/tmp doesn't leak to Chromium/Playwright
(
  export HOME=/tmp
  pulseaudio \
    --daemonize=no \
    --exit-idle-time=-1 \
    --disallow-exit \
    -n \
    --log-target=stderr \
    --load="module-native-protocol-unix socket=/tmp/pulse.sock auth-anonymous=1" \
    --load="module-null-sink sink_name=default_sink"
) &

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

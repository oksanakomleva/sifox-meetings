#!/bin/bash
set -e

DISPLAY_NUM=99
export DISPLAY=:${DISPLAY_NUM}

# ── Xvfb ─────────────────────────────────────────────────────────────────────
# Remove any stale lock/socket left by a PREVIOUS Xvfb that was hard-killed
# (SIGKILL). On Railway a crash-restart (restartPolicy on_failure) reuses the
# same container filesystem, so /tmp/.X99-lock survives the kill and makes the
# fresh Xvfb die with "Server is already active for display 99" — the display
# never comes up and every recording then fails ("Xvfb display :99 not
# responding") until a human redeploys. Clearing the lock first lets the
# container recover the display on its own. (See 2026-07-17 incident.)
cleanup_x_locks() {
  rm -f "/tmp/.X${DISPLAY_NUM}-lock"
  rm -f "/tmp/.X11-unix/X${DISPLAY_NUM}"
}

start_xvfb() {
  cleanup_x_locks
  Xvfb :${DISPLAY_NUM} -screen 0 1280x720x24 -nolisten tcp &
}

xvfb_ready() {
  xdpyinfo -display :${DISPLAY_NUM} >/dev/null 2>&1
}

wait_for_xvfb() {
  # Poll until the display actually answers — replaces a blind `sleep 1` that
  # let uvicorn start against a dead display.
  for _ in $(seq 1 30); do
    if xvfb_ready; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

start_xvfb
if ! wait_for_xvfb; then
  echo "FATAL: Xvfb display :${DISPLAY_NUM} did not become ready — exiting so Railway restarts cleanly" >&2
  exit 1
fi

# Watchdog: if Xvfb dies mid-run, restart it (clearing its stale lock) so the
# container recovers the display without a manual redeploy.
(
  while true; do
    sleep 10
    if ! xvfb_ready; then
      echo "WARN: Xvfb display :${DISPLAY_NUM} not responding — restarting it" >&2
      start_xvfb
      wait_for_xvfb || echo "WARN: Xvfb restart did not become ready" >&2
    fi
  done
) &

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

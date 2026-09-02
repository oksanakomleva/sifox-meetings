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
  Xvfb :${DISPLAY_NUM} -screen 0 1280x720x24 -nolisten tcp -ac &
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

# Railway may restart the application process while a background PulseAudio
# process from the previous run is still alive.  In that state a new daemon
# refuses to start ("Daemon already running"), but the application still comes
# up and every later `parec` exits immediately.  Stop only the daemon referenced
# by our private runtime directory and remove its stale socket before starting a
# fresh instance.
stop_stale_pulseaudio() {
  local pid_file="${PULSE_RUNTIME_PATH}/pid"
  local pulse_pid=""

  if [[ -r "${pid_file}" ]]; then
    pulse_pid="$(tr -dc '0-9' < "${pid_file}")"
  fi
  if [[ -n "${pulse_pid}" ]] && kill -0 "${pulse_pid}" 2>/dev/null; then
    echo "Stopping stale PulseAudio process ${pulse_pid}"
    kill -TERM "${pulse_pid}" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "${pulse_pid}" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "${pulse_pid}" 2>/dev/null; then
      kill -KILL "${pulse_pid}" 2>/dev/null || true
    fi
  fi
  rm -f "${pid_file}" /tmp/pulse.sock
}

stop_stale_pulseaudio

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

# Do not report the service healthy until the audio server actually accepts
# commands.  A blind sleep previously let the web app start with a dead audio
# backend, so the failure surfaced only after the bot had joined a meeting.
pulse_ready() {
  pactl info >/dev/null 2>&1
}

for _ in $(seq 1 30); do
  pulse_ready && break
  sleep 0.2
done
if ! pulse_ready; then
  echo "FATAL: PulseAudio socket ${PULSE_SERVER} did not become ready" >&2
  exit 1
fi

echo "Xvfb and PulseAudio started"

# ── App ───────────────────────────────────────────────────────────────────────
APP_USER=appuser
AUDIO_PATH="${AUDIO_DIR:-/audio}"
mkdir -p "${AUDIO_PATH}"
chown "${APP_USER}:${APP_USER}" "${AUDIO_PATH}"
chmod 0770 "${AUDIO_PATH}"

cd /app
export HOME=/app
exec gosu "${APP_USER}" python -m uvicorn backend.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --log-level info

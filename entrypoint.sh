#!/bin/bash
set -euo pipefail

CRON_SCHEDULE="${CRON_SCHEDULE:-0 */6 * * *}"
CHANNELS_FILE="/config/channels.txt"
LOG_FILE="/logs/download.log"
PID_FILE="/tmp/download.pid"

mkdir -p /logs
touch "${LOG_FILE}"

echo "=== tiktok-dl ==="
echo "Schedule : ${CRON_SCHEDULE}"
echo "Web UI   : http://0.0.0.0:8080"
echo "Channels :"
grep -v '^\s*#' "${CHANNELS_FILE}" 2>/dev/null | grep -v '^\s*$' | while read -r line; do
  echo "  - ${line}"
done
echo ""

# --- Generate crontab ---
echo "${CRON_SCHEDULE} /download.sh >> /logs/download.log 2>&1" > /tmp/crontab

# --- Run initial download ---
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running initial download..." | tee -a "${LOG_FILE}"
/download.sh >> "${LOG_FILE}" 2>&1 &

# --- Start web UI (background) ---
python3 /webui.py &
WEBUI_PID=$!

# --- Start cron (background) ---
supercronic /tmp/crontab &
CRON_PID=$!

# --- Watch channels.txt for edits (foreground) ---
# Watch the DIRECTORY, not the file — editors do atomic saves (write tmp + rename)
# which replace the inode and break file-level inotifywait on bind mounts.
CHANNELS_DIR="$(dirname "${CHANNELS_FILE}")"
CHANNELS_NAME="$(basename "${CHANNELS_FILE}")"
CHANNELS_HASH="$(md5sum "${CHANNELS_FILE}" 2>/dev/null | cut -d' ' -f1)"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watching ${CHANNELS_FILE} for changes..."
while true; do
  inotifywait -qq -e close_write,moved_to,create "${CHANNELS_DIR}" 2>/dev/null || {
    sleep 5
    continue
  }

  # Only react if channels.txt actually changed content
  NEW_HASH="$(md5sum "${CHANNELS_FILE}" 2>/dev/null | cut -d' ' -f1)"
  if [[ "${NEW_HASH}" == "${CHANNELS_HASH}" ]]; then
    continue
  fi
  CHANNELS_HASH="${NEW_HASH}"

  echo "" >> "${LOG_FILE}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] channels.txt changed — triggering download" | tee -a "${LOG_FILE}"

  # Skip if already running
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}" 2>/dev/null)" 2>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Download already in progress, skipping" | tee -a "${LOG_FILE}"
    continue
  fi

  /download.sh >> "${LOG_FILE}" 2>&1 &
  echo $! > "${PID_FILE}"

  # Debounce — ignore further edits for 10s
  sleep 10
done

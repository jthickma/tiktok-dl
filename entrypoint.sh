#!/bin/bash
set -euo pipefail

CRON_SCHEDULE="${CRON_SCHEDULE:-0 */6 * * *}"
export CHANNELS_FILE="/config/channels.txt"
export ARCHIVE_FILE="/config/archive.txt"
export DOWNLOADS_DIR="/downloads"
export LOG_FILE="/logs/download.log"
export PID_FILE="/tmp/tiktok-dl.pid"
export PENDING_FILE="/tmp/tiktok-dl.pending"
export LAST_RUN_FILE="/tmp/tiktok-dl.last-run"
export REQUEST_SCRIPT="/request_download.sh"
APP_USER="app"
APP_GROUP="app"
APP_UID="1000"
APP_GID="1000"

run_as_app() {
  su-exec "${APP_USER}:${APP_GROUP}" "$@"
}

mkdir -p /downloads /logs /config
touch "${LOG_FILE}"
touch /config/archive.txt
chown -R "${APP_UID}:${APP_GID}" /downloads /logs

echo "=== tiktok-dl ==="
echo "Schedule : ${CRON_SCHEDULE}"
echo "Web UI   : http://0.0.0.0:8080"
echo "User     : ${APP_UID}:${APP_GID}"
echo "Channels :"
grep -v '^\s*#' "${CHANNELS_FILE}" 2>/dev/null | grep -v '^\s*$' | while read -r line; do
  echo "  - ${line}"
done
echo ""

# --- Generate crontab ---
echo "${CRON_SCHEDULE} /request_download.sh cron" > /tmp/crontab

# --- Run initial download ---
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running initial download..." | tee -a "${LOG_FILE}"
run_as_app /request_download.sh startup

# --- Start web UI (background) ---
run_as_app python3 /webui.py &
WEBUI_PID=$!

# --- Start cron (background) ---
run_as_app supercronic /tmp/crontab >> "${LOG_FILE}" 2>&1 &
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
  run_as_app /request_download.sh watch

  # Debounce — ignore further edits for 10s
  sleep 10
done

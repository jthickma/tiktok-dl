#!/bin/bash
# Worker loop: drains the pending flag, replays passes if a request lands during
# a run. Never exits non-zero on yt-dlp errors — those are surfaced in the log.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="${PID_FILE:-/tmp/tiktok-dl.pid}"
PENDING_FILE="${PENDING_FILE:-/tmp/tiktok-dl.pending}"
LAST_RUN_FILE="${LAST_RUN_FILE:-/tmp/tiktok-dl.last-run}"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "[$(timestamp)] $*"
}

cleanup() {
  rm -f "${PID_FILE}"
}

trap cleanup EXIT

echo $$ > "${PID_FILE}"

while true; do
  rm -f "${PENDING_FILE}"

  log "Worker starting sync pass"
  "${SCRIPT_DIR}/download.sh" || log "Sync pass reported soft errors (continuing)"
  date +%s > "${LAST_RUN_FILE}" 2>/dev/null || true
  log "Sync pass finished"

  if [[ ! -f "${PENDING_FILE}" ]]; then
    break
  fi

  log "Another sync was requested while busy; starting the next pass"
done

exit 0

#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="${PID_FILE:-/tmp/tiktok-dl.pid}"
PENDING_FILE="${PENDING_FILE:-/tmp/tiktok-dl.pending}"
REQUEST_LOCK_DIR="${REQUEST_LOCK_DIR:-/tmp/tiktok-dl-request.lock}"
LOG_FILE="${LOG_FILE:-/logs/download.log}"
SOURCE="${1:-manual}"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "[$(timestamp)] $*" >> "${LOG_FILE}"
}

is_running() {
  [[ -f "${PID_FILE}" ]] || return 1

  local pid
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] || return 1

  kill -0 "${pid}" 2>/dev/null
}

mkdir -p "$(dirname "${LOG_FILE}")"
touch "${LOG_FILE}"
touch "${PENDING_FILE}"

while ! mkdir "${REQUEST_LOCK_DIR}" 2>/dev/null; do
  sleep 0.1
done
trap 'rmdir "${REQUEST_LOCK_DIR}"' EXIT

if is_running; then
  log "Download request queued (${SOURCE})"
  exit 2
fi

"${SCRIPT_DIR}/download_worker.sh" >> "${LOG_FILE}" 2>&1 &
echo $! > "${PID_FILE}"
log "Download request started (${SOURCE})"

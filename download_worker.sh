#!/bin/bash
set -euo pipefail

PID_FILE="/tmp/download.pid"
PENDING_FILE="/tmp/download.pending"

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

overall_status=0

while true; do
  rm -f "${PENDING_FILE}"

  log "Worker starting sync pass"
  if ! /download.sh; then
    overall_status=1
    log "Sync pass completed with errors"
  else
    log "Sync pass completed successfully"
  fi

  if [[ ! -f "${PENDING_FILE}" ]]; then
    break
  fi

  log "Another sync was requested while busy; starting the next pass"
done

exit "${overall_status}"

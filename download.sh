#!/bin/bash
# Channel sync runner — keeps going through individual download/network failures.
# We deliberately do NOT use `set -e`: a single broken video or DRM-locked post
# must not abort the whole pass.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHANNELS_FILE="${CHANNELS_FILE:-/config/channels.txt}"
ARCHIVE_FILE="${ARCHIVE_FILE:-/config/archive.txt}"
COOKIES_FILE="${COOKIES_FILE:-/config/cookies.txt}"
DOWNLOADS_DIR="${DOWNLOADS_DIR:-/downloads}"
OUTPUT_TEMPLATE="${OUTPUT_TEMPLATE:-%(uploader)s/%(upload_date)s - %(title).80B [%(id)s].%(ext)s}"
MAX_DOWNLOADS="${MAX_DOWNLOADS:-0}"
CONCURRENT_FRAGMENTS="${CONCURRENT_FRAGMENTS:-4}"
RETRIES="${RETRIES:-10}"
SOCKET_TIMEOUT="${SOCKET_TIMEOUT:-30}"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "[$(timestamp)] $*"
}

normalize_channel() {
  echo "$1" | sed 's/#.*//' | xargs
}

build_extra_args() {
  EXTRA_ARGS=()

  if [[ "${MAX_DOWNLOADS}" -gt 0 ]]; then
    EXTRA_ARGS+=(--playlist-end "${MAX_DOWNLOADS}")
  fi

  if [[ -f "${COOKIES_FILE}" ]]; then
    EXTRA_ARGS+=(--cookies "${COOKIES_FILE}")
    log "Using cookies file"
  fi
}

run_channel() {
  local channel="$1"

  yt-dlp \
    --ignore-errors \
    --no-abort-on-error \
    --no-warnings \
    --no-overwrites \
    --continue \
    --retries "${RETRIES}" \
    --fragment-retries "${RETRIES}" \
    --retry-sleep "fragment:exp=1:30" \
    --socket-timeout "${SOCKET_TIMEOUT}" \
    --concurrent-fragments "${CONCURRENT_FRAGMENTS}" \
    --download-archive "${ARCHIVE_FILE}" \
    --output "${DOWNLOADS_DIR}/${OUTPUT_TEMPLATE}" \
    --format "bv*+ba/b" \
    --match-filters "!is_live & original_url!*=/music/ & webpage_url!*=/music/ & ext!=mp3 & ext!=m4a" \
    --merge-output-format mp4 \
    --write-info-json \
    --write-description \
    --write-thumbnail \
    --embed-metadata \
    --embed-thumbnail \
    --restrict-filenames \
    --no-mtime \
    --exec "touch -t %(timestamp>%Y%m%d%H%M.%S)s -- %(filepath)q || true" \
    "${EXTRA_ARGS[@]}" \
    "${channel}"
}

mkdir -p "${DOWNLOADS_DIR}"
touch "${ARCHIVE_FILE}"

log "============================================"
log "Starting download run"
log "============================================"

build_extra_args

failures=0
total=0

while IFS= read -r raw_channel || [[ -n "${raw_channel}" ]]; do
  channel="$(normalize_channel "${raw_channel}")"
  [[ -z "${channel}" ]] && continue

  total=$((total + 1))
  echo ""
  log "Syncing ${channel}"

  if ! run_channel "${channel}"; then
    failures=$((failures + 1))
    log "Channel sync reported errors: ${channel} (continuing)"
  fi
done < "${CHANNELS_FILE}"

echo ""
log "Download run complete — ${total} channel(s), ${failures} with errors"
log "============================================"

# Exit 0 unconditionally so the worker loop and watcher don't treat soft yt-dlp
# errors (private posts, geo blocks, transient 429s) as fatal.
exit 0

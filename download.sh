#!/bin/bash
set -euo pipefail

CHANNELS_FILE="/config/channels.txt"
ARCHIVE_FILE="/config/archive.txt"
OUTPUT_TEMPLATE="${OUTPUT_TEMPLATE:-%(uploader)s/%(upload_date)s - %(title).80B [%(id)s].%(ext)s}"
MAX_DOWNLOADS="${MAX_DOWNLOADS:-0}"
COOKIES_FILE="/config/cookies.txt"
PID_FILE="/tmp/download.pid"

# Write PID for concurrency guard
echo $$ > "${PID_FILE}"
trap 'rm -f "${PID_FILE}"' EXIT

# Ensure archive file exists
touch "${ARCHIVE_FILE}"

echo "============================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting download run"
echo "============================================"

# Build extra args
EXTRA_ARGS=()
if [[ "${MAX_DOWNLOADS}" -gt 0 ]]; then
  EXTRA_ARGS+=(--playlist-end "${MAX_DOWNLOADS}")
fi
if [[ -f "${COOKIES_FILE}" ]]; then
  EXTRA_ARGS+=(--cookies "${COOKIES_FILE}")
  echo "Using cookies file"
fi

# Read channels, skip comments and blank lines
while IFS= read -r channel || [[ -n "${channel}" ]]; do
  # Strip comments and whitespace
  channel="$(echo "${channel}" | sed 's/#.*//' | xargs)"
  [[ -z "${channel}" ]] && continue

  echo ""
  echo "--- Downloading: ${channel} ---"

  yt-dlp \
    --download-archive "${ARCHIVE_FILE}" \
    --output "/downloads/${OUTPUT_TEMPLATE}" \
    --format "best" \
    --embed-metadata \
    --restrict-filenames \
    --no-overwrites \
    --ignore-errors \
    --no-abort-on-error \ 
    --break-on-existing \
    "${EXTRA_ARGS[@]}" \
    "${channel}" || echo "  Warning: errors occurred for ${channel}, continuing..."

done < "${CHANNELS_FILE}"

echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Download run complete"
echo "============================================"

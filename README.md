# tiktok-dl

A small, self-hosted TikTok subscription archiver. Point it at a list of profile URLs; it syncs new posts on a schedule, embeds metadata + thumbnails, and exposes a single-page web UI for browsing the local library.

## Highlights

- **Resilient sync.** A single broken video or transient 429 never aborts the pass — yt-dlp runs with `--ignore-errors`, infinite-style retries, and the worker treats soft failures as informational.
- **Coalesced requests.** Sync requests that arrive while a pass is running are queued (a single pending flag, never a backlog of duplicate work). The worker drains the flag and replays the pass once the active run finishes.
- **Live web UI.** Edit the channel list, request a sync, and browse downloaded media. The status badge, last-sync clock, and recent log pane refresh automatically while a pass is running.
- **Two run modes.** Run it directly with `python run.py`, or use the published Docker image.
- **Sidecar metadata.** Every download keeps `info.json`, `description`, and a thumbnail next to the video for downstream tooling.

## Quick start

### Standalone (no Docker)

```bash
pip install -r requirements.txt   # flask, yt-dlp
# Make sure ffmpeg is on PATH.
python run.py
```

Then open http://localhost:8080. Add TikTok profile URLs in the channels textarea and click **Save and sync now**.

### Docker

Pull the published image:

```bash
docker pull ghcr.io/jthickma/tiktok-dl:latest
```

Or use `compose.yaml`:

```bash
docker compose up -d
docker compose logs -f
```

The compose file mounts `./config`, `./downloads`, and `./logs` so everything is editable from the host. On first start, the container creates `./config/channels.txt` and `./config/archive.txt` if they are missing; `channels.txt` is also editable from the web UI.

## Configuration

All knobs are environment variables. Defaults are sensible — most users only set `CRON_SCHEDULE`.

| Variable | Default | Notes |
|---|---|---|
| `CRON_SCHEDULE` | `0 */6 * * *` | Cron expression for scheduled syncs. Standalone mode parses simple `*/N` patterns. |
| `OUTPUT_TEMPLATE` | `%(uploader)s/%(upload_date)s - %(title).80B [%(id)s].%(ext)s` | yt-dlp output template, relative to the downloads dir. |
| `MAX_DOWNLOADS` | `0` | Per-channel cap per run. `0` = unlimited. |
| `RETRIES` | `10` | yt-dlp retry count for fragments and the overall request. |
| `SOCKET_TIMEOUT` | `30` | Per-request socket timeout (seconds). |
| `CONCURRENT_FRAGMENTS` | `4` | Parallel fragment downloads per video. |
| `PORT` | `8080` | Web UI port (standalone mode). |
| `CHANNELS_FILE` | `/config/channels.txt` (Docker) / `./channels.txt` | One TikTok profile URL per line. `#` starts a comment. Created on startup if missing. |
| `ARCHIVE_FILE` | `/config/archive.txt` / `./archive.txt` | yt-dlp's "already downloaded" log. Created on startup if missing. |
| `COOKIES_FILE` | `/config/cookies.txt` | Optional. Used if present (Netscape format). |
| `DOWNLOADS_DIR` | `/downloads` / `./downloads` | Where finished videos land. |
| `LOG_FILE` | `/logs/download.log` / `./logs/download.log` | Log destination. |

## Web UI

- **Top tile** — live worker state (Idle / Syncing / Queued) and the time since the last completed pass. Updates every few seconds via `/api/status`.
- **Subscriptions** — edit `channels.txt` inline, then save (which optionally triggers a sync). Saving the file from any other tool also triggers a sync via the channel watcher.
- **Media browser** — paginated grid filtered by creator and free-text search across title, caption, source URL, uploader, and filename. Thumbnails are static `<img>`s until clicked, then upgraded to a `<video>` element — keeps the page snappy with hundreds of items.
- **Recent logs** — tail of the worker log, refreshed in place.

## How sync requests are handled

```
request_download.sh  →  writes PENDING flag, starts worker if idle
        ↓
download_worker.sh   →  drain flag, run download.sh, repeat if flag re-appeared
        ↓
download.sh          →  yt-dlp per channel, never aborts on per-video errors
```

A request that arrives while the worker is busy just sets the pending flag — the worker notices it on its way out and does another pass. No duplicate work, no dropped requests, no cron pile-ups.

## Troubleshooting

- **"Sync queued behind the active pass"** — that's normal: a pass was already in flight, your request is staged.
- **No videos downloading** — check `logs/download.log`. Common causes: profile is private (needs `cookies.txt`), profile is empty, all posts already in `archive.txt`. To re-download something, remove its line from `archive.txt`.
- **High CPU during sync** — lower `CONCURRENT_FRAGMENTS`. Default 4 is friendly.
- **Stale media in the UI** — the cache refreshes every 5 minutes in the background; saving channels invalidates it immediately.

## CI

`.github/workflows/docker-ghcr.yml` builds a multi-arch image (amd64 + arm64) on every push and publishes to `ghcr.io/<owner>/<repo>` with branch, tag, short-SHA, and `latest` (on the default branch).

#!/usr/bin/env python3
"""Standalone launcher for tiktok-dl.

Bootstraps state files, starts the Django dev server, schedules periodic syncs,
and watches channels.txt for changes — without Docker, supercronic, or inotifywait.

Usage:
    python run.py

Environment variables (all optional):
    CHANNELS_FILE       path to channels.txt          (default: ./channels.txt)
    ARCHIVE_FILE        path to archive.txt           (default: ./archive.txt)
    DOWNLOADS_DIR       path to downloads folder      (default: ./downloads)
    LOG_FILE            path to download log          (default: ./logs/download.log)
    PID_FILE            worker PID file               (default: /tmp/tiktok-dl.pid)
    PENDING_FILE        pending-sync flag             (default: /tmp/tiktok-dl.pending)
    REQUEST_SCRIPT      request_download.sh path      (default: ./request_download.sh)
    CRON_SCHEDULE       cron expression               (default: 0 */6 * * *)
    PORT                web UI port                   (default: 8080)
    OUTPUT_TEMPLATE     yt-dlp output template
    MAX_DOWNLOADS       max downloads per channel per run (0 = unlimited)
"""

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_BASE = Path(__file__).parent

_defaults = {
    "CHANNELS_FILE": str(_BASE / "channels.txt"),
    "ARCHIVE_FILE": str(_BASE / "archive.txt"),
    "DOWNLOADS_DIR": str(_BASE / "downloads"),
    "LOG_FILE": str(_BASE / "logs" / "download.log"),
    "PID_FILE": "/tmp/tiktok-dl.pid",
    "PENDING_FILE": "/tmp/tiktok-dl.pending",
    "LAST_RUN_FILE": "/tmp/tiktok-dl.last-run",
    "REQUEST_SCRIPT": str(_BASE / "request_download.sh"),
    "DJANGO_SETTINGS_MODULE": "tiktokdl.settings",
}
for key, val in _defaults.items():
    os.environ.setdefault(key, val)

CHANNELS_FILE = Path(os.environ["CHANNELS_FILE"])
DOWNLOADS_DIR = Path(os.environ["DOWNLOADS_DIR"])
LOG_FILE = Path(os.environ["LOG_FILE"])
ARCHIVE_FILE = Path(os.environ["ARCHIVE_FILE"])
REQUEST_SCRIPT = os.environ["REQUEST_SCRIPT"]
CRON_SCHEDULE = os.environ.get("CRON_SCHEDULE", "0 */6 * * *")
PORT = int(os.environ.get("PORT", "8080"))


def _ensure_state_file(path: Path) -> Path:
    if path.exists() and path.is_dir():
        path = path / path.name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def _parse_interval(schedule: str) -> int:
    parts = schedule.strip().split()
    if len(parts) == 5:
        minute_field, hour_field = parts[0], parts[1]
        if hour_field.startswith("*/"):
            try:
                return int(hour_field[2:]) * 3600
            except ValueError:
                pass
        if minute_field.startswith("*/") and hour_field == "*":
            try:
                return int(minute_field[2:]) * 60
            except ValueError:
                pass
    return 6 * 3600


def _trigger_download(source: str = "scheduled") -> None:
    subprocess.run([REQUEST_SCRIPT, source], check=False)


def _scheduler(interval: int) -> None:
    while True:
        time.sleep(interval)
        _trigger_download("scheduled")


def _watch_channels() -> None:
    def _md5(p: Path) -> str:
        try:
            return hashlib.md5(p.read_bytes()).hexdigest()
        except OSError:
            return ""

    current = _md5(CHANNELS_FILE)
    while True:
        time.sleep(5)
        new = _md5(CHANNELS_FILE)
        if new != current:
            current = new
            print("channels.txt changed — triggering sync", flush=True)
            _trigger_download("watch")
            time.sleep(10)


def main() -> None:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.touch(exist_ok=True)
    _ensure_state_file(ARCHIVE_FILE)
    _ensure_state_file(CHANNELS_FILE)

    interval = _parse_interval(CRON_SCHEDULE)
    print("=== tiktok-dl (standalone) ===")
    print(f"Channels : {CHANNELS_FILE}")
    print(f"Downloads: {DOWNLOADS_DIR}")
    print(f"Logs     : {LOG_FILE}")
    print(f"Schedule : every {interval // 60}m  ({CRON_SCHEDULE})")
    print(f"Web UI   : http://localhost:{PORT}")
    print()

    _trigger_download("startup")
    threading.Thread(target=_scheduler, args=(interval,), daemon=True).start()
    threading.Thread(target=_watch_channels, daemon=True).start()

    sys.argv = ["manage.py", "runserver", f"0.0.0.0:{PORT}", "--noreload"]
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

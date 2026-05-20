"""Helpers shared between views: formatting, worker-state queries, sidecar paths."""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings


@dataclass
class MediaEntry:
    creator: str
    title: str
    relative_path: str
    media_url: str
    poster_url: str | None
    info_url: str | None
    description_url: str | None
    file_name: str
    size_label: str
    modified_label: str
    source_url: str | None
    upload_date: str | None
    duration_label: str | None
    caption: str | None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def read_channels() -> str:
    return read_text(settings.CHANNELS_FILE)


def read_logs(lines: int = 120) -> str:
    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(settings.LOG_FILE)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout or "No logs yet."
    except OSError:
        return "No logs yet."


def normalize_channel(raw_line: str) -> str:
    return raw_line.split("#", 1)[0].strip()


def count_active_channels() -> int:
    return len([line for line in read_channels().splitlines() if normalize_channel(line)])


def count_archive_entries() -> int:
    return len([line for line in read_text(settings.ARCHIVE_FILE).splitlines() if line.strip()])


def is_running() -> bool:
    if not settings.PID_FILE.exists():
        return False
    try:
        pid = int(settings.PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError):
        return False


def is_queued() -> bool:
    return settings.PENDING_FILE.exists()


def last_run_label() -> str:
    try:
        ts = int(settings.LAST_RUN_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return "Never"
    delta = max(0, int(time.time()) - ts)
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h {(delta % 3600) // 60}m ago"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def worker_state() -> str:
    running = is_running()
    queued = is_queued()
    if running and queued:
        return "Syncing and queued"
    if running:
        return "Syncing now"
    if queued:
        return "Queued"
    return "Idle"


def request_download(source: str) -> str:
    result = subprocess.run(
        [settings.REQUEST_SCRIPT, source],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return "started"
    if result.returncode == 2:
        return "queued"
    message = result.stderr.strip() or result.stdout.strip() or "request failed"
    raise RuntimeError(message)


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def format_duration(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


_FILENAME_DATE_RE = re.compile(r"^(\d{8}) - ")


def format_upload_date(raw: str | None) -> str | None:
    if not raw:
        return None
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def upload_date_from_filename(file_name: str) -> str | None:
    match = _FILENAME_DATE_RE.match(file_name)
    return match.group(1) if match else None


def clean_title(file_name: str) -> str:
    title = Path(file_name).stem
    if title[:4].isdigit() and " - " in title:
        title = title.split(" - ", 1)[1]
    if " [" in title:
        title = title.rsplit(" [", 1)[0]
    return title.replace("_", " ")

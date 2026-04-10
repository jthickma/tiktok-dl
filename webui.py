#!/usr/bin/env python3
"""Web UI for managing TikTok subscriptions, sync runs, and downloaded media."""

import json
import mimetypes
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, redirect, render_template_string, request, send_file, url_for

app = Flask(__name__)

CHANNELS_FILE = Path("/config/channels.txt")
ARCHIVE_FILE = Path("/config/archive.txt")
DOWNLOADS_DIR = Path("/downloads")
LOG_FILE = Path("/logs/download.log")
PID_FILE = Path("/tmp/download.pid")
PENDING_FILE = Path("/tmp/download.pending")
REQUEST_SCRIPT = "/request_download.sh"
MEDIA_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
THUMBNAIL_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


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


PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>tiktok-dl</title>
  <style>
    :root {
      --bg: #0b1216;
      --bg-2: #101a1f;
      --panel: rgba(14, 22, 27, 0.86);
      --panel-strong: rgba(10, 17, 21, 0.92);
      --line: rgba(204, 178, 118, 0.2);
      --line-strong: rgba(204, 178, 118, 0.38);
      --text: #eef3ef;
      --muted: #9eaba8;
      --accent: #d6b06f;
      --accent-strong: #f3cd8b;
      --good: #85d3a6;
      --warn: #f1b66d;
      --bad: #f19587;
      --mono: "SFMono-Regular", "SF Mono", "Cascadia Code", "Roboto Mono", monospace;
      --serif: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      --sans: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif;
      --shadow: 0 28px 60px rgba(0, 0, 0, 0.35);
    }

    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      font-family: var(--sans);
      background:
        radial-gradient(circle at top left, rgba(214, 176, 111, 0.2), transparent 24rem),
        linear-gradient(180deg, #0f171c 0%, #0a1014 48%, #081014 100%);
    }

    a { color: inherit; }

    .shell {
      width: min(1380px, calc(100vw - 2rem));
      margin: 0 auto;
      padding: 1.25rem 0 2rem;
      animation: rise 320ms ease-out both;
    }

    .masthead {
      position: relative;
      overflow: hidden;
      padding: 1.25rem 1.25rem 1.4rem;
      border: 1px solid var(--line);
      background:
        linear-gradient(160deg, rgba(9, 15, 18, 0.96), rgba(16, 24, 30, 0.8)),
        linear-gradient(90deg, rgba(214, 176, 111, 0.1), transparent);
      box-shadow: var(--shadow);
    }

    .masthead::after {
      content: "";
      position: absolute;
      inset: auto -10% -35% auto;
      width: 18rem;
      height: 18rem;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(214, 176, 111, 0.16), transparent 68%);
      pointer-events: none;
    }

    .topbar,
    .overview,
    .workspace,
    .media-toolbar,
    .media-grid,
    .log-panel {
      display: grid;
      gap: 1rem;
    }

    .topbar {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      align-items: end;
    }

    .eyebrow {
      color: var(--accent);
      text-transform: uppercase;
      letter-spacing: 0.18em;
      font-size: 0.72rem;
      margin: 0 0 0.7rem;
    }

    h1 {
      margin: 0;
      font-family: var(--serif);
      font-size: clamp(2.4rem, 5vw, 4.7rem);
      line-height: 0.95;
      font-weight: 600;
      letter-spacing: -0.04em;
      max-width: 8ch;
    }

    .subhead {
      margin: 0.8rem 0 0;
      max-width: 50rem;
      color: var(--muted);
      font-size: 0.98rem;
      line-height: 1.7;
    }

    .status-tile {
      justify-self: end;
      min-width: 15rem;
      padding: 1rem 1.1rem;
      border: 1px solid var(--line);
      background: rgba(6, 11, 14, 0.5);
      backdrop-filter: blur(14px);
    }

    .status-label {
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.14em;
    }

    .status-value {
      display: flex;
      align-items: center;
      gap: 0.65rem;
      margin-top: 0.85rem;
      font-size: 1rem;
    }

    .status-dot {
      width: 0.8rem;
      height: 0.8rem;
      border-radius: 999px;
      background: var(--good);
      box-shadow: 0 0 0 0.35rem rgba(133, 211, 166, 0.12);
    }

    .status-dot.running {
      background: var(--warn);
      box-shadow: 0 0 0 0.35rem rgba(241, 182, 109, 0.14);
    }

    .status-dot.queued {
      background: var(--accent-strong);
      box-shadow: 0 0 0 0.35rem rgba(214, 176, 111, 0.15);
    }

    .overview {
      margin-top: 1.4rem;
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }

    .metric {
      padding: 1rem 0;
      border-top: 1px solid var(--line);
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }

    .metric strong {
      display: block;
      margin-top: 0.5rem;
      font-family: var(--serif);
      font-size: clamp(1.5rem, 3vw, 2.4rem);
      font-weight: 600;
      color: var(--text);
    }

    .section {
      margin-top: 1.25rem;
      padding: 1.15rem 1.2rem 1.25rem;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }

    .section-head {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1rem;
    }

    .section h2 {
      margin: 0;
      font-family: var(--serif);
      font-size: 1.6rem;
      font-weight: 600;
      letter-spacing: -0.02em;
    }

    .section p {
      margin: 0.35rem 0 0;
      color: var(--muted);
      line-height: 1.6;
      font-size: 0.95rem;
    }

    .workspace {
      grid-template-columns: minmax(0, 1.3fr) minmax(320px, 0.7fr);
      align-items: start;
    }

    .sidebar-list {
      display: grid;
      gap: 0.9rem;
      padding-top: 0.2rem;
    }

    .sidebar-item {
      padding-top: 0.9rem;
      border-top: 1px solid rgba(204, 178, 118, 0.14);
    }

    .sidebar-item:first-child {
      padding-top: 0;
      border-top: 0;
    }

    .sidebar-item span,
    .meta-grid span,
    .panel-note {
      display: block;
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }

    .sidebar-item strong,
    .meta-grid strong {
      display: block;
      margin-top: 0.45rem;
      font-size: 0.97rem;
      font-weight: 500;
      word-break: break-word;
    }

    textarea,
    input,
    select {
      width: 100%;
      border: 1px solid rgba(204, 178, 118, 0.18);
      background: rgba(4, 9, 12, 0.62);
      color: var(--text);
      border-radius: 0;
      padding: 0.78rem 0.9rem;
      font: inherit;
      transition: border-color 140ms ease, transform 140ms ease, background 140ms ease;
    }

    textarea {
      min-height: 23rem;
      resize: vertical;
      font-family: var(--mono);
      font-size: 0.85rem;
      line-height: 1.6;
    }

    textarea:focus,
    input:focus,
    select:focus {
      outline: none;
      border-color: var(--line-strong);
      background: rgba(8, 12, 15, 0.88);
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
      margin-top: 1rem;
    }

    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.45rem;
      border: 1px solid rgba(204, 178, 118, 0.26);
      background: rgba(214, 176, 111, 0.1);
      color: var(--text);
      padding: 0.78rem 1rem;
      cursor: pointer;
      text-decoration: none;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      font-size: 0.73rem;
      transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;
    }

    .btn:hover {
      transform: translateY(-1px);
      border-color: rgba(214, 176, 111, 0.48);
      background: rgba(214, 176, 111, 0.14);
    }

    .btn-primary {
      background: linear-gradient(135deg, rgba(214, 176, 111, 0.22), rgba(214, 176, 111, 0.08));
      border-color: rgba(214, 176, 111, 0.44);
    }

    .btn-strong {
      background: linear-gradient(135deg, rgba(214, 176, 111, 0.34), rgba(214, 176, 111, 0.16));
      border-color: rgba(214, 176, 111, 0.7);
    }

    .flash {
      margin-top: 1rem;
      padding: 0.95rem 1rem;
      border: 1px solid var(--line);
      background: rgba(6, 11, 14, 0.74);
    }

    .flash.ok { border-color: rgba(133, 211, 166, 0.45); color: var(--good); }
    .flash.warn { border-color: rgba(241, 182, 109, 0.45); color: var(--warn); }
    .flash.err { border-color: rgba(241, 149, 135, 0.5); color: var(--bad); }

    .media-toolbar {
      grid-template-columns: minmax(0, 1fr) minmax(0, 220px) auto;
      align-items: end;
    }

    .media-grid {
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      margin-top: 1rem;
    }

    .media-card {
      display: grid;
      gap: 0.85rem;
      padding: 0.95rem;
      border: 1px solid rgba(204, 178, 118, 0.16);
      background: var(--panel-strong);
      transition: transform 180ms ease, border-color 180ms ease, background 180ms ease;
    }

    .media-card:hover {
      transform: translateY(-2px);
      border-color: rgba(214, 176, 111, 0.38);
      background: rgba(8, 14, 17, 0.98);
    }

    .media-card video,
    .media-card img {
      width: 100%;
      aspect-ratio: 9 / 16;
      object-fit: cover;
      background: #05080a;
      border: 1px solid rgba(204, 178, 118, 0.12);
    }

    .media-card h3 {
      margin: 0;
      font-size: 1rem;
      line-height: 1.4;
      font-weight: 600;
    }

    .media-card .creator {
      color: var(--accent);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.72rem;
    }

    .meta-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.75rem;
    }

    .caption {
      color: var(--muted);
      line-height: 1.55;
      font-size: 0.88rem;
      display: -webkit-box;
      -webkit-line-clamp: 4;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .link-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.8rem;
      font-size: 0.84rem;
    }

    .link-row a {
      color: var(--accent-strong);
      text-decoration: none;
      border-bottom: 1px solid transparent;
    }

    .link-row a:hover {
      border-bottom-color: currentColor;
    }

    .empty-state {
      padding: 1rem 0 0.3rem;
      color: var(--muted);
      font-size: 0.95rem;
    }

    .log-panel pre {
      margin: 0;
      padding: 1rem;
      min-height: 12rem;
      border: 1px solid rgba(204, 178, 118, 0.16);
      background: rgba(4, 8, 10, 0.72);
      color: #c0cbc8;
      font-family: var(--mono);
      font-size: 0.82rem;
      line-height: 1.55;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }

    @keyframes rise {
      from {
        opacity: 0;
        transform: translateY(12px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    @media (max-width: 1024px) {
      .topbar,
      .workspace,
      .media-toolbar,
      .overview {
        grid-template-columns: 1fr;
      }

      .status-tile {
        justify-self: start;
      }
    }

    @media (max-width: 720px) {
      .shell {
        width: min(100vw - 1rem, 100%);
        padding-top: 0.5rem;
      }

      .masthead,
      .section {
        padding-left: 0.95rem;
        padding-right: 0.95rem;
      }

      .media-grid,
      .meta-grid {
        grid-template-columns: 1fr;
      }

      textarea {
        min-height: 17rem;
      }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="masthead">
      <div class="topbar">
        <div>
          <p class="eyebrow">TikTok Subscription Archive</p>
          <h1>tiktok-dl</h1>
          <p class="subhead">Track profile subscriptions, queue sync passes without dropping requests, preserve sidecar metadata from original posts, and browse the video archive directly from the control surface.</p>
        </div>
        <div class="status-tile">
          <span class="status-label">Current worker state</span>
          <div class="status-value">
            <span class="status-dot {% if running %}running{% elif queued %}queued{% endif %}"></span>
            <strong>{{ worker_state }}</strong>
          </div>
          <p class="subhead" style="margin-top:0.7rem; font-size:0.88rem;">{% if queued %}Another sync pass is already staged behind the active run.{% elif running %}The active pass will finish before a queued request is promoted.{% else %}No active sync. New requests start immediately.{% endif %}</p>
        </div>
      </div>

      <div class="overview">
        <div class="metric">
          <span>Profiles</span>
          <strong>{{ channel_count }}</strong>
        </div>
        <div class="metric">
          <span>Archive IDs</span>
          <strong>{{ archive_count }}</strong>
        </div>
        <div class="metric">
          <span>Media Files</span>
          <strong>{{ media_count }}</strong>
        </div>
        <div class="metric">
          <span>Library Size</span>
          <strong>{{ library_size }}</strong>
        </div>
      </div>

      {% if flash %}
      <div class="flash {{ flash_type }}">{{ flash }}</div>
      {% endif %}
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <h2>Subscriptions</h2>
          <p>Update the tracked profile list and request a sync pass. If one is already active, the request is queued instead of discarded.</p>
        </div>
      </div>
      <div class="workspace">
        <form method="post" action="/save">
          <textarea name="channels" spellcheck="false">{{ channels }}</textarea>
          <div class="actions">
            <button type="submit" class="btn btn-primary">Save channels</button>
            <button type="submit" formaction="/save?run=1" class="btn btn-strong">{{ run_button_label }}</button>
            <button type="submit" formaction="/run" class="btn">Request sync only</button>
          </div>
        </form>
        <aside class="sidebar-list">
          <div class="sidebar-item">
            <span>Channel file</span>
            <strong>{{ channel_file }}</strong>
          </div>
          <div class="sidebar-item">
            <span>Archive file</span>
            <strong>{{ archive_file }}</strong>
          </div>
          <div class="sidebar-item">
            <span>Downloads root</span>
            <strong>{{ downloads_dir }}</strong>
          </div>
          <div class="sidebar-item">
            <span>Notes</span>
            <strong>Video-only formats are kept. Audio-only `.mp3` and `.m4a` posts are filtered out before download.</strong>
          </div>
        </aside>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <h2>Media Browser</h2>
          <p>Filter the local archive by creator or search text, then stream videos inline and open their metadata sidecars.</p>
        </div>
      </div>

      <form method="get" action="/" class="media-toolbar">
        <label>
          <span class="panel-note">Search title, caption, creator, or source URL</span>
          <input type="text" name="q" value="{{ query }}" placeholder="Search archive">
        </label>
        <label>
          <span class="panel-note">Creator</span>
          <select name="creator">
            <option value="">All creators</option>
            {% for creator_name in creators %}
            <option value="{{ creator_name }}" {% if creator_name == selected_creator %}selected{% endif %}>{{ creator_name }}</option>
            {% endfor %}
          </select>
        </label>
        <div class="actions" style="margin-top:0;">
          <button type="submit" class="btn btn-primary">Apply filters</button>
          <a class="btn" href="/">Reset</a>
        </div>
      </form>

      {% if media_entries %}
      <div class="media-grid">
        {% for entry in media_entries %}
        <article class="media-card">
          <video controls preload="metadata" {% if entry.poster_url %}poster="{{ entry.poster_url }}"{% endif %}>
            <source src="{{ entry.media_url }}">
          </video>
          <div>
            <div class="creator">{{ entry.creator }}</div>
            <h3>{{ entry.title }}</h3>
          </div>
          <div class="meta-grid">
            <div>
              <span>Modified</span>
              <strong>{{ entry.modified_label }}</strong>
            </div>
            <div>
              <span>Size</span>
              <strong>{{ entry.size_label }}</strong>
            </div>
            <div>
              <span>Uploaded</span>
              <strong>{{ entry.upload_date or "Unknown" }}</strong>
            </div>
            <div>
              <span>Duration</span>
              <strong>{{ entry.duration_label or "Unknown" }}</strong>
            </div>
          </div>
          {% if entry.caption %}
          <div class="caption">{{ entry.caption }}</div>
          {% endif %}
          <div class="link-row">
            <a href="{{ entry.media_url }}" target="_blank" rel="noreferrer">Open video</a>
            {% if entry.info_url %}
            <a href="{{ entry.info_url }}" target="_blank" rel="noreferrer">Metadata JSON</a>
            {% endif %}
            {% if entry.description_url %}
            <a href="{{ entry.description_url }}" target="_blank" rel="noreferrer">Description</a>
            {% endif %}
            {% if entry.source_url %}
            <a href="{{ entry.source_url }}" target="_blank" rel="noreferrer">Original post</a>
            {% endif %}
          </div>
        </article>
        {% endfor %}
      </div>
      {% else %}
      <div class="empty-state">No media matched the current filters. Clear the filters or wait for the next sync pass.</div>
      {% endif %}
    </section>

    <section class="section log-panel">
      <div class="section-head">
        <div>
          <h2>Recent Logs</h2>
          <p>Latest sync activity from the worker queue, channel watcher, cron schedule, and yt-dlp runs.</p>
        </div>
      </div>
      <pre>{{ logs }}</pre>
    </section>
  </main>
</body>
</html>
"""


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def read_channels() -> str:
    return read_text(CHANNELS_FILE)


def read_logs(lines: int = 120) -> str:
    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(LOG_FILE)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout or "No logs yet."
    except OSError:
        return "No logs yet."


def count_active_channels() -> int:
    return len([line for line in read_channels().splitlines() if normalize_channel(line)])


def count_archive_entries() -> int:
    return len([line for line in read_text(ARCHIVE_FILE).splitlines() if line.strip()])


def normalize_channel(raw_line: str) -> str:
    stripped = raw_line.split("#", 1)[0].strip()
    return stripped


def is_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError):
        return False


def is_queued() -> bool:
    return PENDING_FILE.exists()


def request_download(source: str) -> str:
    result = subprocess.run(
        [REQUEST_SCRIPT, source],
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
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def format_upload_date(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    if re.fullmatch(r"\d{8}", raw_value):
        return f"{raw_value[:4]}-{raw_value[4:6]}-{raw_value[6:]}"
    return raw_value


def clean_title(file_name: str) -> str:
    title = Path(file_name).stem
    if title.startswith(tuple(str(year) for year in range(2000, 2100))) and " - " in title:
        title = title.split(" - ", 1)[1]
    if " [" in title:
        title = title.rsplit(" [", 1)[0]
    return title.replace("_", " ")


def sidecar_path(media_path: Path, suffix: str) -> Path:
    return media_path.with_name(f"{media_path.stem}{suffix}")


def find_thumbnail(media_path: Path) -> Path | None:
    for extension in THUMBNAIL_EXTENSIONS:
        candidate = sidecar_path(media_path, extension)
        if candidate.exists():
            return candidate
    return None


def load_metadata(info_path: Path) -> dict:
    if not info_path.exists():
        return {}
    try:
        return json.loads(info_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def relative_download_path(path: Path) -> str:
    return path.relative_to(DOWNLOADS_DIR).as_posix()


def download_url(path: Path | None) -> str | None:
    if path is None:
        return None
    return url_for("download_asset", relative_path=relative_download_path(path))


def gather_media_entries(query: str = "", creator: str = "") -> tuple[list[MediaEntry], list[str], int, int]:
    if not DOWNLOADS_DIR.exists():
        return [], [], 0, 0

    media_files = [
        path for path in DOWNLOADS_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
    ]
    media_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    creators = sorted({
        path.relative_to(DOWNLOADS_DIR).parts[0]
        for path in media_files
        if path.relative_to(DOWNLOADS_DIR).parts
    })

    normalized_query = query.strip().lower()
    matched_entries: list[MediaEntry] = []
    total_size = 0

    for media_path in media_files:
        stat = media_path.stat()
        total_size += stat.st_size

        relative_parts = media_path.relative_to(DOWNLOADS_DIR).parts
        media_creator = relative_parts[0] if len(relative_parts) > 1 else "root"
        if creator and media_creator != creator:
            continue

        info_path = sidecar_path(media_path, ".info.json")
        description_path = sidecar_path(media_path, ".description")
        metadata = load_metadata(info_path)
        thumbnail_path = find_thumbnail(media_path)

        title = metadata.get("title") or clean_title(media_path.name)
        caption = metadata.get("description")
        source_url = metadata.get("webpage_url") or metadata.get("original_url")
        searchable_text = " ".join([
            title,
            caption or "",
            source_url or "",
            metadata.get("uploader") or media_creator,
            media_path.name,
        ]).lower()
        if normalized_query and normalized_query not in searchable_text:
            continue

        matched_entries.append(
            MediaEntry(
                creator=metadata.get("uploader") or media_creator,
                title=title,
                relative_path=relative_download_path(media_path),
                media_url=download_url(media_path),
                poster_url=download_url(thumbnail_path),
                info_url=download_url(info_path) if info_path.exists() else None,
                description_url=download_url(description_path) if description_path.exists() else None,
                file_name=media_path.name,
                size_label=format_bytes(stat.st_size),
                modified_label=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                source_url=source_url,
                upload_date=format_upload_date(metadata.get("upload_date")),
                duration_label=format_duration(metadata.get("duration")),
                caption=caption,
            )
        )

    return matched_entries, creators, len(media_files), total_size


def resolve_download_path(relative_path: str) -> Path:
    candidate = (DOWNLOADS_DIR / relative_path).resolve()
    root = DOWNLOADS_DIR.resolve()
    if candidate == root or root not in candidate.parents:
        abort(404)
    if not candidate.is_file():
        abort(404)
    return candidate


@app.route("/", methods=["GET"])
def index():
    flash = request.args.get("flash", "")
    flash_type = request.args.get("ft", "ok")
    query = request.args.get("q", "").strip()
    selected_creator = request.args.get("creator", "").strip()
    running = is_running()
    queued = is_queued()
    channels = read_channels()
    logs = read_logs()
    media_entries, creators, media_count, total_size = gather_media_entries(query, selected_creator)

    if running and queued:
        worker_state = "Syncing and queued"
    elif running:
        worker_state = "Syncing now"
    elif queued:
        worker_state = "Queued"
    else:
        worker_state = "Idle"

    run_button_label = "Save and queue sync" if running or queued else "Save and sync now"

    return render_template_string(
        PAGE_TEMPLATE,
        flash=flash,
        flash_type=flash_type,
        running=running,
        queued=queued,
        worker_state=worker_state,
        channel_count=count_active_channels(),
        archive_count=count_archive_entries(),
        media_count=media_count,
        library_size=format_bytes(total_size),
        channels=channels,
        channel_file=str(CHANNELS_FILE),
        archive_file=str(ARCHIVE_FILE),
        downloads_dir=str(DOWNLOADS_DIR),
        logs=logs,
        media_entries=media_entries,
        creators=creators,
        query=query,
        selected_creator=selected_creator,
        run_button_label=run_button_label,
    )


@app.route("/save", methods=["POST"])
def save():
    channels = request.form.get("channels", "").replace("\r\n", "\n")
    if not channels.endswith("\n"):
        channels += "\n"

    CHANNELS_FILE.write_text(channels, encoding="utf-8")

    if request.args.get("run"):
        try:
            result = request_download("web-save")
        except RuntimeError as exc:
            return redirect(url_for("index", flash=f"Saved, but sync request failed: {exc}", ft="err"))
        if result == "queued":
            return redirect(url_for("index", flash="Channels saved. Sync queued behind the active pass.", ft="warn"))
        return redirect(url_for("index", flash="Channels saved. Sync started.", ft="ok"))

    return redirect(url_for("index", flash="Channels saved.", ft="ok"))


@app.route("/run", methods=["POST"])
def run():
    try:
        result = request_download("web-run")
    except RuntimeError as exc:
        return redirect(url_for("index", flash=f"Sync request failed: {exc}", ft="err"))
    if result == "queued":
        return redirect(url_for("index", flash="Sync queued behind the active pass.", ft="warn"))
    return redirect(url_for("index", flash="Sync started.", ft="ok"))


@app.route("/downloads/<path:relative_path>", methods=["GET"])
def download_asset(relative_path: str):
    file_path = resolve_download_path(relative_path)
    mimetype, _ = mimetypes.guess_type(file_path.name)
    return send_file(file_path, mimetype=mimetype or "application/octet-stream")


if __name__ == "__main__":
    os.makedirs("/logs", exist_ok=True)
    os.makedirs("/downloads", exist_ok=True)
    app.run(host="0.0.0.0", port=8080)

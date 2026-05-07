#!/usr/bin/env python3
"""Web UI for managing TikTok subscriptions, sync runs, and downloaded media."""

import json
import mimetypes
import os
import re
import stat as stat_mod
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_file, url_for
from jinja2 import Template

app = Flask(__name__)

_BASE = Path(__file__).parent
CHANNELS_FILE = Path(os.environ.get("CHANNELS_FILE", _BASE / "channels.txt"))
ARCHIVE_FILE = Path(os.environ.get("ARCHIVE_FILE", _BASE / "archive.txt"))
DOWNLOADS_DIR = Path(os.environ.get("DOWNLOADS_DIR", _BASE / "downloads"))
LOG_FILE = Path(os.environ.get("LOG_FILE", _BASE / "logs" / "download.log"))
PID_FILE = Path(os.environ.get("PID_FILE", "/tmp/tiktok-dl.pid"))
PENDING_FILE = Path(os.environ.get("PENDING_FILE", "/tmp/tiktok-dl.pending"))
LAST_RUN_FILE = Path(os.environ.get("LAST_RUN_FILE", "/tmp/tiktok-dl.last-run"))
REQUEST_SCRIPT = os.environ.get("REQUEST_SCRIPT", str(_BASE / "request_download.sh"))
MEDIA_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
THUMBNAIL_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
PER_PAGE = 24
CACHE_TTL = 300  # seconds — async background refresh, served stale meanwhile


def _ensure_state_file(path: Path) -> Path:
    if path.exists() and path.is_dir():
        path = path / path.name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def ensure_state_files() -> None:
    global CHANNELS_FILE, ARCHIVE_FILE

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.touch(exist_ok=True)
    CHANNELS_FILE = _ensure_state_file(CHANNELS_FILE)
    ARCHIVE_FILE = _ensure_state_file(ARCHIVE_FILE)
    os.environ["CHANNELS_FILE"] = str(CHANNELS_FILE)
    os.environ["ARCHIVE_FILE"] = str(ARCHIVE_FILE)


ensure_state_files()


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


# ---------------------------------------------------------------------------
# Media-index cache — avoid rglob + stat + JSON parse on every page load
# ---------------------------------------------------------------------------

@dataclass
class _CachedFile:
    path: Path
    creator: str
    size: int
    mtime: float
    metadata: dict
    thumbnail: Path | None
    info_path: Path | None
    description_path: Path | None


class _MediaCache:
    """Incremental media index. Keeps parsed metadata; only re-stats/re-parses changed files.

    Initial build is synchronous (cold start). Subsequent refreshes run in a
    background thread so requests never block on rglob + JSON parse.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_path: dict[Path, _CachedFile] = {}
        self._sorted: list[_CachedFile] = []
        self._creators: list[str] = []
        self._total_size: int = 0
        self._built_at: float = 0.0
        self._refreshing = False

    def _scan_once(self) -> None:
        prior = self._by_path
        new_by_path: dict[Path, _CachedFile] = {}
        creator_set: set[str] = set()
        total_size = 0

        for path in DOWNLOADS_DIR.rglob("*"):
            if path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if not stat_mod.S_ISREG(stat.st_mode):
                continue

            existing = prior.get(path)
            if existing and existing.mtime == stat.st_mtime and existing.size == stat.st_size:
                cf = existing
            else:
                rel = path.relative_to(DOWNLOADS_DIR).parts
                creator = rel[0] if len(rel) > 1 else "root"
                info_path = path.with_name(f"{path.stem}.info.json")
                metadata: dict = {}
                info_real: Path | None = None
                if info_path.exists():
                    info_real = info_path
                    try:
                        metadata = json.loads(info_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        pass
                thumb: Path | None = None
                for ext in THUMBNAIL_EXTENSIONS:
                    cand = path.with_name(f"{path.stem}{ext}")
                    if cand.exists():
                        thumb = cand
                        break
                desc_path = path.with_name(f"{path.stem}.description")
                desc_real = desc_path if desc_path.exists() else None
                cf = _CachedFile(
                    path=path, creator=creator, size=stat.st_size,
                    mtime=stat.st_mtime, metadata=metadata,
                    thumbnail=thumb, info_path=info_real, description_path=desc_real,
                )
            new_by_path[path] = cf
            total_size += cf.size
            creator_set.add(cf.creator)

        sorted_files = sorted(new_by_path.values(), key=lambda e: e.mtime, reverse=True)

        with self._lock:
            self._by_path = new_by_path
            self._sorted = sorted_files
            self._creators = sorted(creator_set)
            self._total_size = total_size
            self._built_at = time.monotonic()

    def _refresh_async(self) -> None:
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True

        def _runner() -> None:
            try:
                self._scan_once()
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=_runner, daemon=True).start()

    def get(self) -> tuple[list[_CachedFile], list[str], int, int]:
        if self._built_at == 0.0:
            self._scan_once()  # cold start: must block
        elif time.monotonic() - self._built_at > CACHE_TTL:
            self._refresh_async()  # stale: refresh in background, serve stale now
        with self._lock:
            return self._sorted, self._creators, len(self._sorted), self._total_size

    def invalidate(self) -> None:
        if self._built_at == 0.0:
            return  # cold anyway; get() will build
        self._refresh_async()


_media_cache = _MediaCache()


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
      grid-template-columns: repeat(5, minmax(0, 1fr));
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
    .media-card img,
    .media-thumb {
      width: 100%;
      aspect-ratio: 9 / 16;
      object-fit: cover;
      background: #05080a;
      border: 1px solid rgba(204, 178, 118, 0.12);
    }

    .media-thumb {
      position: relative;
      display: block;
      cursor: pointer;
      overflow: hidden;
    }

    .media-thumb img {
      width: 100%;
      height: 100%;
      border: 0;
    }

    .media-thumb-empty {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      height: 100%;
      color: var(--muted);
      font-size: 0.85rem;
    }

    .media-thumb .play-btn {
      position: absolute;
      inset: 0;
      margin: auto;
      width: 3.2rem;
      height: 3.2rem;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.5);
      background: rgba(0, 0, 0, 0.55);
      color: #fff;
      font-size: 1rem;
      cursor: pointer;
      transition: transform 140ms ease, background 140ms ease;
    }

    .media-thumb:hover .play-btn {
      transform: scale(1.08);
      background: rgba(0, 0, 0, 0.75);
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

    .pagination {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
      margin-top: 1.2rem;
    }

    .pagination a,
    .pagination span {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 2.4rem;
      padding: 0.5rem 0.7rem;
      border: 1px solid rgba(204, 178, 118, 0.2);
      background: rgba(6, 11, 14, 0.5);
      color: var(--muted);
      font-size: 0.82rem;
      text-decoration: none;
      transition: border-color 140ms ease, background 140ms ease, color 140ms ease;
    }

    .pagination a:hover {
      border-color: rgba(214, 176, 111, 0.48);
      background: rgba(214, 176, 111, 0.1);
      color: var(--text);
    }

    .pagination .current {
      border-color: rgba(214, 176, 111, 0.5);
      background: rgba(214, 176, 111, 0.16);
      color: var(--accent-strong);
      font-weight: 600;
    }

    .pagination .info {
      border: none;
      background: none;
      color: var(--muted);
      font-size: 0.8rem;
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

    @media (max-width: 1180px) {
      .overview {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
    }

    @media (max-width: 1024px) {
      .topbar,
      .workspace,
      .media-toolbar {
        grid-template-columns: 1fr;
      }

      .overview {
        grid-template-columns: repeat(2, minmax(0, 1fr));
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
    /* Modal Lightbox */
    .modal {
      display: none;
      position: fixed;
      inset: 0;
      z-index: 1000;
      background: rgba(4, 8, 10, 0.95);
      backdrop-filter: blur(8px);
      align-items: center;
      justify-content: center;
      opacity: 0;
      transition: opacity 200ms ease;
    }

    .modal.open {
      display: flex;
      opacity: 1;
    }

    .modal-content {
      position: relative;
      width: 100%;
      height: 100%;
      max-width: 1200px;
      max-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .modal-video-container {
      position: relative;
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem 4rem;
    }

    .modal video {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      box-shadow: var(--shadow);
      background: #000;
    }

    .modal-close {
      position: absolute;
      top: 1rem;
      right: 1.5rem;
      background: rgba(255, 255, 255, 0.1);
      border: 1px solid rgba(255, 255, 255, 0.2);
      color: #fff;
      font-size: 1.5rem;
      width: 3rem;
      height: 3rem;
      border-radius: 50%;
      cursor: pointer;
      z-index: 1001;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 140ms ease;
    }

    .modal-close:hover {
      background: rgba(255, 255, 255, 0.2);
    }

    .modal-nav {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      background: rgba(0, 0, 0, 0.5);
      border: 1px solid rgba(255, 255, 255, 0.2);
      color: #fff;
      font-size: 1.5rem;
      width: 3.5rem;
      height: 3.5rem;
      border-radius: 50%;
      cursor: pointer;
      z-index: 1001;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 140ms ease, transform 140ms ease;
    }

    .modal-nav:hover {
      background: rgba(0, 0, 0, 0.8);
      transform: translateY(-50%) scale(1.05);
    }

    .modal-nav.prev { left: 1rem; }
    .modal-nav.next { right: 1rem; }

    @media (max-width: 720px) {
      .modal-video-container {
        padding: 1rem 0;
      }
      .modal-nav {
        width: 2.5rem;
        height: 2.5rem;
      }
      .modal-nav.prev { left: 0.5rem; }
      .modal-nav.next { right: 0.5rem; }
      .modal-close {
        top: 0.5rem;
        right: 0.5rem;
        width: 2.5rem;
        height: 2.5rem;
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
            <span class="status-dot {% if running %}running{% elif queued %}queued{% endif %}" data-status-dot></span>
            <strong data-status-text>{{ worker_state }}</strong>
          </div>
          <p class="subhead" style="margin-top:0.7rem; font-size:0.88rem;" data-status-hint>{% if queued %}Another sync pass is already staged behind the active run.{% elif running %}The active pass will finish before a queued request is promoted.{% else %}No active sync. New requests start immediately.{% endif %}</p>
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
        <div class="metric">
          <span>Last Sync</span>
          <strong data-last-run>{{ last_run }}</strong>
        </div>
      </div>

      {% if flash %}
      <div class="flash {{ flash_type }}" data-flash>{{ flash }}</div>
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
          <div class="media-thumb" data-media="{{ entry.media_url }}"{% if entry.poster_url %} data-poster="{{ entry.poster_url }}"{% endif %}>
            {% if entry.poster_url %}
            <img src="{{ entry.poster_url }}" loading="lazy" decoding="async" alt="">
            {% else %}
            <div class="media-thumb-empty">No preview</div>
            {% endif %}
            <button class="play-btn" type="button" aria-label="Play">▶</button>
          </div>
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

      {% if total_pages > 1 %}
      <nav class="pagination">
        <span class="info">{{ matched_count }} result{{ "s" if matched_count != 1 else "" }}</span>
        {% if page > 1 %}
        <a href="{{ page_url(1) }}">1</a>
        {% endif %}
        {% if page > 2 %}
        <a href="{{ page_url(page - 1) }}">&laquo; Prev</a>
        {% endif %}
        <span class="current">{{ page }}</span>
        {% if page < total_pages %}
        <a href="{{ page_url(page + 1) }}">Next &raquo;</a>
        {% endif %}
        {% if page < total_pages %}
        <a href="{{ page_url(total_pages) }}">{{ total_pages }}</a>
        {% endif %}
      </nav>
      {% endif %}

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
      <pre data-log-pane>{{ logs }}</pre>
    </section>
  </main>

  <div class="modal" id="media-modal">
    <button class="modal-close" aria-label="Close" id="modal-close">&times;</button>
    <div class="modal-content">
      <button class="modal-nav prev" aria-label="Previous" id="modal-prev">&#10094;</button>
      <div class="modal-video-container" id="modal-video-container">
        <!-- Video injected here -->
      </div>
      <button class="modal-nav next" aria-label="Next" id="modal-next">&#10095;</button>
    </div>
  </div>

  <script>
  // Modal Lightbox Player
  (function() {
    var modal = document.getElementById('media-modal');
    var container = document.getElementById('modal-video-container');
    var closeBtn = document.getElementById('modal-close');
    var prevBtn = document.getElementById('modal-prev');
    var nextBtn = document.getElementById('modal-next');
    var currentThumb = null;

    function openModal(thumb) {
      if (!thumb || !thumb.dataset.media) return;
      currentThumb = thumb;
      var url = thumb.dataset.media;
      var poster = thumb.dataset.poster || '';
      
      container.innerHTML = '';
      var video = document.createElement('video');
      video.controls = true;
      video.autoplay = true;
      video.preload = 'metadata';
      if (poster) video.poster = poster;
      video.src = url;
      container.appendChild(video);
      
      modal.classList.add('open');
      updateNavButtons();
    }

    function closeModal() {
      modal.classList.remove('open');
      container.innerHTML = '';
      currentThumb = null;
    }

    function getSiblings() {
      return Array.from(document.querySelectorAll('.media-thumb[data-media]'));
    }

    function updateNavButtons() {
      if (!currentThumb) return;
      var thumbs = getSiblings();
      var index = thumbs.indexOf(currentThumb);
      prevBtn.style.display = index > 0 ? 'flex' : 'none';
      nextBtn.style.display = index >= 0 && index < thumbs.length - 1 ? 'flex' : 'none';
    }

    function navigate(direction) {
      if (!currentThumb) return;
      var thumbs = getSiblings();
      var index = thumbs.indexOf(currentThumb);
      var nextIndex = index + direction;
      if (nextIndex >= 0 && nextIndex < thumbs.length) {
        openModal(thumbs[nextIndex]);
      }
    }

    document.addEventListener('click', function(ev) {
      var thumb = ev.target.closest('.media-thumb');
      if (thumb) {
        ev.preventDefault();
        openModal(thumb);
      }
    }, false);

    closeBtn.addEventListener('click', closeModal);
    prevBtn.addEventListener('click', function() { navigate(-1); });
    nextBtn.addEventListener('click', function() { navigate(1); });

    // Close on background click
    modal.addEventListener('click', function(ev) {
      if (ev.target === modal || ev.target === modal.querySelector('.modal-content') || ev.target === container) {
        closeModal();
      }
    });

    // Keyboard navigation
    document.addEventListener('keydown', function(ev) {
      if (!modal.classList.contains('open')) return;
      if (ev.key === 'Escape') closeModal();
      else if (ev.key === 'ArrowLeft') navigate(-1);
      else if (ev.key === 'ArrowRight') navigate(1);
    });
  })();

  // Auto-dismiss flash after 6s.
  (function() {
    var flash = document.querySelector('[data-flash]');
    if (!flash) return;
    setTimeout(function() {
      flash.style.transition = 'opacity 400ms ease';
      flash.style.opacity = '0';
      setTimeout(function() { flash.remove(); }, 450);
    }, 6000);
  })();

  // Live status + log refresh — polls /api/status. Faster cadence while running.
  (function() {
    var dot = document.querySelector('[data-status-dot]');
    var text = document.querySelector('[data-status-text]');
    var hint = document.querySelector('[data-status-hint]');
    var lastRun = document.querySelector('[data-last-run]');
    var logPane = document.querySelector('[data-log-pane]');
    if (!dot || !text) return;
    var lastState = '';
    var hints = {
      'Syncing and queued': 'Another sync pass is already staged behind the active run.',
      'Syncing now':        'The active pass will finish before a queued request is promoted.',
      'Queued':             'Another sync pass is already staged behind the active run.',
      'Idle':               'No active sync. New requests start immediately.'
    };
    function poll() {
      fetch('/api/status', {cache: 'no-store'}).then(function(r) { return r.json(); }).then(function(s) {
        dot.classList.toggle('running', !!s.running);
        dot.classList.toggle('queued', !s.running && !!s.queued);
        text.textContent = s.state;
        if (hint) hint.textContent = hints[s.state] || hints['Idle'];
        if (lastRun) lastRun.textContent = s.last_run;
        if (logPane && s.logs) {
          var atBottom = (logPane.scrollTop + logPane.clientHeight) >= (logPane.scrollHeight - 8);
          logPane.textContent = s.logs;
          if (atBottom) logPane.scrollTop = logPane.scrollHeight;
        }
        // Reload when a sync just finished — surfaces freshly-downloaded media.
        if (lastState && (lastState.indexOf('Syncing') === 0) && s.state === 'Idle') {
          window.location.reload();
        }
        lastState = s.state;
        var delay = s.running || s.queued ? 3000 : 12000;
        setTimeout(poll, delay);
      }).catch(function() { setTimeout(poll, 15000); });
    }
    setTimeout(poll, 2000);
  })();
  </script>
</body>
</html>
"""

# Pre-compile template once at import time
_compiled_template = Template(PAGE_TEMPLATE)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
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


def last_run_label() -> str:
    try:
        ts = int(LAST_RUN_FILE.read_text(encoding="utf-8").strip())
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


_FILENAME_DATE_RE = re.compile(r"^(\d{8}) - ")


def upload_date_from_filename(file_name: str) -> str | None:
    """Filenames are `YYYYMMDD - title [id].ext`. Fallback when info.json missing."""
    match = _FILENAME_DATE_RE.match(file_name)
    if not match:
        return None
    return match.group(1)


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


def relative_download_path(path: Path) -> str:
    return path.relative_to(DOWNLOADS_DIR).as_posix()


def download_url(path: Path | None) -> str | None:
    if path is None:
        return None
    return url_for("download_asset", relative_path=relative_download_path(path))


def gather_media_entries(
    query: str = "", creator: str = "", page: int = 1,
) -> tuple[list[MediaEntry], list[str], int, int, int]:
    """Return (page_entries, creators, total_media_count, total_size, matched_count)."""
    if not DOWNLOADS_DIR.exists():
        return [], [], 0, 0, 0

    cached_files, creators, media_count, total_size = _media_cache.get()

    normalized_query = query.strip().lower()
    matched: list[_CachedFile] = []

    for cf in cached_files:
        if creator and cf.creator != creator:
            continue

        if normalized_query:
            title = cf.metadata.get("title") or clean_title(cf.path.name)
            caption = cf.metadata.get("description") or ""
            source_url = cf.metadata.get("webpage_url") or cf.metadata.get("original_url") or ""
            uploader = cf.metadata.get("uploader") or cf.creator
            searchable = f"{title} {caption} {source_url} {uploader} {cf.path.name}".lower()
            if normalized_query not in searchable:
                continue

        matched.append(cf)

    matched_count = len(matched)

    # Paginate
    start = (page - 1) * PER_PAGE
    page_files = matched[start : start + PER_PAGE]

    entries: list[MediaEntry] = []
    for cf in page_files:
        md = cf.metadata
        upload_raw = md.get("upload_date") or upload_date_from_filename(cf.path.name)

        entries.append(
            MediaEntry(
                creator=md.get("uploader") or cf.creator,
                title=md.get("title") or clean_title(cf.path.name),
                relative_path=relative_download_path(cf.path),
                media_url=download_url(cf.path),
                poster_url=download_url(cf.thumbnail),
                info_url=download_url(cf.info_path),
                description_url=download_url(cf.description_path),
                file_name=cf.path.name,
                size_label=format_bytes(cf.size),
                modified_label=datetime.fromtimestamp(cf.mtime).strftime("%Y-%m-%d %H:%M"),
                source_url=md.get("webpage_url") or md.get("original_url"),
                upload_date=format_upload_date(upload_raw),
                duration_label=format_duration(md.get("duration")),
                caption=md.get("description"),
            )
        )

    return entries, creators, media_count, total_size, matched_count


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
    page = max(1, request.args.get("page", 1, type=int))
    running = is_running()
    queued = is_queued()
    channels = read_channels()
    logs = read_logs()
    media_entries, creators, media_count, total_size, matched_count = gather_media_entries(
        query, selected_creator, page,
    )

    total_pages = max(1, (matched_count + PER_PAGE - 1) // PER_PAGE)
    if page > total_pages:
        page = total_pages

    if running and queued:
        worker_state = "Syncing and queued"
    elif running:
        worker_state = "Syncing now"
    elif queued:
        worker_state = "Queued"
    else:
        worker_state = "Idle"

    run_button_label = "Save and queue sync" if running or queued else "Save and sync now"

    def page_url(p: int) -> str:
        params = {}
        if query:
            params["q"] = query
        if selected_creator:
            params["creator"] = selected_creator
        if p > 1:
            params["page"] = str(p)
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return f"/?{qs}" if qs else "/"

    return _compiled_template.render(
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
        page=page,
        total_pages=total_pages,
        matched_count=matched_count,
        page_url=page_url,
        last_run=last_run_label(),
    )


@app.route("/save", methods=["POST"])
def save():
    ensure_state_files()
    channels = request.form.get("channels", "").replace("\r\n", "\n")
    if not channels.endswith("\n"):
        channels += "\n"

    CHANNELS_FILE.write_text(channels, encoding="utf-8")
    _media_cache.invalidate()

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


@app.route("/api/status", methods=["GET"])
def api_status():
    running = is_running()
    queued = is_queued()
    if running and queued:
        state = "Syncing and queued"
    elif running:
        state = "Syncing now"
    elif queued:
        state = "Queued"
    else:
        state = "Idle"
    return jsonify(
        running=running,
        queued=queued,
        state=state,
        last_run=last_run_label(),
        logs=read_logs(40),
    )


@app.route("/downloads/<path:relative_path>", methods=["GET"])
def download_asset(relative_path: str):
    file_path = resolve_download_path(relative_path)
    mimetype, _ = mimetypes.guess_type(file_path.name)
    response = send_file(
        file_path,
        mimetype=mimetype or "application/octet-stream",
        conditional=True,
    )
    # Files are content-addressed by path; safe to cache aggressively.
    # Flask's send_file(conditional=True) sets no_cache; clear it before our override.
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


if __name__ == "__main__":
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_FILE.touch(exist_ok=True)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

"""Incremental media-index cache.

Avoids rglob + stat + JSON parse on every page load. Initial build blocks; later
refreshes run in a background thread so requests serve stale results meanwhile.
"""
from __future__ import annotations

import json
import stat as stat_mod
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

MEDIA_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
THUMBNAIL_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
CACHE_TTL = 300  # seconds


@dataclass
class CachedFile:
    path: Path
    creator: str
    size: int
    mtime: float
    metadata: dict
    thumbnail: Path | None
    info_path: Path | None
    description_path: Path | None


class MediaCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_path: dict[Path, CachedFile] = {}
        self._sorted: list[CachedFile] = []
        self._creators: list[str] = []
        self._total_size: int = 0
        self._built_at: float = 0.0
        self._refreshing = False

    def _scan_once(self) -> None:
        downloads_dir: Path = settings.DOWNLOADS_DIR
        prior = self._by_path
        new_by_path: dict[Path, CachedFile] = {}
        creator_set: set[str] = set()
        total_size = 0

        if not downloads_dir.exists():
            with self._lock:
                self._by_path = {}
                self._sorted = []
                self._creators = []
                self._total_size = 0
                self._built_at = time.monotonic()
            return

        for path in downloads_dir.rglob("*"):
            if path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if not stat_mod.S_ISREG(st.st_mode):
                continue

            existing = prior.get(path)
            if existing and existing.mtime == st.st_mtime and existing.size == st.st_size:
                cf = existing
            else:
                rel = path.relative_to(downloads_dir).parts
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
                cf = CachedFile(
                    path=path, creator=creator, size=st.st_size,
                    mtime=st.st_mtime, metadata=metadata,
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

        def runner() -> None:
            try:
                self._scan_once()
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=runner, daemon=True).start()

    def get(self) -> tuple[list[CachedFile], list[str], int, int]:
        if self._built_at == 0.0:
            self._scan_once()
        elif time.monotonic() - self._built_at > CACHE_TTL:
            self._refresh_async()
        with self._lock:
            return self._sorted, self._creators, len(self._sorted), self._total_size

    def invalidate(self) -> None:
        if self._built_at == 0.0:
            return
        self._refresh_async()


media_cache = MediaCache()

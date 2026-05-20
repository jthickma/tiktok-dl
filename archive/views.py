"""Django views: index, save channels, request sync, status JSON, asset download."""
from __future__ import annotations

import mimetypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST


def require_GET(view):
    return require_http_methods(["GET", "HEAD"])(view)

from .cache import media_cache
from .utils import (
    MediaEntry,
    clean_title,
    count_active_channels,
    count_archive_entries,
    format_bytes,
    format_duration,
    format_upload_date,
    is_queued,
    is_running,
    last_run_label,
    read_channels,
    read_logs,
    request_download,
    upload_date_from_filename,
    worker_state,
)

PER_PAGE = 24


def _download_url(path: Path | None) -> str | None:
    if path is None:
        return None
    rel = path.relative_to(settings.DOWNLOADS_DIR).as_posix()
    return reverse("download_asset", args=[rel])


def _gather_media(query: str, creator: str, page: int):
    if not settings.DOWNLOADS_DIR.exists():
        return [], [], 0, 0, 0

    cached_files, creators, media_count, total_size = media_cache.get()
    needle = query.strip().lower()
    matched = []

    for cf in cached_files:
        if creator and cf.creator != creator:
            continue
        if needle:
            md = cf.metadata
            title = md.get("title") or clean_title(cf.path.name)
            caption = md.get("description") or ""
            source_url = md.get("webpage_url") or md.get("original_url") or ""
            uploader = md.get("uploader") or cf.creator
            haystack = f"{title} {caption} {source_url} {uploader} {cf.path.name}".lower()
            if needle not in haystack:
                continue
        matched.append(cf)

    matched_count = len(matched)
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
                relative_path=cf.path.relative_to(settings.DOWNLOADS_DIR).as_posix(),
                media_url=_download_url(cf.path),
                poster_url=_download_url(cf.thumbnail),
                info_url=_download_url(cf.info_path),
                description_url=_download_url(cf.description_path),
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


def _redirect(flash: str = "", ft: str = "ok") -> HttpResponseRedirect:
    qs = {}
    if flash:
        qs["flash"] = flash
        qs["ft"] = ft
    target = reverse("index")
    if qs:
        target = f"{target}?{urlencode(qs)}"
    return HttpResponseRedirect(target)


@require_GET
def index(request: HttpRequest) -> HttpResponse:
    flash = request.GET.get("flash", "")
    flash_type = request.GET.get("ft", "ok")
    query = request.GET.get("q", "").strip()
    selected_creator = request.GET.get("creator", "").strip()
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1

    running = is_running()
    queued = is_queued()
    state = worker_state()
    channels = read_channels()
    logs = read_logs()
    media_entries, creators, media_count, total_size, matched_count = _gather_media(
        query, selected_creator, page,
    )
    total_pages = max(1, (matched_count + PER_PAGE - 1) // PER_PAGE)
    if page > total_pages:
        page = total_pages

    def page_url(p: int) -> str:
        params = {}
        if query:
            params["q"] = query
        if selected_creator:
            params["creator"] = selected_creator
        if p > 1:
            params["page"] = str(p)
        return f"/?{urlencode(params)}" if params else "/"

    pagination_links = []
    if total_pages > 1:
        if page > 1:
            pagination_links.append(("First", page_url(1), False))
            pagination_links.append(("Prev", page_url(page - 1), False))
        pagination_links.append((str(page), "", True))
        if page < total_pages:
            pagination_links.append(("Next", page_url(page + 1), False))
            pagination_links.append((f"Last ({total_pages})", page_url(total_pages), False))

    return render(request, "archive/index.html", {
        "flash": flash,
        "flash_type": flash_type,
        "running": running,
        "queued": queued,
        "worker_state": state,
        "channel_count": count_active_channels(),
        "archive_count": count_archive_entries(),
        "media_count": media_count,
        "library_size": format_bytes(total_size),
        "channels": channels,
        "channel_file": str(settings.CHANNELS_FILE),
        "archive_file": str(settings.ARCHIVE_FILE),
        "downloads_dir": str(settings.DOWNLOADS_DIR),
        "logs": logs,
        "media_entries": media_entries,
        "creators": creators,
        "query": query,
        "selected_creator": selected_creator,
        "run_button_label": "Save and queue sync" if (running or queued) else "Save and sync now",
        "page": page,
        "total_pages": total_pages,
        "matched_count": matched_count,
        "pagination_links": pagination_links,
        "last_run": last_run_label(),
    })


@csrf_exempt
@require_POST
def save(request: HttpRequest) -> HttpResponse:
    channels = request.POST.get("channels", "").replace("\r\n", "\n")
    if not channels.endswith("\n"):
        channels += "\n"
    settings.CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings.CHANNELS_FILE.write_text(channels, encoding="utf-8")
    media_cache.invalidate()

    if request.GET.get("run"):
        try:
            result = request_download("web-save")
        except RuntimeError as exc:
            return _redirect(f"Saved, but sync request failed: {exc}", "err")
        if result == "queued":
            return _redirect("Channels saved. Sync queued behind the active pass.", "warn")
        return _redirect("Channels saved. Sync started.", "ok")
    return _redirect("Channels saved.", "ok")


@csrf_exempt
@require_POST
def run(request: HttpRequest) -> HttpResponse:
    try:
        result = request_download("web-run")
    except RuntimeError as exc:
        return _redirect(f"Sync request failed: {exc}", "err")
    if result == "queued":
        return _redirect("Sync queued behind the active pass.", "warn")
    return _redirect("Sync started.", "ok")


@require_GET
def api_status(request: HttpRequest) -> JsonResponse:
    return JsonResponse({
        "running": is_running(),
        "queued": is_queued(),
        "state": worker_state(),
        "last_run": last_run_label(),
        "logs": read_logs(40),
    })


@require_GET
def download_asset(request: HttpRequest, relative_path: str) -> HttpResponse:
    candidate = (settings.DOWNLOADS_DIR / relative_path).resolve()
    root = settings.DOWNLOADS_DIR.resolve()
    if candidate == root or root not in candidate.parents or not candidate.is_file():
        raise Http404
    mimetype, _ = mimetypes.guess_type(candidate.name)
    response = FileResponse(candidate.open("rb"), content_type=mimetype or "application/octet-stream")
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["Accept-Ranges"] = "bytes"
    return response

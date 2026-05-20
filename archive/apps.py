from django.apps import AppConfig


class ArchiveConfig(AppConfig):
    name = "archive"

    def ready(self) -> None:
        from django.conf import settings

        for path in (settings.DOWNLOADS_DIR, settings.LOG_FILE.parent):
            path.mkdir(parents=True, exist_ok=True)
        settings.LOG_FILE.touch(exist_ok=True)
        for path in (settings.CHANNELS_FILE, settings.ARCHIVE_FILE):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)

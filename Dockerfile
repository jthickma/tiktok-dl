FROM alpine:3.21

RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-flask \
    ffmpeg \
    bash \
    supercronic \
    inotify-tools \
    su-exec \
    && pip install --break-system-packages --no-cache-dir yt-dlp

RUN addgroup -g 1000 -S app \
    && adduser -S -D -H -u 1000 -G app app

COPY entrypoint.sh /entrypoint.sh
COPY download.sh /download.sh
COPY request_download.sh /request_download.sh
COPY download_worker.sh /download_worker.sh
COPY webui.py /webui.py
RUN chmod +x /entrypoint.sh /download.sh /request_download.sh /download_worker.sh

RUN mkdir -p /config /downloads /logs

WORKDIR /downloads
EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]

FROM alpine:3.21

RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-flask \
    ffmpeg \
    bash \
    supercronic \
    inotify-tools \
    && pip install --break-system-packages --no-cache-dir yt-dlp

COPY entrypoint.sh /entrypoint.sh
COPY download.sh /download.sh
COPY webui.py /webui.py
RUN chmod +x /entrypoint.sh /download.sh

RUN mkdir -p /config /downloads /logs

WORKDIR /downloads
EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]

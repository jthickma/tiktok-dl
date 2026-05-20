FROM python:3.12-alpine

RUN apk add --no-cache \
    ffmpeg \
    bash \
    supercronic \
    inotify-tools \
    su-exec \
    tini

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN addgroup -g 1000 -S app \
    && adduser -S -D -h /home/app -u 1000 -G app app \
    && mkdir -p /home/app \
    && chown app:app /home/app

ENV HOME=/home/app

COPY manage.py /app/manage.py
COPY tiktokdl /app/tiktokdl
COPY archive /app/archive
COPY entrypoint.sh /entrypoint.sh
COPY download.sh /download.sh
COPY request_download.sh /request_download.sh
COPY download_worker.sh /download_worker.sh
RUN chmod +x /entrypoint.sh /download.sh /request_download.sh /download_worker.sh

# Collect static once at build time so gunicorn can serve from disk via whitenoise.
RUN DJANGO_SECRET=build python /app/manage.py collectstatic --noinput \
    && chown -R app:app /app

RUN mkdir -p /config /downloads /logs \
    && chown -R app:app /config /downloads /logs

WORKDIR /downloads
EXPOSE 8080

ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]

#!/usr/bin/env python3
"""Minimal web UI for managing tiktok-dl channels and triggering downloads."""

import os
import subprocess
import threading
import time
from datetime import datetime
from flask import Flask, request, redirect, url_for
from markupsafe import Markup

app = Flask(__name__)

CHANNELS_FILE = "/config/channels.txt"
LOG_FILE = "/logs/download.log"
PID_FILE = "/tmp/download.pid"

def is_running():
    """Check if a download is currently in progress."""
    if not os.path.exists(PID_FILE):
        return False
    try:
        pid = int(open(PID_FILE).read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, FileNotFoundError):
        return False

def trigger_download():
    """Run download.sh in background."""
    if is_running():
        return False
    def _run():
        with open(LOG_FILE, "a") as log:
            log.write(f"\n{'='*50}\n")
            log.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Triggered via web UI\n")
            log.write(f"{'='*50}\n")
            log.flush()
            proc = subprocess.Popen(
                ["/download.sh"],
                stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ}
            )
            with open(PID_FILE, "w") as pf:
                pf.write(str(proc.pid))
            proc.wait()
            try:
                os.remove(PID_FILE)
            except FileNotFoundError:
                pass
    threading.Thread(target=_run, daemon=True).start()
    return True

def read_channels():
    try:
        return open(CHANNELS_FILE).read()
    except FileNotFoundError:
        return ""

def read_logs(lines=80):
    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), LOG_FILE],
            capture_output=True, text=True
        )
        return result.stdout
    except Exception:
        return "No logs yet."

def count_channels():
    content = read_channels()
    return len([l for l in content.splitlines()
                if l.strip() and not l.strip().startswith("#")])

PAGE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, system-ui, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 1.5rem; max-width: 900px; margin: 0 auto; }
h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 1rem; }
h1 span { color: #888; font-weight: 400; font-size: 0.9rem; }
.card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.card h2 { font-size: 1rem; margin-bottom: 0.75rem; color: #ccc; }
textarea { width: 100%; height: 320px; background: #111; color: #e0e0e0; border: 1px solid #333; border-radius: 4px; padding: 0.6rem; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.82rem; line-height: 1.5; resize: vertical; }
textarea:focus { outline: none; border-color: #555; }
.actions { display: flex; gap: 0.5rem; margin-top: 0.75rem; align-items: center; }
.btn { padding: 0.5rem 1.1rem; border: none; border-radius: 6px; font-size: 0.85rem; font-weight: 500; cursor: pointer; transition: background 0.15s; }
.btn-primary { background: #2563eb; color: #fff; }
.btn-primary:hover { background: #1d4ed8; }
.btn-green { background: #16a34a; color: #fff; }
.btn-green:hover { background: #15803d; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.status { font-size: 0.82rem; color: #888; margin-left: auto; }
.status .running { color: #f59e0b; font-weight: 600; }
.status .idle { color: #22c55e; }
pre { background: #111; color: #aaa; padding: 0.6rem; border-radius: 4px; font-size: 0.78rem; line-height: 1.4; overflow-x: auto; max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; }
.badge { display: inline-block; background: #2a2a2a; color: #aaa; padding: 0.15rem 0.5rem; border-radius: 10px; font-size: 0.75rem; margin-left: 0.5rem; }
.flash { padding: 0.6rem 1rem; border-radius: 6px; margin-bottom: 1rem; font-size: 0.85rem; }
.flash-ok { background: #052e16; border: 1px solid #16a34a; color: #4ade80; }
.flash-warn { background: #451a03; border: 1px solid #d97706; color: #fbbf24; }
.flash-err { background: #2a0000; border: 1px solid #dc2626; color: #f87171; }
@media (max-width: 600px) { body { padding: 0.75rem; } textarea { height: 250px; } }
"""

@app.route("/", methods=["GET"])
def index():
    flash = request.args.get("flash", "")
    flash_type = request.args.get("ft", "ok")
    running = is_running()
    channels = read_channels()
    logs = read_logs()
    n = count_channels()

    flash_html = ""
    if flash:
        flash_html = f'<div class="flash flash-{flash_type}">{Markup.escape(flash)}</div>'

    status_dot = '<span class="running">● downloading</span>' if running else '<span class="idle">● idle</span>'

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tiktok-dl</title>
<style>{PAGE_CSS}</style>
</head><body>
<h1>tiktok-dl <span>subscription manager</span></h1>
{flash_html}
<div class="card">
  <h2>Channels <span class="badge">{n} active</span></h2>
  <form method="POST" action="/save">
    <textarea name="channels" spellcheck="false">{Markup.escape(channels)}</textarea>
    <div class="actions">
      <button type="submit" class="btn btn-primary">Save channels</button>
      <button type="submit" formaction="/save?run=1" class="btn btn-green" {"disabled" if running else ""}>Save &amp; download now</button>
      <div class="status">{status_dot}</div>
    </div>
  </form>
</div>
<div class="card">
  <h2>Recent logs</h2>
  <pre>{Markup.escape(logs)}</pre>
</div>
</body></html>"""

@app.route("/save", methods=["POST"])
def save():
    channels = request.form.get("channels", "")
    # Normalize line endings
    channels = channels.replace("\r\n", "\n")
    if not channels.endswith("\n"):
        channels += "\n"

    with open(CHANNELS_FILE, "w") as f:
        f.write(channels)

    run = request.args.get("run", "")
    if run:
        ok = trigger_download()
        if ok:
            return redirect(url_for("index", flash="Saved. Download started.", ft="ok"))
        else:
            return redirect(url_for("index", flash="Saved. Download already running.", ft="warn"))

    return redirect(url_for("index", flash="Channels saved.", ft="ok"))

@app.route("/run", methods=["POST"])
def run():
    ok = trigger_download()
    if ok:
        return redirect(url_for("index", flash="Download started.", ft="ok"))
    return redirect(url_for("index", flash="Download already running.", ft="warn"))

if __name__ == "__main__":
    os.makedirs("/logs", exist_ok=True)
    app.run(host="0.0.0.0", port=8080)

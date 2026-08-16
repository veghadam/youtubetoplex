#!/usr/bin/env python3
"""Hourly ChannelHoarder download-status check (run from cron).

Logs a concise status line to download_status.log each run, and flags
anomalies so problems (unexpected pause, stalled queue, growing failures)
stand out instead of scrolling past.
"""
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "http://127.0.0.1:8587/api/v1"
LOG = Path("/home/devilke/youtubetoplex/download_status.log")
STATE = Path("/home/devilke/youtubetoplex/.download_status_state.json")


def _get(path: str):
    with urllib.request.urlopen(f"{BASE}/{path}", timeout=15) as r:
        return json.loads(r.read())


def main() -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        stats = _get("dashboard/stats")
        paused = _get("downloads/paused").get("paused", False)
    except Exception as e:
        line = f"{ts} | ERROR: could not reach ChannelHoarder API: {e}"
        with LOG.open("a") as f:
            f.write(f"WARNING {line}\n")
        print(line)
        return

    downloaded = stats.get("total_downloaded", 0)
    failed = stats.get("total_failed", 0)
    pending = stats.get("queue_length", stats.get("total_pending", 0))
    active = stats.get("active_downloads", 0)

    try:
        prev = json.loads(STATE.read_text())
    except Exception:
        prev = {}

    flags = []
    if paused and pending > 0:
        flags.append("QUEUE PAUSED with downloads pending")
    elif pending > 0 and active == 0:
        flags.append("NO ACTIVE DOWNLOADS with items pending")
    if stats.get("cookies_expired"):
        flags.append("COOKIES EXPIRED")
    if prev.get("failed") is not None and failed > prev["failed"]:
        flags.append(f"NEW FAILURES (+{failed - prev['failed']})")
    if prev.get("downloaded") is not None and downloaded < prev["downloaded"]:
        flags.append("DOWNLOAD COUNT DECREASED")

    line = (
        f"{ts} | down={downloaded} failed={failed} pending={pending} "
        f"active={active} paused={paused}"
    )

    out = line + ("  <-- " + "; ".join(flags) if flags else "")
    if flags:
        out = "WARNING " + out
    with LOG.open("a") as f:
        f.write(out + "\n")

    STATE.write_text(json.dumps(
        {"downloaded": downloaded, "failed": failed, "pending": pending}
    ))
    print(out)


if __name__ == "__main__":
    main()
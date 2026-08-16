"""Resolve subscription @handles to channel IDs via YouTube Data API, compare
against the DB, and print the ones missing (to be added via the app API).

Runs inside the container: PYTHONPATH=/app python3 /config/resolve_subs.py
"""
import json
import logging
import os
import sqlite3
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("resolve_subs")

API_BASE = "https://www.googleapis.com/youtube/v3"
API_KEY_FILE = os.environ.get("YOUTUBE_API_KEY_FILE", "/config/youtube_api_key")

HANDLES_FILE = "/config/subs_handles.txt"


def _api_key() -> str:
    """Read the YouTube Data API key from the environment or the key file.

    Never hardcode the key here - this repo is public.
    """
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if key:
        return key
    try:
        return Path(API_KEY_FILE).read_text().strip()
    except OSError as e:
        raise SystemExit(
            f"No API key: set YOUTUBE_API_KEY or provide {API_KEY_FILE} ({e})"
        )


API_KEY = _api_key()


def resolve_handles(handles):
    results = {}
    with httpx.Client(timeout=30) as client:
        for h in handles:
            resp = client.get(
                f"{API_BASE}/channels",
                params={"part": "snippet,contentDetails", "forHandle": h, "key": API_KEY},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if not items:
                log.warning("No channel found for handle @%s", h)
                results[h] = None
                continue
            item = items[0]
            uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
            results[h] = {
                "id": item["id"],
                "title": item.get("snippet", {}).get("title", h),
                "uploads": uploads,
            }
    return results


def main():
    with open(HANDLES_FILE) as f:
        handles = [line.strip() for line in f if line.strip()]
    log.info("Resolving %d handles", len(handles))

    resolved = resolve_handles(handles)

    con = sqlite3.connect("/config/archiver.db")
    existing_ids = {r[0] for r in con.execute("SELECT channel_id FROM channels")}

    found = []
    missing = []
    for h in handles:
        info = resolved.get(h)
        if not info:
            continue
        found.append(info)
        if info["id"] not in existing_ids:
            missing.append((h, info["title"], info["id"]))
        else:
            log.info("Already added: %s (%s)", info["title"], info["id"])

    log.info("Resolved %d channels, %d already added, %d missing",
             len(found), len(found) - len(missing), len(missing))
    print(json.dumps({"missing": missing}, ensure_ascii=False, indent=2))

    with open("/config/subs_missing.json", "w") as f:
        json.dump({"missing": missing}, f, ensure_ascii=False, indent=2)
    con.close()


if __name__ == "__main__":
    main()

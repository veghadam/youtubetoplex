"""Helper: resolve real upload dates for videos whose dates were defaulted to
today by the scan fallback, then skip those older than 30 days.

Uses the YouTube Data API (batch, no per-video rate limits) and covers ALL
channels. Also renumbers episodes afterwards.

Runs inside the container: PYTHONPATH=/app python3 /config/resolve_dates.py
"""
import asyncio
import json
import logging
import sqlite3
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("resolve_dates")

DB = "/config/archiver.db"
CUTOFF = (datetime.now(timezone.utc).date() - timedelta(days=30)).strftime("%Y%m%d")
MAX_AGE_DAYS = 30

# Videos with an upload_date within the last few days are candidates: either they
# are genuinely recent (safe to re-resolve, resolves to the same date) or they
# carry the scan fallback date (needs correction). The scan assigns today's date
# as fallback, so this window catches them all.
WINDOW_START = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()


def parse_date(raw) -> str | None:
    if not raw:
        return None
    raw = str(raw).replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    return raw


def main():
    from app.services.youtube_api_service import YouTubeAPIService

    api = YouTubeAPIService()

    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row

    videos = con.execute(
        "SELECT id, video_id, title, status, channel_id, upload_date FROM videos "
        "WHERE upload_date >= ? "
        "ORDER BY channel_id, id",
        (WINDOW_START,),
    ).fetchall()
    log.info("Resolving dates for %d candidate videos (cutoff %s, window >= %s)",
             len(videos), CUTOFF, WINDOW_START)
    if not videos:
        return

    # Build {video_id: db row} to batch-resolve in chunks of 50 via the API.
    by_id: dict[str, dict] = {}
    for v in videos:
        by_id[v["video_id"]] = v

    ids = list(by_id.keys())
    all_dates: dict[str, str | None] = {}
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        dates = asyncio.run(api.get_video_dates(batch))
        all_dates.update(dates)
        if (i // 50 + 1) % 10 == 0 or i + 50 >= len(ids):
            log.info("API batch %d/%d resolved", i // 50 + 1, (len(ids) + 49) // 50)

    updated = 0
    skipped = 0
    unresolved = 0
    date_only = 0
    for v in videos:
        real = parse_date(all_dates.get(v["video_id"]))
        if not real:
            unresolved += 1
            continue
        iso = real[:4] + "-" + real[4:6] + "-" + real[6:]
        if iso == v["upload_date"]:
            continue
        # Only skip/requeue videos that were queued; already-skipped videos just
        # get their date corrected so the UI shows the real air date.
        if v["status"] in ("queued", "downloading"):
            if real < CUTOFF:
                con.execute(
                    "UPDATE videos SET upload_date=?, status='skipped', monitored=0 WHERE id=?",
                    (iso, v["id"]),
                )
                con.execute("DELETE FROM download_queue WHERE video_id=?", (v["id"],))
                skipped += 1
            else:
                con.execute("UPDATE videos SET upload_date=? WHERE id=?", (iso, v["id"]))
                updated += 1
        else:
            con.execute("UPDATE videos SET upload_date=? WHERE id=?", (iso, v["id"]))
            date_only += 1

    con.commit()
    log.info("DONE. updated=%d skipped=%d date_only_corrected=%d unresolved=%d",
             updated, skipped, date_only, unresolved)

    # Renumber episodes per channel so numbers match corrected dates.
    channels = con.execute("SELECT id FROM channels").fetchall()
    for ch in channels:
        ch_videos = con.execute(
            "SELECT id, video_id, upload_date, is_short, is_livestream, episode, season FROM videos "
            "WHERE channel_id=? ORDER BY upload_date, id",
            (ch["id"],),
        ).fetchall()
        counts: dict[int, int] = {}
        changed = 0
        for vid in ch_videos:
            if vid["is_short"] or vid["is_livestream"]:
                if vid["episode"] != 0:
                    con.execute("UPDATE videos SET episode=0 WHERE id=?", (vid["id"],))
                    changed += 1
                continue
            season = int(vid["upload_date"][:4])
            counts[season] = counts.get(season, 0) + 1
            ep = counts[season]
            if vid["season"] != season or vid["episode"] != ep:
                con.execute("UPDATE videos SET season=?, episode=? WHERE id=?",
                            (season, ep, vid["id"]))
                changed += 1
        if changed:
            log.info("Renumbered %d episodes for channel %s", changed, ch["id"])
    con.commit()
    con.close()


if __name__ == "__main__":
    main()

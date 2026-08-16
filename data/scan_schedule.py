#!/usr/bin/env python3
"""Control the ChannelHoarder scan schedule.

Usage (run inside the container, /config is the mounted data dir):
    python3 /config/scan_schedule.py pause   # stop scans until told otherwise
    python3 /config/scan_schedule.py resume  # stagger scans at ~N/hour, daily
    python3 /config/scan_schedule.py status  # show current scan settings

pause:  sets scan_paused=true and pushes every enabled channel's next_scan_at
        far into the future so no scan runs.

resume: clears scan_paused and lays out next_scan_at so enabled channels scan
        about `channels_per_hour` at a time across the next `window_hours` hours
        (default: all channels spread across one hour). Combined settings applied:
          - scan_skip_queue_above=0      scan only when the download queue is empty
          - scan_fixed_interval_hours=1  each channel rescans hourly after its turn
          - scan_max_channels_per_tick=15  up to 15 channels per 10-min tick
          - scan_jitter_max_seconds=10   small intra-tick spacing
        The queue-empty gate means scanning never competes with downloads, so an
        hourly cadence is safe.
"""
import argparse
import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DB = "/config/archiver.db"

DEFAULT_CHANNELS_PER_HOUR = 86
DEFAULT_WINDOW_HOURS = 1
FAR_FUTURE = "2099-01-01 00:00:00"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB)


def _get_setting(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row:
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return row[0]
    return None


def _set_setting(con: sqlite3.Connection, key: str, value) -> None:
    encoded = json.dumps(value)
    cur = con.execute("SELECT COUNT(*) FROM app_settings WHERE key = ?", (key,))
    if cur.fetchone()[0]:
        con.execute("UPDATE app_settings SET value = ? WHERE key = ?", (encoded, key))
    else:
        con.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, encoded))


def pause() -> None:
    con = _conn()
    cur = con.execute("UPDATE channels SET next_scan_at = ? WHERE enabled = 1", (FAR_FUTURE,))
    updated = cur.rowcount
    _set_setting(con, "scan_paused", True)
    con.commit()
    con.close()
    print(f"Scan paused: {updated} channels pushed to {FAR_FUTURE}, scan_paused=true")


def resume(channels_per_hour: int, window_hours: int) -> None:
    con = _conn()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = con.execute(
        "SELECT id FROM channels WHERE enabled = 1 AND channel_id != '__standalone__' ORDER BY id"
    ).fetchall()
    ids = [r[0] for r in rows]
    if not ids:
        print("No enabled channels; nothing to schedule.")
        return

    # Spread channels across the window at ~channels_per_hour per hour.
    per_hour = max(1, channels_per_hour)
    minutes_between = 60 / per_hour
    schedule = []
    for idx, cid in enumerate(ids):
        offset_minutes = int(idx * minutes_between)
        base = now + timedelta(minutes=offset_minutes)
        # Jitter within the slot so identical ids don't collide on ticks.
        slot_jitter = random.uniform(0, minutes_between * 60)
        ts = base + timedelta(seconds=slot_jitter)
        if ts > now + timedelta(hours=window_hours):
            ts = now + timedelta(hours=window_hours) + timedelta(minutes=random.randint(0, 59))
        schedule.append((cid, ts.strftime("%Y-%m-%d %H:%M:%S")))

    for cid, ts in schedule:
        con.execute("UPDATE channels SET next_scan_at = ? WHERE id = ?", (ts, cid))

    _set_setting(con, "scan_paused", False)
    _set_setting(con, "scan_max_channels_per_tick", 15)
    _set_setting(con, "scan_min_interval_hours", 24)
    _set_setting(con, "scan_fixed_interval_hours", 1)
    _set_setting(con, "scan_skip_queue_above", 0)
    _set_setting(con, "scan_jitter_max_seconds", 10)
    con.commit()
    con.close()

    # Bucket counts for reporting
    buckets: dict[str, int] = {}
    for _, ts in schedule:
        buckets.setdefault(ts[:13], 0)
        buckets[ts[:13]] += 1
    spread = ", ".join(f"{h}: {c}" for h, c in sorted(buckets.items()))
    print(f"Scan resumed: {len(ids)} channels over ~{window_hours}h at ~{per_hour}/hour")
    print("Per-hour distribution:", spread)


def status() -> None:
    con = _conn()
    paused = _get_setting(con, "scan_paused")
    cap = _get_setting(con, "scan_max_channels_per_tick")
    min_interval = _get_setting(con, "scan_min_interval_hours")
    fixed_interval = _get_setting(con, "scan_fixed_interval_hours")
    skip_above = _get_setting(con, "scan_skip_queue_above")
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    due = con.execute(
        "SELECT COUNT(*) FROM channels WHERE enabled = 1 AND (next_scan_at IS NULL OR next_scan_at <= ?)",
        (now,),
    ).fetchone()[0]
    upcoming = con.execute(
        "SELECT COUNT(*) FROM channels WHERE enabled = 1 AND next_scan_at > ?", (now,)
    ).fetchone()[0]
    con.close()
    print(f"scan_paused:                   {paused}")
    print(f"scan_max_channels_per_tick:    {cap}")
    print(f"scan_min_interval_hours:       {min_interval}")
    print(f"scan_fixed_interval_hours:     {fixed_interval}")
    print(f"scan_skip_queue_above:         {skip_above}")
    print(f"channels due now:              {due}")
    print(f"channels scheduled:            {upcoming}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("pause")
    r = sub.add_parser("resume")
    r.add_argument("--per-hour", type=int, default=DEFAULT_CHANNELS_PER_HOUR,
                   help=f"channels to scan per hour (default {DEFAULT_CHANNELS_PER_HOUR})")
    r.add_argument("--window", type=int, default=DEFAULT_WINDOW_HOURS,
                   help=f"hours to spread the first pass over (default {DEFAULT_WINDOW_HOURS})")
    sub.add_parser("status")

    args = parser.parse_args()
    if args.cmd == "pause":
        pause()
    elif args.cmd == "resume":
        resume(args.per_hour, args.window)
    elif args.cmd == "status":
        status()


if __name__ == "__main__":
    sys.exit(main())

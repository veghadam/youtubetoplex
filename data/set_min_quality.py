"""Set min_quality=720p on all channels via the ChannelHoarder API."""
import json
import time
import urllib.request
import urllib.error

API = "http://127.0.0.1:8000/api/v1/channels/"

updated = 0
failed = 0
with urllib.request.urlopen(API, timeout=60) as resp:
    channels = json.loads(resp.read())

for i, ch in enumerate(channels, 1):
    cid = ch["id"]
    if ch.get("min_quality") == "720p":
        continue
    body = json.dumps({"min_quality": "720p"}).encode()
    req = urllib.request.Request(
        f"{API}{cid}", data=body, method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
        print(f"[{i}/{len(channels)}] {ch['channel_name']}: min_quality=720p")
        updated += 1
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        print(f"[{i}/{len(channels)}] FAILED {ch['channel_name']}: HTTP {e.code} {err}")
        failed += 1
    except Exception as e:
        print(f"[{i}/{len(channels)}] ERROR {ch['channel_name']}: {e}")
        failed += 1
    time.sleep(0.3)

print(f"\nDONE. updated={updated} failed={failed}")

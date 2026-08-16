"""Add missing subscription channels via the ChannelHoarder API."""
import json
import time
import urllib.request

API = "http://127.0.0.1:8000/api/v1/channels/"

with open("/config/subs_missing.json") as f:
    missing = json.load(f)["missing"]

added = 0
skipped = 0
for i, (handle, title, cid) in enumerate(missing, 1):
    url = f"https://www.youtube.com/channel/{cid}"
    body = json.dumps({"url": url, "enabled": True, "auto_download": True}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        print(f"[{i}/{len(missing)}] ADDED {title} ({cid})")
        added += 1
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        print(f"[{i}/{len(missing)}] FAILED {title}: HTTP {e.code} {err}")
        skipped += 1
    except Exception as e:
        print(f"[{i}/{len(missing)}] ERROR {title}: {e}")
        skipped += 1
    time.sleep(1.0)

print(f"\nDONE. added={added} skipped={skipped}")

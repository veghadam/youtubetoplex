# ChannelHoarder Setup Guide

## Overview

ChannelHoarder automatically downloads YouTube videos from specified channels and organizes them for Plex. Videos are downloaded in **H.264 + AAC** format for native playback on Samsung TVs (including S90F 4K OLED) without transcoding.

## Prerequisites

- Docker + docker-compose installed
- YouTube account with cookies exported
- Plex server running
- Storage at `/mnt/media/videos/`

## Step 1: Export YouTube Cookies from Your Mac

### Option A: Using Browser Extension (Easiest)
1. Install the "Get cookies.txt LOCALLY" extension in Chrome/Safari on your Mac
2. Go to youtube.com (make sure you're logged in)
3. Click the extension icon → Export cookies for youtube.com
4. Save the `cookies.txt` file

### Option B: Using yt-dlp directly on your Mac
```bash
# Install yt-dlp if you haven't
brew install yt-dlp

# Export cookies from your logged-in browser session
yt-dlp --extract-cookies-from-browser --cookies /path/to/cookies.txt https://www.youtube.com
```

### Option C: Manual cookie export
1. Open Chrome/Safari on your Mac
2. Go to youtube.com (logged in)
3. Open Developer Tools (F12) → Application/Storage → Cookies
4. Copy all cookies for youtube.com and www.youtube.com into a file

## Step 2: Transfer Cookies to This Server

```bash
# From your Mac, transfer the cookies file:
scp /path/to/cookies.txt devilke@<SERVER_IP>:/home/devilke/youtubetoplex/data/cookies.txt
```

## Step 3: Verify Cookies Are Working

```bash
# Test that the cookies work by fetching your subscriptions
sudo docker exec channelhoarder python -c "
import yt_dlp
ydl = yt_dlp.YoutubeDL({'cookiefile': '/app/data/cookies.txt'})
# Try to access your subscriptions page
info = ydl.extract_info('https://www.youtube.com/feed/subscriptions', download=False)
print('Subscriptions page loaded successfully!')
"
```

## Step 4: Start ChannelHoarder

```bash
cd /home/devilke/youtubetoplex
sudo docker-compose up -d
```

Verify it's running:
```bash
sudo docker ps --filter name=channelhoarder
sudo docker logs channelhoarder
```

## Step 5: Add Channels in the Web UI

1. Open http://<SERVER_IP>:8587
2. Create your admin account (first visit)
3. Go to Channels → Add Channel
4. Enter YouTube channel URLs you want to monitor
5. ChannelHoarder will auto-download new videos to `/mnt/media/videos/`

## Step 6: Configure Plex

1. In Plex, create a new **TV Shows** library
2. Set the agent to **"Personal Media Shows"** (not TVDB)
3. Enable **"Use local assets"**
4. Point the library to `/mnt/media/videos/`

## Configuration Details

### Download Settings
- **Video Codec**: H.264 (avc1) - native playback on Samsung TVs
- **Audio Codec**: AAC (mp4a) - native playback on Samsung TVs
- **Max Resolution**: 2160p (4K)
- **Max Frame Rate**: 60fps
- **AV1/Opus excluded** to avoid Plex transcoding

### Environment Variables
| Variable | Value | Description |
|----------|-------|-------------|
| DATABASE_URL | sqlite+aiosqlite:////config/archiver.db | Database location |
| CH_COOKIE_MODE | full | Cookie authentication mode |
| CH_POT_SERVER_ENABLED | true | PO token server enabled |
| CH_MAX_VIDEO_DOWNLOAD_AGE_DAYS | 30 | Max age for downloads |
| CH_DOWNLOAD_COOLDOWN_SECONDS | 10 | Cooldown between downloads |
| CH_DOWNLOAD_CONCURRENCY | 2 | Max concurrent downloads |
| CH_DOWNLOAD_RATE_LIMIT | 1 | Rate limit |
| CH_DOWNLOAD_JITTER | 0.5 | Download jitter |
| CH_USER_AGENT | Mozilla/5.0 ... | User agent string |
| CH_YTDLP_PLAYER_CLIENT | default | YouTube player client |
| CH_YTDLP_REMOTE_COMPONENTS | ejs:github | Remote components |

### Known Issues & Fixes
- **Database initialization**: The database file must not pre-exist as 0 bytes. If corrupted, delete `/config/channelhoarder.db` and restart.
- **Stuck downloads**: If a download hangs, reset it via the database:
  ```bash
  sudo docker exec channelhoarder python -c "
  import sqlite3
  db = sqlite3.connect('/config/archiver.db')
  cursor = db.cursor()
  cursor.execute('UPDATE download_queue SET started_at = NULL')
  cursor.execute('UPDATE videos SET status = \"queued\", retry_count = 0 WHERE status IN (\"queued\", \"downloading\", \"failed\")')
  db.commit()
  "
  ```

## Troubleshooting

### Container won't start
- Check logs: `sudo docker logs channelhoarder`
- Ensure cookies file exists at `./data/cookies.txt`
- Verify `/mnt/media/videos/` directory exists and is writable

### Downloads not starting
- Check queue: `sudo docker exec channelhoarder python -c "import sqlite3; db = sqlite3.connect('/config/archiver.db'); print('Queue:', db.execute('SELECT COUNT(*) FROM download_queue').fetchone()[0]); print('Queued videos:', db.execute('SELECT COUNT(*) FROM videos WHERE status = \"queued\"').fetchone()[0])"`
- Reset stuck downloads (see above)

### Plex transcoding videos
- Videos should be H.264 + AAC for native playback
- Check video codec: `file /mnt/media/videos/<channel>/<video>.mp4`
- If AV1 detected, re-download by marking videos as queued

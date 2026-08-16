#!/usr/bin/env python3
"""Watch ChannelHoarder docker logs for YouTube rate-limit errors and
automatically pause the download queue when one is detected, then
auto-resume after the rate-limit window (default 1 hour) so downloads
recover without manual intervention.

Runs as a systemd user service (see rate_limit_watcher.service).
Env overrides:
  RL_WATCH_RESUME_DELAY   seconds to wait after a rate-limit before resuming (default 3600)
  RL_WATCH_CHECK_INTERVAL seconds between resume checks (default 60)
"""
import json
import logging
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

LOG = Path(os.environ.get("RL_WATCH_LOG", "/home/devilke/youtubetoplex/rate_limit_watcher.log"))
STATE = Path(os.environ.get("RL_WATCH_STATE", "/home/devilke/youtubetoplex/.rate_limit_watcher.json"))
PAUSE_URL = os.environ.get("RL_WATCH_PAUSE_URL", "http://127.0.0.1:8587/api/v1/downloads/pause")
RESUME_URL = PAUSE_URL.rsplit("/", 1)[0] + "/resume"
PAUSED_URL = PAUSE_URL.rsplit("/", 1)[0] + "/paused"
CONTAINER = os.environ.get("RL_WATCH_CONTAINER", "channelhoarder")
PATTERN = os.environ.get("RL_WATCH_PATTERN", "rate-limited by YouTube")
RESUME_DELAY = int(os.environ.get("RL_WATCH_RESUME_DELAY", "3600"))
CHECK_INTERVAL = int(os.environ.get("RL_WATCH_CHECK_INTERVAL", "60"))
# Within 2x the resume delay of the last detected hit, the pause is presumed to be
# ours (rate-limit), so auto-resume is allowed. Prevents auto-resuming a pause the
# user made manually for some unrelated reason long after a rate-limit.
RESUME_WINDOW = max(600, 2 * RESUME_DELAY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler()],
)
logger = logging.getLogger("rate_limit_watcher")

_state_lock = threading.Lock()


def _load_state() -> dict:
    with _state_lock:
        try:
            return json.loads(STATE.read_text())
        except Exception:
            return {}


def _save_state(state: dict) -> None:
    with _state_lock:
        try:
            STATE.write_text(json.dumps(state, indent=2))
        except Exception:
            pass


def _get_paused() -> bool:
    try:
        with urllib.request.urlopen(PAUSED_URL, timeout=10) as r:
            return json.loads(r.read()).get("paused", False)
    except Exception:
        return False


def _pause() -> bool:
    req = urllib.request.Request(PAUSE_URL, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
            logger.warning("Auto-paused queue: %s", body)
            return True
    except Exception as e:
        logger.error("Failed to pause queue: %s", e)
        return False


def _resume() -> bool:
    req = urllib.request.Request(RESUME_URL, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
            logger.info("Auto-resumed queue: %s", body)
            return True
    except Exception as e:
        logger.error("Failed to resume queue: %s", e)
        return False


def _tail():
    """Yield docker log lines forever, reconnecting if the container restarts."""
    while True:
        try:
            proc = subprocess.Popen(
                ["docker", "logs", "--follow", "--since", "5s", CONTAINER],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                yield line
            proc.wait()
        except FileNotFoundError:
            logger.error("docker not found in PATH")
            time.sleep(30)
            continue
        except Exception as e:
            logger.error("tail stream error: %s", e)
            time.sleep(10)
            continue
        logger.warning("docker logs stream ended (container restart?), reconnecting")
        time.sleep(5)


def _resume_loop():
    """Background thread: resume the queue once the rate-limit window has passed."""
    logger.info("Resume thread started (resume_delay=%ds, check=%ds)", RESUME_DELAY, CHECK_INTERVAL)
    while True:
        try:
            state = _load_state()
            resume_at = state.get("resume_at")
            if resume_at:
                resume_at = float(resume_at)
                last_hit = state.get("last_hit", 0)
                within_window = last_hit and (time.time() - float(last_hit)) < RESUME_WINDOW
                if time.time() >= resume_at:
                    if not within_window:
                        logger.info("Pause not tied to a recent rate-limit; not auto-resuming")
                        state.pop("resume_at", None)
                        _save_state(state)
                    elif _get_paused():
                        if _resume():
                            state.pop("resume_at", None)
                            state["last_resume"] = time.strftime("%Y-%m-%d %H:%M:%S")
                            _save_state(state)
                    else:
                        state.pop("resume_at", None)
                        _save_state(state)
        except Exception as e:
            logger.error("Resume loop error: %s", e)
        time.sleep(CHECK_INTERVAL)


def main() -> None:
    state = _load_state()
    logger.info(
        "Rate-limit watcher started (container=%s, pattern=%r, resume_delay=%ds)",
        CONTAINER, PATTERN, RESUME_DELAY,
    )
    threading.Thread(target=_resume_loop, daemon=True).start()
    for line in _tail():
        if PATTERN.lower() not in line.lower():
            continue
        ts = int(time.time())
        last_hit = state.get("last_hit", 0)
        if ts - last_hit < 600:
            continue  # already handled from this window
        state["last_hit"] = ts
        state["last_message"] = line.strip()[:500]
        _save_state(state)
        if not _get_paused():
            if _pause():
                state["last_pause"] = time.strftime("%Y-%m-%d %H:%M:%S")
                state["resume_at"] = ts + RESUME_DELAY
                _save_state(state)
                logger.info(
                    "Queue paused; will auto-resume at %s",
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["resume_at"])),
                )
        else:
            logger.info("Rate-limit seen but queue already paused; skipped")


if __name__ == "__main__":
    main()

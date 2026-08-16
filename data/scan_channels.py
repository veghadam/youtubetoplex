import asyncio
import json
import logging
import random
from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from app.database import async_session
from app.models import AppSetting, Channel, DownloadQueue
from app.services.channel_service import ChannelService
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


async def _get_jitter_settings(db) -> tuple[bool, int]:
    """Read scan jitter settings from the database."""
    enabled = True
    max_seconds = 300

    try:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "scan_jitter_enabled")
        )
        setting = result.scalar_one_or_none()
        if setting:
            enabled = bool(json.loads(setting.value))
    except Exception:
        pass

    try:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "scan_jitter_max_seconds")
        )
        setting = result.scalar_one_or_none()
        if setting:
            max_seconds = int(json.loads(setting.value))
    except Exception:
        pass

    return enabled, max_seconds


async def _get_scan_paused(db) -> bool:
    """Global kill-switch: when scan_paused is true, no channel scans run."""
    try:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "scan_paused")
        )
        setting = result.scalar_one_or_none()
        if setting:
            return bool(json.loads(setting.value))
    except Exception:
        pass
    return False


async def _get_max_channels_per_tick(db) -> int:
    """Max channels allowed to scan in a single 10-minute tick.

    Ticks run every 10 minutes, so a cap of 1 yields at most 6 scans/hour.
    This prevents bursts when many channels come due at once (the previous
    behaviour, which is what trips YouTube rate limits).
    """
    try:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "scan_max_channels_per_tick")
        )
        setting = result.scalar_one_or_none()
        if setting:
            return max(1, int(json.loads(setting.value)))
    except Exception:
        pass
    return 1


async def _get_scan_skip_queue_above(db) -> int:
    """Skip scanning entirely while the pending download queue exceeds this.

    0 (default) means scanning only runs when the download queue is completely
    empty - no new-video discovery competes with active downloads, and no scan
    traffic adds to the YouTube load while the queue is draining.
    """
    try:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "scan_skip_queue_above")
        )
        setting = result.scalar_one_or_none()
        if setting:
            return max(0, int(json.loads(setting.value)))
    except Exception:
        pass
    return 0


async def scan_due_channels():
    """Tick task: scan a bounded number of due channels.

    Runs every 10 minutes. Each channel has its own randomized scan time assigned
    within the configured daily window, so scans spread naturally instead of
    firing in a burst. A per-tick cap hard-limits how many channels can scan at
    once (default 1 -> <=6/hour) to avoid hammering YouTube.
    """
    async with async_session() as db:
        if await _get_scan_paused(db):
            logger.info("Scan tick: paused (scan_paused=true), skipping")
            return

        # Queue-empty gate: while downloads are pending, don't scan at all.
        queue_gate = await _get_scan_skip_queue_above(db)
        pending = await db.scalar(select(func.count(DownloadQueue.id))) or 0
        if pending > queue_gate:
            logger.info(
                "Scan tick: %d downloads pending (scan only when <= %d), skipping",
                pending, queue_gate,
            )
            return

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await db.execute(
            select(Channel.id, Channel.channel_name)
            .where(Channel.enabled == True)
            .where(Channel.channel_id != "__standalone__")
            .where(or_(Channel.next_scan_at.is_(None), Channel.next_scan_at <= now_utc))
        )
        due_channels = [(row[0], row[1]) for row in result.all()]

        if not due_channels:
            return

        max_per_tick = await _get_max_channels_per_tick(db)
        if len(due_channels) > max_per_tick:
            logger.info(
                "Scan tick: %d channels due, throttled to %d per tick",
                len(due_channels), max_per_tick,
            )
            due_channels = due_channels[:max_per_tick]
        else:
            logger.info("Scan tick: %d channels due", len(due_channels))

        jitter_enabled, jitter_max = await _get_jitter_settings(db)

    random.shuffle(due_channels)

    total_new = 0
    for i, (channel_id, channel_name) in enumerate(due_channels):
        if jitter_enabled and i > 0 and jitter_max > 0:
            delay = random.uniform(0, jitter_max)
            logger.debug("Scan jitter: sleeping %.1fs before scanning %s", delay, channel_name)
            await asyncio.sleep(delay)

        # Fresh session per channel -- prevents rollback contamination and
        # avoids holding a connection during jitter sleeps
        try:
            async with async_session() as db:
                channel = await db.get(Channel, channel_id)
                if not channel:
                    continue
                service = ChannelService(db)
                new_count = await service.scan_channel(channel)
                total_new += new_count
                if new_count > 0:
                    logger.info("Found %d new videos for %s", new_count, channel_name)
        except Exception as e:
            logger.error("Scan failed for %s: %s", channel_name, e)
            try:
                async with async_session() as db:
                    channel = await db.get(Channel, channel_id)
                    if channel:
                        channel.health_status = "warning"
                        channel.last_error_code = "SCAN_FAILED"
                        service = ChannelService(db)
                        channel.next_scan_at = await service._compute_next_scan_at()
                        await db.commit()
            except Exception:
                # next_scan_at wasn't updated, so scan_due_channels (which treats
                # NULL as due) will retry this channel every 10-minute tick until the
                # DB recovers - a real rate-limit risk. Surface it.
                logger.warning(
                    "Could not update scan status after failure for %s; it will be "
                    "retried every tick until this clears", channel_name, exc_info=True,
                )

    logger.info("Scan tick complete: scanned %d channels, found %d new videos", len(due_channels), total_new)

    await NotificationService.broadcast("scan_complete", {
        "channels_scanned": len(due_channels),
        "new_videos": total_new,
    })

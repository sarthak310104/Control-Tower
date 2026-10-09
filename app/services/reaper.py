import logging
from datetime import timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db import SessionLocal
from app.services.claims import reap_expired_claims

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _sweep() -> None:
    db = SessionLocal()
    try:
        expired = reap_expired_claims(db)
        if expired:
            logger.info("reaper: expired %d stale claim(s): %s", len(expired), [c.id for c in expired])
    finally:
        db.close()


def start_reaper() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    # Pinned to UTC: letting APScheduler resolve the host's local zone crashes
    # startup on hosts whose zone is a legacy alias (e.g. Asia/Calcutta).
    _scheduler = AsyncIOScheduler(timezone=timezone.utc)
    _scheduler.add_job(_sweep, "interval", seconds=settings.reaper_interval_seconds, id="claim_reaper")
    _scheduler.start()
    logger.info(
        "reaper started: sweeping every %ss, claim TTL %ss",
        settings.reaper_interval_seconds, settings.claim_ttl_seconds,
    )


def stop_reaper() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
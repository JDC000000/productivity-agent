"""Scheduled jobs for the bot.

Currently:
- Daily brief at 8:00 AM Mon-Fri local time
- Friday review at 4:00 PM Friday local time

Wiring uses python-telegram-bot's built-in JobQueue (APScheduler under the hood).
The JobQueue starts/stops automatically with the Application.
"""
from __future__ import annotations

import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram.ext import Application, ContextTypes

from .briefing import build_briefing
from .config import BRAIN_DUMP_DOC_ID, TELEGRAM_ALLOWED_CHAT_ID, TIMEZONE
from .data_sources.brain_dump import get_active_items
from .data_sources.calendar import get_todays_events
from .data_sources.tasks import get_open_tasks
from .friday_review import build_friday_review
from .google_auth import get_credentials

log = logging.getLogger(__name__)


async def daily_brief_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the morning briefing to the allowlisted chat."""
    if TELEGRAM_ALLOWED_CHAT_ID is None:
        log.warning("daily_brief: TELEGRAM_ALLOWED_CHAT_ID not set; skipping send")
        return

    log.info("daily_brief: building...")
    try:
        creds = get_credentials()
        events = get_todays_events(creds)
        tasks = get_open_tasks(creds)
        brain_dump = (
            get_active_items(creds, BRAIN_DUMP_DOC_ID) if BRAIN_DUMP_DOC_ID else []
        )
        msg = build_briefing(events, tasks, brain_dump)
        await context.bot.send_message(
            chat_id=TELEGRAM_ALLOWED_CHAT_ID,
            text=f"Good morning, Jon. Daily briefing:\n\n{msg}",
        )
        log.info("daily_brief: sent")
    except Exception:
        log.exception("daily_brief failed")


async def friday_review_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the Friday review at 4pm Friday."""
    if TELEGRAM_ALLOWED_CHAT_ID is None:
        log.warning("friday_review: TELEGRAM_ALLOWED_CHAT_ID not set; skipping send")
        return

    log.info("friday_review: building...")
    try:
        msg = build_friday_review()
        await context.bot.send_message(
            chat_id=TELEGRAM_ALLOWED_CHAT_ID,
            text=msg,
        )
        log.info("friday_review: sent")
    except Exception:
        log.exception("friday_review failed")


def schedule_jobs(app: Application) -> None:
    """Wire scheduled jobs onto the Application's JobQueue.

    Called once during startup. JobQueue starts/stops with the Application.
    """
    if app.job_queue is None:
        log.warning(
            "JobQueue unavailable (install python-telegram-bot[job-queue]). "
            "Skipping all scheduled jobs."
        )
        return

    tz = ZoneInfo(TIMEZONE)

    # 8:00 AM Mon-Fri — daily brief
    app.job_queue.run_daily(
        daily_brief_job,
        time=time(hour=8, minute=0, tzinfo=tz),
        days=(0, 1, 2, 3, 4),  # Mon-Fri (PTB convention: Mon=0)
        name="daily_brief",
    )
    log.info("Scheduled: daily_brief at 8:00 AM Mon-Fri %s", TIMEZONE)

    # 4:00 PM Friday — week-in-review
    app.job_queue.run_daily(
        friday_review_job,
        time=time(hour=16, minute=0, tzinfo=tz),
        days=(4,),  # Friday
        name="friday_review",
    )
    log.info("Scheduled: friday_review at 4:00 PM Friday %s", TIMEZONE)

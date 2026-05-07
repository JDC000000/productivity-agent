"""Fetch today's events from the user's primary Google Calendar."""
from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from ..config import TIMEZONE


def get_todays_events(creds) -> list[dict[str, Any]]:
    """Return today's events, sorted by start time. All-day events included."""
    tz = ZoneInfo(TIMEZONE)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    today = datetime.now(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz)
    end = datetime.combine(today, time.max, tzinfo=tz)

    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


def format_event_time(event: dict) -> str:
    """Render the start time as e.g. '9:00 am' or 'all-day'."""
    start = event.get("start", {})
    if "dateTime" in start:
        dt = datetime.fromisoformat(start["dateTime"])
        # %-I avoids zero-padded hour (Mac/Linux). Falls back below for Windows.
        try:
            return dt.strftime("%-I:%M %p").lower()
        except ValueError:
            return dt.strftime("%I:%M %p").lstrip("0").lower()
    return "all-day"

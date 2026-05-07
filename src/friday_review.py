"""Friday review — analytics from the audit log + Claude-generated recommendation.

Reads ~/.productivity-agent/logs/actions.jsonl, computes counts for the
current week (Mon 00:00 → Fri 16:00 local), and asks Claude Haiku for one
actionable observation.

Run via /review (manual) or scheduled at Friday 4pm by scheduler.py.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from .config import ACTIONS_LOG_PATH, ANTHROPIC_API_KEY, TIMEZONE

log = logging.getLogger(__name__)

REVIEW_MODEL = "claude-haiku-4-5-20251001"
MAX_TASKS_SHOWN = 5


def _read_log() -> list[dict[str, Any]]:
    if not ACTIONS_LOG_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    with ACTIONS_LOG_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _week_window_utc() -> tuple[datetime, datetime, datetime]:
    """Return (week_start_utc, now_utc, now_local) for the current week.

    Week starts Monday 00:00 local time.
    """
    tz = ZoneInfo(TIMEZONE)
    now_local = datetime.now(tz)
    monday_local = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (
        monday_local.astimezone(timezone.utc),
        now_local.astimezone(timezone.utc),
        now_local,
    )


def compute_stats(week_start_utc: datetime, week_end_utc: datetime) -> dict[str, Any]:
    """Tally outcomes from the audit log for the given window."""
    entries = _read_log()

    completed: list[dict[str, Any]] = []
    added_tasks = 0
    added_notes = 0
    parse_errors = 0
    no_match = 0
    canceled = 0
    other_errors = 0

    for e in entries:
        ts_iso = e.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        except ValueError:
            continue
        if not (week_start_utc <= ts <= week_end_utc):
            continue

        intent = e.get("intent") or {}
        name = intent.get("name", "")
        result = e.get("result", "")

        if name == "complete_task" and result == "ok":
            completed.append(e)
        elif name == "add_task" and result == "ok":
            added_tasks += 1
        elif name == "append_to_brain_dump" and result == "ok":
            added_notes += 1
        elif result == "parse_error":
            parse_errors += 1
        elif result == "no_match":
            no_match += 1
        elif result == "canceled":
            canceled += 1
        elif result == "error":
            other_errors += 1

    return {
        "completed_count": len(completed),
        "completed": [e.get("details", "(?)") for e in completed],
        "added_tasks": added_tasks,
        "added_notes": added_notes,
        "parse_errors": parse_errors,
        "no_match": no_match,
        "canceled": canceled,
        "other_errors": other_errors,
    }


def generate_recommendation(stats: dict[str, Any]) -> str:
    """Claude Haiku produces one actionable observation. ~1 sentence."""
    if not ANTHROPIC_API_KEY:
        return "(Anthropic key not set — recommendation skipped)"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Jon's productivity agent activity this week:
- Completed tasks: {stats['completed_count']}
- New tasks added: {stats['added_tasks']}
- Notes added to brain dump: {stats['added_notes']}
- Friction signals: {stats['parse_errors']} unclear messages, {stats['no_match']} couldn't-match-task, {stats['canceled']} canceled actions, {stats['other_errors']} errors

Jon's context: founder of RaceCraft (50% time), looking for new paid work (20%), exercises 3-4x/week, no meetings before 9am or after 2pm, max 3 meetings/day. Impact-first prioritizer.

Give Jon ONE actionable operational improvement based on this week's pattern. Be specific and concrete (a behavior to start, stop, or change). 1-2 sentences max. No preamble, no pleasantries."""

    try:
        resp = client.messages.create(
            model=REVIEW_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return "(no text response)"
    except Exception as exc:
        log.exception("recommendation failed")
        return f"(recommendation failed: {type(exc).__name__})"


def build_friday_review() -> str:
    """Build the full Friday review message text."""
    week_start_utc, now_utc, now_local = _week_window_utc()
    stats = compute_stats(week_start_utc, now_utc)

    week_label = now_local.strftime("Week of %b %d, %Y")
    lines: list[str] = [
        f"Friday Review — {week_label}",
        f"({now_local.strftime('%a %b %d %-I:%M %p')})",
        "",
        "BY THE NUMBERS",
        f"  Completed tasks:   {stats['completed_count']}",
        f"  New tasks added:   {stats['added_tasks']}",
        f"  Notes captured:    {stats['added_notes']}",
        "",
        "FRICTION",
        f"  Unclear messages:  {stats['parse_errors']}",
        f"  Couldn't match:    {stats['no_match']}",
        f"  Canceled:          {stats['canceled']}",
        f"  Errors:            {stats['other_errors']}",
        "",
    ]

    if stats["completed"]:
        lines.append("WHAT SHIPPED")
        for item in stats["completed"][:MAX_TASKS_SHOWN]:
            lines.append(f"  • {item}")
        if len(stats["completed"]) > MAX_TASKS_SHOWN:
            lines.append(f"  ... +{len(stats['completed']) - MAX_TASKS_SHOWN} more")
        lines.append("")

    lines.append("ONE THING TO TRY NEXT WEEK")
    lines.append(generate_recommendation(stats))

    return "\n".join(lines)

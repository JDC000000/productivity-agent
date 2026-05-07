"""Assemble the daily briefing message — what /brief returns to Telegram.

Plain text only (no Markdown) — Telegram's Markdown parser is fussy and we
already had one bug from it in Phase 1.

Phase 4: tasks are now scored and sorted by priority. "Hot" tasks (score >= 8)
are surfaced in their own section above the full open-tasks list.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .config import TIMEZONE
from .data_sources.calendar import format_event_time
from .priority import HOT_THRESHOLD, annotate_and_sort, hot_tasks

DISPLAY_CAP = 10  # tasks/items beyond this are summarized as "+N more"
HOT_CAP = 5


def _format_task_line(t: dict) -> str:
    score = t.get("_score", 0)
    title = t.get("title", "(untitled)")
    due = t.get("due", "")
    due_str = f"  due {due[:10]}" if due else ""
    return f"  [{score}] {title}{due_str}"


def build_briefing(
    events: list[dict],
    tasks: list[dict],
    brain_dump: list[str],
) -> str:
    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()

    # Score and sort tasks by priority (mutates input).
    tasks = annotate_and_sort(tasks)
    hot = hot_tasks(tasks)

    lines: list[str] = [f"Briefing — {today.strftime('%a %b %d, %Y')}", ""]

    # --- Events ---
    lines.append("EVENTS TODAY")
    if events:
        for e in events:
            t = format_event_time(e)
            title = e.get("summary", "(no title)")
            lines.append(f"  {t}  {title}")
    else:
        lines.append("  (none)")
    lines.append("")

    # --- HOT (only if any tasks meet threshold) ---
    if hot:
        lines.append(f"HOT (score >= {HOT_THRESHOLD})")
        for t in hot[:HOT_CAP]:
            lines.append(_format_task_line(t))
        if len(hot) > HOT_CAP:
            lines.append(f"  ... +{len(hot) - HOT_CAP} more hot")
        lines.append("")

    # --- All open tasks (sorted by score) ---
    lines.append(f"OPEN TASKS ({len(tasks)})  -- sorted by priority")
    if tasks:
        for t in tasks[:DISPLAY_CAP]:
            lines.append(_format_task_line(t))
        if len(tasks) > DISPLAY_CAP:
            lines.append(f"  ... +{len(tasks) - DISPLAY_CAP} more")
    else:
        lines.append("  (none)")
    lines.append("")

    # --- Brain Dump ---
    lines.append(f"BRAIN DUMP — Active ({len(brain_dump)})")
    if brain_dump:
        for item in brain_dump[:DISPLAY_CAP]:
            lines.append(f"  • {item}")
        if len(brain_dump) > DISPLAY_CAP:
            lines.append(f"  ... +{len(brain_dump) - DISPLAY_CAP} more")
    else:
        lines.append("  (empty)")

    return "\n".join(lines)

"""Task prioritization scoring.

Formula (locked 2026-04-23):

    final_priority = impact_score
                   + racecraft_bonus      (+1 if tagged)
                   + deadline_urgency      (+10 if <48h, +5 if <7d)
                   - staleness_penalty     (-1 if untouched >14d)

Tasks with score >= 8 are "hot" — surfaced first in /brief.

Impact + racecraft tag are stored in the task's `notes` field at creation
time by the intent parser, e.g.:
    "impact:5
    #racecraft
    follow up with Whistler organizers"

Older tasks without these tags get default impact=3 and racecraft=false.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

HOT_THRESHOLD = 8
DEFAULT_IMPACT = 3
RACECRAFT_TAGS = ("#racecraft", "racecraft")

_IMPACT_RE = re.compile(r"impact[:=]\s*(\d)", re.IGNORECASE)


def _impact_from_notes(notes: str | None) -> int:
    if not notes:
        return DEFAULT_IMPACT
    m = _IMPACT_RE.search(notes)
    if not m:
        return DEFAULT_IMPACT
    try:
        v = int(m.group(1))
        return max(1, min(5, v))
    except ValueError:
        return DEFAULT_IMPACT


def _is_racecraft(title: str | None, notes: str | None) -> bool:
    text = f"{title or ''} {notes or ''}".lower()
    return any(tag in text for tag in RACECRAFT_TAGS)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _deadline_urgency(due_iso: str | None, now_utc: datetime) -> int:
    due = _parse_iso(due_iso)
    if not due:
        return 0
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    delta = due - now_utc
    secs = delta.total_seconds()
    if secs < 48 * 3600:
        return 10
    if secs < 7 * 86400:
        return 5
    return 0


def _staleness_penalty(updated_iso: str | None, now_utc: datetime) -> int:
    updated = _parse_iso(updated_iso)
    if not updated:
        return 0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if (now_utc - updated).days >= 14:
        return 1
    return 0


def score_task(task: dict[str, Any]) -> int:
    """Compute the priority score. Higher = more important."""
    now_utc = datetime.now(timezone.utc)
    impact = _impact_from_notes(task.get("notes"))
    racecraft = 1 if _is_racecraft(task.get("title"), task.get("notes")) else 0
    urgency = _deadline_urgency(task.get("due"), now_utc)
    staleness = _staleness_penalty(task.get("updated"), now_utc)
    return impact + racecraft + urgency - staleness


def annotate_and_sort(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inject _score on each task and sort descending. Mutates input list."""
    for t in tasks:
        t["_score"] = score_task(t)
    tasks.sort(key=lambda t: -t.get("_score", 0))
    return tasks


def hot_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tasks with score >= HOT_THRESHOLD. Assumes annotate_and_sort was called."""
    return [t for t in tasks if t.get("_score", 0) >= HOT_THRESHOLD]

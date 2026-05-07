"""Read + write Google Tasks across all of the user's tasklists.

Notes-as-metadata convention (used here, in priority.py, and by the parser):
  - "impact:N"          (1-5)         — priority signal, default 3
  - "#racecraft"        (line)        — RaceCraft tag, +1 priority bonus
  - "snoozed_until:YYYY-MM-DD" (line) — Phase 3g hide marker; tasks whose
        snoozed_until date is strictly in the future are filtered out of
        get_open_tasks() so they don't appear in /brief, the parser snapshot,
        or find_best_match. They reappear ON the snooze date.

Free-text user notes (anything else) are preserved untouched by munge_notes.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from ..config import TIMEZONE


_SNOOZE_RE = re.compile(r"^snoozed_until:(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)


def _today_local_iso() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()


def _is_snoozed(task: dict[str, Any], today_iso: str) -> bool:
    """True iff the task carries a snoozed_until marker strictly after today."""
    notes = task.get("notes") or ""
    m = _SNOOZE_RE.search(notes)
    return bool(m and m.group(1) > today_iso)


# ---------------- READ ----------------

def get_open_tasks(creds, max_per_list: int = 100) -> list[dict[str, Any]]:
    """Return all incomplete tasks from every tasklist, EXCLUDING snoozed.

    A task is "snoozed" if its notes contain `snoozed_until:YYYY-MM-DD` with a
    date strictly after today (local time). Snoozed tasks are filtered out
    here so /brief, the parser snapshot, find_best_match, and any other caller
    see a single consistent view.

    Each task dict gets two extra fields injected:
      - _list_title : human-readable name of its tasklist
      - _list_id    : tasklist ID (needed for write operations)
    """
    service = build("tasks", "v1", credentials=creds, cache_discovery=False)

    lists_resp = service.tasklists().list(maxResults=20).execute()
    today_iso = _today_local_iso()
    out: list[dict[str, Any]] = []

    for tl in lists_resp.get("items", []):
        tasks_resp = (
            service.tasks()
            .list(
                tasklist=tl["id"],
                showCompleted=False,
                showHidden=False,
                maxResults=max_per_list,
            )
            .execute()
        )
        for t in tasks_resp.get("items", []):
            if _is_snoozed(t, today_iso):
                continue
            t["_list_title"] = tl.get("title", "")
            t["_list_id"] = tl["id"]
            out.append(t)

    # Sort: tasks with due dates first (chronological), then undated
    out.sort(key=lambda t: (t.get("due") is None, t.get("due", "")))
    return out


# ---------------- WRITE ----------------

def _default_tasklist_id(service) -> str:
    lists_resp = service.tasklists().list(maxResults=1).execute()
    items = lists_resp.get("items", [])
    if not items:
        raise RuntimeError("No tasklists found in your Google Tasks account.")
    return items[0]["id"]


def create_task(
    creds,
    title: str,
    due: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Insert a new task into the user's default (first) tasklist.

    The returned dict has the API response plus an injected `_list_id` field
    (mirrors get_open_tasks's convention) so callers can record which tasklist
    the task landed in — needed by Phase 3f /undo to DELETE it later.
    """
    service = build("tasks", "v1", credentials=creds, cache_discovery=False)
    tasklist_id = _default_tasklist_id(service)

    body: dict[str, Any] = {"title": title}
    if due:
        # Google Tasks API expects RFC3339 timestamps. If a date-only string was passed,
        # pad to midnight UTC so the API accepts it.
        body["due"] = f"{due}T00:00:00.000Z" if len(due) == 10 else due
    if notes:
        body["notes"] = notes

    result = service.tasks().insert(tasklist=tasklist_id, body=body).execute()
    result["_list_id"] = tasklist_id
    return result


def complete_task_by_id(
    creds,
    task_id: str,
    tasklist_id: str | None = None,
) -> dict[str, Any]:
    """Mark a task complete. Falls back to the default tasklist if id not provided."""
    service = build("tasks", "v1", credentials=creds, cache_discovery=False)
    if tasklist_id is None:
        tasklist_id = _default_tasklist_id(service)

    return (
        service.tasks()
        .patch(tasklist=tasklist_id, task=task_id, body={"status": "completed"})
        .execute()
    )


def reopen_task_by_id(
    creds,
    task_id: str,
    tasklist_id: str | None = None,
) -> dict[str, Any]:
    """Mark a previously-completed task as open again (status=needsAction).

    Used by Phase 3f /undo when reverting a complete_task action.
    """
    service = build("tasks", "v1", credentials=creds, cache_discovery=False)
    if tasklist_id is None:
        tasklist_id = _default_tasklist_id(service)

    return (
        service.tasks()
        .patch(tasklist=tasklist_id, task=task_id, body={"status": "needsAction"})
        .execute()
    )


def delete_task_by_id(
    creds,
    task_id: str,
    tasklist_id: str | None = None,
) -> None:
    """Permanently delete a task. Used by Phase 3f /undo when reverting an add_task."""
    service = build("tasks", "v1", credentials=creds, cache_discovery=False)
    if tasklist_id is None:
        tasklist_id = _default_tasklist_id(service)

    service.tasks().delete(tasklist=tasklist_id, task=task_id).execute()


def update_task(
    creds,
    task_id: str,
    tasklist_id: str | None = None,
    *,
    title: str | None = None,
    due: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Phase 3g: PATCH a task's title / due / notes.

    Only fields explicitly passed are sent to the API. The Google Tasks API
    handles partial updates natively via PATCH, so unset fields stay as-is.

    Due dates accept ISO YYYY-MM-DD; padded to RFC3339 same as create_task.
    """
    service = build("tasks", "v1", credentials=creds, cache_discovery=False)
    if tasklist_id is None:
        tasklist_id = _default_tasklist_id(service)

    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if due is not None:
        body["due"] = f"{due}T00:00:00.000Z" if len(due) == 10 else due
    if notes is not None:
        body["notes"] = notes

    return (
        service.tasks()
        .patch(tasklist=tasklist_id, task=task_id, body=body)
        .execute()
    )


# ---------------- HELPERS ----------------

_IMPACT_LINE_RE = re.compile(r"^impact[:=]\s*(\d)\s*$", re.IGNORECASE)
_RACECRAFT_LINE_RE = re.compile(r"^#?racecraft\s*$", re.IGNORECASE)
_SNOOZE_LINE_RE = re.compile(r"^snoozed_until:(\d{4}-\d{2}-\d{2})\s*$")
_UNSET = object()  # sentinel for "leave alone"


def parse_notes_metadata(notes: str | None) -> dict[str, Any]:
    """Extract structured tags from a notes blob without modifying it.

    Used to capture before_state for an edit. Returns:
      {"impact": int|None, "racecraft": bool, "snoozed_until": str|None}
    impact is None if absent (vs DEFAULT_IMPACT in priority.py — that's a
    rendering default; here we want fidelity for /undo restoration).
    """
    impact: int | None = None
    racecraft = False
    snoozed_until: str | None = None
    if not notes:
        return {"impact": impact, "racecraft": racecraft, "snoozed_until": snoozed_until}

    for line in notes.splitlines():
        m = _IMPACT_LINE_RE.match(line)
        if m:
            try:
                impact = max(1, min(5, int(m.group(1))))
            except ValueError:
                pass
            continue
        if _RACECRAFT_LINE_RE.match(line):
            racecraft = True
            continue
        m = _SNOOZE_LINE_RE.match(line)
        if m:
            snoozed_until = m.group(1)

    return {"impact": impact, "racecraft": racecraft, "snoozed_until": snoozed_until}


def munge_notes(
    notes: str | None,
    *,
    impact: int | object = _UNSET,
    racecraft: bool | object = _UNSET,
    snoozed_until: str | None | object = _UNSET,
) -> str:
    """Rewrite the structured tags in a notes blob, preserving free-text lines.

    Each kwarg uses the _UNSET sentinel as the default — pass nothing to leave
    the corresponding tag alone. Pass None to clear (only meaningful for
    snoozed_until; passing impact=None or racecraft=None would be ambiguous so
    those args don't accept None).

    Free-text user lines (anything that doesn't match impact:N / #racecraft /
    snoozed_until:DATE) are preserved in original order. Tag lines that ARE
    being changed are removed; replacements are appended at the end in a
    canonical order so the result is stable.

    Returns a newline-joined string. If the result would be empty, returns "".
    """
    free_text: list[str] = []
    seen_impact: int | None = None
    seen_racecraft = False
    seen_snooze: str | None = None

    for line in (notes or "").splitlines():
        m = _IMPACT_LINE_RE.match(line)
        if m:
            try:
                seen_impact = int(m.group(1))
            except ValueError:
                pass
            continue
        if _RACECRAFT_LINE_RE.match(line):
            seen_racecraft = True
            continue
        m = _SNOOZE_LINE_RE.match(line)
        if m:
            seen_snooze = m.group(1)
            continue
        free_text.append(line)

    # Decide final tag values. _UNSET => keep what was seen.
    final_impact = seen_impact if impact is _UNSET else impact
    final_racecraft = seen_racecraft if racecraft is _UNSET else racecraft
    final_snooze = seen_snooze if snoozed_until is _UNSET else snoozed_until

    # Trim trailing empty lines from free_text so we don't pile up blank lines.
    while free_text and not free_text[-1].strip():
        free_text.pop()

    out_lines = list(free_text)
    if final_impact is not None:
        out_lines.append(f"impact:{final_impact}")
    if final_racecraft:
        out_lines.append("#racecraft")
    if final_snooze:
        out_lines.append(f"snoozed_until:{final_snooze}")

    return "\n".join(out_lines)


def find_best_match(query: str, tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Two-tier fuzzy match: exact substring first, then word-overlap.

    Returns the best match or None. Caller is expected to confirm with the user
    before completing — this is a best-guess, not a certainty.
    """
    if not tasks:
        return None

    q = query.lower().strip()

    # Tier 1: exact substring (most specific)
    for t in tasks:
        title = (t.get("title", "") or "").lower()
        if q and q in title:
            return t

    # Tier 2: word overlap
    q_words = set(q.split())
    if not q_words:
        return None
    best: dict[str, Any] | None = None
    best_overlap = 0
    for t in tasks:
        title = (t.get("title", "") or "").lower()
        overlap = len(q_words & set(title.split()))
        if overlap > best_overlap:
            best_overlap = overlap
            best = t
    return best

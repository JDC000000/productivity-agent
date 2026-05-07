"""Phase 3f: find the most recent undoable action in the audit log.

`/undo` walks the JSONL audit log backwards, skipping:
  - non-ok entries
  - undo entries themselves (you can't undo an undo)
  - entries whose ts appears in any undo's `undid_ts` field (already undone)
  - append_to_brain_dump entries (appends are hard to reverse cleanly)

If only brain-dump entries appear before the next undoable action, we surface
that distinctly so the user gets a clear "edit the doc directly" hint.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import ACTIONS_LOG_PATH


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


def find_undoable_entry() -> tuple[str, dict[str, Any] | None]:
    """Return ('found', entry) | ('brain_dump_only', None) | ('empty', None)."""
    entries = _read_log()

    undone_ts: set[str] = set()
    for e in entries:
        intent = e.get("intent") or {}
        if (
            intent.get("name") == "undo"
            and e.get("result") == "ok"
            and e.get("undid_ts")
        ):
            undone_ts.add(e["undid_ts"])

    saw_brain_dump = False
    for e in reversed(entries):
        intent = e.get("intent") or {}
        name = intent.get("name", "")
        if e.get("result") != "ok":
            continue
        if name == "undo":
            continue
        if e.get("ts") in undone_ts:
            continue
        if name == "append_to_brain_dump":
            saw_brain_dump = True
            continue
        if name in ("add_task", "complete_task"):
            return ("found", e)

    return ("brain_dump_only", None) if saw_brain_dump else ("empty", None)


def describe_target(entry: dict[str, Any]) -> tuple[str, str, str]:
    """Return (verb, past_tense, noun) for a confirmation/result message.

    Examples:
      add_task     -> ("delete",  "deleted",   "'Call Lisa (due 2026-05-07)'")
      complete_task-> ("re-open", "re-opened", "'Buy milk'")
    """
    intent = entry.get("intent") or {}
    name = intent.get("name", "")
    inp = intent.get("input") or {}

    if name == "add_task":
        title = inp.get("title") or "(unknown)"
        due = inp.get("due")
        noun = f"'{title} (due {due})'" if due else f"'{title}'"
        return ("delete", "deleted", noun)

    if name == "complete_task":
        # Phase 3d entries put the matched title in details: "Completed: <title>".
        details = entry.get("details") or ""
        if isinstance(details, str) and details.startswith("Completed: "):
            title = details[len("Completed: "):]
        else:
            title = inp.get("title_query") or "(unknown)"
        return ("re-open", "re-opened", f"'{title}'")

    return ("undo", "undone", "(unknown)")

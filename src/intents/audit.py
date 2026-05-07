"""Append-only JSONL log of every action the agent took.

One line per record. Use `jq` or grep to inspect later. Friday review reads
this to compute completion rate, top wins, and time-to-close.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..config import ACTIONS_LOG_PATH


def log_action(
    user_input: str,
    intent: dict[str, Any] | None,
    result: str,
    details: Any = None,
    error: str | None = None,
    via: str = "text",
    created_task_id: str | None = None,
    created_task_list_id: str | None = None,
    undid_ts: str | None = None,
    before_state: dict[str, Any] | None = None,
) -> None:
    """Append one record to actions.jsonl. Best-effort — doesn't raise if logging fails.

    `via` indicates the input channel ("text" or "voice"). Friday review uses it
    to break down activity by channel.

    Phase 3f optional fields (only included when non-None):
      created_task_id / created_task_list_id : set on add_task ok entries so
        /undo can DELETE the created task later.
      undid_ts : set on undo ok entries; the ts of the audit entry being undone.
        Used to skip already-undone actions on subsequent /undo scans.

    Phase 3g optional field:
      before_state : pre-edit values for whichever task fields the edit_task
        intent changed. Stashed for a future /undo on edits (Phase 3j).
    """
    ACTIONS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_input": user_input,
        "intent": intent,
        "result": result,
        "via": via,
    }
    if details is not None:
        record["details"] = details
    if error is not None:
        record["error"] = error
    if created_task_id is not None:
        record["created_task_id"] = created_task_id
    if created_task_list_id is not None:
        record["created_task_list_id"] = created_task_list_id
    if undid_ts is not None:
        record["undid_ts"] = undid_ts
    if before_state is not None:
        record["before_state"] = before_state

    try:
        with ACTIONS_LOG_PATH.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        # Audit log should never crash the bot.
        import logging
        logging.getLogger(__name__).exception("audit log write failed")

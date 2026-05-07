"""Route a parsed intent to the matching Google API write call."""
from __future__ import annotations

from typing import Any

from ..config import BRAIN_DUMP_DOC_ID
from ..data_sources.brain_dump import append_to_active, get_recent_active_items
from ..data_sources.tasks import (
    complete_task_by_id,
    create_task,
    munge_notes,
    parse_notes_metadata,
    update_task,
)

DEFAULT_QUERY_LIMIT = 10


def execute_intent(intent: dict[str, Any], creds) -> str:
    """Execute the intent. Returns a short human-readable confirmation string."""
    name = intent["name"]
    inp = intent.get("input", {})

    if name == "add_task":
        # Phase 3e: tasks is a list. Loop and create each. Per-task records
        # are appended to intent['created_records'] as we go so the bot can
        # write per-task audit entries — and so partial successes survive a
        # mid-loop failure (we leave the trail and re-raise).
        tasks_in = inp.get("tasks") or []
        if not tasks_in:
            raise RuntimeError("add_task: no tasks provided.")

        intent.setdefault("created_records", [])
        summary_lines: list[str] = []

        for t in tasks_in:
            title = t.get("title")
            if not title:
                raise RuntimeError("add_task: task missing title.")
            impact = t.get("impact", 3)
            racecraft = bool(t.get("racecraft"))

            # Stuff priority signals into the notes field — priority.py reads them at /brief time.
            notes_parts: list[str] = []
            if t.get("notes"):
                notes_parts.append(t["notes"])
            notes_parts.append(f"impact:{impact}")
            if racecraft:
                notes_parts.append("#racecraft")
            notes = "\n".join(notes_parts)

            api_result = create_task(
                creds,
                title=title,
                due=t.get("due"),
                notes=notes,
            )

            line = f"Added task: {api_result.get('title') or title}"
            if api_result.get("due"):
                line += f" (due {api_result['due'][:10]})"
            line += f" [impact:{impact}{', racecraft' if racecraft else ''}]"
            summary_lines.append(line)

            # Singular-shaped intent.input for the per-task audit entry — only
            # include fields the user actually set, plus the impact default.
            task_input: dict[str, Any] = {"title": title, "impact": impact}
            if t.get("due"):
                task_input["due"] = t["due"]
            if t.get("notes"):
                task_input["notes"] = t["notes"]
            if racecraft:
                task_input["racecraft"] = True

            intent["created_records"].append({
                "task_input": task_input,
                "summary_line": line,
                # Phase 3f: capture IDs so /undo can DELETE this task later.
                "created_task_id": api_result.get("id"),
                "created_task_list_id": api_result.get("_list_id"),
            })

        if len(summary_lines) == 1:
            return summary_lines[0]
        bullets = "\n".join(f"- {line.removeprefix('Added task: ')}" for line in summary_lines)
        return f"Added {len(summary_lines)}:\n{bullets}"

    if name == "append_to_brain_dump":
        if not BRAIN_DUMP_DOC_ID:
            raise RuntimeError("BRAIN_DUMP_DOC_ID not set in .env")
        append_to_active(creds, BRAIN_DUMP_DOC_ID, inp["text"])
        return f"Added to Brain Dump (Active): {inp['text']}"

    if name == "query_brain_dump":
        # Phase 3i. Read-only; no confirmation flow. Pulls newest-first via the
        # 30s-cached helper, applies optional keyword filter, slices to limit.
        if not BRAIN_DUMP_DOC_ID:
            raise RuntimeError("BRAIN_DUMP_DOC_ID not set in .env")

        limit = int(inp.get("limit") or DEFAULT_QUERY_LIMIT)
        keyword = (inp.get("keyword") or "").strip()

        all_items = get_recent_active_items(creds, BRAIN_DUMP_DOC_ID, limit=None)

        if not all_items:
            intent["_audit_summary"] = "Returned 0 entries (doc empty)"
            return "Brain dump is empty."

        if keyword:
            kw_lower = keyword.lower()
            filtered = [i for i in all_items if kw_lower in i.lower()]
        else:
            filtered = all_items

        results = filtered[:limit]
        n = len(results)

        if n == 0:
            # Only reached when keyword filtered everything out.
            intent["_audit_summary"] = f"Returned 0 entries (keyword={keyword!r})"
            return f"No brain dump entries match {keyword!r}."

        header = (
            f"Last {n} brain dump entries"
            + (f" matching {keyword!r}" if keyword else "")
            + ":"
        )
        body = "\n".join(f"{i}. {item}" for i, item in enumerate(results, 1))

        summary = f"Returned {n} entries"
        if keyword:
            summary += f" (keyword={keyword!r})"
        intent["_audit_summary"] = summary

        return f"{header}\n{body}"

    if name == "edit_task":
        # Phase 3g: edits is a list. The BOT runs find_best_match per query and
        # attaches matched_edits = [{task: <full task dict>, edit: <input>}, ...]
        # before calling us. We patch each, capture before_state, accumulate
        # per-edit records on intent['edited_records'] for the bot to audit.
        matched_edits = intent.get("matched_edits") or []
        if not matched_edits:
            raise RuntimeError("edit_task: no matched edits — caller should match first.")

        intent.setdefault("edited_records", [])
        summary_lines: list[str] = []

        for me in matched_edits:
            task = me["task"]
            edit = me["edit"]
            target_query = edit.get("target_query", "")

            # Validate: at least one edit field set. JSON schema can't enforce this.
            edit_fields = {
                "new_title": edit.get("new_title"),
                "new_due": edit.get("new_due"),
                "new_impact": edit.get("new_impact"),
                "new_racecraft": edit.get("new_racecraft"),
                "snooze_until": edit.get("snooze_until"),
            }
            if all(v is None for v in edit_fields.values()):
                raise RuntimeError(
                    f"edit_task: no fields to change on '{task.get('title', '')}'"
                )

            # Capture before_state for fields the user is changing.
            old_meta = parse_notes_metadata(task.get("notes"))
            before: dict[str, Any] = {}
            change_lines: list[str] = []

            new_title = edit_fields["new_title"]
            if new_title is not None:
                before["title"] = task.get("title", "")
                change_lines.append(f"title '{before['title']}' → '{new_title}'")

            # snooze_until implies a due date set too; treat it specially so the
            # before_state captures BOTH old due and old snooze marker.
            snooze_until = edit_fields["snooze_until"]
            new_due = edit_fields["new_due"]
            if snooze_until is not None:
                before["due"] = task.get("due")
                before["snoozed_until"] = old_meta["snoozed_until"]
                # snooze sets due to the same date; user's explicit new_due (if
                # also passed, unusual) takes precedence.
                effective_due = new_due if new_due is not None else snooze_until
                change_lines.append(f"snoozed until {snooze_until}")
            elif new_due is not None:
                before["due"] = task.get("due")
                old_due_short = (task.get("due") or "")[:10] or "(none)"
                change_lines.append(f"due {old_due_short} → {new_due}")
                effective_due = new_due
            else:
                effective_due = None  # don't touch due

            new_impact = edit_fields["new_impact"]
            if new_impact is not None:
                before["impact"] = old_meta["impact"]
                old_impact_str = (
                    str(old_meta["impact"]) if old_meta["impact"] is not None else "?"
                )
                change_lines.append(f"impact {old_impact_str} → {new_impact}")

            new_racecraft = edit_fields["new_racecraft"]
            if new_racecraft is not None:
                before["racecraft"] = old_meta["racecraft"]
                if new_racecraft and not old_meta["racecraft"]:
                    change_lines.append("tagged racecraft")
                elif not new_racecraft and old_meta["racecraft"]:
                    change_lines.append("racecraft tag removed")
                else:
                    # No-op rewrite (already in desired state); skip line, keep before.
                    pass

            # Build the new notes blob via munge_notes (None means leave alone
            # since we use a sentinel internally; we pass actual values when set).
            munge_kwargs: dict[str, Any] = {}
            if new_impact is not None:
                munge_kwargs["impact"] = new_impact
            if new_racecraft is not None:
                munge_kwargs["racecraft"] = new_racecraft
            if snooze_until is not None:
                munge_kwargs["snoozed_until"] = snooze_until
            new_notes = (
                munge_notes(task.get("notes"), **munge_kwargs)
                if munge_kwargs
                else None  # don't touch notes if no metadata field changed
            )

            # PATCH the task. Pass only the fields that actually changed.
            patch_kwargs: dict[str, Any] = {}
            if new_title is not None:
                patch_kwargs["title"] = new_title
            if effective_due is not None:
                patch_kwargs["due"] = effective_due
            if new_notes is not None:
                patch_kwargs["notes"] = new_notes

            update_task(
                creds,
                task_id=task["id"],
                tasklist_id=task.get("_list_id"),
                **patch_kwargs,
            )

            display_title = new_title or task.get("title") or "(untitled)"
            line = f"{display_title}: " + ", ".join(change_lines)
            summary_lines.append(line)

            intent["edited_records"].append({
                "edit_input": edit,
                "matched_task_id": task["id"],
                "matched_task_list_id": task.get("_list_id"),
                "summary_line": line,
                "before_state": before,
            })

        if len(summary_lines) == 1:
            return f"Updated 1:\n- {summary_lines[0]}"
        bullets = "\n".join(f"- {line}" for line in summary_lines)
        return f"Updated {len(summary_lines)}:\n{bullets}"

    if name == "complete_task":
        # Bot is expected to have done find_best_match + confirmation already
        # and stashed a list of matched tasks onto the intent.
        matched_tasks = intent.get("matched_tasks") or []
        if not matched_tasks:
            raise RuntimeError("complete_task: no matched tasks — caller should confirm first.")

        completed_titles: list[str] = []
        for m in matched_tasks:
            result = complete_task_by_id(
                creds,
                task_id=m["id"],
                tasklist_id=m.get("list_id"),
            )
            completed_titles.append(result.get("title") or m.get("title") or "(untitled)")

        if len(completed_titles) == 1:
            return f"Completed: {completed_titles[0]}"
        bullets = "\n".join(f"- {t}" for t in completed_titles)
        return f"Completed {len(completed_titles)}:\n{bullets}"

    raise RuntimeError(f"Unknown intent: {name}")

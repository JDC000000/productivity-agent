# Phase 3g — Edit / Reschedule / Snooze

Adds a single `edit_task` intent that can change a task's title, due date,
impact, racecraft tag, or snooze it (hide from /brief until a date). Multi-edit
in one message ("push X and Y to Tuesday") works the same way Phase 3e's
multi-add and Phase 3d's multi-complete do.

**No yes/no confirmation** — edits execute immediately. They're reversible by
re-firing the message. A future Phase 3j can wire `/undo` for edits using the
`before_state` field already captured in audit entries.

## What changed

- `src/intents/audit.py` — new `before_state` kwarg on `log_action`.
- `src/data_sources/tasks.py` — new `update_task()` (PATCH wrapper), pure
  helpers `munge_notes()` and `parse_notes_metadata()`, snoozed-task filter
  inside `get_open_tasks()`.
- `src/intents/parser.py` — new `edit_task` tool schema, "Editing tasks" block
  in the system prompt, snooze-aware visibility note.
- `src/intents/executor.py` — `edit_task` branch: validate, capture
  before_state, munge notes, PATCH, accumulate per-edit records.
- `src/bot.py` — new `_handle_edit_task()` (no confirmation flow), per-task
  audit entries with `before_state`, partial-match reply.

## Setup

    ./install-launchd.sh
    launchctl list | grep productivity-agent
    tail -f ~/.productivity-agent/logs/agent.log

## Test plan

Each row is a separate Telegram message (text or voice). Watch the agent log
and `tail ~/.productivity-agent/logs/actions.jsonl | jq` after each.

| # | Send                                                       | Expected |
|---|------------------------------------------------------------|----------|
| 1 | `add a task to call Whistler tomorrow`                     | `[OK] Added task: Call Whistler (due ...)` (baseline) |
| 2 | `rename call Whistler to email Whistler organizers`        | `Updated 1: Email Whistler organizers: title 'Call Whistler' → 'Email Whistler organizers'` |
| 3 | `bump the Whistler email to impact 5 and tag racecraft`    | `Updated 1: ...: impact 3 → 5, tagged racecraft` |
| 4 | `push the Whistler email to next Tuesday`                  | `Updated 1: ...: due <today+1> → <next-Tue ISO>` |
| 5 | (voice) `snooze the Whistler email until Friday`           | `Updated 1: ...: snoozed until <Fri ISO>` |
| 6 | `/brief`                                                   | OPEN TASKS does NOT include the Whistler task ✅ |
| 7 | `add task A and add task B` (or two adds)                  | baseline for multi-edit |
| 8 | `push A and B to Tuesday`                                  | `Updated 2: \n- A: due ... → ... \n- B: due ... → ...` |
| 9 | `push A and nonexistent task to Friday`                    | `Updated 1: A: ... \n\n No match: 'nonexistent task'` |

After Step 5, the audit JSONL entry for the snooze should look like:

    {
      "ts": "...",
      "user_input": "Snooze the Whistler email until Friday.",
      "intent": {
        "name": "edit_task",
        "input": {"target_query": "Email Whistler organizers", "snooze_until": "2026-05-08"},
        "matched_task_id": "...",
        "matched_task_list_id": "..."
      },
      "result": "ok",
      "via": "voice",
      "details": "Email Whistler organizers: snoozed until 2026-05-08",
      "before_state": {"due": "2026-05-12T00:00:00.000Z", "snoozed_until": null}
    }

## Snooze semantics

- `snoozed_until:YYYY-MM-DD` is appended to the task's notes field.
- A task is hidden iff `snoozed_until > today_local` (strict). It reappears ON
  the snooze date, not the day after.
- The snooze filter lives inside `get_open_tasks()` so /brief, the parser
  snapshot, and `find_best_match` all see a consistent view.
- Friday review reads the audit log, NOT `get_open_tasks`, so its "what
  shipped" stats are unaffected by snoozes — completions still count.

## Known limitation: unsnoozing

Because snoozed tasks are filtered out of the parser snapshot, Haiku can't
"see" them to act on. *"Unsnooze the gym thing"* will hit needs_clarification.
To unsnooze right now: edit the date in Google Tasks directly, or wait for the
snooze date to pass. If this becomes a real friction point, a future phase can
add a `/snoozed` command or a snapshot flag.

## Notes-as-metadata convention

(Documented at the top of `src/data_sources/tasks.py`.)

```
<optional free-text notes>
impact:N         (1-5)
#racecraft       (optional)
snoozed_until:YYYY-MM-DD   (optional, Phase 3g)
```

`munge_notes()` preserves free-text lines and rewrites only the structured
tags. Tag lines are appended at the end in canonical order so the result is
stable across rewrites.

# Phase 5 — Conversational Layer

The bot used to read like a CLI: `[OK] Added task: Buy milk (due 2026-05-08) [impact:3]`.
Now it reads like texting a sharp executive assistant: `Added — buy milk for Thursday.`

## What changed

- `src/intents/parser.py` — `needs_clarification` tool removed. New return shape
  `{text_reply, tool_calls, assistant_message}`. Accepts `history` arg.
  `tool_choice` switched from `"any"` to `"auto"` so Haiku can stay text-only.
  System prompt rewritten with persona, voice rules, GOOD/BAD examples,
  context Jon expects Haiku to remember (RaceCraft, no meetings before 9am, etc.).
- `src/bot.py` — per-chat sliding-window conversation history (`_history` dict,
  10 user turns max, in-memory only). New `_dispatch_tool_call`,
  `_execute_complete_immediate`, `_execute_edit_immediate`. Old
  `_handle_complete_task`, `_handle_edit_task`, `_execute_confirmed` removed.
  `[OK]` prefixes stripped from all replies.
- **`complete_task` no longer stages a yes/no confirm** — it fires immediately,
  same as `add_task`/`edit_task`. `/undo` is the safety net.
- `src/intents/executor.py`, `src/intents/audit.py` — **unchanged**. Audit log
  shape and per-tool result strings preserved exactly.

## Setup

    ./install-launchd.sh
    launchctl list | grep productivity-agent
    tail -f ~/.productivity-agent/logs/agent.log

## Behavior changes worth flagging

1. **Replies are shorter and warmer.** Haiku writes them. No more `[OK] Added task: Buy milk (due 2026-05-08) [impact:3]` — expect things like `Added — buy milk for Thursday.` Sometimes silence (no reply at all) when the action is trivial; the user can see what changed.
2. **`complete_task` fires without confirm.** Says "mark gym done" → it's done. `/undo` reverses if needed. Phase 3h's task-aware snapshot makes Haiku confident enough to skip the safety net.
3. **`needs_clarification` is gone.** When unclear, Haiku writes a plain-text question instead of calling a tool. Same UX, less ceremony.
4. **Continuity within a chat.** "actually make it oat milk" right after "add buy milk" works — Haiku sees the prior turn in history and emits `edit_task` against the just-added task.

## Acceptance test plan

Send each line below. Watch the bot's reply + `tail -f ~/.productivity-agent/logs/actions.jsonl | python3 -m json.tool --json-lines`.

### 1. Continuity

| # | Send | Expected reply | Audit |
|---|------|----------------|-------|
| 1 | `add buy milk` | something like `Added — buy milk.` | one `add_task` `result: "ok"` entry |
| 2 | `actually make it oat milk` | something like `Renamed — oat milk.` | one `edit_task` `result: "ok"` with `new_title="Buy oat milk"` (or similar) and `before_state.title="Buy milk"` |

If turn 2 fails with "no open task matched" or "couldn't tell", the history isn't being passed correctly.

### 2. Chaining

Pre-req: have an open task to mark done (e.g. "Go to the gym").

Send: `mark the gym task done and note that I'm starting deep work`

Expected: ONE bot reply (something like `Done. Deep work noted.`). Both actions fire in the same turn.

Audit: TWO entries — one `complete_task` `result: "ok"`, one `append_to_brain_dump` `result: "ok"`.

### 3. Natural clarification

Send: `do the Whistler thing`

Expected: a plain-text question, e.g. `Complete the Whistler call task or add a new one?`. NO tool call. NO `[OK]` prefix. NO structured `interpretation/question_for_user` shape.

Audit: nothing (Haiku didn't call a tool).

### 4. Quiet wins

Send: `call lisa tuesday`

Expected: a one-line ack like `Added — call Lisa Tuesday.` No `[OK]`, no `(due 2026-05-12)`, no `[impact:3]` brackets.

Audit: one `add_task` `result: "ok"` entry — exactly the same fields as before Phase 5 (`details: "Added task: Call Lisa (due 2026-05-12) [impact:3]"`, `created_task_id`, `created_task_list_id`).

### 5. No regressions

Run the audit-shape comparison:

    grep '"add_task"' ~/.productivity-agent/logs/actions.jsonl | tail -2 | python3 -m json.tool --json-lines

The most recent two `add_task` entries (one from before Phase 5, one from after) should have identical field sets. Same keys: `ts`, `user_input`, `intent`, `result`, `via`, `details`, `created_task_id`, `created_task_list_id`. The values for `intent.input.title`, `intent.input.impact`, `details` should follow the same conventions.

Also verify the unrelated commands still work:
- `/brief` → unchanged formatting
- `/undo` → still asks "About to undo: ... Yes/no?" before reverting (its confirm flow is preserved)
- `/chatid`, `/ping` → unchanged

## Voice path

Voice messages still work the same way: Whisper transcribes → `_process_user_text` runs the conversational pipeline. The `Heard: ...` echo before processing is preserved (useful for catching mistranscriptions).

## Conversation history details

- **Per-chat**, keyed on Telegram chat_id. Single-user bot in practice.
- **In-memory** — wiped on bot restart (any `./install-launchd.sh`). Across restarts, Haiku has no memory of prior turns.
- **Sliding window**: last 10 *real-user* messages + their assistant responses.
- **Format**: standard Anthropic API messages list — `[{role: user/assistant, content: ...}, ...]`. Assistant messages preserve text + tool_use blocks so Haiku sees what it called last turn (key for the continuity acceptance test).
- **Not persisted** to the audit log or anywhere on disk.

## Unsupported / known limits

- **No memory across restarts.** If the bot reloads mid-conversation, history resets. Per spec, this is intentional.
- **Snoozed tasks remain invisible to Haiku.** Same as Phase 3g — to unsnooze, edit Google Tasks directly or wait for the snooze date.
- **No streaming replies.** Telegram gets the bot's reply in one shot after Haiku finishes.
- **Brain dump appends still inherit Heading 2 style** in the doc UI. Phase 3i's read parser handles this transparently — the bullet text is recognized regardless. Future improvement: explicit paragraph styling in `append_to_active`.

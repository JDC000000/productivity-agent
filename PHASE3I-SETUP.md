# Phase 3i — Brain Dump Query

Adds a read path on the Brain Dump doc. Until now the doc was append-only —
to recall what you'd noted, you had to open it. Now `"what did I dump"`,
`"show recent dumps about pricing"`, etc. work via Telegram (text or voice).

No yes/no confirmation. Read-only. Audit-logged so Friday review can see
read-side activity if/when we want to display it.

## What changed

- `src/data_sources/brain_dump.py` — `_DOC_CACHE` (30s TTL keyed on
  `(id(creds), doc_id)`), new `get_recent_active_items()` returning newest-first,
  cache invalidated automatically by `append_to_active()`.
- `src/intents/parser.py` — new `query_brain_dump` tool (both `limit` 1–30 and
  `keyword` are optional). New "Querying brain dump" block in the system prompt
  noting that temporal qualifiers ("last week") are not supported because the
  doc isn't timestamped.
- `src/intents/executor.py` — `query_brain_dump` branch: fetch all (cached),
  filter case-insensitive substring, slice to limit. Stashes a short audit
  summary on `intent["_audit_summary"]`.
- `src/bot.py` — one-line tweak in the existing audit branch to prefer
  `intent["_audit_summary"]` over the full reply text when present.

## Why no timestamps in the doc

The audit log already records every `append_to_brain_dump` with a UTC
timestamp. Future date-filtering (`"what did I dump last week"`) can read from
there if/when we need it, without polluting the doc's visual style with prefixes
that older entries don't have.

## Setup

    ./install-launchd.sh
    launchctl list | grep productivity-agent
    tail -f ~/.productivity-agent/logs/agent.log

## Test plan

Run these in order. Each line is a separate Telegram message (text or voice).

| # | Send                                                | Expected reply |
|---|-----------------------------------------------------|----------------|
| 1 | `note that the new chain is louder than expected`   | `[OK] Added to Brain Dump (Active): the new chain is louder than expected` |
| 2 | `note that pricing for race-day pack is too low`    | `[OK] Added to Brain Dump (Active): pricing for race-day pack is too low` |
| 3 | `note that whistler comms team needs new logos`     | `[OK] Added to Brain Dump (Active): whistler comms team needs new logos` |
| 4 | `what did I dump`                                   | `Last N brain dump entries:\n1. whistler...\n2. pricing...\n3. the new chain...\n...` (newest first; up to 10) |
| 5 | `show my last 2 brain dump entries`                 | `Last 2 brain dump entries:\n1. whistler...\n2. pricing...` |
| 6 | `find brain dump entries about pricing`             | `Last 1 brain dump entries matching 'pricing':\n1. pricing for race-day pack is too low` |
| 7 | `find brain dump entries about banana`              | `No brain dump entries match 'banana'.` |
| 8 | (voice) `what did I dump about whistler last week`  | Drops "last week", searches keyword='whistler'. Same result format as #6 with whistler entry. |

After Step 8 the audit JSONL entry should look like:

    {
      "ts": "...",
      "user_input": "What did I dump about Whistler last week.",
      "intent": {
        "name": "query_brain_dump",
        "input": {"keyword": "whistler"}
      },
      "result": "ok",
      "via": "voice",
      "details": "Returned 1 entries (keyword='whistler')"
    }

The `details` is the short summary, NOT the full multi-line reply — the bot's
audit branch reads `intent["_audit_summary"]` when present.

## Cache behavior

Two consecutive `query_brain_dump` calls within 30 seconds hit the same Docs
API result (no second network call). Confirm by tailing the agent log: the
second query won't show a `googleapiclient.discovery_cache` line for the doc
fetch.

If you append a new note via `note that X` and immediately query, the cache is
invalidated by `append_to_active()`, so the new entry appears on the very next
query — no waiting out the TTL.

## Doesn't break Phase 1–3h

- `add_task`, `complete_task`, `edit_task`, `append_to_brain_dump`, `/undo`,
  `needs_clarification`, voice transcription, multi-task pipes, snooze filter:
  all unchanged.
- Friday review reads the audit log; it has no branch for `query_brain_dump`
  so those entries land in the log but don't affect any counter. Same behavior
  as `edit_task` entries from Phase 3g.

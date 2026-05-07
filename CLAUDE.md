# Productivity Agent V1 — Project Context for Claude Code

## What this is
A Telegram-native productivity agent built for Jon Cartwright. Aggregates Google Calendar + Google Tasks + a Google Doc ("Task Brain Dump") into a single daily briefing. Accepts free-text commands via Telegram, parses intent with Claude Haiku, writes back to Google. Friday review with audit-log analytics and Claude-generated recommendations.

## Tech stack
- Python 3.14 (running on macOS)
- python-telegram-bot[job-queue] >= 21
- Anthropic SDK (Haiku 4.5 for intent parsing + Friday review)
- Google APIs: Calendar v3, Tasks v1, Docs v1
- launchd for auto-start (com.jon.productivity-agent.plist)
- SQLite-free design — append-only JSONL audit log

## What's shipped
- **Phase 1**: Telegram echo bot, /chatid, /ping
- **Phase 2**: Google OAuth (read+write scopes for calendar/tasks/documents), /brief command
- **Phase 3a**: Free-text → Claude tool-use intent parser → writes to Google Tasks + Brain Dump doc. Three intents: add_task, append_to_brain_dump, complete_task. needs_clarification fallback for ambiguous inputs.
- **Phase 3 Ops**: launchd auto-start, 8am Mon-Fri daily brief, Friday 4pm review (both via PTB JobQueue)
- **Phase 4**: Smart prioritization (impact-first scoring with RaceCraft +1, deadline override, staleness penalty, hot threshold ≥8). Tasks scored at /brief time, sorted descending. Friday review reads audit log, generates one Haiku-written recommendation.

## What's NOT shipped
- **Phase 3b: voice messages** ← this is the next build
- 7-day OAuth re-auth automation (current pain: Google token expires weekly because OAuth app is in Testing mode)
- V2 cloud migration

## File structure
```
~/productivity-agent/
├── README.md
├── PHASE2-SETUP.md, PHASE3A-SETUP.md, PHASE3-OPS-SETUP.md, PHASE4-SETUP.md
├── requirements.txt
├── run.sh                  # bot launcher (creates venv, syncs deps, runs main)
├── install-launchd.sh      # idempotent launchd install via bootstrap/bootout
├── .env (gitignored)       # secrets
├── launchd/com.jon.productivity-agent.plist.example
└── src/
    ├── __init__.py
    ├── main.py             # entry point
    ├── config.py           # env loader (single source of truth)
    ├── logger.py           # rotating file + stdout
    ├── google_auth.py      # OAuth flow + get_credentials()
    ├── bot.py              # Telegram handlers
    ├── briefing.py         # builds /brief output
    ├── friday_review.py    # builds /review output + Claude rec
    ├── priority.py         # scoring formula
    ├── scheduler.py        # JobQueue scheduled jobs
    ├── data_sources/
    │   ├── calendar.py     # today's events (read)
    │   ├── tasks.py        # Google Tasks (read + write)
    │   └── brain_dump.py   # Google Doc parser + append
    └── intents/
        ├── parser.py       # Claude tool-use intent parsing
        ├── executor.py     # routes intent → write
        └── audit.py        # JSONL action log
```

## Conventions
- All env vars loaded once in `src/config.py`. Add new vars there with `_optional()` or `_require()`.
- Plain text only in Telegram messages — Markdown parser is fussy and broke once already.
- launchd needs the project OUTSIDE `~/Documents` (TCC blocks Documents). Already at `~/productivity-agent`.
- Audit log at `~/.productivity-agent/logs/actions.jsonl`. Friday review reads from here.
- Tasks store priority signals in their `notes` field as `impact:N` and `#racecraft`.
- Default impact is 3. Hot threshold (surfaced separately in /brief) is ≥8.

## Operating principles for Jon
- RaceCraft is the 2026 priority bet (50% of his time). Tasks tagged #racecraft get +1.
- No meetings before 9am or after 2pm. Max 3 meetings/day.
- 8am daily brief, 4pm Friday review (already scheduled).
- Communication: concise, bullets, avoid jargon, analogies welcome, learn-by-doing.

## Useful commands
- Restart bot: `./install-launchd.sh` (idempotent — bootouts old, bootstraps new)
- Tail logs: `tail -f ~/.productivity-agent/logs/agent.log`
- Verify running: `launchctl list | grep productivity-agent` (PID then 0 = healthy)
- Refresh Google OAuth (every 7 days while in Testing mode): `./.venv/bin/python -m src.google_auth setup`

## Known issues
1. Google OAuth token expires weekly (Testing mode limitation)
2. Brain Dump appended bullets show as plain text "- " prefix instead of styled bullets in Google Docs UI
3. faster-whisper not yet integrated for voice

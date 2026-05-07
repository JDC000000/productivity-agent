# Phase 3a Setup — Free-text intents → writes

**What's new:** type a normal sentence to the bot, it figures out what you meant and writes to Google Tasks or your Brain Dump doc. No voice yet (that's Phase 3b).

**Examples:**
- `add a task to call Whistler organizers tomorrow` → adds to Google Tasks
- `finished the pitch deck` → fuzzy-matches an open task, asks "yes/no", marks complete
- `note that the new chain on the bike is louder than expected` → appends to Brain Dump → Active

**Time:** ~5 min, mostly Anthropic console clicks.

Analogy: Phase 2 was a thermometer (reads). Phase 3a is the thermostat — it reads AND adjusts.

---

## Step 1 — Get an Anthropic API key (3 min)

1. Go to https://console.anthropic.com
2. Sign up or log in (use any email — not connected to your Claude.ai sub)
3. Top-left, click your org name → **Get API keys**
4. Click **Create Key**
5. Name: `productivity-agent`
6. **Copy the key** (starts with `sk-ant-...`) — you can only see it once. Paste somewhere safe for now.
7. **Add credits:** Settings → Billing → Add Credits. $5 lasts months for personal intent parsing (each command is fractions of a penny on Haiku 4.5).

---

## Step 2 — Add the key to `.env` (1 min)

```bash
open -e "$HOME/Documents/Claude/Projects/Claude _ Tools + Master/productivity-agent/.env"
```

Find the `ANTHROPIC_API_KEY=` line and paste your key after the equals sign:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Save and close.

---

## Step 3 — Restart the bot (30 sec)

In the Terminal where the bot is running:
- **Ctrl+C** to stop
- `./run.sh` to restart (it'll auto-install the new `anthropic` library)

You should see:
```
Syncing dependencies...
Starting productivity agent V1 — Phase 1 (plumbing)
Telegram bot initialized, starting polling loop...
```

---

## Step 4 — Test it (1 min)

In Telegram, type these one at a time:

**Add a task:**
```
add a task to test phase 3 tomorrow
```
→ should reply: `[OK] Added task: test phase 3 (due 2026-04-28)`

**Add a brain-dump note:**
```
note that productivity agent phase 3 shipped on a sunday
```
→ should reply: `[OK] Added to Brain Dump (Active): productivity agent phase 3 shipped on a sunday`

**Complete a task (fuzzy-matched):**
```
finished test phase 3
```
→ should reply with a confirmation:
```
Mark this complete?

  • test phase 3

Reply 'yes' to confirm or 'no' to cancel.
```
Type `yes`. → `[OK] Completed: test phase 3`

**Ambiguous input:**
```
the bike thing
```
→ should reply asking for clarification:
```
You mentioned a bike thing but I'm not sure if you want to add a task, log a note, or mark something done.

Could you say "add a task to ..." or "note that ..." or be more specific?
```

If all four work, **Phase 3a is shipped.**

---

## Where things go now

| What you type | Where it lands |
|---|---|
| Task-shaped sentences ("add", "remind me to", "I should") | Google Tasks (default list) |
| Note-shaped sentences ("note that", longer thoughts) | Brain Dump → Active section |
| "X is done" / "finished X" / "mark X complete" | Asks confirmation, then marks Google Task complete |
| Ambiguous / single-word | Bot asks for clarification |

Every action is logged to `~/.productivity-agent/logs/actions.jsonl` (one JSON line per action) — that's what Friday review will use.

---

## Troubleshooting

**`Free-text commands need an Anthropic API key`** → You skipped Step 2 or didn't restart.

**`Couldn't parse that: AuthenticationError`** → Key is wrong or has no credits. Check Step 1 step 7 and re-paste in `.env`.

**`No open task matched 'X'`** → Either you have no open tasks (run `/brief` to confirm) or the wording was too different. Use words from the task title.

**Brain dump appears but isn't bulleted visually** → Phase 3a inserts plain `- text`. The parser still picks it up. Phase 3b will add proper bullet styling. If it bothers you, manually highlight new lines and click the bulleted-list button in Docs.

**Bot replies twice or feels confused** → Check `~/.productivity-agent/logs/agent.log` for errors. Paste any traceback to me.

---

## What's next (Phase 3b preview)

- Voice messages from Telegram → `faster-whisper` (local, free) → same intent pipeline
- Reschedule task intent
- Maybe: calendar event creation with confirmation flow

When ready, say "go Phase 3b".

---

## Cost reality check

Anthropic Haiku 4.5 is roughly $0.001 per command. If you use the bot 50 times a day, that's $1.50/month. The $5 starter credits will last a year of normal use.

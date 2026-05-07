# Phase 4 — Smart prioritization + Friday review

**What's new:**
- Tasks are now SCORED at /brief time. Hot tasks (score ≥ 8) surface in their own section.
- Intent parser estimates impact (1–5) and detects RaceCraft tasks automatically when you add them.
- `/review` (manual) and Friday 4pm (auto) send a week-in-review with completion stats + one Claude-generated recommendation.

Analogy: Phase 3a was "agent picks up the pen." Phase 4 is "agent learned to read your handwriting and tells you on Fridays whether the week was actually productive."

**Time:** 2 min — just reload the bot, then test.

---

## Step 1 — Reload the bot (1 min)

Code changes don't auto-pick-up — need to bounce launchd.

```bash
cd "$HOME/Documents/Claude/Projects/Claude _ Tools + Master/productivity-agent"
./install-launchd.sh
```

That's idempotent — it unloads the old process and loads the new one with the new code.

Verify:
```bash
launchctl list | grep productivity-agent
```
Should show a process ID and exit code 0.

---

## Step 2 — Test prioritization (1 min)

In Telegram:

1. **Add a low-impact task:**
   ```
   add a task to file expense report
   ```
   Bot should reply: `[OK] Added task: file expense report [impact:2]` (or similar)

2. **Add a high-impact task with deadline:**
   ```
   add a task to close the seed round, due Friday, this is critical
   ```
   Bot should infer impact:5, racecraft:true, due:<this Friday>.

3. **Run /brief**
   You should see:
   - **HOT** section listing the seed-round task
   - **OPEN TASKS** sorted by score, each line prefixed with `[N]` (the score)

---

## Step 3 — Test the Friday review

Send `/review` in Telegram. You'll get:
- Counts (completed, added, notes, friction signals) for the current week
- A "WHAT SHIPPED" list of completed tasks
- One Claude-generated "ONE THING TO TRY NEXT WEEK"

Today is mid-week so it'll be a sparse review. After a full week of dogfooding, it'll be more meaningful. The real one auto-sends Fridays at 4pm local.

---

## How scoring works

```
score = impact (1-5)
      + racecraft bonus (+1 if tagged)
      + deadline urgency (+10 if <48h, +5 if <7d)
      - staleness (-1 if >14d untouched)
```

Examples:
- "File quarterly TPS report" → impact 1, no due, no racecraft → score **1**
- "Pitch deck for investor meeting Friday" → impact 4, due in 3d, racecraft? maybe → score **9–10** (HOT)
- "Email Whistler organizers about ambassador program" → impact 4, racecraft +1, no immediate due → score **5**

You can see your stored impact/racecraft tags in Google Tasks app — they go into the task's notes field as `impact:N` and `#racecraft`.

---

## How to override

The intent parser does its best, but you can force values:

- **Want impact:5?** Say "this is critical" or "really important" or "high impact" in the message.
- **Want low impact?** Say "small thing" or "just admin."
- **Want RaceCraft tag?** Mention RaceCraft, riders, fundraising, or partnerships explicitly.
- **After the fact:** open Google Tasks, edit the task notes directly. Add `impact:5` or `#racecraft`.

---

## What `/review` shows

| Section | What it means |
|---|---|
| BY THE NUMBERS | Counts of completed tasks, new tasks, notes captured this week |
| FRICTION | Times the bot was confused or you canceled an action |
| WHAT SHIPPED | Specific tasks completed, latest first |
| ONE THING TO TRY | Claude reads the patterns + your operating principles, suggests one operational improvement |

---

## Troubleshooting

**`/brief` shows tasks without `[N]` scores** → bot didn't pick up the new code. Re-run `./install-launchd.sh`.

**Review shows "Anthropic key not set"** → unlikely now, but means `.env` lost the key.

**Hot section never appears** → you have no tasks scoring ≥ 8 yet. Try adding one with "this is critical" or a near-term deadline.

**Recommendation feels generic** → the audit log only has a few days of data. Quality of the rec scales with weeks of usage.

---

## What's left

- **Phase 3b: voice messages** (faster-whisper local) — when you want to send voice notes from your phone
- **V2: cloud migration** — when you want to stop relying on the Mac being awake

Friday's 9am scheduled session will help you decide.

# Phase 3 Ops — Auto-start + scheduled daily brief

**What this phase ships:**
- Bot survives Mac restarts and crashes (via launchd)
- Daily brief auto-sends to Telegram at **8:00 AM Mon-Fri**
- One-command install/uninstall

Analogy: until now, the bot was a guest who needs you to open the door. After this, it's a tenant — comes back automatically, lets itself in.

**Time:** ~5 min, mostly clicking through System Settings.

---

## Step 1 — Mac power settings (2 min)

The bot can't run while your Mac is asleep. Two settings:

1. Apple menu → **System Settings**
2. **Battery** (or "Lock Screen" → "Energy" on older macOS):
   - **On power adapter**: set "Turn display off after" to your preference, but make sure **"Prevent automatic sleeping when display is off"** is **on** (or set computer sleep to "Never")
3. Optional but recommended: **Lock Screen** → set screen lock to a comfortable interval. Locking the screen is fine — it doesn't sleep the machine.

If you close your laptop lid, it will sleep regardless. The bot is designed for an always-plugged-in or always-open MacBook. (V2 cloud move solves this — already in your spec.)

---

## Step 2 — Stop any running bot (10 sec)

In whatever Terminal window has `./run.sh` running, hit **Ctrl+C**. We're about to hand the job over to launchd.

---

## Step 3 — Install the launchd agent (30 sec)

In Terminal, in the productivity-agent folder:

```bash
chmod +x install-launchd.sh
./install-launchd.sh
```

You should see output ending with `[OK] launchd agent installed.` followed by a list of management commands.

---

## Step 4 — Verify it's running (30 sec)

```bash
launchctl list | grep productivity-agent
```

You should see something like:
```
12345  0   com.jon.productivity-agent
```
- First column = process ID (any number = running)
- Second column = last exit code (0 = healthy)

Also tail the logs to watch it boot:
```bash
tail -f ~/.productivity-agent/logs/launchd.stderr.log
```
You should see the same startup messages you saw with `./run.sh`. Press Ctrl+C to stop tailing.

---

## Step 5 — Test from Telegram (1 min)

Send `/ping` from Telegram. Bot should reply `pong`.

Send `/brief`. Should return today's briefing.

If both work, **you're done**.

---

## What happens now

- **At 8:00 AM Mon-Fri:** the bot sends you the daily briefing automatically.
- **If your Mac restarts:** launchd restarts the bot at login.
- **If the bot crashes:** launchd waits 30 sec, then restarts it.
- **You don't need to keep a Terminal window open.** Close them all if you want.

---

## Useful commands

| Want to... | Run |
|---|---|
| See if bot is running | `launchctl list \| grep productivity-agent` |
| Tail live logs | `tail -f ~/.productivity-agent/logs/agent.log` |
| Stop the bot | `launchctl unload ~/Library/LaunchAgents/com.jon.productivity-agent.plist` |
| Start the bot | `launchctl load ~/Library/LaunchAgents/com.jon.productivity-agent.plist` |
| Re-install after code changes | `./install-launchd.sh` (idempotent — handles unload/reload) |
| Uninstall completely | `launchctl unload ~/Library/LaunchAgents/com.jon.productivity-agent.plist && rm ~/Library/LaunchAgents/com.jon.productivity-agent.plist` |

---

## Troubleshooting

**`launchctl list \| grep productivity-agent` shows nothing** → install didn't take. Re-run `./install-launchd.sh` and look for errors.

**Process ID is `-` and exit code is non-zero** → bot crashed at startup. Check `~/.productivity-agent/logs/launchd.stderr.log` for the traceback.

**Bot doesn't respond at 8am** → check `~/.productivity-agent/logs/agent.log` around 8am. If you see `daily_brief: TELEGRAM_ALLOWED_CHAT_ID not set`, your `.env` is missing that value.

**Want a different time than 8am** → edit `src/scheduler.py`, change `time(hour=8, minute=0, ...)`, then run `./install-launchd.sh` to reload.

**Want to skip weekends but include Saturday for race-day briefings** → edit `src/scheduler.py`, change `days=(0, 1, 2, 3, 4)` to e.g. `(0, 1, 2, 3, 4, 5)` (adds Saturday).

---

## What's next

The bot is now production-ish on your Mac. Next builds:

- **Phase 3b: voice messages** (faster-whisper local transcription)
- **Phase 4: prioritization + Friday review** (the "chief of staff" payoff)
- **V2: cloud migration** (Railway/Fly.io — when you outgrow the Mac)

Friday morning's scheduled review session at 9am will tee up which one to do next based on a few days of dogfooding.

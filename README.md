# Productivity Agent — Phase 1 (Plumbing)

**What you're running:** A Telegram bot that echoes whatever you send it. No Google integration yet — that's Phase 2.

**Why this phase matters:** We're proving the pipe works end-to-end — your phone → Telegram servers → bot on your Mac → reply back to your phone. If this works, Phase 2 just slots real logic into the same pipe.

Analogy: this is plumbing-before-paint. Ugly but load-bearing.

**Time to ship:** ~20 min, mostly waiting for Python installs and BotFather.

---

## Step 1 — Create your Telegram bot (3 min)

1. Open Telegram on any device. Search `@BotFather` and start a chat.
2. Send `/newbot`.
3. **Name** (what shows in chat): anything — e.g. `Jon's Cowork Bot`.
4. **Username** (must end in `bot`): e.g. `jon_coworkbot`. If taken, try another.
5. **Copy the token** BotFather sends back. Looks like `123456789:ABCdef-GhIjK...`. Keep it private — anyone with this token controls your bot.
6. Send `/setprivacy` → pick your bot → **Disable**. (Lets the bot see all messages in DMs, not just slash-commands.)

---

## Step 2 — Set up the code (5 min)

Open Terminal (Cmd+Space → "Terminal"). Then:

```bash
cd "$HOME/Documents/Claude/Projects/Claude _ Tools + Master/productivity-agent"
cp .env.example .env
open -e .env
```

That last command opens `.env` in TextEdit. Paste your Telegram token after `TELEGRAM_BOT_TOKEN=`. Leave everything else blank. Save and close.

**Prereq:** You need Python 3.10+ on your Mac. Check by running `python3 --version`. If it says something like `Python 3.11.x`, you're fine. If not installed, run `brew install python` first (needs [Homebrew](https://brew.sh)).

---

## Step 3 — Run the bot (2 min)

From Terminal, still in the `productivity-agent` folder:

```bash
chmod +x run.sh
./run.sh
```

First run takes ~1 min while Python installs dependencies. Once you see `Telegram bot initialized, starting polling loop...`, the bot is live. Leave this Terminal window open while testing.

---

## Step 4 — Test it from your phone (2 min)

1. In Telegram, search for your bot's username (e.g. `@jon_coworkbot`).
2. Send `/start` → bot should reply with a welcome message.
3. Send `/chatid` → bot replies with your numeric chat_id (e.g. `123456789`).
4. **Copy that chat_id.** Paste it into `.env` as `TELEGRAM_ALLOWED_CHAT_ID=123456789`. This locks the bot to just you.
5. Stop the bot: Ctrl+C in Terminal.
6. Restart: `./run.sh`.
7. Send any message → bot replies `Echo: your message`.
8. Send `/ping` → bot replies `pong`.

**That's Phase 1 shipped.** Tell me "Phase 1 works, go Phase 2" and we'll add Google auth + daily briefing.

---

## Commands available in Phase 1

| Command | What it does |
|---|---|
| `/start` | Welcome message |
| `/chatid` | Returns your Telegram chat_id (for allowlist) |
| `/ping` | Replies `pong` (liveness check) |
| Any text | Echoes back with `Echo:` prefix |

---

## Troubleshooting

**`Missing required env var: TELEGRAM_BOT_TOKEN`** → Token not in `.env`, or you saved the wrong file. Re-run `open -e .env` and check.

**Bot doesn't reply to regular messages, only commands** → You skipped `/setprivacy → Disable` in BotFather (step 1.6).

**`Unauthorized` in Terminal** → Token is wrong or revoked. In BotFather, send `/revoke` and generate a fresh one.

**`ModuleNotFoundError`** → Virtualenv didn't install. Delete the `.venv` folder and run `./run.sh` again.

**Bot replies to someone else** → If `TELEGRAM_ALLOWED_CHAT_ID` is blank in `.env`, the bot talks to anyone who finds it. Set it to your chat_id after step 4.

---

## Files in this folder

- `src/main.py` — entry point
- `src/bot.py` — Telegram handlers (start, chatid, ping, echo)
- `src/config.py` — loads `.env`
- `src/logger.py` — logging setup
- `requirements.txt` — Python deps
- `run.sh` — one-command launcher
- `.env.example` — copy to `.env` and fill in
- `launchd/` — auto-start on Mac boot (ignore until Phase 4 works)

---

## Phase 2 — Google integration + /brief

→ **[PHASE2-SETUP.md](PHASE2-SETUP.md)** ← Google Cloud + Brain Dump + OAuth + test

After Phase 2 you have a `/brief` command that returns today's events, open Google Tasks, and bullets from your Brain Dump doc.

## Phase 3a — Free-text intents → writes

→ **[PHASE3A-SETUP.md](PHASE3A-SETUP.md)** ← Anthropic API key + restart + test 4 example messages

After Phase 3a you can type natural sentences to the bot ("add a task to call X", "finished Y", "note that Z") and it writes to Google Tasks or the Brain Dump doc.

## Phase 3 Ops — Auto-start + 8am daily brief

→ **[PHASE3-OPS-SETUP.md](PHASE3-OPS-SETUP.md)** ← `./install-launchd.sh` makes the bot survive reboots and start automatically. Daily brief auto-sends at 8am weekdays.

## Phase 4 — Smart prioritization + Friday review

→ **[PHASE4-SETUP.md](PHASE4-SETUP.md)** ← Tasks scored by impact + RaceCraft + deadline. `/brief` surfaces HOT items. `/review` (and Fri 4pm auto) sends week-in-review with stats + one actionable recommendation.

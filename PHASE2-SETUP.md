# Phase 2 Setup — Google Integration + /brief

**What you're adding:** the bot can pull today's calendar events, open Google Tasks, and bullets from a Brain Dump doc into one unified `/brief` message.

**Time:** ~20 min, mostly Google Cloud clicks.

Analogy: Phase 1 was the doorbell. Phase 2 wires the doorbell to actually look out and tell you who's at the door.

---

## Part A — Google Cloud project (10 min, in browser)

### 1. Create the project
- Go to https://console.cloud.google.com/projectcreate
- Project name: `productivity-agent`
- Click **Create**, wait ~10 sec for it to provision
- Confirm the project name shows in the top bar

### 2. Enable three APIs
Open each link, click **Enable**:
- Calendar: https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
- Tasks: https://console.cloud.google.com/apis/library/tasks.googleapis.com
- Docs: https://console.cloud.google.com/apis/library/docs.googleapis.com

### 3. Configure the OAuth consent screen
- Go to https://console.cloud.google.com/apis/credentials/consent
- User type: **External** → **Create**
- App name: `productivity-agent`
- User support email: your gmail
- Developer contact: your gmail
- **Save and continue** (skip the Scopes screen for now)
- Test users: add `joncartwright00@gmail.com`
- **Save and continue** through to the end

### 4. Create OAuth Desktop credentials
- Go to https://console.cloud.google.com/apis/credentials
- Click **+ Create Credentials** → **OAuth client ID**
- Application type: **Desktop app**
- Name: `productivity-agent-desktop`
- Click **Create** → on the next dialog click **Download JSON**

### 5. Move the downloaded file to the right place
In Terminal:
```bash
mkdir -p ~/.productivity-agent/secrets
mv ~/Downloads/client_secret_*.json ~/.productivity-agent/secrets/credentials.json
```

---

## Part B — Brain Dump Google Doc (3 min)

### 1. Create the doc
- Open https://docs.new
- Title it: **Task Brain Dump — Jon**

### 2. Set up the section structure
Type this content. **Important:** for `Active`, `Done`, `Backlog` use **Heading 2** style (Format menu → Paragraph styles → Heading 2), not just typed `##`.

```
Active
- first task
- second task

Done

Backlog
```

### 3. Grab the doc ID
- Look at the URL: `https://docs.google.com/document/d/THIS_PART_HERE/edit`
- Copy `THIS_PART_HERE` — it's a long string of letters and numbers
- You'll paste it into `.env` next

---

## Part C — Update `.env` (1 min)

Open `.env`:
```bash
open -e "$HOME/Documents/Claude/Projects/Claude _ Tools + Master/productivity-agent/.env"
```

Make sure these lines exist (most are already there from Phase 1; just add the doc ID and timezone):

```
GOOGLE_CREDENTIALS_PATH=~/.productivity-agent/secrets/credentials.json
GOOGLE_TOKEN_PATH=~/.productivity-agent/secrets/token.json
BRAIN_DUMP_DOC_ID=PASTE_YOUR_DOC_ID_HERE
TIMEZONE=America/Vancouver
```

Save and close.

---

## Part D — Run the OAuth flow once (3 min)

In Terminal, in the productivity-agent folder:

```bash
cd "$HOME/Documents/Claude/Projects/Claude _ Tools + Master/productivity-agent"
./.venv/bin/pip install -q -r requirements.txt
./.venv/bin/python -m src.google_auth setup
```

A browser window opens automatically:
1. Sign in with `joncartwright00@gmail.com`
2. You'll see a warning: **"Google hasn't verified this app"** — this is normal for personal apps. Click **Continue** (or **Advanced** → **Go to productivity-agent (unsafe)**).
3. Approve the requested scopes (Calendar, Tasks, Docs).
4. You'll see **"The authentication flow has completed. You may close this window."**

Back in Terminal you should see: `[OK] Google OAuth complete. Token saved to ...`

---

## Part E — Test `/brief` (1 min)

Stop the bot if running (Ctrl+C in the bot's Terminal window), then start it:
```bash
./run.sh
```

In Telegram, send `/brief`. You should see something like:
```
Briefing — Fri Apr 24, 2026

EVENTS TODAY
  9:00 am  Standup
  2:00 pm  Investor call

OPEN TASKS (3)
  • Email Whistler organizers  due 2026-04-26  [My Tasks]
  • Update pitch deck  [My Tasks]
  • Review Q2 budget  [My Tasks]

BRAIN DUMP — Active (2)
  • first task
  • second task
```

If you get that, **Phase 2 is shipped.** Tell me "Phase 2 works, go Phase 3" when you're ready.

---

## Troubleshooting

**`No Google token at ...`** → You skipped Part D. Run `./.venv/bin/python -m src.google_auth setup`.

**`Missing OAuth client credentials at ...`** → The downloaded JSON didn't get to `~/.productivity-agent/secrets/credentials.json`. Re-do Part A step 5.

**`Access blocked: This app hasn't been verified`** → On the warning page, click **Advanced** → **Go to productivity-agent (unsafe)**. This is normal.

**`/brief` returns empty everything** → Make sure you have at least one calendar event today, one Google Task, and one bullet under "Active" in the doc.

**Brain Dump shows nothing but the doc has bullets** → The "Active" line needs to be Heading 2, not regular text. Click on the line, then Format → Paragraph styles → Heading 2.

**`invalid_grant` errors after a long time away** → Refresh tokens can expire if the app stays in test mode for a while. Re-run Part D.

---

## What's now in the codebase

- `src/google_auth.py` — OAuth flow + credential loader
- `src/data_sources/calendar.py` — today's events
- `src/data_sources/tasks.py` — open Google Tasks across all your lists
- `src/data_sources/brain_dump.py` — parses the Active section of your doc
- `src/briefing.py` — assembles the message
- `src/bot.py` — adds `/brief` command

---

## What's next (Phase 3 preview)

- Voice messages → Whisper → Claude → action
- Voice commands: add task, mark complete, ask "what's next"
- Sync writes back to Calendar, Tasks, and the Brain Dump doc

Phase 3 needs an Anthropic API key + an OpenAI key (Whisper only). I'll walk you through both when ready.

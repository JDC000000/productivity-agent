# Phase 3b — Voice Messages

Send a voice note in Telegram → bot transcribes locally with faster-whisper →
transcript flows through the same intent parser as a typed message.

No API key needed for transcription. Runs on this Mac, free.

## What changed

- `requirements.txt` — added `faster-whisper>=1.0,<2.0`
- `src/voice.py` (new) — lazy-loaded WhisperModel("small") + `transcribe()`
- `src/bot.py` — new `voice_message` handler, `free_text` refactored to share
  `_process_user_text()` with the voice path
- `src/intents/audit.py` — `log_action` now records `via: "text" | "voice"`

## Setup steps

1. **Sync deps + restart the bot:**

       ./install-launchd.sh

   The script bootouts the old launchd job and bootstraps the new one. `run.sh`
   re-syncs the venv against `requirements.txt`, so faster-whisper installs
   automatically. First install pulls ~50–100MB of wheels (ctranslate2,
   tokenizers, onnxruntime).

2. **Confirm the bot is healthy:**

       launchctl list | grep productivity-agent
       tail -f ~/.productivity-agent/logs/agent.log

   Look for `Application started`.

## How to test

Send these in Telegram and verify each one:

| Voice note                                                      | Expected behavior                                               |
| --------------------------------------------------------------- | --------------------------------------------------------------- |
| "Add a task to call Whistler organizers tomorrow"               | `Heard: ...` → `[OK] Added task: ...` → check Google Tasks      |
| "Note that the new chain on the bike is louder than expected"   | `Heard: ...` → `[OK] Added to Brain Dump (Active): ...`         |
| "Finished the pitch deck" (only if such a task exists)          | `Heard: ...` → `Mark this complete?` → reply (text or voice) yes|
| (silence / mumbling)                                            | `Couldn't make out any speech in that clip.`                    |

**First voice note will be slow** — faster-whisper downloads the small model
(~150MB) into `~/.cache/huggingface/hub/` on first call. Watch the agent log:

    Loading faster-whisper model: small (first call may download ~150MB)
    faster-whisper model loaded.

Subsequent voice notes transcribe in 2–5 seconds for ~30s clips.

## Verifying via= in the audit log

    tail -n 5 ~/.productivity-agent/logs/actions.jsonl | jq '{ts, via, result, intent: .intent.name}'

Voice messages will show `"via": "voice"`. The Friday review will eventually
read this to break down activity by channel.

## Known limits / caveats

- **Model size**: hardcoded to `small`. If quality is poor on your accent or
  noisy environments, bump to `medium` in `src/voice.py` (~500MB, slower).
- **Language**: faster-whisper auto-detects. English voice notes work fine; if
  it ever guesses wrong, we can pin `language="en"` in the `transcribe()` call.
- **First boot after deploy**: if launchd starts the bot and immediately gets a
  voice message, the first transcription will block while the model downloads.
  No timeouts in PTB for handlers, so it'll just take a minute.
- **Telegram Voice vs Audio**: this handler catches `filters.VOICE` (the
  microphone-recorded "voice notes"). Forwarded music or attached `.mp3` files
  would need `filters.AUDIO` — not wired up, intentionally out of scope.

## Rollback

If anything is broken, comment out the `MessageHandler(filters.VOICE, ...)`
line in `src/bot.py` and rerun `./install-launchd.sh`. Phase 3a text path is
unaffected by the voice changes (the refactor is behavior-preserving).

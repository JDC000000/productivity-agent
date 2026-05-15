# Phase 3c — In-Telegram OAuth Re-Auth

Google's Testing-mode OAuth token expires every ~7 days. Until now the only way
to refresh was to SSH/terminal in and run `python -m src.google_auth setup`.
After Phase 3c, you can re-auth entirely from Telegram — works from the phone,
no context switch.

Bonus: any Google API call that fails with `invalid_grant` (or 401, or "no
token") now prompts the user with `Token expired. Send /reauth to refresh.`
instead of dumping a stack trace.

## What changed

- `src/google_auth.py` — Two new functions: `build_consent_url()` returns
  `(auth_url, flow)` for a Telegram-driven OAuth handshake;
  `complete_consent(flow, code_or_url)` exchanges the user-pasted code (or
  full localhost URL with `?code=` in it) for tokens and writes them to the
  same `token.json` the CLI flow uses. Existing `run_oauth_setup()` (CLI) and
  `get_credentials()` are unchanged.
- `src/bot.py` — New `/reauth` command, `_reauth_pending[chat_id]` stash for
  the in-flight `flow` object, `_handle_reauth_code` to complete the exchange,
  `_looks_like_oauth_code` heuristic so stray free-text doesn't get fed to
  `fetch_token`. Added `_is_auth_error` + `_maybe_notify_reauth` helpers and
  wired them into 6 existing exception handlers.

## Setup

    ./install-launchd.sh
    launchctl list | grep productivity-agent
    tail -f ~/.productivity-agent/logs/agent.log

## Mobile flow walkthrough

1. In Telegram, send `/reauth`.
2. Bot replies with a Google consent URL.
3. Tap the URL. Sign in / consent on Google's page.
4. Google redirects to `http://localhost?state=...&code=4/0Ae...&scope=...`.
   Your phone shows a "site can't be reached" page — that's expected.
5. **Long-press the address bar → Copy URL**.
6. Paste into Telegram and send.
7. Bot replies: `Reauthorized. Token good for 7 more days.`

Bare code also works — if you can extract just `4/0AeoWuM...` from the URL
manually, pasting that is fine too. Most phones make the URL copy easier.

## Desktop flow

Same as mobile, just less awkward — the localhost URL fails to load, copy the
full address bar URL, paste into Telegram.

## CLI fallback (unchanged)

If Telegram is unavailable for any reason, the original works:

    ./.venv/bin/python -m src.google_auth setup

Same `token.json`, same scopes.

## Test cases

| # | Action | Expected |
|---|--------|----------|
| 1 | `/reauth` then paste the full localhost URL after consent | `Reauthorized. Token good for 7 more days.` |
| 2 | `/reauth` then paste just the bare code (e.g. `4/0AeoWuM...`) | Same as #1 |
| 3 | `/reauth` then run `/reauth` again | Second one resets, new URL produced |
| 4 | `/reauth` then send `add buy milk` (non-code free text) | Falls through — task gets added, re-auth state stays. Send the code next when you're ready. |
| 5 | `/reauth` then send a typo-ed code | `Couldn't exchange that code: ...` — state retained so you can paste again |
| 6 | After token expiry, run `/brief` | Reply: `Token expired. Send /reauth to refresh.` (no stack trace) |

## How the invalid_grant detector works

`_is_auth_error(exc)` returns True for three real-world failure shapes:
- `google.auth.exceptions.RefreshError` — the actual `invalid_grant` case when
  the refresh token has been revoked or hit Testing-mode's 7-day cap
- `googleapiclient.errors.HttpError` with status 401 — API call rejected with
  a stale-but-not-yet-detected-as-expired token
- `RuntimeError("Google credentials invalid...")` from `get_credentials()`
  when `token.json` is missing or malformed

Wired into 6 exception handlers in `bot.py`:
- `brief()` — the most common surface
- `_dispatch_tool_call`'s execute-error catch (add_task / append / query)
- `_execute_complete_immediate` — both fetch-tasks and execute catches
- `_execute_edit_immediate` — both fetch-tasks and execute catches

Other exception handlers (voice transcription, Friday review, parse_intent
Anthropic errors) are not Google-related, so they're untouched.

## Telegram-state and yes/no coexistence

Two in-memory dicts on the bot keyed by `chat_id`:
- `_pending` — `/undo` yes/no confirmation (Phase 3f)
- `_reauth_pending` — in-flight OAuth `flow` (Phase 3c)

They don't share semantics and the priority order in `free_text` is:
1. If the user is in `_reauth_pending` AND the message looks like an OAuth
   code/URL, treat it as the code.
2. Otherwise fall through to `_process_user_text` (which handles yes/no and
   the Haiku conversation pipeline).

Voice messages bypass the re-auth intercept entirely — pasting a base-64 code
via voice transcription would never work.

## Known limits

- Telegram strips trailing characters from very long URLs in some clients —
  if the paste fails to extract `code=`, fall back to pasting the bare code.
- The 7-day window is a Google Testing-mode limitation, not anything we
  control. To get rid of it permanently, the OAuth app would need to be in
  Production mode, which requires Google's verification process.

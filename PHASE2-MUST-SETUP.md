# Phase 2 (MUST) — Voice Echo + Smart Confidence-Tiered Retry

About 1 in 8 voice notes hallucinates or mistranscribes. Phase 5.1's bare-verb
guard catches some, but action-shaped mistranscriptions slip through. Phase 2
adds a **confidence-tiered confirmation** layer driven by faster-whisper's
per-segment `avg_logprob`.

## Behavior

| Score | Tier | Bot behavior |
|---|---|---|
| ≥ 0.70 | HIGH | Silent. Processes voice immediately, same UX as Phase 5. |
| 0.50–0.70 | MEDIUM | `Heard: <text>. Acting in 5s — send 'fix' or 'no' to abort.` |
| < 0.50 | LOW | `Couldn't catch that clearly: <text>. Send the action again or as text.` Does NOT act. |

Score is `exp(duration-weighted-mean(avg_logprob))` in `[0, 1]`. Thresholds in
`src/voice.py`:

```python
CONFIDENCE_THRESHOLD = 0.6
CONFIDENCE_MARGIN    = 0.1
# HIGH:   score >= THRESHOLD + MARGIN   (0.7)
# LOW:    score <  THRESHOLD - MARGIN   (0.5)
# MEDIUM: otherwise
```

## What changed

- `src/voice.py` — new `_segment_confidence()` helper; `transcribe()` now returns `(text, confidence)` instead of `text`. Added `CONFIDENCE_THRESHOLD` / `CONFIDENCE_MARGIN` constants.
- `src/intents/audit.py` — new optional `transcription_confidence: float | None` kwarg; persisted only when set.
- `src/bot.py` —
  - New `_voice_pending: dict[int, dict]` state (cancel-window task + metadata).
  - `_classify_voice_cancel()` (mirrors `_classify_confirmation`, scoped to `{fix, no, nope, cancel, stop, abort}`).
  - `_is_filler_or_empty()` for `""` / `"."`/`"um"`/`"uh"` transcripts.
  - `voice_message` refactored into tier-branching: HIGH silent, MEDIUM starts a 5s asyncio cancel window, LOW refuses.
  - `_voice_pending_proceed()` async helper that sleeps 5s then calls `_process_user_text` if state still there.
  - `free_text` checks `_voice_pending` FIRST: cancel-word → abort, any other text → supersede then process normally.
  - `transcription_confidence` threaded through `_process_user_text` → `_dispatch_tool_call` → `_execute_complete_immediate` / `_execute_edit_immediate` and into every per-tool audit call.

## State coexistence

Three independent per-chat dicts:
- `_pending` — `/undo` yes/no confirmation (Phase 3f)
- `_reauth_pending` — OAuth flow object (Phase 3c)
- `_voice_pending` — MEDIUM-tier voice cancel window (Phase 2 MUST)

`free_text` priority order: voice_pending → reauth_pending → conversational pipeline.

## Setup

    ./install-launchd.sh
    launchctl list | grep productivity-agent
    tail -f ~/.productivity-agent/logs/agent.log

(But per the build spec, you'll smoke-test in Telegram on the branch *before* merging — `install-launchd.sh` happens when you choose to deploy.)

## Test plan

| # | Send | Expected reply | Audit |
|---|------|----------------|-------|
| 1 | Clean voice: "add buy milk for thursday" | Same as today — Haiku replies "Added — buy milk for Thursday." No "Heard:" prefix. | `add_task` `result: "ok"` with `transcription_confidence` ≥ 0.7 |
| 2 | Mumbled voice intended to add a task | `Heard: <text>\nActing in 5s — send 'fix' or 'no' to abort.` Then after 5s, the action lands and Haiku replies. | First a `MEDIUM`-tier echo (no audit). Then `add_task` `result: "ok"` with `transcription_confidence` in [0.5, 0.7). |
| 3 | Mumble + immediately send "no" in text | `Cancelled. Re-send when ready.` | `voice_canceled` entry with the transcript and confidence. |
| 4 | Mumble + send any other text within 5s | The text processes normally as if no voice happened. | `voice_superseded` entry for the original; normal audit for the new text. |
| 5 | Mumble + send another voice within 5s | Same — second voice supersedes first. | `voice_superseded` entry for the original. |
| 6 | Heavy-noise voice ≈ unintelligible | `Couldn't catch that clearly: ...\nSend the action again or as text.` No action. | `low_confidence` entry, no intent. |
| 7 | Voice with only "um" / "." / silence | `Didn't catch any speech. Try again.` | No audit (a non-action isn't worth a row). |
| 8 | `/undo` after a HIGH-tier voice add | `About to undo: delete '...'. Yes/no?` → "yes" → `Undid: deleted '...'.` | Existing undo flow unchanged; voice confidence stays only on the original add entry, NOT on the undo entry. |

## How to interpret the audit log

Voice entries now carry one new field:

```json
{
  "ts": "...",
  "user_input": "Add buy milk for Thursday.",
  "intent": {"name": "add_task", "input": {...}},
  "result": "ok",
  "via": "voice",
  "details": "Added task: Buy milk [impact:3]",
  "created_task_id": "...",
  "created_task_list_id": "...",
  "transcription_confidence": 0.86
}
```

Friday review can bucket-count by `transcription_confidence` ranges. Text-input entries don't have the field at all.

## Async task lifecycle across restarts

- `_voice_pending` and the asyncio Task live in process memory only.
- Restart during the 5s window → task is killed and dict is wiped.
- The MEDIUM action silently does not fire. User sees no failure, no completion.
- This is intentionally fail-safe (no action) over fail-unsafe (wrong action). v1.

## Don't-break verification

Every prior phase still works:
- `/brief`, `/undo`, `/reauth`, `/chatid`, `/ping`, `/echo` untouched
- Text-input add/complete/edit/note still works, same audit shape
- `/undo` confirmation flow still uses `_pending`, no collision with `_voice_pending`
- `/reauth` flow still uses `_reauth_pending`, no collision
- Conversational continuity (Phase 5) preserved across voice and text turns

## Tuning thresholds

If after a week of use the MEDIUM band is too noisy (constantly asking to confirm clear speech) or too leaky (letting hallucinations through), tweak `CONFIDENCE_THRESHOLD` and `CONFIDENCE_MARGIN` at the top of `src/voice.py`. The audit log's `transcription_confidence` distribution is the right input — look at the histogram of HIGH vs. MEDIUM vs. LOW counts.

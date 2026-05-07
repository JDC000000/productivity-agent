"""Telegram bot — handlers for Phase 1 + 2 + 3a + 3b.

Phase 1 handlers:
- /start  : welcome
- /chatid : print chat_id (for allowlist)
- /ping   : liveness
- /echo   : echo back the args (kept for testing)

Phase 2 handlers:
- /brief  : daily briefing — events + tasks + brain dump

Phase 3a behavior:
- Free text (no slash) → Claude intent parser → write to Google Tasks / Brain Dump
- Yes/no replies confirm a pending complete_task

Phase 3b behavior:
- Voice messages → faster-whisper transcription → same intent pipeline as text.

Authorization model:
- If TELEGRAM_ALLOWED_CHAT_ID is unset, the bot replies to anyone (setup mode).
- Once set, the bot silently drops messages from anyone else (log only).
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .briefing import build_briefing
from .config import (
    ANTHROPIC_API_KEY,
    BRAIN_DUMP_DOC_ID,
    TELEGRAM_ALLOWED_CHAT_ID,
    TELEGRAM_BOT_TOKEN,
)
from .data_sources.brain_dump import get_active_items
from .data_sources.calendar import get_todays_events
from .data_sources.tasks import (
    delete_task_by_id,
    find_best_match,
    get_open_tasks,
    reopen_task_by_id,
)
from .friday_review import build_friday_review
from .google_auth import get_credentials
from .intents.audit import log_action
from .intents.executor import execute_intent
from .intents.parser import parse_intent
from .intents.undo import describe_target, find_undoable_entry
from .scheduler import schedule_jobs
from .voice import transcribe

log = logging.getLogger(__name__)

# In-memory store of pending confirmations, keyed by chat_id.
# After Phase 5, only /undo uses this — complete_task fires immediately.
# Single-user bot, so this dict is tiny.
_pending: dict[int, dict[str, Any]] = {}

# Phase 5: per-chat conversation history, alternating user/assistant messages
# in Anthropic API shape. In-memory only — wiped on bot restart.
_history: dict[int, list[dict[str, Any]]] = {}

# Phase 5: tool_result blocks waiting to be sent on the NEXT user turn. The
# Anthropic API requires every tool_use block in an assistant message to be
# followed IMMEDIATELY by a user message containing the matching tool_result
# blocks. Since we run tools after Haiku replies (no second API call this turn),
# we stash the results here and merge them into the next user message's content
# (text and tool_result blocks can co-exist in a single user message).
_pending_tool_results: dict[int, list[dict[str, Any]]] = {}

_HISTORY_USER_TURNS = 10  # cap: keep the last N real-user turns + their assistant pairs


def _is_real_user_turn(m: dict[str, Any]) -> bool:
    """A 'real user turn' is a user message containing at least one text block
    (possibly mixed with tool_result blocks). Pure tool_result-only messages
    don't count — they're API plumbing, not turns."""
    if m.get("role") != "user":
        return False
    content = m.get("content", "")
    if isinstance(content, str):
        return True
    return any(b.get("type") == "text" for b in content)


def _trim_history(messages: list[dict[str, Any]], max_user_turns: int = _HISTORY_USER_TURNS) -> list[dict[str, Any]]:
    """Slide the window: keep the last N real-user turns + everything after.

    Slices at the cutoff user message so the result starts with role=user.
    Strips any orphan tool_result blocks from that first message — they'd
    refer to a tool_use we just cut off, which the API would reject.
    """
    user_idxs = [i for i, m in enumerate(messages) if _is_real_user_turn(m)]
    if len(user_idxs) <= max_user_turns:
        return messages
    cutoff = user_idxs[-max_user_turns]
    first = messages[cutoff]
    content = first.get("content", "")
    if isinstance(content, list):
        cleaned = [b for b in content if b.get("type") != "tool_result"]
        first = {"role": "user", "content": cleaned if cleaned else first.get("content")}
    return [first] + messages[cutoff + 1:]


def _is_authorized(update: Update) -> bool:
    if TELEGRAM_ALLOWED_CHAT_ID is None:
        return True
    return update.effective_chat.id == TELEGRAM_ALLOWED_CHAT_ID


# ---------------- Phase 1 handlers ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        log.warning("Rejected /start from chat_id=%s", update.effective_chat.id)
        return
    await update.message.reply_text(
        "Hi Jon. Productivity agent V1 is online.\n\n"
        "Commands:\n"
        "  /brief — today's briefing (events + scored tasks + brain dump)\n"
        "  /review — this week's review (also auto-sends Fri 4pm)\n"
        "  /chatid — get your chat_id\n"
        "  /ping — liveness check\n"
        "  /echo (text) — echo it back\n\n"
        "Or just type a message and I'll figure out what to do.\n"
        "Examples:\n"
        "  add a task to call Whistler organizers tomorrow\n"
        "  finished the pitch deck\n"
        "  note that the new chain on the bike is louder than expected\n\n"
        "Daily brief auto-sends at 8am Mon-Fri."
    )


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        log.warning("Rejected /chatid from chat_id=%s", update.effective_chat.id)
        return
    cid = update.effective_chat.id
    await update.message.reply_text(
        f"Your chat_id is: {cid}\n\n"
        "Paste this into your .env as TELEGRAM_ALLOWED_CHAT_ID=" + str(cid) +
        " and restart to lock the bot to you only."
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text("pong")


async def echo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/echo (any text) — echoes it back. Kept for plumbing tests."""
    if not _is_authorized(update):
        return
    text = " ".join(context.args) if context.args else "(no text)"
    await update.message.reply_text(f"Echo: {text}")


# ---------------- Phase 2 handler ----------------

async def brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text("Pulling today's briefing...")
    try:
        creds = get_credentials()
        events = get_todays_events(creds)
        tasks = get_open_tasks(creds)
        brain_dump = (
            get_active_items(creds, BRAIN_DUMP_DOC_ID) if BRAIN_DUMP_DOC_ID else []
        )
        msg = build_briefing(events, tasks, brain_dump)
        await update.message.reply_text(msg)
    except Exception as exc:
        log.exception("brief failed")
        await update.message.reply_text(
            f"Brief failed: {type(exc).__name__}: {exc}\n\n"
            "Common causes:\n"
            "• Run setup first:  ./.venv/bin/python -m src.google_auth setup\n"
            "• Set BRAIN_DUMP_DOC_ID in .env\n"
            "• Check ~/.productivity-agent/logs/agent.log for details"
        )


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/review — manually trigger the Friday review (also fires at 4pm Friday)."""
    if not _is_authorized(update):
        return
    await update.message.reply_text("Building this week's review...")
    try:
        msg = build_friday_review()
        await update.message.reply_text(msg)
    except Exception as exc:
        log.exception("review failed")
        await update.message.reply_text(f"Review failed: {type(exc).__name__}: {exc}")


# ---------------- Phase 3a/3b text + voice handlers ----------------

_YES_WORDS = {"yes", "y", "yep", "yeah", "yup", "ok", "okay", "sure", "confirm"}
_NO_WORDS = {"no", "n", "nope", "nah", "cancel", "stop", "abort"}


def _classify_confirmation(text: str) -> str | None:
    """Return 'yes' / 'no' / None for a (likely-voice) confirmation reply.

    Tolerant of trailing punctuation and short polite forms — Whisper transcripts
    routinely produce 'Yes.' / 'No thanks.' which the old exact-match against
    {'yes', 'no', ...} silently dropped.

    Matches when the cleaned message is at most 3 words AND the first word is in
    _YES_WORDS or _NO_WORDS. The length cap prevents 'yes I want to also add
    buy milk' from being interpreted as a confirmation.
    """
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > 3:
        return None
    first = words[0]
    if first in _YES_WORDS:
        return "yes"
    if first in _NO_WORDS:
        return "no"
    return None


async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free text → intent → action. Or yes/no → confirm a pending action."""
    if not _is_authorized(update):
        log.warning(
            "Rejected message from unauthorized chat_id=%s text=%r",
            update.effective_chat.id,
            update.message.text,
        )
        return

    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id
    await _process_user_text(update, text, chat_id, via="text")


async def voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram voice note → faster-whisper transcript → same pipeline as free_text."""
    if not _is_authorized(update):
        log.warning("Rejected voice from chat_id=%s", update.effective_chat.id)
        return

    voice = update.message.voice
    if voice is None:
        return  # MessageHandler(filters.VOICE) shouldn't deliver this, but guard anyway.

    try:
        await update.message.chat.send_action(ChatAction.TYPING)
    except Exception:
        pass

    tmp_path: str | None = None
    try:
        tg_file = await voice.get_file()
        fd, tmp_path = tempfile.mkstemp(suffix=".ogg", prefix="ptb-voice-")
        os.close(fd)
        await tg_file.download_to_drive(tmp_path)
        text = transcribe(tmp_path)
    except Exception as exc:
        log.exception("voice download/transcribe failed")
        await update.message.reply_text(
            f"Voice transcription failed: {type(exc).__name__}: {exc}"
        )
        return
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                log.warning("couldn't clean up temp voice file: %s", tmp_path)

    text = text.strip()
    if not text:
        await update.message.reply_text("Couldn't make out any speech in that clip.")
        return

    # Echo the transcript so Jon can spot mistranscriptions before the action lands.
    await update.message.reply_text(f"Heard: {text}")

    chat_id = update.effective_chat.id
    await _process_user_text(update, text, chat_id, via="voice")


async def _process_user_text(
    update: Update, text: str, chat_id: int, via: str
) -> None:
    """Phase 5: conversational pipeline.

    1. yes/no check stays separate (only used by /undo after Phase 5).
    2. Call Haiku with sliding-window history; receive {text_reply, tool_calls}.
    3. Run each tool call through the executor, write per-tool audit entries
       (shape unchanged from Phase 3a–3i).
    4. Update history.
    5. Send Haiku's text_reply, or stay silent.
    """
    # 1. Pending confirmation? (only /undo uses this after Phase 5)
    pending = _pending.get(chat_id)
    confirmation = _classify_confirmation(text) if pending is not None else None
    if pending is not None and confirmation is not None:
        _pending.pop(chat_id, None)
        if confirmation == "yes":
            await _execute_undo_confirmed(update, pending)
        else:
            log_action(
                user_input=pending["original_text"],
                intent={"name": "undo"},
                result="canceled",
                via=pending.get("via", via),
            )
            await update.message.reply_text("Canceled.")
        return

    # 2. Need an Anthropic key for the conversational pipeline
    if not ANTHROPIC_API_KEY:
        await update.message.reply_text(
            "Free-text commands need an Anthropic API key.\n"
            "See PHASE3A-SETUP.md for the 4-step walkthrough."
        )
        return

    # 3. Typing indicator + creds for the open-task snapshot
    try:
        await update.message.chat.send_action(ChatAction.TYPING)
    except Exception:
        pass

    parser_creds = None
    try:
        parser_creds = get_credentials()
    except Exception:
        log.warning(
            "creds fetch failed before parse_intent; parsing without snapshot",
            exc_info=True,
        )

    # 4. Build this turn's user message. If the prior assistant turn ended
    # with tool_use blocks, the API requires a tool_result message immediately
    # after — we stashed those blocks in _pending_tool_results when the tools
    # ran, and now merge them into this turn's user content (a single user
    # message can carry both tool_result and text blocks).
    pending_tr = _pending_tool_results.pop(chat_id, [])
    if pending_tr:
        user_msg = {
            "role": "user",
            "content": list(pending_tr) + [{"type": "text", "text": text}],
        }
    else:
        user_msg = {"role": "user", "content": text}

    history = list(_history.get(chat_id, []))
    messages_for_api = history + [user_msg]

    try:
        result = parse_intent(messages_for_api, creds=parser_creds)
    except Exception as exc:
        log.exception("parse failed")
        log_action(user_input=text, intent=None, result="parse_error", error=str(exc), via=via)
        await update.message.reply_text(f"Couldn't parse that: {type(exc).__name__}: {exc}")
        return

    text_reply = result["text_reply"]
    tool_calls = result["tool_calls"]
    assistant_message = result["assistant_message"]

    # 5. Run each tool call. Per-tool audit shape preserved.
    # Capture each tool's result string for the NEXT turn's tool_result blocks.
    new_tool_results: list[dict[str, Any]] = []
    for call in tool_calls:
        result_str = await _dispatch_tool_call(update, text, via, call)
        new_tool_results.append({
            "type": "tool_result",
            "tool_use_id": call["id"],
            "content": result_str or "(no result)",
        })

    # 6. Update history (alternating user/assistant). Stash tool_results for
    # the next turn so they can be merged into that user message's content.
    chat_history = _history.setdefault(chat_id, [])
    chat_history.append(user_msg)
    chat_history.append(assistant_message)
    _history[chat_id] = _trim_history(chat_history)

    if new_tool_results:
        _pending_tool_results[chat_id] = new_tool_results

    # 7. Send Haiku's text reply. Empty text == intentional silence.
    if text_reply:
        await update.message.reply_text(text_reply)


async def _dispatch_tool_call(
    update: Update, original_text: str, via: str, call: dict[str, Any]
) -> str:
    """Phase 5: run one tool_use call from Haiku, write per-tool audits, and
    return a result string for the API tool_result block (so Haiku knows what
    happened on subsequent turns).

    Bot does NOT send a Telegram reply for normal tool successes — Haiku's
    text_reply (relayed at the end of _process_user_text) is the user-facing
    message. Errors and no_match conditions get a bot-side reply for safety.
    """
    name = call["name"]

    if name == "complete_task":
        return await _execute_complete_immediate(update, original_text, call, via)

    if name == "edit_task":
        return await _execute_edit_immediate(update, original_text, call, via)

    # add_task, append_to_brain_dump, query_brain_dump
    intent_shape = {"name": name, "input": call["input"]}
    try:
        creds = get_credentials()
        result_msg = execute_intent(intent_shape, creds)
    except Exception as exc:
        log.exception("execute failed: %s", name)
        # add_task partial-success handling preserved from Phase 3e.
        if name == "add_task" and intent_shape.get("created_records"):
            done = intent_shape["created_records"]
            total = len(intent_shape.get("input", {}).get("tasks", []))
            for r in done:
                log_action(
                    user_input=original_text,
                    intent={"name": "add_task", "input": r["task_input"]},
                    result="ok",
                    details=r["summary_line"],
                    via=via,
                    created_task_id=r.get("created_task_id"),
                    created_task_list_id=r.get("created_task_list_id"),
                )
            log_action(
                user_input=original_text,
                intent=intent_shape,
                result="error",
                error=str(exc),
                details=f"Failed at task {len(done) + 1} of {total}",
                via=via,
            )
            await update.message.reply_text(
                f"Added {len(done)} of {total} before failing: {type(exc).__name__}: {exc}"
            )
            return f"Partial: added {len(done)} of {total}, then failed: {exc}"
        log_action(
            user_input=original_text, intent=intent_shape, result="error",
            error=str(exc), via=via,
        )
        await update.message.reply_text(f"Failed: {type(exc).__name__}: {exc}")
        return f"Error: {type(exc).__name__}: {exc}"

    # Per-task audit entries for add_task (one per created task).
    if name == "add_task":
        for r in intent_shape.get("created_records", []):
            log_action(
                user_input=original_text,
                intent={"name": "add_task", "input": r["task_input"]},
                result="ok",
                details=r["summary_line"],
                via=via,
                created_task_id=r.get("created_task_id"),
                created_task_list_id=r.get("created_task_list_id"),
            )
    else:
        # append_to_brain_dump, query_brain_dump — single audit entry.
        audit_details = intent_shape.get("_audit_summary") or result_msg
        log_action(
            user_input=original_text, intent=intent_shape, result="ok",
            details=audit_details, via=via,
        )

    return result_msg


async def _execute_complete_immediate(
    update: Update, original_text: str, call: dict[str, Any], via: str
) -> str:
    """Phase 5: complete_task fires immediately, no yes/no staging.

    Per-matched-task audit shape preserved exactly from Phase 3d's
    _execute_confirmed loop so /undo and Friday review keep working.

    Returns a result string for the API tool_result block.
    """
    queries_raw = (call.get("input") or {}).get("title_queries") or []
    queries = [q.strip() for q in queries_raw if isinstance(q, str) and q.strip()]
    if not queries:
        await update.message.reply_text("Couldn't tell which task(s) to complete.")
        return "Error: no title_queries provided"

    try:
        creds = get_credentials()
        tasks = get_open_tasks(creds)
    except Exception as exc:
        log.exception("fetch tasks failed")
        await update.message.reply_text(f"Couldn't fetch tasks: {exc}")
        return f"Error fetching tasks: {exc}"

    matched: list[dict[str, Any]] = []
    unmatched: list[str] = []
    seen_ids: set[str] = set()  # Phase 5.1: dedupe within a multi-query batch
    for q in queries:
        m = find_best_match(q, tasks, seen_ids=seen_ids)
        if m:
            seen_ids.add(m["id"])
            matched.append({
                "id": m["id"],
                "list_id": m.get("_list_id"),
                "title": m.get("title", "(untitled)"),
                "query": q,
            })
        else:
            unmatched.append(q)

    if not matched:
        log_action(
            user_input=original_text,
            intent={"name": "complete_task", "input": call["input"]},
            result="no_match",
            details={"unmatched": unmatched},
            via=via,
        )
        bullets = "\n".join(f"  • {q}" for q in unmatched)
        await update.message.reply_text(f"No open task matched:\n{bullets}")
        return f"No matches for: {unmatched}"

    intent_with_matches = {
        "name": "complete_task",
        "input": call["input"],
        "matched_tasks": matched,
    }
    try:
        result_msg = execute_intent(intent_with_matches, creds)
    except Exception as exc:
        log.exception("complete_task execute failed")
        log_action(
            user_input=original_text, intent=intent_with_matches,
            result="error", error=str(exc), via=via,
        )
        await update.message.reply_text(f"Failed: {type(exc).__name__}: {exc}")
        return f"Error: {type(exc).__name__}: {exc}"

    # Per-matched-task audit (mirrors Phase 3d _execute_confirmed shape).
    for m in matched:
        log_action(
            user_input=original_text,
            intent={
                "name": "complete_task",
                "input": {"title_query": m.get("query", "")},
                "matched_task_id": m["id"],
                "matched_task_list_id": m.get("list_id"),
            },
            result="ok",
            details=f"Completed: {m.get('title', '(untitled)')}",
            via=via,
        )

    # Audit unmatched queries separately so Friday review counts each.
    for q in unmatched:
        log_action(
            user_input=original_text,
            intent={"name": "complete_task", "input": {"title_query": q}},
            result="no_match",
            details=f"unmatched: {q!r}",
            via=via,
        )

    return result_msg


async def _execute_edit_immediate(
    update: Update, original_text: str, call: dict[str, Any], via: str
) -> str:
    """Phase 3g+5: edit_task fires immediately. No bot reply on success — Haiku
    composes the user-facing message. Per-edit audit + before_state preserved.

    Returns a result string for the API tool_result block.
    """
    edits_in = (call.get("input") or {}).get("edits") or []
    edits = [e for e in edits_in if isinstance(e, dict) and e.get("target_query")]
    if not edits:
        await update.message.reply_text("Couldn't tell which task(s) to edit.")
        return "Error: no edits provided"

    try:
        creds = get_credentials()
        tasks = get_open_tasks(creds)
    except Exception as exc:
        log.exception("fetch tasks failed")
        await update.message.reply_text(f"Couldn't fetch tasks: {exc}")
        return f"Error fetching tasks: {exc}"

    matched_edits: list[dict[str, Any]] = []
    unmatched: list[str] = []
    seen_ids: set[str] = set()  # Phase 5.1: dedupe within a multi-edit batch
    for e in edits:
        q = e.get("target_query", "").strip()
        m = find_best_match(q, tasks, seen_ids=seen_ids)
        if m:
            seen_ids.add(m["id"])
            matched_edits.append({"task": m, "edit": e})
        else:
            unmatched.append(q)

    if not matched_edits:
        log_action(
            user_input=original_text,
            intent={"name": "edit_task", "input": call["input"]},
            result="no_match",
            details={"unmatched": unmatched},
            via=via,
        )
        bullets = "\n".join(f"  • {q}" for q in unmatched)
        await update.message.reply_text(f"No open task matched:\n{bullets}")
        return f"No matches for: {unmatched}"

    intent_with_matches = {
        "name": "edit_task",
        "input": call["input"],
        "matched_edits": matched_edits,
    }

    try:
        result_msg = execute_intent(intent_with_matches, creds)
    except Exception as exc:
        log.exception("edit execute failed")
        # Mirror Phase 3e add_task partial-success handling.
        done = intent_with_matches.get("edited_records") or []
        total = len(matched_edits)
        for r in done:
            log_action(
                user_input=original_text,
                intent={
                    "name": "edit_task",
                    "input": r["edit_input"],
                    "matched_task_id": r["matched_task_id"],
                    "matched_task_list_id": r["matched_task_list_id"],
                },
                result="ok",
                details=r["summary_line"],
                via=via,
                before_state=r["before_state"],
            )
        log_action(
            user_input=original_text,
            intent={"name": "edit_task", "input": call["input"]},
            result="error",
            error=str(exc),
            details=f"Failed at edit {len(done) + 1} of {total}",
            via=via,
        )
        await update.message.reply_text(
            f"Edited {len(done)} of {total} before failing: {type(exc).__name__}: {exc}"
        )
        return f"Partial: edited {len(done)} of {total}, then failed: {exc}"

    # Per-edit success audit.
    for r in intent_with_matches.get("edited_records", []):
        log_action(
            user_input=original_text,
            intent={
                "name": "edit_task",
                "input": r["edit_input"],
                "matched_task_id": r["matched_task_id"],
                "matched_task_list_id": r["matched_task_list_id"],
            },
            result="ok",
            details=r["summary_line"],
            via=via,
            before_state=r["before_state"],
        )
    for q in unmatched:
        log_action(
            user_input=original_text,
            intent={"name": "edit_task", "input": {"target_query": q}},
            result="no_match",
            details=f"unmatched: {q!r}",
            via=via,
        )

    return result_msg


# ---------------- Phase 3f: /undo ----------------

async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/undo — revert the most recent add_task or complete_task (with confirmation)."""
    if not _is_authorized(update):
        log.warning("Rejected /undo from chat_id=%s", update.effective_chat.id)
        return

    status, entry = find_undoable_entry()
    if status == "empty":
        await update.message.reply_text("Nothing recent to undo.")
        return
    if status == "brain_dump_only":
        await update.message.reply_text(
            "Last actions were brain dump appends — can't auto-undo. "
            "Edit the doc directly."
        )
        return

    # status == "found"
    target_intent = (entry.get("intent") or {})
    target_name = target_intent.get("name", "")

    # Pre-3f add_task entries lack created_task_id — can't delete via API.
    if target_name == "add_task" and not entry.get("created_task_id"):
        await update.message.reply_text(
            "This add was logged before /undo existed and doesn't have a task ID — "
            "delete it via Google Tasks directly."
        )
        return

    verb, past_tense, noun = describe_target(entry)

    chat_id = update.effective_chat.id
    _pending[chat_id] = {
        "original_text": "/undo",
        "kind": "undo",
        "target_entry": entry,
        "verb": verb,
        "past_tense": past_tense,
        "noun": noun,
        "via": "text",
    }

    await update.message.reply_text(
        f"About to undo: {verb} {noun}.\nYes/no?"
    )


async def _execute_undo_confirmed(update: Update, pending: dict[str, Any]) -> None:
    """Execute the undo against Google Tasks, then write an undo audit entry."""
    via = pending.get("via", "text")
    entry = pending["target_entry"]
    past_tense = pending["past_tense"]
    noun = pending["noun"]

    target_intent = entry.get("intent") or {}
    target_name = target_intent.get("name", "")

    try:
        creds = get_credentials()
        if target_name == "add_task":
            delete_task_by_id(
                creds,
                task_id=entry["created_task_id"],
                tasklist_id=entry.get("created_task_list_id"),
            )
        elif target_name == "complete_task":
            reopen_task_by_id(
                creds,
                task_id=target_intent["matched_task_id"],
                tasklist_id=target_intent.get("matched_task_list_id"),
            )
        else:
            raise RuntimeError(f"Cannot undo intent type: {target_name!r}")
    except Exception as exc:
        log.exception("undo failed")
        log_action(
            user_input=pending["original_text"],
            intent={"name": "undo"},
            result="error",
            error=str(exc),
            via=via,
        )
        await update.message.reply_text(f"Undo failed: {type(exc).__name__}: {exc}")
        return

    details = f"Undid: {past_tense} {noun}"
    log_action(
        user_input=pending["original_text"],
        intent={"name": "undo"},
        result="ok",
        details=details,
        via=via,
        undid_ts=entry.get("ts"),
    )
    # Phase 5: drop the [OK] prefix; the past-tense detail reads cleanly on its own.
    await update.message.reply_text(details)


# ---------------- Wiring ----------------

def build_application() -> Application:
    """Wire up handlers and return a ready-to-run Application."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("echo", echo_cmd))
    app.add_handler(CommandHandler("brief", brief))
    app.add_handler(CommandHandler("review", review))
    app.add_handler(CommandHandler("undo", undo))

    # Free-text intent handler — must come AFTER command handlers.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))

    # Phase 3b: voice messages → faster-whisper → same intent pipeline.
    app.add_handler(MessageHandler(filters.VOICE, voice_message))

    # Scheduled jobs (daily brief, etc.)
    schedule_jobs(app)

    return app

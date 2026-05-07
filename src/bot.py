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
# Single-user bot, so this dict is tiny.
_pending: dict[int, dict[str, Any]] = {}


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
    """Shared pipeline for both text and voice inputs.

    `via` is "text" or "voice" — gets propagated into the audit log so Friday
    review can see how Jon prefers to interact.
    """
    # 1. Pending confirmation?
    pending = _pending.get(chat_id)
    confirmation = _classify_confirmation(text) if pending is not None else None
    if pending is not None and confirmation is not None:
        _pending.pop(chat_id, None)
        is_undo = pending.get("kind") == "undo"
        if confirmation == "yes":
            if is_undo:
                await _execute_undo_confirmed(update, pending)
            else:
                await _execute_confirmed(update, pending)
        else:
            cancel_intent = (
                {"name": "undo"} if is_undo else pending.get("intent")
            )
            log_action(
                user_input=pending["original_text"],
                intent=cancel_intent,
                result="canceled",
                via=pending.get("via", via),
            )
            await update.message.reply_text("Canceled.")
        return

    # 2. Need an Anthropic key for the rest
    if not ANTHROPIC_API_KEY:
        await update.message.reply_text(
            "Free-text commands need an Anthropic API key.\n"
            "See PHASE3A-SETUP.md for the 4-step walkthrough.\n\n"
            f"Echo: {text}"
        )
        return

    # 3. Parse intent (Phase 3e: pass creds so the parser can inject an
    # open-task snapshot into Haiku's system prompt for numeric/"all"/fuzzy
    # references). On creds failure we still parse — just without the snapshot.
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

    try:
        intent = parse_intent(text, parser_creds)
    except Exception as exc:
        log.exception("intent parse failed")
        log_action(
            user_input=text, intent=None, result="parse_error", error=str(exc), via=via
        )
        await update.message.reply_text(f"Couldn't parse that: {type(exc).__name__}: {exc}")
        return

    name = intent["name"]
    inp = intent.get("input", {})

    # 4. Route by intent
    if name == "needs_clarification":
        await update.message.reply_text(
            f"{inp.get('interpretation', '')}\n\n"
            f"{inp.get('question_for_user', 'Could you rephrase?')}"
        )
        log_action(user_input=text, intent=intent, result="clarification_asked", via=via)
        return

    if name == "complete_task":
        await _handle_complete_task(update, text, intent, chat_id, via=via)
        return

    if name == "edit_task":
        await _handle_edit_task(update, text, intent, via=via)
        return

    # 5. Low-stakes: execute immediately (add_task, append_to_brain_dump)
    try:
        creds = get_credentials()
        result_msg = execute_intent(intent, creds)
    except Exception as exc:
        log.exception("execute failed")
        # Phase 3e: add_task can partially succeed in multi-task mode. The
        # executor leaves a trail on intent['created_records'] so we can still
        # log the successes as ok before recording the failure.
        if intent.get("name") == "add_task" and intent.get("created_records"):
            done = intent["created_records"]
            total = len(intent.get("input", {}).get("tasks", []))
            for r in done:
                log_action(
                    user_input=text,
                    intent={"name": "add_task", "input": r["task_input"]},
                    result="ok",
                    details=r["summary_line"],
                    via=via,
                    created_task_id=r.get("created_task_id"),
                    created_task_list_id=r.get("created_task_list_id"),
                )
            log_action(
                user_input=text,
                intent=intent,
                result="error",
                error=str(exc),
                details=f"Failed at task {len(done) + 1} of {total}",
                via=via,
            )
            await update.message.reply_text(
                f"Added {len(done)} of {total} before failing: {type(exc).__name__}: {exc}"
            )
            return

        log_action(
            user_input=text, intent=intent, result="error", error=str(exc), via=via
        )
        await update.message.reply_text(f"Failed: {type(exc).__name__}: {exc}")
        return

    # Phase 3e: per-task audit entries for add_task so Friday review counts
    # each created task, not each multi-add intent.
    if intent.get("name") == "add_task":
        for r in intent.get("created_records", []):
            log_action(
                user_input=text,
                intent={"name": "add_task", "input": r["task_input"]},
                result="ok",
                details=r["summary_line"],
                via=via,
                created_task_id=r.get("created_task_id"),
                created_task_list_id=r.get("created_task_list_id"),
            )
    else:
        # Phase 3i: query_brain_dump (and other read intents) stash a short
        # audit summary on intent['_audit_summary'] so the audit log doesn't
        # capture the full multi-line reply text.
        audit_details = intent.get("_audit_summary") or result_msg
        log_action(user_input=text, intent=intent, result="ok", details=audit_details, via=via)

    await update.message.reply_text(f"[OK] {result_msg}")


async def _handle_complete_task(
    update: Update, original_text: str, intent: dict, chat_id: int, via: str
) -> None:
    queries_raw = intent.get("input", {}).get("title_queries") or []
    # Defensive: drop empties, preserve order.
    queries = [q.strip() for q in queries_raw if isinstance(q, str) and q.strip()]
    if not queries:
        await update.message.reply_text("Couldn't tell which task(s) to complete.")
        return

    try:
        creds = get_credentials()
        tasks = get_open_tasks(creds)
    except Exception as exc:
        log.exception("fetch tasks failed")
        await update.message.reply_text(f"Couldn't fetch tasks: {exc}")
        return

    matched: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for q in queries:
        m = find_best_match(q, tasks)
        if m:
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
            intent=intent,
            result="no_match",
            details={"unmatched": unmatched},
            via=via,
        )
        bullets = "\n".join(f"  • {q}" for q in unmatched)
        await update.message.reply_text(
            f"No open tasks matched:\n{bullets}\n\nTry the exact wording from /brief."
        )
        return

    _pending[chat_id] = {
        "original_text": original_text,
        "intent": {**intent, "matched_tasks": matched},
        "matched_tasks": matched,
        "unmatched": unmatched,
        "via": via,
    }

    lines = ["About to complete:"]
    for i, m in enumerate(matched, 1):
        lines.append(f"{i}. {m['title']}")
    if unmatched:
        lines.append("")
        for q in unmatched:
            lines.append(f"No match: '{q}'")
    lines.append("")
    lines.append("Yes/no?")
    await update.message.reply_text("\n".join(lines))


async def _handle_edit_task(
    update: Update, original_text: str, intent: dict, via: str
) -> None:
    """Phase 3g: edits execute immediately (no yes/no), one audit entry per edit.

    For each edit, fuzzy-match against open tasks (snoozed are filtered out by
    get_open_tasks already). Unmatched queries get a no_match audit entry and
    appear inline in the reply. Matched ones go through the executor in a
    single call so partial failure mid-loop still leaves a record trail on
    intent['edited_records'].
    """
    edits_in = (intent.get("input") or {}).get("edits") or []
    edits = [e for e in edits_in if isinstance(e, dict) and e.get("target_query")]
    if not edits:
        await update.message.reply_text("Couldn't tell which task(s) to edit.")
        return

    try:
        creds = get_credentials()
        tasks = get_open_tasks(creds)
    except Exception as exc:
        log.exception("fetch tasks failed")
        await update.message.reply_text(f"Couldn't fetch tasks: {exc}")
        return

    matched_edits: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for e in edits:
        q = e.get("target_query", "").strip()
        m = find_best_match(q, tasks)
        if m:
            matched_edits.append({"task": m, "edit": e})
        else:
            unmatched.append(q)

    if not matched_edits:
        log_action(
            user_input=original_text,
            intent=intent,
            result="no_match",
            details={"unmatched": unmatched},
            via=via,
        )
        bullets = "\n".join(f"  • {q}" for q in unmatched)
        await update.message.reply_text(
            f"No open tasks matched:\n{bullets}\n\nTry the exact wording from /brief."
        )
        return

    intent_with_matches = {**intent, "matched_edits": matched_edits}

    try:
        result_msg = execute_intent(intent_with_matches, creds)
    except Exception as exc:
        log.exception("edit execute failed")
        # Mirror Phase 3e add_task partial-success handling: log per-task
        # successes already on intent_with_matches['edited_records'], then the
        # error.
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
            intent=intent,
            result="error",
            error=str(exc),
            details=f"Failed at edit {len(done) + 1} of {total}",
            via=via,
        )
        await update.message.reply_text(
            f"Edited {len(done)} of {total} before failing: {type(exc).__name__}: {exc}"
        )
        return

    # Per-task success audit entries.
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

    # Per-query no_match entries (separate from successes so Friday review
    # counts each independently).
    for q in unmatched:
        log_action(
            user_input=original_text,
            intent={"name": "edit_task", "input": {"target_query": q}},
            result="no_match",
            details=f"unmatched: {q!r}",
            via=via,
        )

    reply = result_msg
    if unmatched:
        reply += "\n\n" + "\n".join(f"No match: '{q}'" for q in unmatched)
    await update.message.reply_text(reply)


async def _execute_confirmed(update: Update, pending: dict[str, Any]) -> None:
    via = pending.get("via", "text")
    intent = pending["intent"]
    original_text = pending["original_text"]

    try:
        creds = get_credentials()
        result_msg = execute_intent(intent, creds)
    except Exception as exc:
        log.exception("confirmed execute failed")
        log_action(
            user_input=original_text,
            intent=intent,
            result="error",
            error=str(exc),
            via=via,
        )
        await update.message.reply_text(f"Failed: {type(exc).__name__}: {exc}")
        return

    # Per-task audit entries for complete_task so Friday review's count and
    # "WHAT SHIPPED" listing stay accurate. Each entry is shaped like a
    # singular completion (intent.input.title_query) for backwards compat.
    if intent.get("name") == "complete_task":
        for m in pending.get("matched_tasks") or []:
            per_task_intent = {
                "name": "complete_task",
                "input": {"title_query": m.get("query", "")},
                "matched_task_id": m["id"],
                "matched_task_list_id": m.get("list_id"),
            }
            log_action(
                user_input=original_text,
                intent=per_task_intent,
                result="ok",
                details=f"Completed: {m.get('title', '(untitled)')}",
                via=via,
            )
    else:
        log_action(
            user_input=original_text,
            intent=intent,
            result="ok",
            details=result_msg,
            via=via,
        )

    await update.message.reply_text(f"[OK] {result_msg}")


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
    await update.message.reply_text(f"[OK] {details}")


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

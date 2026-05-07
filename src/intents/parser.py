"""Conversational intent parser (Phase 5).

Haiku acts as Jon's executive assistant inside Telegram. Each call returns
EITHER tool calls, OR a plain-text reply, OR both — Jon's bot routes the tool
calls to the executor and relays the text reply.

Tools available to Haiku (5):
  - add_task
  - complete_task
  - edit_task
  - append_to_brain_dump
  - query_brain_dump

needs_clarification was removed — Haiku writes clarifying questions as plain
text instead. tool_choice is "auto" so Haiku can stay text-only when needed.

History is per-chat, sliding window of the last ~10 user turns, in-memory only.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from ..config import ANTHROPIC_API_KEY, TIMEZONE
from ..data_sources.tasks import get_open_tasks

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"  # cheap + fast — great for intent parsing

# Phase 3e: cache the open-task snapshot for a short window so rapid back-to-back
# messages don't re-hammer Google. Keyed on id(creds) — in-place token refresh
# stays the same key, full re-auth lands a new key. Either is correct.
#
# Known staleness: if a task is completed and Jon types another command within
# the TTL window, the snapshot still shows the just-completed task. Haiku may
# emit it; the bot's fresh fetch in _handle_complete_task fails to match it and
# routes it to the unmatched bucket. Harmless.
_TASK_CACHE: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_TASK_CACHE_TTL_SECONDS = 30


def _get_cached_open_tasks(creds) -> list[dict[str, Any]] | None:
    """Return cached open tasks if fresh, else fetch. None on failure."""
    key = id(creds)
    now = time.monotonic()
    cached = _TASK_CACHE.get(key)
    if cached and (now - cached[0]) < _TASK_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        tasks = get_open_tasks(creds)
    except Exception:
        log.warning("parser snapshot fetch failed; calling Haiku without it", exc_info=True)
        return None

    _TASK_CACHE[key] = (now, tasks)
    return tasks


def _render_open_tasks_block(creds) -> str:
    """Render the snapshot for splicing into the system prompt.

    Returns "" if creds is None or fetch failed (system prompt still works).
    Returns "\\n\\n<block>" otherwise — leading newlines slot it cleanly into
    the template, preserving spacing whether the block is present or not.
    """
    if creds is None:
        return ""
    tasks = _get_cached_open_tasks(creds)
    if tasks is None:
        return ""

    if not tasks:
        return "\n\nCurrently open tasks: (none)"

    lines = ["Currently open tasks:"]
    for i, t in enumerate(tasks, 1):
        title = (t.get("title") or "(untitled)").strip()
        due = t.get("due") or ""
        if due:
            # Due is RFC3339 ("2026-05-08T00:00:00.000Z") — keep just the date.
            lines.append(f"{i}. {title} (due {due[:10]})")
        else:
            lines.append(f"{i}. {title}")
    return "\n\n" + "\n".join(lines)

INTENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "add_task",
        "description": (
            "Add one or more new tasks to Google Tasks. Use when the user wants "
            "to create todos, action items, or remember to do things — including "
            "multiple in a single message ('add X and Y', 'add three: A, B, C')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "description": (
                        "List of tasks to add. For 'add X and Y' produce two entries. "
                        "For a single task, produce a 1-element list."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Short title of the task (ideally <80 chars).",
                            },
                            "due": {
                                "type": "string",
                                "description": (
                                    "Due date in ISO YYYY-MM-DD. Convert relative dates like "
                                    "'tomorrow', 'Friday', 'next week'. Each task in the list "
                                    "gets its own due date. Omit if no date specified."
                                ),
                            },
                            "notes": {
                                "type": "string",
                                "description": "Optional longer notes if the user provided extra context for this task.",
                            },
                            "impact": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": (
                                    "Impact score 1-5 based on what Jon said. "
                                    "5 = changes RaceCraft trajectory or unlocks 7-figure outcome. "
                                    "4 = moves a top goal forward >10% OR lands key hire/investor/partner. "
                                    "3 = solid progress (DEFAULT — use this if unsure). "
                                    "2 = keeps things running, could delegate. "
                                    "1 = admin, forgettable. "
                                    "Look for cues like 'really important', 'critical', 'just admin', 'small thing'."
                                ),
                            },
                            "racecraft": {
                                "type": "boolean",
                                "description": (
                                    "True if this task advances RaceCraft (Jon's main bet for 2026). "
                                    "RaceCraft tasks are anything touching: rider signups, MRR, retention, "
                                    "fundraising, partnerships, RaceCraft team, race events/organizers. "
                                    "When in doubt, false."
                                ),
                            },
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "append_to_brain_dump",
        "description": (
            "Append a thought, idea, or longer note to the Brain Dump doc's Active section. "
            "Use this for free-form notes, observations, or ideas that aren't crisp tasks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to add as a new bullet under Active.",
                }
            },
            "required": ["text"],
        },
    },
    {
        "name": "complete_task",
        "description": (
            "Mark one or more existing Google Tasks as complete. Use when the user says 'done', "
            "'mark complete', 'finished', 'X is done', or lists multiple things to complete in "
            "one message ('complete X, Y, and Z')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title_queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 10,
                    "description": (
                        "List of search strings, one per task to complete. "
                        "For 'complete X, Y, and Z' produce ['X', 'Y', 'Z']. "
                        "For a single task, produce a 1-element list like ['X']."
                    ),
                }
            },
            "required": ["title_queries"],
        },
    },
    {
        "name": "edit_task",
        "description": (
            "Edit one or more existing Google Tasks. Use for renames, "
            "rescheduling (push to a new date), changing impact, toggling the "
            "racecraft tag, or snoozing (hide until a date). Each edit targets "
            "ONE task and may set any subset of {new_title, new_due, new_impact, "
            "new_racecraft, snooze_until} — at least one field per edit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "description": (
                        "List of edits. For 'push X and Y to Tuesday' produce "
                        "two entries with the same new_due. For a single edit, "
                        "produce a 1-element list."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_query": {
                                "type": "string",
                                "description": (
                                    "Search string identifying the task to edit. "
                                    "When the open-tasks snapshot is shown above, "
                                    "copy the title VERBATIM from the list — same "
                                    "rule as complete_task title_queries."
                                ),
                            },
                            "new_title": {
                                "type": "string",
                                "description": "Replacement title. Use for renames.",
                            },
                            "new_due": {
                                "type": "string",
                                "description": (
                                    "New due date in ISO YYYY-MM-DD. Convert "
                                    "relative dates ('Tuesday', 'next week') to "
                                    "absolute dates same as add_task."
                                ),
                            },
                            "new_impact": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": (
                                    "New impact score. Same 1-5 scale as add_task."
                                ),
                            },
                            "new_racecraft": {
                                "type": "boolean",
                                "description": (
                                    "True to add the racecraft tag, false to "
                                    "remove it. Omit if the user didn't mention "
                                    "racecraft for this edit."
                                ),
                            },
                            "snooze_until": {
                                "type": "string",
                                "description": (
                                    "ISO YYYY-MM-DD. Hides the task from /brief "
                                    "and the parser snapshot until this date. "
                                    "Also sets the task's due date to the same "
                                    "value, since 'snooze until X' implies "
                                    "'I'll think about it on X'."
                                ),
                            },
                        },
                        "required": ["target_query"],
                    },
                },
            },
            "required": ["edits"],
        },
    },
    {
        "name": "query_brain_dump",
        "description": (
            "Read recent entries from the Brain Dump doc's Active section. Use "
            "when the user wants to recall what they dumped, search their notes, "
            "or browse recent ideas. Both fields are optional — bare 'what did I "
            "dump' uses the defaults."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "description": (
                        "Max number of matching entries to return. Default is 10 "
                        "if omitted. Caps how many entries the user sees, AFTER "
                        "any keyword filter is applied."
                    ),
                },
                "keyword": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive substring filter. Only entries "
                        "containing this string anywhere are returned. Omit if the "
                        "user didn't ask about a specific topic."
                    ),
                },
            },
        },
    },
]


SYSTEM_PROMPT = """You are Jon's executive assistant inside Telegram.

Today is {today} ({weekday}), timezone {tz}.{open_tasks_section}

You can do FIVE things by calling tools, plus reply in text:
  - add_task              create one or more Google Tasks
  - complete_task         mark one or more open tasks done
  - edit_task             rename / reschedule / re-impact / tag / snooze
  - append_to_brain_dump  capture a thought into the Brain Dump doc
  - query_brain_dump      recall recent dump entries (newest first)

You may call zero, one, or several tools per turn AND reply in text. Tools
execute in the order you call them. Text-only replies (no tool call) are right
when you need to clarify, confirm understanding, or react conversationally.

VOICE AND TONE
- Founder-to-EA. Warm but dry. Observant. Comfortable with silence.
- Brevity is respect. ONE LINE is the default. Two lines if there's a real
  choice to surface. Three almost never.
- Never chirpy. No "Of course!", "I'd be happy to", "Great question!". No
  exclamation marks.
- Refer to tasks by name in normal sentence case ("buy milk", "the deck"),
  not as quoted strings or [bracketed metadata].
- Don't say "[OK]" or "Successfully" or repeat the structured input
  ("Added task: Buy milk (due 2026-05-08) [impact:3]").
- For obvious actions, an empty text reply is fine — the user can see what
  changed; no need to narrate.

GOOD REPLIES (model these):
  user: add buy milk for thursday
    → tool: add_task; text: "Added — buy milk for Thursday."
  user: mark the gym task done and note that I'm starting deep work
    → tools: [complete_task, append_to_brain_dump]; text: "Done. Deep work noted."
  user: do the Whistler thing
    → no tool; text: "Complete the Whistler call task or add a new one?"
  user: rename buy milk to oat milk
    → tool: edit_task; text: "Renamed — oat milk."
  user: thanks
    → no tool; text: "Sure."

BAD REPLIES (do not produce):
  "[OK] Added task: Buy milk (due 2026-05-08) [impact:3]"
  "Successfully added the new task to your list!"
  "Of course! I'd be happy to do that for you."
  Any reply containing "!"

ROUTING
- "add / remind me / I should / todo"               -> add_task
- "X is done / mark X done / finished X"            -> complete_task
- "push / reschedule / rename / bump / snooze / tag"-> edit_task
- "note that / thinking about / brain-dumping"      -> append_to_brain_dump
- "what did I dump / show recent dumps / find X"    -> query_brain_dump
- ambiguous / single noun / no clear verb           -> NO tool, text-only question

CLARIFICATION (no longer a tool)
When the user's intent isn't clear, write a short plain-text question. Don't
preface with "I'm not sure" — just ask.
  user: dragon
    → "Add a task, note an idea, or something else?"
  user: do the Whistler thing
    → "Complete the Whistler call task or add a new one?"

Bare-verb guard: If the user's message is just a verb (add, complete, edit,
snooze, push, rename, undo) without a clear noun or quoted phrase to act on,
ALWAYS reply in plain text asking what they mean. Never invent a target from
prior conversation context — context is for resolving REFERENCES like "it" or
"the second one", not for filling in missing nouns.
  user: Add separate
    → "Add what?"
  user: complete                                (no noun)
    → "Complete which task?"
  user: complete it                             (reference, not bare verb)
    → use prior context to resolve "it", emit complete_task as usual
  user: snooze
    → "Snooze which task, until when?"

CONTINUITY
The conversation history shows your prior turns including the tool_use inputs
you emitted. If the user follows up with "actually make it oat milk" right
after you added "Buy milk", that means edit_task on the buy milk task — read
the prior tool_use input for the title to use as target_query.

MULTI-TASK COMPLETION
complete_task takes title_queries as a LIST. Split multi-target requests:
- "done with the deck and the budget"             -> ["deck", "budget"]
- "X, Y, and Z are all complete"                  -> ["X", "Y", "Z"]
- "finished the pitch deck"                       -> ["pitch deck"]   (1-element)
Single-task input always uses a 1-element list, never a bare string.

MULTI-TASK ADD
add_task takes a "tasks" LIST. Split multi-target requests:
- "add buy milk and call Lisa"                       -> 2 tasks
- "remind me to email Whistler tomorrow"             -> 1 task (1-element)
- "add finalize pitch deck (high impact, racecraft)" -> 1 task with impact=5, racecraft=true
Each task's due date is resolved independently — "tomorrow" attached to one
task doesn't apply to others unless the user said so.

EDITING TASKS
edit_task takes an "edits" LIST. Each edit targets ONE task and sets any
subset of new_title, new_due, new_impact, new_racecraft, snooze_until. Set
ONLY the fields the user mentioned. At least one edit field per edit.
- "push the Whistler email to next Tuesday"   -> [{{target_query: "...", new_due: <Tue ISO>}}]
- "rename buy milk to buy oat milk"           -> [{{target_query: "Buy milk", new_title: "Buy oat milk"}}]
- "bump the deck to impact 5"                 -> [{{target_query: "...deck...", new_impact: 5}}]
- "tag the Whistler call as racecraft"        -> [{{target_query: "...", new_racecraft: true}}]
- "snooze gym until Friday"                   -> [{{target_query: "...gym...", snooze_until: <Fri ISO>}}]
- "push X and Y to Tuesday"                   -> 2 edits, same new_due
- "rename A to B and bump impact to 5"        -> 1 edit with BOTH fields
target_query follows the snapshot-verbatim rule: copy the matching title
exactly from the open-tasks list above so downstream fuzzy-matching is trivial.
Snoozed tasks don't appear in the snapshot — if the user asks to unsnooze
something you can't see, ask in text.

RESOLVING COMPLETIONS
When a "Currently open tasks" list is shown above, treat it as truth.
- Numeric refs ("1, 3 done")           -> look up those numbers, emit titles
- "all open done"                       -> emit every title in the list
- Fuzzy / Whisper-garbled refs          -> fuzzy-match, emit the closest title verbatim
- Number out of range / no fuzzy hit    -> ask in text instead
- Always emit titles copied from the list, not the user's raw words.

QUERYING BRAIN DUMP
query_brain_dump returns the most recent entries (newest first). Both inputs
are optional.
- "what did I dump"                       -> no args (default limit 10)
- "show my last 20 brain dump entries"    -> limit=20
- "find brain dump entries about pricing" -> keyword="pricing"
- "show 5 recent dumps about racecraft"   -> limit=5, keyword="racecraft"
The doc isn't timestamped — temporal qualifiers ("last week", "recent") are
NOT supported. Drop them and use limit/keyword. If the user only specified a
date qualifier with no topic and no count, ask in text what they want.

DATES
- "tomorrow" -> {tomorrow}
- "Friday", "next Tuesday", etc -> compute the actual ISO date
- "in two weeks" -> add 14 days

CONTEXT JON EXPECTS YOU TO REMEMBER
- RaceCraft is the 2026 priority bet (50% of his time). Tasks tagged
  #racecraft matter most.
- No meetings before 9am or after 2pm. Max 3 meetings/day.
- Brain dump is for unprocessed thoughts. Tasks belong in Google Tasks.

When unsure, ask in text rather than guess.
"""


def _coerce_assistant_content(response_content) -> list[dict[str, Any]]:
    """Convert anthropic SDK content blocks into JSON-safe dicts for history."""
    out: list[dict[str, Any]] = []
    for b in response_content:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({
                "type": "tool_use",
                "id": b.id,
                "name": b.name,
                "input": dict(b.input),
            })
    return out


def parse_intent(
    messages: list[dict[str, Any]],
    creds=None,
) -> dict[str, Any]:
    """Phase 5: conversational parse.

    Caller passes the full Anthropic-API-shaped messages list, including the
    current user turn as the last entry. The bot is responsible for merging
    any prior turn's tool_result blocks into that user message's content (the
    API requires tool_result to immediately follow tool_use).

    Returns:
      {
        "text_reply":        str,           # Haiku's prose (may be '')
        "tool_calls":        list[dict],    # [{id, name, input}, ...] (may be [])
        "assistant_message": dict,          # {role: "assistant", content: [...]} for history
      }

    If creds is provided, a snapshot of currently-open Google Tasks is spliced
    into the system prompt so Haiku can resolve numeric references, "all open"
    requests, and fuzzy/Whisper-garbled titles against real data.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set in .env. See PHASE3A-SETUP.md for the 4-step walkthrough."
        )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tz = ZoneInfo(TIMEZONE)
    today_local = datetime.now(tz).date()
    tomorrow_iso = date.fromordinal(today_local.toordinal() + 1).isoformat()

    system = SYSTEM_PROMPT.format(
        today=today_local.isoformat(),
        weekday=today_local.strftime("%A"),
        tz=TIMEZONE,
        tomorrow=tomorrow_iso,
        open_tasks_section=_render_open_tasks_block(creds),
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=system,
        tools=INTENT_TOOLS,
        tool_choice={"type": "auto"},
        messages=messages,
    )

    assistant_content = _coerce_assistant_content(response.content)

    text_reply = "\n".join(
        b["text"].strip() for b in assistant_content if b["type"] == "text"
    ).strip()

    tool_calls = [
        {"id": b["id"], "name": b["name"], "input": b["input"]}
        for b in assistant_content
        if b["type"] == "tool_use"
    ]

    log.info(
        "parsed: text=%r tools=%s",
        text_reply[:80],
        [(t["name"], list(t["input"].keys())) for t in tool_calls],
    )

    return {
        "text_reply": text_reply,
        "tool_calls": tool_calls,
        "assistant_message": {"role": "assistant", "content": assistant_content},
    }



"""Intent parser — uses Claude tool-use to convert natural language into a structured intent.

We use tool_choice={"type": "any"} to force Claude to pick exactly one of:
  - add_task
  - append_to_brain_dump
  - complete_task
  - edit_task
  - query_brain_dump
  - needs_clarification

"Ask, don't guess" is implemented by encouraging Claude to call needs_clarification
whenever the message is ambiguous, rather than guessing.
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
    {
        "name": "needs_clarification",
        "description": (
            "Use when the user's intent is unclear or ambiguous. Better to ask than to guess. "
            "Examples: a single noun with no verb, a vague phrase, or two plausible interpretations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interpretation": {
                    "type": "string",
                    "description": "Your best-guess paraphrase of what the user might mean.",
                },
                "question_for_user": {
                    "type": "string",
                    "description": "A short clarifying question (one sentence).",
                },
            },
            "required": ["interpretation", "question_for_user"],
        },
    },
]


SYSTEM_PROMPT = """You are a productivity agent's intent parser.

Today is {today} ({weekday}), timezone {tz}.{open_tasks_section}

Your job: pick exactly ONE of the four tools and fill it in based on the user's message.

Routing rules:
- "add a task to X" / "remind me to X" / "I should X" / "todo: X"  -> add_task
- "X is done" / "mark X complete" / "finished X" / "done with X"   -> complete_task
- "push / reschedule / rename / bump / snooze / tag X"             -> edit_task
- "note that X" / "thinking about X" / longer brain-dumps          -> append_to_brain_dump
- "what did I dump" / "show recent dumps" / "find dumps about X"   -> query_brain_dump
- ambiguous / single noun / no clear verb                          -> needs_clarification

Multi-task completion:
complete_task takes title_queries as a LIST. When the user lists multiple things
to complete in one message, split them into separate entries. Examples:
- "done with the deck and the budget"             -> ["deck", "budget"]
- "X, Y, and Z are all complete"                  -> ["X", "Y", "Z"]
- "mark these complete: call Alex, send invoice"  -> ["call Alex", "send invoice"]
- "finished the pitch deck"                       -> ["pitch deck"]   (1-element list)
Single-task input always uses a 1-element list, never a bare string.

Multi-task add:
add_task takes a "tasks" LIST. When the user mentions multiple things to add in
one message, split them into separate entries. Each entry gets its own due date
resolved independently — "tomorrow" attached to one task doesn't apply to others
unless the user said so.
- "add buy milk and call Lisa"                       -> 2 tasks
- "remind me to email Whistler tomorrow"             -> 1 task (1-element list)
- "add three: deck draft, expense report, gym"       -> 3 tasks
- "add finalize pitch deck (high impact, racecraft)" -> 1 task with impact=5, racecraft=true
Single-task input always uses a 1-element list, never a bare object.

Editing tasks:
edit_task takes an "edits" LIST. Each edit targets ONE task and sets any subset
of these optional fields: new_title, new_due, new_impact, new_racecraft,
snooze_until. Set ONLY the fields the user mentioned — others stay None. At
least one edit field per edit.
- "push the Whistler email to next Tuesday"   -> [{{target_query: "...", new_due: <Tue ISO>}}]
- "rename buy milk to buy oat milk"           -> [{{target_query: "Buy milk", new_title: "Buy oat milk"}}]
- "bump the deck to impact 5"                 -> [{{target_query: "...deck...", new_impact: 5}}]
- "tag the Whistler call as racecraft"        -> [{{target_query: "...", new_racecraft: true}}]
- "snooze gym until Friday"                   -> [{{target_query: "...gym...", snooze_until: <Fri ISO>}}]
- "push X and Y to Tuesday"                   -> 2 edits, same new_due
- "rename A to B and bump impact to 5"        -> 1 edit with BOTH new_title and new_impact
Single-edit input always uses a 1-element list, never a bare object.
target_query follows the same snapshot-verbatim rule as complete_task: when an
open-tasks list is shown above, copy the matching title exactly so downstream
fuzzy-matching is trivial.
Snoozed tasks (with a future snoozed_until in their notes) do NOT appear in the
snapshot. If the user asks to unsnooze something you can't see, prefer
needs_clarification.

Resolving completions:
When a "Currently open tasks" list is shown above, treat it as your source of truth.
- Numeric references ("1, 3 done", "complete 2 and 4")
    -> look up those numbers in the list, emit title_queries = the resolved titles
- "all open done" / "everything complete" / "all of those are done"
    -> emit title_queries = every title in the list
- Fuzzy / partial / Whisper-garbled references ("the milk thing", "by milk", "whistler one")
    -> fuzzy-match against the listed titles, emit the closest title verbatim
- If a number is out of range or nothing fuzzy-matches, prefer needs_clarification.
- Always emit titles copied from the list, not the user's raw words. This makes
  downstream matching trivial.
If no list is shown, just echo the user's words into title_queries; downstream
code will fuzzy-match.

Querying brain dump:
query_brain_dump returns the most recent entries from the Active section of the
Brain Dump doc (newest first). Both inputs are optional.
- "what did I dump"                          -> no args (uses default limit 10)
- "show my last 20 brain dump entries"       -> limit=20
- "find brain dump entries about whistler"   -> keyword="whistler"
- "show recent dumps about pricing"          -> keyword="pricing"
- "show 5 recent dumps about racecraft"      -> limit=5, keyword="racecraft"
The doc isn't timestamped, so temporal qualifiers like "last week", "yesterday",
"recent" are NOT supported. Drop them and use limit/keyword. If the user only
specified a temporal qualifier with no keyword and no count, prefer
needs_clarification to ask what topic or how many entries they want.

Date conversion:
- "tomorrow" -> {tomorrow}
- "Friday", "next Tuesday", etc -> compute the actual ISO date
- "in two weeks" -> add 14 days

When unsure, prefer needs_clarification over guessing. The user prefers being asked over being wrong.
"""


def parse_intent(user_text: str, creds=None) -> dict[str, Any]:
    """Return {'name': str, 'input': dict} for the chosen tool.

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
    tomorrow = (today_local.toordinal() + 1)
    tomorrow_iso = date.fromordinal(tomorrow).isoformat()

    system = SYSTEM_PROMPT.format(
        today=today_local.isoformat(),
        weekday=today_local.strftime("%A"),
        tz=TIMEZONE,
        tomorrow=tomorrow_iso,
        open_tasks_section=_render_open_tasks_block(creds),
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=system,
        tools=INTENT_TOOLS,
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": user_text}],
    )

    for block in response.content:
        if block.type == "tool_use":
            log.info("parsed intent: %s input=%s", block.name, block.input)
            return {"name": block.name, "input": dict(block.input)}

    raise RuntimeError(
        f"Claude didn't call a tool. stop_reason={response.stop_reason} content={response.content}"
    )

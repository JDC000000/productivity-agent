"""Read + write the Brain Dump Google Doc.

Convention (locked 2026-04-23):
- The doc is structured with Heading 2 sections.
- Section "Active" holds open tasks; agent reads it for /brief.
- Sections "Done" and "Backlog" are ignored by /brief.
- Bullet items under Active are tasks (one per line).

Parser is forgiving: matches headings by either `namedStyleType` (real Heading 2)
OR text starting with `#` (Markdown-style typed by hand). Bullets match either
the doc's bullet list or lines starting with `- ` / `* ` / `• `.

Phase 3i adds a 30s read cache (mirrors the parser's task cache) so back-to-back
query_brain_dump calls don't hammer the Docs API. append_to_active invalidates
the cache for the doc_id it touched so newly-dumped notes are immediately
visible to the next query.
"""
from __future__ import annotations

import time
from typing import Any

from googleapiclient.discovery import build


# Cache key: (id(creds), doc_id). Value: (monotonic_ts, items_in_doc_order).
# id(creds) survives in-place token refresh; full re-auth lands a new key.
_DOC_CACHE: dict[tuple[int, str], tuple[float, list[str]]] = {}
_DOC_CACHE_TTL_SECONDS = 30


# ---------------- internal helpers ----------------

def _para_text(paragraph: dict) -> str:
    return "".join(
        e.get("textRun", {}).get("content", "")
        for e in paragraph.get("elements", [])
    ).rstrip("\n").strip()


def _is_heading(paragraph: dict, text: str) -> bool:
    style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
    if style.startswith("HEADING"):
        return True
    return text.startswith("#")


def _is_bullet(paragraph: dict, text: str) -> bool:
    if "bullet" in paragraph:
        return True
    return text.startswith(("- ", "* ", "• "))


# ---------------- READ ----------------

def get_active_items(creds, doc_id: str) -> list[str]:
    """Return cleaned bullet text from under the Active heading."""
    service = build("docs", "v1", credentials=creds, cache_discovery=False)
    doc = service.documents().get(documentId=doc_id).execute()

    items: list[str] = []
    in_active = False

    for element in doc.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue

        text = _para_text(para)
        if not text:
            continue

        # Bullets win over headings: when append_to_active inserts new text
        # right after the "Active" heading, Google Docs inherits HEADING_2 on
        # the new paragraph. So text like "- foo" can have namedStyleType
        # HEADING_2 while still being a bullet by intent. Trust the text shape
        # over the style for bullet markers.
        if _is_bullet(para, text):
            if in_active:
                cleaned = text.lstrip("-*• ").strip()
                if cleaned:
                    items.append(cleaned)
            continue

        if _is_heading(para, text):
            heading_text = text.lstrip("#").strip().lower()
            in_active = heading_text == "active"
            continue

    return items


def _get_active_items_cached(creds, doc_id: str) -> list[str]:
    """Cached underlying fetch. Returns the full Active list in document order
    (oldest first, newest last). Public callers should prefer
    get_recent_active_items which handles the reverse + truncate.
    """
    key = (id(creds), doc_id)
    now = time.monotonic()
    cached = _DOC_CACHE.get(key)
    if cached and (now - cached[0]) < _DOC_CACHE_TTL_SECONDS:
        return cached[1]

    items = get_active_items(creds, doc_id)
    _DOC_CACHE[key] = (now, items)
    return items


def get_recent_active_items(
    creds, doc_id: str, limit: int | None = None
) -> list[str]:
    """Phase 3i: return Active bullets newest-first, optionally truncated.

    Goes through the 30s cache in _get_active_items_cached. Pass limit=None
    to get all entries (caller does its own filtering/slicing). Pass an int to
    cap the result.
    """
    items = list(reversed(_get_active_items_cached(creds, doc_id)))
    if limit is None:
        return items
    return items[:max(0, int(limit))]


# ---------------- WRITE ----------------

def append_to_active(creds, doc_id: str, text: str) -> None:
    """Append a new bullet line under the Active heading.

    Strategy: walk the doc, find the END of the Active section (either right
    after the heading if no bullets yet, or after the last bullet), and insert
    a new line there.

    The new line is prefixed with "- " so the parser sees it as a bullet even
    if Google doesn't auto-style it. Visual styling will be plain text in the
    doc; if Jon wants it bulleted-looking he can highlight and click bullet.
    """
    service = build("docs", "v1", credentials=creds, cache_discovery=False)
    doc = service.documents().get(documentId=doc_id).execute()

    insert_index: int | None = None
    in_active = False

    for element in doc.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue

        para_text = _para_text(para)
        end_index = element.get("endIndex")
        start_index = element.get("startIndex")

        # Bullets win over headings — Google Docs sometimes inherits HEADING_2
        # on inserted paragraphs, but a "- foo" line is still a bullet by
        # intent. Treat it as one and push insert_index past it so we land at
        # the actual end of the Active section.
        if _is_bullet(para, para_text):
            if in_active and end_index is not None:
                insert_index = end_index - 1
            continue

        if _is_heading(para, para_text):
            heading_text = para_text.lstrip("#").strip().lower()
            if heading_text == "active":
                in_active = True
                if end_index is not None:
                    insert_index = end_index - 1
                continue
            if in_active:
                # Hit the next non-bullet heading after Active; insert before its start.
                if start_index is not None and start_index > 1:
                    insert_index = start_index - 1
                break
            in_active = False
            continue

        # Plain paragraph inside Active (not a bullet, not a heading) — track
        # its end so we land after it if Active has only free-text lines.
        if in_active and end_index is not None:
            insert_index = end_index - 1

    if insert_index is None or insert_index < 1:
        raise RuntimeError(
            "Couldn't find 'Active' heading in Brain Dump doc. "
            "Make sure 'Active' is styled as Heading 2 (Format → Paragraph styles → Heading 2)."
        )

    # Insert leading newline + "- text" so it lands as its own paragraph.
    new_text = f"\n- {text}"
    service.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [
                {"insertText": {"location": {"index": insert_index}, "text": new_text}}
            ]
        },
    ).execute()

    # Phase 3i: invalidate the read cache for this doc so the next query sees
    # the freshly-appended bullet without waiting out the TTL.
    for k in list(_DOC_CACHE.keys()):
        if k[1] == doc_id:
            _DOC_CACHE.pop(k, None)

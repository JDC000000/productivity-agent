"""Config loader — reads environment from a local .env file.

Keep this simple: it's the single source of truth for secrets + paths.
If you add a new env var anywhere in the codebase, add it here too so
there's one obvious place to look when something's missing.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Loads .env from the current working directory (and parents).
# Values already set in the shell environment take precedence — handy for overrides.
load_dotenv()


def _require(key: str) -> str:
    """Fetch a required env var; fail loudly if missing."""
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"Missing required env var: {key} — check your .env file "
            f"(copy .env.example to .env and fill in values)"
        )
    return val


def _optional(key: str, default: str | None = None) -> str | None:
    val = os.getenv(key)
    return val if val else default


# --- Phase 1 (required) ---
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")

# If unset, bot replies to anyone who messages it (fine for first-run setup).
# After you see your chat_id via /chatid, set this to lock the bot to just you.
_raw_chat_id = _optional("TELEGRAM_ALLOWED_CHAT_ID")
TELEGRAM_ALLOWED_CHAT_ID: int | None = int(_raw_chat_id) if _raw_chat_id else None


# --- Phase 2 (required for /brief, but bot still boots without these) ---
GOOGLE_CREDENTIALS_PATH: Path = Path(
    _optional("GOOGLE_CREDENTIALS_PATH", "~/.productivity-agent/secrets/credentials.json") or ""
).expanduser()

GOOGLE_TOKEN_PATH: Path = Path(
    _optional("GOOGLE_TOKEN_PATH", "~/.productivity-agent/secrets/token.json") or ""
).expanduser()


def _extract_doc_id(value: str | None) -> str | None:
    """Accept either a bare doc ID or a full Google Docs URL; return just the ID.

    Handles:
      - "abc123XYZ"                                 -> "abc123XYZ"
      - "https://docs.google.com/document/d/abc123XYZ/edit?tab=t.0"  -> "abc123XYZ"
      - "abc123XYZ/edit?tab=t.0"                    -> "abc123XYZ"
    """
    if not value:
        return None
    if "/document/d/" in value:
        value = value.split("/document/d/", 1)[1]
    # Trim anything after the ID (slashes, query strings, fragments).
    return value.split("/")[0].split("?")[0].split("#")[0]


BRAIN_DUMP_DOC_ID: str | None = _extract_doc_id(_optional("BRAIN_DUMP_DOC_ID"))

TIMEZONE: str = _optional("TIMEZONE", "America/Vancouver") or "America/Vancouver"


# --- Phase 3a (required for free-text intent commands) ---
ANTHROPIC_API_KEY: str | None = _optional("ANTHROPIC_API_KEY")


# --- Runtime ---
LOG_LEVEL: str = _optional("LOG_LEVEL", "INFO") or "INFO"
LOG_PATH: Path = Path(
    _optional("LOG_PATH", "~/.productivity-agent/logs/agent.log") or ""
).expanduser()
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# Append-only audit log of every action the agent took.
ACTIONS_LOG_PATH: Path = LOG_PATH.parent / "actions.jsonl"

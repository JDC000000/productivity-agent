"""Google OAuth + credential management.

Phase 2 read+write scopes (locked 2026-04-23):
- calendar (full)
- tasks (full)
- documents (full)

Phase 3c added Telegram-driven re-auth via /reauth:
    build_consent_url()  -> bot sends URL to user
    complete_consent()   -> bot exchanges pasted code for tokens

First-time setup (CLI fallback):
    python -m src.google_auth setup

Bot startup uses get_credentials() to load + auto-refresh the saved token.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .config import GOOGLE_CREDENTIALS_PATH, GOOGLE_TOKEN_PATH

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/documents",
]


def get_credentials() -> Credentials:
    """Load stored credentials, refreshing if expired. Raise if not yet authorized."""
    token_path: Path = GOOGLE_TOKEN_PATH
    if not token_path.exists():
        raise RuntimeError(
            f"No Google token at {token_path}.\n"
            "Run setup first:  ./.venv/bin/python -m src.google_auth setup"
        )

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            log.info("Refreshing expired Google access token...")
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        else:
            raise RuntimeError(
                "Google credentials invalid or revoked. Re-run:\n"
                "  ./.venv/bin/python -m src.google_auth setup"
            )
    return creds


# ---------------- Phase 3c: Telegram-driven re-auth ----------------

def build_consent_url() -> tuple[str, InstalledAppFlow]:
    """Return (auth_url, flow) for a Telegram-driven OAuth handshake.

    The bot sends auth_url to the user, who opens it (possibly on a phone),
    completes Google's consent screen, and pastes the resulting code (or full
    redirect URL) back into chat. Bot then calls complete_consent(flow, ...).

    redirect_uri is http://localhost (no port). On a phone, the browser will
    fail to connect after consent, but the URL bar shows ?code=... — the user
    copies that URL and sends it to the bot. We extract the code from the URL.

    The returned `flow` is stateful (holds PKCE code_verifier) and must be the
    same instance passed to complete_consent.
    """
    cred_path: Path = GOOGLE_CREDENTIALS_PATH
    if not cred_path.exists():
        raise RuntimeError(
            f"Missing OAuth client credentials at {cred_path}. "
            "See PHASE2-SETUP.md to download the OAuth client JSON."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), SCOPES)
    flow.redirect_uri = "http://localhost"
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        prompt="consent",  # force re-consent so we always get a refresh_token
    )
    return auth_url, flow


def complete_consent(flow: InstalledAppFlow, code_or_url: str) -> None:
    """Exchange the user-pasted code (or full localhost redirect URL) for
    tokens, then save them to GOOGLE_TOKEN_PATH. Caller is responsible for
    handling exceptions (bad code, network error, etc.).
    """
    code = _extract_code(code_or_url)
    flow.fetch_token(code=code)

    token_path: Path = GOOGLE_TOKEN_PATH
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(flow.credentials.to_json())
    log.info("Saved Google token to %s (via /reauth)", token_path)


def _extract_code(s: str) -> str:
    """Pull the OAuth code out of either a bare code string or a redirect URL.

    Accepts:
      - "4/0AeoWuM..."                                  -> "4/0AeoWuM..."
      - "http://localhost?state=...&code=4/0Ae...&..."  -> "4/0Ae..."
      - "http://localhost/?code=4/0Ae..."               -> "4/0Ae..."
    """
    s = s.strip()
    if s.startswith("http"):
        params = parse_qs(urlparse(s).query)
        code = params.get("code", [None])[0]
        if not code:
            raise ValueError(
                "Pasted URL has no 'code' parameter — "
                "make sure you copied the URL after Google's consent screen."
            )
        return code
    return s


def run_oauth_setup() -> None:
    """Interactive OAuth — opens a browser, captures consent, saves token.json."""
    cred_path: Path = GOOGLE_CREDENTIALS_PATH
    token_path: Path = GOOGLE_TOKEN_PATH

    if not cred_path.exists():
        raise RuntimeError(
            f"Missing OAuth client credentials at {cred_path}.\n"
            "Steps to fix:\n"
            "  1. Go to https://console.cloud.google.com/apis/credentials\n"
            "  2. Create an OAuth client ID (type: Desktop app)\n"
            "  3. Download the JSON\n"
            f"  4. Move it to {cred_path}\n"
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)

    flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json())

    log.info("Saved Google token to %s", token_path)
    print(f"\n[OK] Google OAuth complete. Token saved to {token_path}")
    print("You can now restart the bot with ./run.sh")


def main() -> None:
    """CLI entry: `python -m src.google_auth setup`"""
    from .logger import setup_logging

    setup_logging()

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        run_oauth_setup()
    else:
        print("Usage: python -m src.google_auth setup")
        sys.exit(1)


if __name__ == "__main__":
    main()

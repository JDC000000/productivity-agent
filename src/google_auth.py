"""Google OAuth + credential management.

Phase 2 read+write scopes (locked 2026-04-23):
- calendar (full)
- tasks (full)
- documents (full)

First-time setup:
    python -m src.google_auth setup

Bot startup uses get_credentials() to load + auto-refresh the saved token.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

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

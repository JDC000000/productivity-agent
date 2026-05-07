#!/bin/bash
# One-command launcher for the productivity agent.
# - First run creates a virtualenv and installs deps.
# - Every run syncs requirements (idempotent; fast when already satisfied).
# Usage: ./run.sh
set -euo pipefail

cd "$(dirname "$0")"

# Create virtualenv on first run
if [ ! -d ".venv" ]; then
  echo "First run: creating virtual environment..."
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
fi

# Sync deps every run — fast no-op when nothing changed.
echo "Syncing dependencies..."
./.venv/bin/pip install -q -r requirements.txt

# Run the bot. `exec` replaces the shell so Ctrl+C stops Python cleanly.
exec ./.venv/bin/python -m src.main

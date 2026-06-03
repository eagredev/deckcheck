#!/bin/bash
# Launches the deckcheck GUI using the bundled venv's Python.
# This is what the .desktop launcher calls, the user never sees a terminal.
set -e
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
VENV_PY="$REPO_DIR/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  # No venv yet, try to set it up (needs network the first time).
  echo "First run: setting up deckcheck's environment..."
  "$REPO_DIR/setup.sh" || {
    command -v kdialog >/dev/null && kdialog --error \
      "deckcheck needs a one-time setup that requires internet. Open a terminal in this folder and run ./setup.sh"
    exit 1
  }
fi

# Run from the gui/ dir so 'import checks' resolves.
cd "$SCRIPT_DIR"
exec "$VENV_PY" deckcheck_gui.py "$@"

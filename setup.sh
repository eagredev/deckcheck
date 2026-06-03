#!/bin/bash
# One-time setup for the deckcheck GUI.
# Creates a local Python venv and installs PySide6 into it. The CLI scripts in
# scripts/ need none of this, they're plain bash. This is only for the GUI.
#
# SteamOS note: the OS filesystem is immutable, so we install into a venv inside
# the repo rather than system-wide. Nothing is installed outside this folder.
set -e
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$REPO_DIR"

echo "Creating Python virtual environment in .venv/ ..."
python3 -m venv .venv

echo "Installing PySide6 (Qt for Python), this downloads ~100MB the first time..."
.venv/bin/pip install --quiet --disable-pip-version-check --upgrade pip
.venv/bin/pip install --quiet --disable-pip-version-check PySide6

echo ""
echo "Done. Launch the GUI with:  ./gui/run_gui.sh"
echo "Or install the desktop launcher with:  ./install-launcher.sh"

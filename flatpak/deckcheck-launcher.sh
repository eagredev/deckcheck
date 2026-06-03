#!/bin/bash
# Flatpak entry point for deckcheck. Runs the GUI with the runtime's python3
# (PySide6 was installed into the app prefix at build time).
exec python3 /app/share/deckcheck/deckcheck_gui.py "$@"

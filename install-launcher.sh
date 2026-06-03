#!/bin/bash
# Installs a desktop launcher so deckcheck appears as a clickable icon in the
# application menu / app grid, no terminal needed to start it afterwards.
set -e
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APPS_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons/hicolor"
mkdir -p "$APPS_DIR"

# Install icons into the user's hicolor theme so the launcher/taskbar pick the right
# size automatically (we install by theme name "deckcheck", not a hardcoded file path).
for size in 16 24 32 48 64 128 256; do
  src="$REPO_DIR/gui/icons/${size}x${size}/deckcheck.png"
  if [ -f "$src" ]; then
    mkdir -p "$ICONS_DIR/${size}x${size}/apps"
    cp "$src" "$ICONS_DIR/${size}x${size}/apps/deckcheck.png"
  fi
done
# Scalable SVG variant too.
mkdir -p "$ICONS_DIR/scalable/apps"
cp "$REPO_DIR/gui/deckcheck.svg" "$ICONS_DIR/scalable/apps/deckcheck.svg"

DESKTOP="$APPS_DIR/deckcheck.desktop"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=deckcheck
Comment=A friendly health check for your Steam Deck
Exec=$REPO_DIR/gui/run_gui.sh
Icon=deckcheck
Terminal=false
Categories=System;Utility;Monitor;
EOF
chmod +x "$DESKTOP"

# Refresh caches if the tools are available (all harmless if missing).
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$ICONS_DIR" 2>/dev/null || true

echo "Installed launcher: $DESKTOP"
echo "Installed icons into: $ICONS_DIR"
echo "deckcheck should now appear in your application menu (under System/Utilities)."
echo "To remove it later:  rm \"$DESKTOP\"  and the deckcheck.png/svg under $ICONS_DIR"

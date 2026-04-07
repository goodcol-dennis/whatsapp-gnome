#!/bin/bash
# Install the WhatsApp desktop entry and make the app launchable from GNOME

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill running instance if any
pkill -f "$SCRIPT_DIR/whatsapp.py" 2>/dev/null && echo "Stopped running instance." || true

# Make the app executable
chmod +x "$SCRIPT_DIR/whatsapp.py"

# Install icon into hicolor theme
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$ICON_DIR"
cp "$SCRIPT_DIR/whatsapp.svg" "$ICON_DIR/whatsapp.svg"
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null || true

# Install desktop entry
cp "$SCRIPT_DIR/whatsapp.desktop" ~/.local/share/applications/whatsapp.desktop

# Update desktop database
update-desktop-database ~/.local/share/applications 2>/dev/null || true

# Relaunch the app
echo "Relaunching WhatsApp..."
nohup "$SCRIPT_DIR/whatsapp.py" > /dev/null 2>&1 &
disown

echo "Done! WhatsApp is running and available in your GNOME app launcher."

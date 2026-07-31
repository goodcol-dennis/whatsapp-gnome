#!/bin/bash
# Install the WhatsApp desktop entry and make the app launchable from GNOME

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill running instance if any. Match the interpreter invocation rather than a
# bare path, so a moved checkout still stops without matching unrelated shells.
pkill -f "python3? .*/whatsapp\.py" 2>/dev/null && echo "Stopped running instance." || true

# Make the app executable
chmod +x "$SCRIPT_DIR/whatsapp.py"

# Install icon into hicolor theme
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$ICON_DIR"
cp "$SCRIPT_DIR/whatsapp.svg" "$ICON_DIR/whatsapp.svg"
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null || true

# Install desktop entry, rewriting Exec/Icon to this checkout's location.
# Filename MUST match the app id (com.local.WhatsApp) or GNOME rejects every
# Gio.Notification sent by the app.
mkdir -p ~/.local/share/applications
sed -e "s|^Exec=.*|Exec=$SCRIPT_DIR/whatsapp.py|" \
    -e "s|^Icon=.*|Icon=$SCRIPT_DIR/whatsapp.svg|" \
    "$SCRIPT_DIR/com.local.WhatsApp.desktop" > ~/.local/share/applications/com.local.WhatsApp.desktop
# Purge the stale pre-rename entry
rm -f ~/.local/share/applications/whatsapp.desktop

# Update desktop database
update-desktop-database ~/.local/share/applications 2>/dev/null || true

# Relaunch the app
echo "Relaunching WhatsApp..."
setsid nohup "$SCRIPT_DIR/whatsapp.py" > /dev/null 2>&1 < /dev/null &
disown

echo "Done! WhatsApp is running and available in your GNOME app launcher."

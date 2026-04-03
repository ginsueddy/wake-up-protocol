#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.ginsueddy.wake-up-protocol"
PLIST_SRC="$SCRIPT_DIR/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

uninstall() {
    echo "Uninstalling Wake Up Protocol..."
    launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "Done. LaunchAgent removed."
}

install() {
    echo "=== Wake Up Protocol Installer ==="
    echo

    # Compile Swift trigger
    echo "Compiling wake_trigger..."
    swiftc -O "$SCRIPT_DIR/wake_trigger.swift" -o "$SCRIPT_DIR/wake_trigger"
    echo "  Built: $SCRIPT_DIR/wake_trigger"

    # Create logs directory
    mkdir -p "$SCRIPT_DIR/logs"

    # Unload existing agent if present
    launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true

    # Install plist
    ln -sf "$PLIST_SRC" "$PLIST_DST"
    echo "  Linked: $PLIST_DST"

    # Load agent
    launchctl bootstrap "gui/$UID_NUM" "$PLIST_DST"
    echo "  LaunchAgent loaded."

    # Check for DND shortcut
    if shortcuts list 2>/dev/null | grep -qx "Enable DND"; then
        echo "  Found 'Enable DND' shortcut ✓"
    else
        echo
        echo "⚠  OPTIONAL: Create an 'Enable DND' shortcut to auto-silence notifications."
        echo "   1. Open Shortcuts.app"
        echo "   2. Create a new shortcut named exactly: Enable DND"
        echo "   3. Add the 'Set Focus' action"
        echo "   4. Configure it to turn ON 'Do Not Disturb'"
        echo "   5. Set duration to 'Until I turn it off'"
    fi

    echo
    echo "=== Installed successfully ==="
    echo
    echo "The wake trigger is now running. It will listen for a double-clap"
    echo "for 2 minutes every time you wake or unlock your computer."
    echo
    echo "Reminders:"
    echo "  - Terminal.app needs Microphone access in:"
    echo "    System Settings > Privacy & Security > Microphone"
    echo "  - Terminal.app needs Accessibility access in:"
    echo "    System Settings > Privacy & Security > Accessibility"
    echo
    echo "Logs: $SCRIPT_DIR/logs/trigger.log"
    echo "Uninstall: $0 uninstall"
}

case "${1:-install}" in
    uninstall) uninstall ;;
    install)   install ;;
    *)         echo "Usage: $0 [install|uninstall]"; exit 1 ;;
esac

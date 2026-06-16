#!/usr/bin/env bash
# uninstall-auto.command
# Removes the launchd task for the automatic backup. Double-click to uninstall.

LABEL="com.respaldos-llms.backup"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "== Uninstall automatic backup =="
if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null
  rm -f "$PLIST"
  echo "✓ Uninstalled. The backup no longer runs on its own."
  echo "  (You can still run it by hand with update-backup.command whenever you want.)"
else
  echo "It wasn't installed (couldn't find $PLIST)."
fi
echo ""
read -r -p "Done. Press Enter to close."

#!/usr/bin/env bash
# install-auto.command
# Installs (or reinstalls) the launchd task that runs the backup every day at noon.
# Double-click to install. The base is the folder where this file lives.

cd "$(dirname "$0")" || exit 1
BASE="$(pwd)"
SCRIPT="$BASE/update-backup.sh"
LABEL="com.respaldos-llms.backup"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "== Install daily automatic backup (12:00) =="
echo "Base folder: $BASE"

if [ ! -f "$SCRIPT" ]; then
  echo "ERROR: can't find update-backup.sh in this folder."
  read -r -p "Press Enter to close."; exit 1
fi
chmod +x "$SCRIPT"

mkdir -p "$HOME/Library/LaunchAgents"

# Write the .plist. RunAtLoad=false so it doesn't run on install; StartCalendarInterval = 12:00.
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$SCRIPT</string>
        <string>$BASE</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>12</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$BASE/.sync-state/launchd-out.log</string>
    <key>StandardErrorPath</key>
    <string>$BASE/.sync-state/launchd-err.log</string>
</dict>
</plist>
PLISTEOF

mkdir -p "$BASE/.sync-state"

# reload: unload if already present, then load again
launchctl unload "$PLIST" 2>/dev/null
if launchctl load "$PLIST" 2>/dev/null; then
  echo "✓ Installed. The backup will run every day at 12:00 (or when the Mac wakes up if it was asleep)."
  echo "  To see it working: open the viewer and check the run-history panel, or look at $BASE/.sync-state/log.json"
else
  echo "There was a problem loading the task. macOS may ask for Full Disk Access."
  echo "System Settings → Privacy & Security → Full Disk Access → add 'bash' or Terminal."
fi
echo ""
read -r -p "Done. Press Enter to close."

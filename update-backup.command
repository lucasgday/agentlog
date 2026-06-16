#!/usr/bin/env bash
# Double-click to update the conversation backup.
# This wrapper runs update-backup.sh from the same folder.
cd "$(dirname "$0")" || exit 1
./update-backup.sh
echo ""
echo "──────────────────────────────────────────"
read -r -p "Done. Press Enter to close this window."

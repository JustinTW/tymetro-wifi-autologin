#!/bin/zsh
# 移除 LaunchAgent。紀錄檔和 ~/Library/Caches/tymetro-wifi-autologin 留著，要清自己刪。
set -euo pipefail

LABEL=io.github.justintw.tymetro-wifi-autologin
launchctl bootout gui/$UID/$LABEL 2>/dev/null || true
rm -f ~/Library/LaunchAgents/$LABEL.plist
echo "已移除 $LABEL"

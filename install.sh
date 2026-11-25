#!/bin/zsh
# 安裝 LaunchAgent：登入 macOS 後常駐，連上 TyMetro-Train 就自動登入。
# 多餘的參數會傳給 tymetro_login.py --watch，例如連車站的 TyMetro 也要自動登入：
#   ./install.sh --ssid TyMetro-train --ssid TyMetro
set -euo pipefail

LABEL=io.github.justintw.tymetro-wifi-autologin
REPO=${0:A:h}
PY=${PYTHON:-$(command -v python3)}
PLIST=~/Library/LaunchAgents/$LABEL.plist
LOG=~/Library/Logs/tymetro-wifi-autologin.log

mkdir -p ~/Library/LaunchAgents ~/Library/Logs
"$PY" - "$PLIST" "$LABEL" "$LOG" "$PY" "$REPO/tymetro_login.py" --watch "$@" <<'EOF'
import plistlib, sys
plist, label, log, *args = sys.argv[1:]
with open(plist, "wb") as f:
    plistlib.dump({
        "Label": label,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }, f)
EOF

launchctl bootout gui/$UID/$LABEL 2>/dev/null || true
launchctl bootstrap gui/$UID "$PLIST"

echo "已安裝：$PLIST"
echo "Python：$PY"
echo "紀錄檔：$LOG"
echo "看狀態：launchctl print gui/$UID/$LABEL | grep -E 'state|pid'"

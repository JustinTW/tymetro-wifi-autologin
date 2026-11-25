#!/bin/zsh
# 錄下「人手動在 macOS 登入視窗點完整個流程」時的系統紀錄，給入口網站改版時分析用。
# 說明在 docs/maintenance/recording-portal-flow.md。
#
# 錄製期間 Mac 會離開原本的網路，AI agent 會斷線，所以一定要在背景跑：
#   nohup tools/record_portal.sh >/dev/null 2>&1 &
#
# 它會：暫停自動登入的 LaunchAgent → 開始錄 debug 等級的系統紀錄 → 等 Mac 連上 TyMetro 且需要登入
# → 存下閘道網址與狀態 → 等人點完、可以上網後再錄 20 秒 → 停止並恢復 LaunchAgent。最多跑 40 分鐘。
set -u

REPO=${0:A:h:h}
OUT=${1:-$REPO/captures/$(date +%Y%m%d-%H%M%S)}
LABEL=io.github.justintw.tymetro-wifi-autologin
PY=${PYTHON:-python3}
mkdir -p $OUT
exec >> $OUT/record.log 2>&1
echo "$(date +%T) 開始，輸出到 $OUT"

agent_was_loaded=0
if launchctl print gui/$UID/$LABEL >/dev/null 2>&1; then
  agent_was_loaded=1
  launchctl bootout gui/$UID/$LABEL && echo "$(date +%T) 已暫停自動登入"
fi

# debug 等級的紀錄（登入視窗載入的每個網址、WISPr 參數）只有 log stream 即時錄得到，事後用 log show 看不到。
# 注意 zsh 有內建的 log 指令，一定要用 /usr/bin/log。
/usr/bin/log stream --level debug --style compact --predicate '
  subsystem == "com.apple.captive" OR process == "captiveagent"
  OR process == "Captive Network Assistant"
  OR (subsystem == "com.apple.WebKit" AND (category == "Loading" OR category == "Network" OR category == "Process"))' \
  > $OUT/stream.log 2>&1 &
LOGPID=$!

cleanup() {
  kill $LOGPID 2>/dev/null
  if (( agent_was_loaded )); then
    launchctl bootstrap gui/$UID ~/Library/LaunchAgents/$LABEL.plist && echo "$(date +%T) 已恢復自動登入"
  fi
  echo "$(date +%T) 結束"
}
trap cleanup EXIT
trap 'exit 1' INT TERM

iface=$($PY -c "import sys; sys.path.insert(0, '$REPO'); import tymetro_login as t; print(t.wifi_iface())")
ssid() {
  $PY -c "import sys; sys.path.insert(0, '$REPO'); import tymetro_login as t; t.IFACE='$iface'; print(t.current_ssid() or '')"
}

phase=wait
deadline=$(( $(date +%s) + 2400 ))
while (( $(date +%s) < deadline )); do
  s=$(ssid)
  if [[ ${s:l} == tymetro* ]]; then
    st=$($PY $REPO/tymetro_login.py --status --iface $iface 2>&1)
    if [[ $phase == wait && $st == *需要登入* ]]; then
      print -r -- "$st" > $OUT/status.txt
      scutil <<< "show State:/Network/Interface/$iface/CaptiveNetwork" > $OUT/captive-state.txt
      { date; ifconfig $iface; scutil --dns | head -40; netstat -rn -f inet | head -20; } > $OUT/netstate.txt 2>&1
      phase=captive
      echo "$(date +%T) 連上 $s，需要登入，已存閘道網址；等人在登入視窗點完"
    elif [[ $phase == captive && $st == *可以上網* ]]; then
      echo "$(date +%T) 已經可以上網，再錄 20 秒"
      sleep 20
      exit 0
    fi
  fi
  sleep 3
done
echo "$(date +%T) 超過 40 分鐘，停止"

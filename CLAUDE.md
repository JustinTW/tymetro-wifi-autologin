# tymetro-wifi-autologin

macOS 上自動登入桃園捷運免費 Wi-Fi（TyMetro-Train / TyMetro）的工具。這個 Wi-Fi 用的是中華電信「看廣告上網」入口網站。LaunchAgent 常駐，偵測到連上目標 SSID，就照人手動點的流程（「我已同意」→ 等廣告 → 「按此開始免費上網」）完成開通，30 分鐘到期後重登。

## 結構

```
tymetro_login.py      唯一的程式。只用標準函式庫，單一檔案
install.sh            產生並載入 LaunchAgent（多餘參數會傳給 --watch，例如 --ssid）
uninstall.sh          卸載 LaunchAgent
tools/
  record_portal.sh    背景錄製：人手動點登入視窗時的 debug 系統紀錄 → captures/<時間>/
  extract_flow.py     從 stream.log 抽出流程時間線（--redact 遮掉個資）
docs/maintenance/
  recording-portal-flow.md   入口網站改版時的錄製、分析、更新步驟，含目前流程的對照表和已知的坑
captures/             錄製輸出，不進 git
```

`tymetro_login.py` 由上到下分成三段：

1. **Wi-Fi 網卡、SSID、DNS**：`wifi_iface()`、`current_ssid()`、`dns_servers()`、`dns_query()`、`resolve()`、`wifi_connection()`
2. **HTTP**：`make_opener()`、`fetch()`（`--dump` 時把每一步存檔）、`probe()`、`wait_for_network()`
3. **登入流程與模式**：`login()`（入口網站的每一步，有註解對應文件裡的表格）、`run_once()`、`watch()`（`network_events()`、`wait_event()`、`watch_check()`）、`status()`、`main()`

## 執行環境

- LaunchAgent label：`io.github.justintw.tymetro-wifi-autologin`，plist 在 `~/Library/LaunchAgents/`，直接執行 repo 裡的 `tymetro_login.py --watch`，所以 `git pull` 後要 `launchctl kickstart -k gui/$UID/io.github.justintw.tymetro-wifi-autologin`（或重跑 `./install.sh`）才會生效
- 紀錄檔：`~/Library/Logs/tymetro-wifi-autologin.log`。常駐模式在目標 Wi-Fi 上每次檢查都寫一行：`檢查（事件 IPv4 / 計時器 / 啟動）：可以上網 / 已登入，下次 HH:MM:SS`，失敗寫 `檢查（…）失敗：<原因>`，連上、離開目標 Wi-Fi 也會記
- `~/Library/Caches/tymetro-wifi-autologin/runs/<時間>/`（兩種都算在「留最近 10 份」裡）：
  - 每次登入的流程：`flow.txt`（第一行是閘道網址）+ 每頁 HTML
  - 檢查失敗時的網路狀態快照：`diag.txt`（每台 DNS 伺服器、系統 DNS 的查詢結果、DHCP 資訊、CaptiveNetwork 狀態、`scutil --dns`、路由）。連續失敗只存第一次
- `--trace`：在目標 Wi-Fi 上時用 `log stream --level debug` 錄 `com.apple.captive` 和 `com.apple.CaptiveNetworkAssistant`，存到 `~/Library/Caches/tymetro-wifi-autologin/traces/<時間>.log`，離開就停。可以直接丟給 `tools/extract_flow.py`
- 登入時會用 `osascript` 跳 macOS 通知（正在登入、已登入、失敗），因為 macOS 自己的登入視窗會同時跳出來、停在同意頁，使用者會以為沒反應而自己去按
- **常駐模式不輪詢**：`network_events()` 開一個 `scutil` 訂閱 `State:/Network/Interface/<網卡>/IPv4` 和 `.../CaptiveNetwork` 的變化，`watch()` 平常停在 `select()`，只有兩種情況會醒來：
  - 網路事件（連上、斷線、換網路、macOS 偵測到要登入）：讀 SSID，不是目標 Wi-Fi 就繼續睡，而且不設計時器
  - 計時器（只在目標 Wi-Fi 上才有，見 `next_check()`）：自己登入後睡到 30 分鐘到期（`SESSION`），到期後 10 分鐘內每 20 秒檢查一次直到重登；不知道何時登入的（例如手動登入）每 60 秒檢查一次；失敗 `RETRY` 秒（30）後重試
  - 車上換基地台時 Wi-Fi 會斷線幾秒再連回，閘道的登入狀態還在，所以離開目標 Wi-Fi 時不清 `login_at`

## 改程式前要知道的

- **還沒登入時，一般程式不能用這個 Wi-Fi**（macOS 把網卡優先順序設成 Never，沒有全域預設路由和 DNS）。所有連線都要經過 `wifi_connection()`：用 `IP_BOUND_IF` 綁在 Wi-Fi 網卡上，DNS 自己查 DHCP 給的伺服器。不要改用 `curl`、`requests` 或系統的 DNS。
- **SSID 從 `scutil` 的 `CachedScanRecord` 解出來**，因為 macOS 26 其他地方都遮掉了。
- **TLS**：`wifiplususer.cht.com.tw` 只接受 `AES128-SHA256`，所以要 `set_ciphers("DEFAULT:@SECLEVEL=1")`；LibreSSL 不認得時就用預設。
- **User-Agent 要用瀏覽器的**，閘道看到 macOS 偵測器的 UA 會回 WISPr XML，不會轉址。
- 其他細節（HSTS、手機熱點、WISPr、錄製的坑）在 `docs/maintenance/recording-portal-flow.md` 第 5 節。

## 慣例

- 只用 Python 標準函式庫，而且要能在 `/usr/bin/python3`（3.9 + LibreSSL）和 Homebrew `python3` 上跑。兩個都要測。
- 給使用者看的訊息、註解、文件都用繁體中文。
- 不要 commit `captures/`、`runs/`、`--dump` 的輸出：裡面有 MAC 位址、IP，以及開通表單的一次性帳號密碼。要把網址寫進文件時，先用 `tools/extract_flow.py --redact` 遮掉。
- 不要印出開通表單的 `password` 欄位。

## 測試

登入前的狀態 agent 自己沒有網路，沒辦法直接測。能測的方法：

```sh
python3 tymetro_login.py --status        # SSID、DNS、可以上網或閘道網址
python3 tymetro_login.py --start '<閘道網址>' --dump /tmp/replay
/usr/bin/python3 tymetro_login.py --start '<閘道網址>'
```

`--start` 要在 TyMetro 的 Wi-Fi 上跑（登入前或登入後都可以），看到 `開通成功（Login success）` 才算過。閘道網址從最新的 `runs/*/flow.txt` 第一行拿，或從 `captures/*/status.txt` 拿。

## 入口網站改版

照 `docs/maintenance/recording-portal-flow.md` 做：先看 runs 紀錄、用閘道網址重播，不行才請使用者配合錄製。改完要更新該文件第 4 節的流程表。

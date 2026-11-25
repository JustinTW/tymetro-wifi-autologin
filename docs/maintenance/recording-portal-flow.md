# 入口網站改版時：錄製流程並更新 script

給 AI agent 看的維護手冊。中華電信的登入頁改版，`tymetro_login.py` 就會失敗，照這份文件找出改了哪一步，再更新程式。

整份文件的前提：**登入前的那段流程，agent 本身沒有網路**（agent 跟 Mac 用同一個 Wi-Fi，還沒登入就連不到 API）。凡是要在「還沒登入」的狀態下觀察的東西，都要寫成背景程式先跑起來，請使用者操作，等網路恢復後再分析。

## 0. 先判斷要不要錄

依序試，能解決就不用往下：

1. **看自動登入留下的紀錄**（不用使用者配合）
   - `~/Library/Logs/tymetro-wifi-autologin.log`：每次登入的步驟和失敗原因，例如「沒有看到「我已同意」頁，入口網站可能改版了」。
   - `~/Library/Caches/tymetro-wifi-autologin/runs/<時間>/`：常駐模式每次登入都會存一份。`flow.txt` 第一行是閘道網址，後面是每個 request 和轉址；`NN-*.html` 是每一步拿到的頁面。最後一個檔案通常就是改版的那一頁。
2. **用閘道網址重播**（要在 TyMetro 的 Wi-Fi 上，登入前或登入後都可以）
   ```sh
   python3 tymetro_login.py --start '<flow.txt 第一行的 gateway 網址>' --dump /tmp/replay
   ```
   入口網站的伺服器都在公開網路上，登入後也連得到；閘道對已登入的裝置再開通一次也會回 `Login success`。所以使用者在車上手動登入後，agent 就能反覆重播、改程式、再重播，不必一直斷線。
   閘道網址裡有 `client_ip`，離開 TyMetro 後重播，最後的開通應該會失敗（沒驗證過），前面的頁面還是抓得到。
3. 前兩步看不出新流程怎麼走時（例如按鈕改成 JavaScript 動態產生網址），才需要**錄製人手動點的過程**，見下一節。

## 1. 錄製（需要使用者配合）

`tools/record_portal.sh` 會在背景錄下 macOS 登入視窗載入的每一個網址。agent 準備好後，把下面的步驟給使用者照做：

1. 確認 repo 是最新的，然後請使用者在**另一個終端機分頁**執行：
   ```sh
   nohup ~/workspace/github/JustinTW/tymetro-wifi-autologin/tools/record_portal.sh >/dev/null 2>&1 &
   ```
   它會自動暫停自動登入的 LaunchAgent，結束時再恢復。
2. **手機熱點要關掉**，或是在 Mac 的 Wi-Fi 設定裡把手機熱點的「自動加入」關掉。否則 macOS 偵測到 TyMetro 要登入時，會把 TyMetro 暫時封鎖、改連熱點，連登入視窗都不會開。
3. Mac 連上 TyMetro-Train，在跳出的登入視窗裡**手動把流程點完**（目前是兩個按鈕：「我已同意」，以及廣告頁的「按此開始免費上網」）。
4. 可以上網後，錄製程式會再錄 20 秒，然後自動停止。請使用者回來告訴 agent。

如果 Mac 在這 30 分鐘內已經登入過，閘道不會再要求登入，錄製程式會一直等。這時要等原本的 30 分鐘到期再連。

輸出在 `captures/<時間>/`（已列在 `.gitignore`）：

| 檔案 | 內容 |
|---|---|
| `stream.log` | debug 等級的系統紀錄：configd、captiveagent、登入視窗、WebKit |
| `status.txt` | 剛連上時 `tymetro_login.py --status` 的輸出，包含閘道網址 |
| `captive-state.txt` | `scutil` 裡的 CaptiveNetwork 狀態（WISPr LoginURL 等） |
| `netstate.txt` | 網卡、DNS、路由 |
| `record.log` | 錄製程式自己的進度 |

## 2. 分析

```sh
tools/extract_flow.py captures/<時間>/stream.log            # 完整網址，只在本機看
tools/extract_flow.py captures/<時間>/stream.log --redact   # 要寫進文件或 commit 時用這個
```

輸出的 `page` 是登入視窗依序載入的網址。拿來跟第 4 節的「目前的流程」比對，找出哪一步不一樣。需要看頁面內容時，用 `status.txt` 裡的閘道網址照第 0 節重播並 `--dump`，再讀存下來的 HTML。

## 3. 修改與驗證

- 流程都在 `tymetro_login.py` 的 `login()`，每一步都有註解對應到下面的表格。
- 改完一定要在 TyMetro 上重播，看到 `開通成功（Login success）` 才算數：
  ```sh
  python3 tymetro_login.py --start '<閘道網址>' --dump /tmp/replay
  /usr/bin/python3 tymetro_login.py --start '<閘道網址>'   # 系統內建的 Python 3.9 + LibreSSL 也要能跑
  ```
- 如果流程改了，同步更新第 4 節的表格（網址用 `--redact` 過的版本）和 `CLAUDE.md`。
- 改完重新載入常駐程式：`./install.sh`（會覆蓋原本的 LaunchAgent）。
- 不要 commit `captures/`，也不要把 `runs/`、`--dump` 的檔案放進 repo。這些檔案裡有 MAC 位址、IP，以及開通表單裡的一次性帳號密碼。

## 4. 目前的流程（2026-11-25 錄製）

| # | 動作 | 網址 | 回應 | script 的處理 |
|---|---|---|---|---|
| 1 | 偵測 | `http://captive.apple.com/hotspot-detect.html` | 302 → 閘道 | `probe()` 不跟轉址，取 `Location` |
| 2 | 閘道 | `http://wifiplus.cht.com.tw/?client_ip=…&gwip=…&umac=…&vlanid=…&nasid=…&gwmac=…&cid=…&SerID=10006` | 瀏覽器 UA：302 → wifiad2；`CaptiveNetworkSupport … wispr` UA：200 + WISPr XML | 用 Safari UA |
| 3 | 入口 | `http(s)://wifiad2.cht.com.tw/?access_point_key=k4tos&…&ssid=TyMetro&action=<閘道參數>` | 302 → `check.html` | 自動跟轉址 |
| 4 | cookie 檢查 | `/check.html?tmst=…` | 200，用 JS 設 cookie 再跳 `/?tmst=` | 跳過，直接打下一步 |
| 5 | 同意頁 | `/?tmst=<unix time>` | 200，有 `id="agree_btn"`「我已同意」 | 檢查 `agree_btn` |
| 6 | 按「我已同意」 | `/internet/media`（前端先 ping `/robots.txt`，可省略） | 302 → `/authentication/login_form` → 302 `https://wifiplususer.cht.com.tw/mbr/Login.jsp?area_owner_id=220&umac=…` → 302 `/?token=…`，200，用 JS 導回 `/internet/media` | 打兩次 `/internet/media` |
| 7 | 廣告頁 | `/internet/media` | 200，「您可在觀看 5 秒後」、`closeadv()` 裡有 `/internet/abort_ad/<代碼>/84/` | 抓 `abort_ad` 路徑和秒數 |
| 8 | 按「按此開始免費上網」 | `/internet/abort_ad/<代碼>/84/<播放秒數>` | 302 → `/internet/active` → 302 `https://wifiplususer.cht.com.tw/mbr/Inet_open_svc.jsp?…` | 等秒數 + 1 後打，秒數也用這個值 |
| 9 | 開通頁 | `Inet_open_svc.jsp` | 200，`<form name='loginform' action='https://wlangw.hinet.net/v2_0/wifiplus_auth/auth_page.php'>`，`body onload` 自動送出 | 抓 action 和所有 hidden input |
| 10 | 開通 | POST `auth_page.php`（`username`、`password`、`client_ip`、`gwip`、`umac`、`vlanid`、`nasid`、`gwmac`、`cid`、`SerID`） | 302 → `http://wifiplususer.cht.com.tw/mbr/InetService_open.jsp?code=0&msg=Login+success` | `code=0` 才算成功 |
| 11 | 確認 | 再打一次偵測頁 | 200 `Success` | |

入口頁寫明一次只給 30 分鐘；常駐模式每 60 秒檢查一次，到期就重登。

## 5. 已知的坑

- **還沒登入時，一般程式不能用這個 Wi-Fi。** macOS 把網卡優先順序設成 `Never`：預設路由只有綁定網卡的程式能用（`netstat -rn` 的 flags 有 `I`），系統 DNS 是 `Not Reachable`，只有系統的登入視窗能連。所以 script 用 `IP_BOUND_IF` 把 socket 綁在 Wi-Fi 網卡上，DNS 自己組封包，送給 `ipconfig getpacket en0` 裡 DHCP 給的伺服器。直接用 `urllib`、`curl` 會查不到 DNS。
- **讀不到 SSID。** macOS 26 的 `networksetup`、`ipconfig getsummary`、`system_profiler` 都會把 SSID 遮成 `<redacted>`，`scutil` 的 `SSID_STR` 也是空的。目前還能從 `scutil` 的 `State:/Network/Interface/en0/AirPort` → `CachedScanRecord`（NSKeyedArchiver）解出 `SSID_STR`，見 `current_ssid()`。如果哪天這裡也讀不到，`--status` 會顯示「（讀不到）」，常駐模式就不會動作。
- **手機熱點會搶走連線。** 有其他已知網路可用時，macOS 對 WISPr 自動登入失敗的網路會直接封鎖（系統紀錄依序出現 `PresentUI result Temporary Failure (6)`、`Temporarily disabling Wi-Fi (deny-listing)`），連登入視窗都不開。
- **macOS 自己的 WISPr 自動登入會失敗。** 每次在登入視窗登入成功，macOS 都會把開通表單裡的一次性帳號（`wifiplus1/.99.<數字>.umac@itw`）存進鑰匙圈，下次先拿它自動登入。這組帳號每次都會換（`.99.` 後面的 8 位數字每次不同），所以會 `Login attempt failed`，然後才開登入視窗。這不影響 script。
- **TLS。** `wifiplususer.cht.com.tw` 只接受 `AES128-SHA256`。Homebrew Python 3.15 用的 OpenSSL 4.1 預設不接受，要 `set_ciphers("DEFAULT:@SECLEVEL=1")`；LibreSSL 不認得 `@SECLEVEL`，但預設就接受。
- **HSTS。** `wifiad2.cht.com.tw` 送 `Strict-Transport-Security: …; includeSubdomains; preload`，登入視窗實際上也把 http 自動升成 https 了（錄到的網址都是 http 後面緊接著 https）。轉址的 `Location` 常常寫 http，script 跟瀏覽器一樣改成 https。
- **User-Agent。** 閘道看到 macOS 偵測器的 UA（`CaptiveNetworkSupport-… wispr`）會回 WISPr XML，不會轉址。一定要用瀏覽器 UA。
- **剛連上 Wi-Fi 的前幾秒還沒有 IP 或 DNS。** `wait_for_network()` 會重試 45 秒。
- **錄製相關。** zsh 有內建的 `log` 指令，要寫 `/usr/bin/log`。登入視窗載入的網址（`Probe request prepared for … URL:`）、WISPr 解析結果都是 debug 等級，只有 `log stream --level debug` 即時錄得到，事後 `log show` 看不到。
- **11/20 的空白登入視窗**：原因沒查到，當時的紀錄沒有 WebKit 細節。之後若再遇到，錄製時 `stream.log` 的 `error` 列應該會有線索。

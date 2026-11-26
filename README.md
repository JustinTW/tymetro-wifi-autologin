# tymetro-wifi-autologin

Mac 一連上桃園捷運的免費 Wi-Fi（TyMetro-Train），就自動完成登入：按「我已同意」，等廣告秒數，再按「按此開始免費上網」。一次只能用 30 分鐘，到期會自動重登。

只支援 macOS（在 macOS 26 上測過）。只用 Python 標準函式庫，不用另外安裝套件。

## 安裝

```sh
git clone git@github.com:JustinTW/tymetro-wifi-autologin.git ~/workspace/github/JustinTW/tymetro-wifi-autologin
cd ~/workspace/github/JustinTW/tymetro-wifi-autologin
./install.sh
```

這會裝一個 LaunchAgent，登入 macOS 後就在背景常駐。它平常是睡著的，不耗 CPU，只有 Wi-Fi 連上、斷線或換網路時才會被系統叫醒；在 TyMetro 上登入後，會睡到 30 分鐘到期再起來重登。車站的 `TyMetro` 也要自動登入的話：

```sh
./install.sh --ssid TyMetro-train --ssid TyMetro
```

移除：`./uninstall.sh`

## 使用

裝好就不用管。連上 TyMetro-Train 後，macOS 還是會跳出自己的登入視窗、停在「我已同意」頁，**不用按**：script 在背景登入，大約 20 秒（其中 6 秒是規定的廣告時間），會跳通知「正在自動登入」和「已自動登入」，登入完成後視窗應該會自己關掉。

```sh
python3 tymetro_login.py --status   # 看目前連的 Wi-Fi、能不能上網
python3 tymetro_login.py            # 手動登入一次
tail -f ~/Library/Logs/tymetro-wifi-autologin.log
```

除錯時可以加 `--trace` 重新安裝（`./install.sh --trace`），在 TyMetro 上時會另外錄下 macOS 登入視窗的系統紀錄到 `~/Library/Caches/tymetro-wifi-autologin/traces/`。

## 注意

- **手機熱點要關掉**（或把它的「自動加入」關掉）。macOS 發現 TyMetro 要登入、旁邊又有熱點可用時，會直接改連熱點，script 來不及登入。
- 一次登入只對一台裝置有效，手機登入不會讓 Mac 也能上網。
- 這不是官方工具。中華電信的入口網站改版後，script 可能會失效，修復方法見 [docs/maintenance/recording-portal-flow.md](docs/maintenance/recording-portal-flow.md)。

## 運作方式

macOS 在登入前不讓一般程式使用這個 Wi-Fi，所以 script 把連線綁在 Wi-Fi 網卡上，DNS 也自己查。接著照入口網站的流程一步一步打，最後送出開通表單。詳細流程和設計理由見 [CLAUDE.md](CLAUDE.md) 和維護文件。

## 授權

MIT，見 [LICENSE](LICENSE)。

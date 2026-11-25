#!/usr/bin/env python3
"""從 tools/record_portal.sh 錄到的 stream.log 抽出登入流程的時間線。

  tools/extract_flow.py captures/<時間>/stream.log            完整網址（只在本機看）
  tools/extract_flow.py captures/<時間>/stream.log --redact   遮掉 IP、MAC、token 等，可以貼進文件或 commit

輸出每行：時間  類別  內容
  wispr   閘道回的 WISPr 參數（LoginURL、RedirectHostname）
  system  configd 的關鍵狀態（偵測、自動登入、開視窗、上線、封鎖）
  page    登入視窗依序載入的網址（同一網址 http→https 只留一筆）
  error   登入視窗載入失敗
"""
import re
import sys

LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d\.\d+) +\S+ +([^\[]+?)\[")
SYSTEM = re.compile(
    r"WISPr detected|Evaluate result|Login attempt|PresentUI|Launching Websheet|WebSheet Complete"
    r"|Online|deny-listing|not captive|storing credentials|probed timed out|SSID '")
REDACT = re.compile(r"((?:client_ip|umac|gwmac|cid|token|gwip|vlanid|gwport|nasid|password|username)"
                    r"(?:=|%3D))[^&\s\"%]+(?:%3A[^&\s\"%]+)*", re.I)


def main():
    path = sys.argv[1]
    redact = "--redact" in sys.argv
    events, last_page = [], None
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = LINE.match(line)
        i += 1
        if not m:
            continue
        t, proc = m.groups()
        if "detected WISPr {" in line:
            block = []
            while i < len(lines) and not LINE.match(lines[i]):
                kv = re.match(r'\s+"?(LoginURL|RedirectHostname)"? = "?([^";]+)', lines[i])
                if kv:
                    block.append(f"{kv.group(1)}={kv.group(2)}")
                i += 1
            events += [(t, "wispr", b) for b in block]
        elif proc == "configd" and "com.apple.captive" in line and SYSTEM.search(line):
            events.append((t, "system", line.split("] ", 1)[-1]))
        elif "Probe request prepared for" in line:
            url = re.search(r"URL: (\S+) \}", line)
            if url:
                key = re.sub(r"^https?://", "", url.group(1))
                if key != last_page:
                    events.append((t, "page", url.group(1)))
                last_page = key
        elif "Did fail" in line and proc == "Captive Network Assistant":
            events.append((t, "error", line.split("] ", 1)[-1][:300]))

    for t, kind, text in events:
        if redact:
            text = re.sub(r"(for user )\S+", r"\1…", REDACT.sub(r"\1…", text))
        print(f"{t[:12]}  {kind:6}  {text}")


if __name__ == "__main__":
    main()

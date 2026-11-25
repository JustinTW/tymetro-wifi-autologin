#!/usr/bin/env python3
"""自動登入桃園捷運免費 Wi-Fi（TyMetro-Train / TyMetro，中華電信「看廣告上網」）。

用法：
  tymetro_login.py              登入一次
  tymetro_login.py --watch      常駐：連上 TyMetro-Train 就自動登入，30 分鐘到期後重登
                                （LaunchAgent 跑的就是這個）
  tymetro_login.py --status     顯示 Wi-Fi 名稱、DNS、是否需要登入、被轉去的閘道網址
  tymetro_login.py --start URL --dump DIR
                                從閘道網址重跑整個流程並存下每一頁（入口網站改版時除錯用）

登入流程（跟手動點的一樣）：
  1. 偵測頁被轉到中華電信閘道 wifiplus.cht.com.tw → wifiad2.cht.com.tw 同意頁
  2. 「我已同意」：/internet/media → 會員登入轉址 → 廣告頁
  3. 等廣告秒數，「按此開始免費上網」：/internet/abort_ad/... → /internet/active
     → wifiplususer 開通頁（自動送出的表單）→ POST wlangw.hinet.net 開通
"""
import argparse
import http.cookiejar
import os
import plistlib
import pty
import random
import re
import select
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/26.0 Safari/605.1.15")
PROBE = "http://captive.apple.com/hotspot-detect.html"
PORTAL = "https://wifiad2.cht.com.tw"
CHT_HOSTS = ("wifiplus.cht.com.tw", "wifiad2.cht.com.tw")
DEFAULT_SSIDS = ["TyMetro-train"]
TIMEOUT = 15
IP_BOUND_IF = 25  # <netinet/in.h>：socket 只走指定網卡

SESSION = 30 * 60  # 入口頁寫明看完廣告可用 30 分鐘
WATCH_KEYS = ("IPv4", "CaptiveNetwork")  # 常駐模式訂閱的 State:/Network/Interface/<網卡>/ 底下的 key
RUNS_DIR = os.path.expanduser("~/Library/Caches/tymetro-wifi-autologin/runs")

IFACE = "en0"  # main() 會換成實際的 Wi-Fi 網卡
DUMP = None    # 有設定時，每個 request 和頁面都存到這個目錄


class LoginError(Exception):
    pass


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True).stdout


# ---------------------------------------------------------------------------
# Wi-Fi 網卡、SSID、DNS
#
# 還沒登入時，macOS 把這個 Wi-Fi 的優先順序設成 Never：沒有全域預設路由，系統 DNS 也不給用，
# 只有系統的登入視窗能用。所以所有連線都綁在 Wi-Fi 網卡上（IP_BOUND_IF），
# DNS 直接問 DHCP 給的伺服器。

def wifi_iface():
    out = run(["/usr/sbin/networksetup", "-listallhardwareports"])
    m = re.search(r"Hardware Port: Wi-Fi\nDevice: (\S+)", out)
    return m.group(1) if m else "en0"


def current_ssid():
    """macOS 會把 SSID_STR 遮掉，但 CachedScanRecord（NSKeyedArchiver）裡還有。讀不到回傳 None。"""
    out = run(["/usr/sbin/scutil"], f"show State:/Network/Interface/{IFACE}/AirPort\n")
    m = re.search(r"CachedScanRecord : <data> 0x([0-9a-f]+)", out)
    if not m:
        return None
    try:
        arc = plistlib.loads(bytes.fromhex(m.group(1)))
        objs = arc["$objects"]
        root = objs[arc["$top"]["root"].data]
        rec = {objs[k.data]: objs[v.data] for k, v in zip(root["NS.keys"], root["NS.objects"])}
        return rec.get("SSID_STR") or None
    except Exception:
        return None


def dns_servers():
    out = run(["/usr/sbin/ipconfig", "getpacket", IFACE])
    m = re.search(r"domain_name_server \(ip_mult\): \{([^}]*)\}", out)
    return [s.strip() for s in m.group(1).split(",")] if m else []


def bound_socket(kind):
    s = socket.socket(socket.AF_INET, kind)
    s.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, socket.if_nametoindex(IFACE))
    return s


def skip_name(data, pos):
    while data[pos]:
        if data[pos] & 0xC0 == 0xC0:
            return pos + 2
        pos += data[pos] + 1
    return pos + 1


def dns_query(name, server):
    """最小的 DNS A 紀錄查詢，從 Wi-Fi 網卡送出。"""
    qid = random.randrange(1 << 16)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.rstrip(".").split(".")) + b"\0"
    packet = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + qname + struct.pack(">HH", 1, 1)
    with bound_socket(socket.SOCK_DGRAM) as s:
        s.settimeout(3)
        s.sendto(packet, (server, 53))
        data = s.recv(4096)
    rid, _, qdcount, ancount = struct.unpack(">HHHH", data[:8])
    if rid != qid:
        raise OSError("DNS 回應的 id 不符")
    pos = 12
    for _ in range(qdcount):
        pos = skip_name(data, pos) + 4
    ips = []
    for _ in range(ancount):
        pos = skip_name(data, pos)
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        if rtype == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(data[pos:pos + 4]))
        pos += rdlen
    return ips


def resolve(host):
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
        return host
    servers = dns_servers()
    for server in servers:
        try:
            ips = dns_query(host, server)
        except OSError:
            continue
        if ips:
            return ips[0]
    raise OSError(f"查不到 {host}（{IFACE} 的 DNS：{', '.join(servers) or '無'}）")


def wifi_connection(address, timeout=None, source_address=None, *args, **kwargs):
    """取代 socket.create_connection，讓 http.client 的連線都走 Wi-Fi 網卡。"""
    host, port = address[:2]
    s = bound_socket(socket.SOCK_STREAM)
    s.settimeout(timeout if isinstance(timeout, (int, float)) else TIMEOUT)
    try:
        s.connect((resolve(host), port))
    except BaseException:
        s.close()
        raise
    return s


# ---------------------------------------------------------------------------
# HTTP

def dump(name, text):
    if DUMP:
        with open(os.path.join(DUMP, name), "a" if name == "flow.txt" else "w") as f:
            f.write(text)


class Redirects(urllib.request.HTTPRedirectHandler):
    """wifiad2.cht.com.tw 有 HSTS，瀏覽器會把 http:// 轉址自動升成 https://，這裡照做。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname == "wifiad2.cht.com.tw":
            newurl = re.sub(r"^http://", "https://", newurl)
        dump("flow.txt", f"    {code} {req.full_url}\n     -> {newurl}\n")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def make_opener(follow=True):
    # wifiplususer.cht.com.tw 只接受 AES128-SHA256（RSA 金鑰交換），OpenSSL 4.1 預設不接受；憑證照樣驗證
    ctx = ssl.create_default_context()
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    except ssl.SSLError:
        pass  # LibreSSL（/usr/bin/python3）不認得 @SECLEVEL，但預設就接受 AES128-SHA256
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        Redirects() if follow else NoRedirect())
    opener.addheaders = [("User-Agent", UA)]
    return opener


_seq = 0


def fetch(opener, url, data=None):
    """回傳 (status, 最後的網址, headers, body)。"""
    global _seq
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        resp = opener.open(url, data=body, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        resp = e
    status, final, headers = resp.status, resp.geturl(), resp.headers
    text = resp.read().decode("utf-8", "replace")
    if DUMP:
        _seq += 1
        name = f"{_seq:02d}-" + re.sub(r"[^A-Za-z0-9.]+", "_", urllib.parse.urlsplit(final).hostname
                                        + urllib.parse.urlsplit(final).path)[:60] + ".html"
        dump("flow.txt", f"{_seq:02d} {'POST' if data is not None else 'GET'} {url}\n"
                         f"   = {status} {final}  ({len(text)} bytes → {name})\n")
        dump(name, text)
    return status, final, headers, text


def probe():
    """回傳 (是否已能上網, 被轉去的網址)。"""
    status, _, headers, body = fetch(make_opener(follow=False), PROBE)
    if status == 200 and "<TITLE>Success</TITLE>" in body:
        return True, None
    loc = headers.get("Location")
    if not loc:
        m = re.search(r"""(https?://(?:%s)[^"'<>\s]*)""" % "|".join(map(re.escape, CHT_HOSTS)), body)
        loc = m and m.group(1).replace("&amp;", "&")
    return False, loc


def wait_for_network(seconds=45):
    """剛連上 Wi-Fi 時 IP/DNS 可能還沒好，先重試一陣子。"""
    deadline = time.time() + seconds
    while True:
        try:
            return probe()
        except (urllib.error.URLError, OSError) as e:
            if time.time() > deadline:
                raise LoginError(f"網路還沒通（{e}）")
            time.sleep(2)


# ---------------------------------------------------------------------------
# 登入流程

def login(start):
    if urllib.parse.urlsplit(start).hostname not in CHT_HOSTS:
        raise LoginError(f"被轉到的不是中華電信的登入頁，不處理：{start}")
    op = make_opener()

    # 1. 閘道 → wifiad2 同意頁（中間的 check.html 只是用 JS 檢查 cookie，直接跳過）
    fetch(op, start)
    page = fetch(op, f"{PORTAL}/?tmst={int(time.time())}")[3]
    if "agree_btn" not in page:
        raise LoginError("沒有看到「我已同意」頁，入口網站可能改版了")
    log("按「我已同意」")

    # 2. 「我已同意」→ 會員登入轉址 → token 頁（用 JS 再導回 /internet/media）→ 廣告頁
    ad = fetch(op, f"{PORTAL}/internet/media")[3]
    if "abort_ad" not in ad:
        ad = fetch(op, f"{PORTAL}/internet/media")[3]
    m = re.search(r"(/internet/abort_ad/\w+/\d+/)", ad)
    if not m:
        raise LoginError("沒有在廣告頁找到「按此開始免費上網」，入口網站可能改版了")
    wait = re.search(r"觀看\s*(\d+)\s*秒", ad)
    secs = (int(wait.group(1)) if wait else 5) + 1
    log(f"等廣告 {secs} 秒")
    time.sleep(secs)

    # 3. 「按此開始免費上網」→ /internet/active → 開通頁（自動送出的表單）→ POST 開通
    log("按「按此開始免費上網」")
    _, url, _, form = fetch(op, PORTAL + m.group(1) + str(secs))
    action = re.search(r"""<form[^>]*action=["']([^"']+)["']""", form)
    fields = dict(re.findall(
        r"""<input[^>]*type=["']hidden["'][^>]*name=["']([^"']+)["'][^>]*value=["']([^"']*)["']""", form))
    if not action or "username" not in fields:
        raise LoginError(f"開通頁格式不如預期（{url}）")

    _, url, _, _ = fetch(op, urllib.parse.urljoin(url, action.group(1)), fields)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    code, msg = q.get("code", ["?"])[0], q.get("msg", [""])[0]
    if code != "0":
        raise LoginError(f"開通失敗：code={code} msg={msg}（{url}）")
    log(f"開通成功（{msg}），可用 30 分鐘")


def run_once(start=None):
    if start is None:
        online, start = wait_for_network()
        if online:
            log("已經可以上網，不用登入")
            return
        if not start:
            raise LoginError("偵測頁沒有被轉到登入頁，可能不是在 TyMetro 的 Wi-Fi")
    login(start)
    online, _ = wait_for_network(15)
    if not online:
        raise LoginError("送出開通後仍然上不了網")


def new_run_dir():
    """常駐模式每次登入都把流程存一份，入口網站改版時直接看最新一份就知道卡在哪。只留最近 10 份。"""
    global _seq
    os.makedirs(RUNS_DIR, exist_ok=True)
    for old in sorted(os.listdir(RUNS_DIR))[:-9]:
        path = os.path.join(RUNS_DIR, old)
        for f in os.listdir(path):
            os.remove(os.path.join(path, f))
        os.rmdir(path)
    path = os.path.join(RUNS_DIR, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(path, exist_ok=True)
    _seq = 0
    return path


def watch_check():
    """需要登入就登入。回傳 True 表示這次有登入，False 表示本來就能上網。"""
    global DUMP
    online, start = wait_for_network(30)
    if online:
        return False
    if not start:
        raise LoginError("偵測頁沒有被轉到登入頁")
    DUMP = new_run_dir()
    dump("flow.txt", f"gateway {start}\n")
    log(f"需要登入（流程紀錄：{DUMP}）")
    try:
        login(start)
        if not wait_for_network(15)[0]:
            raise LoginError("送出開通後仍然上不了網")
    finally:
        DUMP = None
    return True


def network_events():
    """訂閱 Wi-Fi 的 IPv4（連上、斷線、換網路）和 CaptiveNetwork（macOS 偵測到要登入）變化。
    scutil 輸出到 pipe 時會整塊緩衝，通知不會即時送出，所以把它的輸出接在 pty 上。"""
    master, slave = pty.openpty()
    proc = subprocess.Popen(["/usr/sbin/scutil"], stdin=subprocess.PIPE, stdout=slave, stderr=slave, text=True)
    os.close(slave)
    keys = "".join(f"n.add State:/Network/Interface/{IFACE}/{k}\n" for k in WATCH_KEYS)
    proc.stdin.write(keys + "n.watch\n")
    proc.stdin.flush()
    return proc, master


def wait_event(fd, timeout):
    """睡到網路有變化（回傳 True）或逾時（False）。一次變化常連發好幾個通知，多等 1 秒一起讀掉。"""
    ready = select.select([fd], [], [], timeout)[0]
    if not ready:
        return False
    while ready:
        if not os.read(fd, 4096):
            raise OSError("scutil 結束了")
        ready = select.select([fd], [], [], 1)[0]
    return True


def watch(ssids):
    """平常停在 select() 不耗 CPU，只有網路事件或計時器到了才醒來。
    計時器只在目標 Wi-Fi 上才設：登入後睡到 30 分鐘到期，之後每 20 秒看一次，直到重登。"""
    targets = {s.lower() for s in ssids}
    proc, events = network_events()
    log(f"常駐中（網卡 {IFACE}），目標 Wi-Fi：{', '.join(ssids)}")
    last_ssid, login_at, timer = None, None, 0.0  # timer：下次檢查的時間，None 表示等網路事件就好

    while True:
        if timer is not None and time.time() >= timer:
            ssid = current_ssid()
            if not (ssid and ssid.lower() in targets):
                last_ssid, login_at, timer = ssid, None, None
            else:
                if ssid != last_ssid:
                    log(f"連上 {ssid}")
                    last_ssid = ssid
                try:
                    if watch_check():
                        login_at = time.time()
                    if login_at and time.time() < login_at + SESSION:
                        timer = login_at + SESSION + 5
                    else:
                        timer = time.time() + (20 if login_at else 60)
                except (LoginError, urllib.error.URLError, OSError) as e:
                    log(f"失敗：{e}，30 秒後再試")
                    timer = time.time() + 30
        if wait_event(events, None if timer is None else max(0.0, timer - time.time())):
            timer = 0.0


def status():
    print(f"Wi-Fi 網卡：{IFACE}")
    print(f"Wi-Fi 名稱：{current_ssid() or '（讀不到）'}")
    print(f"DNS：{', '.join(dns_servers()) or '（沒有）'}")
    try:
        online, start = probe()
        print("狀態：可以上網" if online else f"狀態：需要登入\n閘道網址：{start}")
    except (urllib.error.URLError, OSError) as e:
        print(f"狀態：連不出去（{e}）")


def main():
    global IFACE, DUMP
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", action="store_true", help="常駐，連上目標 Wi-Fi 就自動登入")
    ap.add_argument("--ssid", action="append", help=f"目標 Wi-Fi 名稱，可重複（預設 {DEFAULT_SSIDS[0]}）")
    ap.add_argument("--status", action="store_true", help="顯示目前狀態")
    ap.add_argument("--start", metavar="URL", help="從這個閘道網址開始（除錯、重播用）")
    ap.add_argument("--dump", metavar="DIR", help="把每個 request 和頁面存到 DIR（除錯用）")
    ap.add_argument("--iface", help="Wi-Fi 網卡（預設自動偵測）")
    args = ap.parse_args()

    IFACE = args.iface or wifi_iface()
    socket.create_connection = wifi_connection
    if args.dump:
        DUMP = args.dump
        os.makedirs(DUMP, exist_ok=True)

    if args.status:
        status()
    elif args.watch:
        watch(args.ssid or DEFAULT_SSIDS)
    else:
        try:
            run_once(args.start)
        except (LoginError, urllib.error.URLError, OSError) as e:
            log(f"失敗：{e}")
            sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""End-to-end test of the Android app in an emulator (GitHub Actions: .github/workflows/android.yml).

    adb devices    # an emulator running, API 35+ with Google Play services
    python mobile/emulator_test.py mobile/android/app/build/outputs/apk/debug/app-debug.apk

Installs the debug APK and talks to its WebView over the DevTools protocol (debug builds only).
Checks:
  - with Wi-Fi and mobile data off it still starts (the bundled Python runtime) and counts life
  - on the table there's no banner; opening the menu asks for one and leaves its strip free;
    closing the menu takes it away (Google's sample ads; loading one needs the network, so a
    banner that doesn't arrive in time is reported, not failed)
  - Android Back closes the menu and keeps the app open
  - no crash in the log
Screenshots go to $LIFEPI_SHOTS.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

PKG = "io.github.therealshadoh.lifepi"
SHOTS = Path(os.environ.get("LIFEPI_SHOTS") or tempfile.mkdtemp())
SHOTS.mkdir(parents=True, exist_ok=True)
BOOTED = ("(() => { const c = document.querySelector('#canvas');"
          " return !!c && c.width > 1 && !!localStorage['lifepi-state']; })()")
LIVES = "JSON.parse(localStorage['lifepi-state']).players.map(p => p.life)"
NATIVE = ("(() => { const n = window.lifepiNative; return n ? {ads: n.ads, removed: n.removed, want: n._want,"
          " shown: n._shown, loaded: n._loaded, reserve: n.reserve, sheets: n._sheets} : null; })()")


def adb(*args, check=True, timeout=120):
    r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"adb {' '.join(args)}: {r.stderr or r.stdout}")
    return r.stdout


def shot(name):
    png = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, timeout=60).stdout
    (SHOTS / name).write_bytes(png)


def screen_size():
    m = re.search(r"(\d+)x(\d+)", adb("shell", "wm", "size").splitlines()[-1])
    return int(m.group(1)), int(m.group(2))


def network(on):
    for svc in ("wifi", "data"):
        adb("shell", "svc", svc, "enable" if on else "disable", check=False)
    time.sleep(3)


class Page:
    """DevTools connection to the app's WebView."""

    def __init__(self, timeout=60):
        end = time.time() + timeout
        while True:
            try:
                pid = adb("shell", "pidof", PKG).strip().split()[0]
                socks = adb("shell", "cat", "/proc/net/unix")
                name = f"webview_devtools_remote_{pid}"
                if name in socks:
                    break
            except (RuntimeError, IndexError):
                pass
            if time.time() > end:
                raise RuntimeError("the app's WebView never offered DevTools (debug build?)")
            time.sleep(1)
        adb("forward", "--remove-all", check=False)
        adb("forward", "tcp:9333", f"localabstract:{name}")
        for _ in range(60):
            try:
                tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9333/json", timeout=5).read())
                page = next(t for t in tabs if t.get("type") == "page")
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("no page in the WebView")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True, timeout=30)
        self.n = 0

    def js(self, expr):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": "Runtime.evaluate",
                                 "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my:
                return msg.get("result", {}).get("result", {}).get("value")

    def wait(self, expr, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr):
                    return
            except Exception:
                pass
            time.sleep(0.5)
        term = self.js('((document.querySelector(".xterm-rows") || {}).innerText || "").slice(-2000)')
        raise AssertionError(f"timed out waiting for {what}\n{term}")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def launch(clear=False):
    adb("shell", "am", "force-stop", PKG)
    if clear:
        adb("shell", "pm", "clear", PKG)
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y)))
    time.sleep(1.0)


def main(apk):
    adb("install", "-r", "-g", apk, timeout=300)
    adb("logcat", "-c", check=False)
    W, H = screen_size()
    print("screen", W, H)

    # 1. offline first start: the bundled runtime
    network(False)
    launch(clear=True)
    p = Page()
    p.wait(BOOTED, 180, "the app to start offline")
    shot("android_offline_boot.png")
    before = p.js(LIVES)
    tap(W * 0.88, H * 0.75)                                    # bottom-right player's + half
    time.sleep(1)
    after = p.js(LIVES)
    assert before != after and sum(after) == sum(before) + 1, (before, after)
    print("offline start and a tap ok", after)
    p.close()

    # 2. the banner: none on the table; with the menu open, asked for and its strip kept free
    network(True)
    launch()
    p = Page()
    p.wait(BOOTED, 120, "the app to start")
    time.sleep(2)
    n = p.js(NATIVE)
    assert n and n["ads"] and not n["want"] and not n["shown"], n
    shot("android_table_no_ad.png")
    tap(W / 2, H / 2)                                          # the round menu button, centre
    p.wait("window.lifepiNative && window.lifepiNative._want", 10, "the menu to ask for the banner")
    try:
        p.wait("window.lifepiNative._loaded && window.lifepiNative.reserve > 0", 60, "a (sample) banner")
        loaded = True
    except AssertionError:
        loaded = False
    time.sleep(2)
    shot("android_menu_with_ad.png")
    n = p.js(NATIVE)
    print("menu open:", n, "(banner loaded)" if loaded else "(no ad arrived in time - network?)")
    assert n["want"] and n["sheets"] >= 1, n

    # 3. Back closes the menu (and the banner goes); the app stays
    adb("shell", "input", "keyevent", "4")
    p.wait("window.lifepiNative && !window.lifepiNative._want && window.lifepiNative._sheets === 0", 10,
           "Back to close the menu")
    time.sleep(1.5)
    n = p.js(NATIVE)
    assert not n["shown"], n
    assert PKG in adb("shell", "dumpsys", "activity", "activities", check=False), "the app closed"
    shot("android_back_closed_menu.png")
    print("back closes the menu, banner gone ok")
    p.close()

    log = adb("logcat", "-d", "-b", "crash", check=False)
    assert PKG not in log, log[-3000:]
    print("no crashes; screenshots in", SHOTS)


if __name__ == "__main__":
    main(sys.argv[1])

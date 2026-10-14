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
          " shown: n._shown, loaded: n._loaded, reserve: n.reserve, sheets: n._sheets, calls: n._calls, backs: n._backs} : null; })()")
ESCAPE = ("(() => { const c = document.getElementById('canvas') || window;"
          " const e = {key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true};"
          " c.dispatchEvent(new KeyboardEvent('keydown', e));"
          " setTimeout(() => c.dispatchEvent(new KeyboardEvent('keyup', e)), 40); return true; })()")


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
                # LifePi's page - the ads SDK has hidden pages of its own in the same process
                page = next(t for t in tabs if t.get("type") == "page" and t.get("url", "").startswith("https://localhost"))
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("no page in the WebView")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True, timeout=30)
        self.n, self.events, self.urls = 0, [], {}
        for m in ("Runtime.enable", "Network.enable", "Log.enable"):
            self.call(m)

    def call(self, method, **params):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my:
                return msg.get("result", {})
            ev, prm = msg.get("method", ""), msg.get("params", {})
            if ev == "Runtime.exceptionThrown":
                d = prm.get("exceptionDetails", {})
                self.events.append("exception: " + str(d.get("exception", {}).get("description") or d.get("text"))[:300])
            elif ev == "Runtime.consoleAPICalled" and prm.get("type") in ("error", "warning"):
                self.events.append("console: " + " ".join(str(a.get("value", a.get("description", ""))) for a in prm.get("args", []))[:300])
            elif ev == "Log.entryAdded" and prm.get("entry", {}).get("level") == "error":
                e = prm["entry"]
                self.events.append(f"log: {e.get('text', '')[:200]} {e.get('url', '')}")
            elif ev == "Network.requestWillBeSent":
                url = prm.get("request", {}).get("url", "")
                self.urls[prm.get("requestId")] = url
                self.events.append("req: " + url[:160])
            elif ev == "Network.loadingFailed":
                url = self.urls.get(prm.get("requestId"), "?")
                self.events.append(f"FAILED: {url[:160]} {prm.get('errorText')} {prm.get('blockedReason') or ''}")

    def diagnose(self):
        """What the page saw: errors, failed requests, the Python terminal."""
        try:
            self.js("1")
            term = self.js('((document.querySelector(".xterm-rows") || {}).innerText || "").slice(-800)')
            state = self.js("JSON.stringify({href: location.href, ready: document.readyState, cap: !!window.Capacitor,"
                            " saved: !!localStorage['lifepi-state']})") + " native " + json.dumps(self.js(NATIVE))
        except Exception as e:
            term, state = f"(no page: {e})", ""
        keep = [e for e in self.events if not e.startswith("req: ")
                and "MEDIA USER ACTION" not in e and not e.startswith("    at ")]
        failed = sorted({e for e in self.events if e.startswith("FAILED: ")})
        return "\n".join(["page: " + str(state), *keep[-20:], "--- failed requests ---", *failed,
                          "--- terminal ---", str(term)])

    def js(self, expr):
        try:
            r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        except (websocket.WebSocketException, OSError) as e:      # DevTools dropped: once more
            print(f"devtools connection lost ({e!r}); app pid now: {adb('shell', 'pidof', PKG, check=False).strip()}")
            self.close()
            self.__init__()
            r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def wait(self, expr, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr):
                    return
            except Exception:
                pass
            time.sleep(0.5)
        print(self.diagnose())
        raise AssertionError(f"timed out waiting for {what}")

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


def focus():
    lines = [l.strip() for l in adb("shell", "dumpsys", "window", check=False).splitlines() if "mCurrentFocus" in l]
    return lines[0] if lines else ""


def ensure_focus(timeout=20):
    """Our activity in front: a slow emulator pops "not responding" dialogs for its own apps
    (the launcher), and a key press would go to them instead."""
    end = time.time() + timeout
    while PKG not in focus() and time.time() < end:
        adb("shell", "am", "broadcast", "-a", "android.intent.action.CLOSE_SYSTEM_DIALOGS", check=False)
        time.sleep(1)
    assert PKG in focus(), f"LifePi isn't in front: {focus()}"


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y)))
    time.sleep(1.0)


def main(apk):
    adb("install", "-r", "-g", apk, timeout=300)
    adb("shell", "settings", "put", "global", "hide_error_dialogs", "1", check=False)   # no ANR pop-ups
    # Android's one-time "Viewing full screen" tip (a person taps "Got it"), already seen
    adb("shell", "settings", "put", "secure", "immersive_mode_confirmations", "confirmed", check=False)
    adb("logcat", "-c", check=False)
    W, H = screen_size()
    print("screen", W, H)

    # 1. start (with internet), and the banner: none on the table; with the menu open, asked
    #    for and its strip kept free
    network(True)
    launch(clear=True)
    p = Page()
    p.wait(BOOTED, 300, "the app to start")                  # a cold emulator can be very slow
    time.sleep(2)
    p.wait("window.lifepiNative && window.lifepiNative._calls > 0", 15, "the game to talk to the app")
    n = p.js(NATIVE)
    assert n and n["ads"] and not n["want"] and not n["shown"], n
    shot("android_table_no_ad.png")
    p.js(ESCAPE)                                               # Escape opens LifePi's menu
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

    # 2. Back closes the menu (and the banner goes); the app stays
    ensure_focus()
    adb("shell", "input", "keyevent", "4")
    time.sleep(1.5)
    print("logcat:", adb("logcat", "-d", "-s", "LifePi:I", check=False).strip()[-800:])
    p.wait("window.lifepiNative && !window.lifepiNative._want && window.lifepiNative._sheets === 0", 10,
           "Back to close the menu")
    time.sleep(1.5)
    n = p.js(NATIVE)
    assert not n["shown"], n
    assert PKG in adb("shell", "dumpsys", "activity", "activities", check=False), "the app closed"
    shot("android_back_closed_menu.png")
    print("back closes the menu, banner gone ok")
    p.close()

    # 3. store screenshots: a game in progress (names, damage, poison, random card art). Written
    #    and the page reloaded straight away; if the game saved over it first, again.
    DEMO = """(() => { const s = JSON.parse(localStorage['lifepi-state']);
      const names = ["Chris", "Mike", "Nate", "Jared"], life = [34, 29, 42, 17];
      s.players.forEach((p, i) => { p.name = names[i] || p.name; p.life = life[i] ?? p.life;
                                    p.art = null; p.art_auto = false; p.credit = ""; });
      if (s.players[1]) s.players[1].cmd[0] = 12;
      if (s.players[3]) { s.players[3].cmd[2] = 18; s.players[3].counters.poison = 3; }
      s.history = []; s.first = 0;
      localStorage['lifepi-state'] = JSON.stringify(s);
      localStorage.removeItem('lifepi-state.bak');
      location.reload(); return true; })()"""
    DEMO_IN = "(() => { try { return JSON.parse(localStorage['lifepi-state']).players[0].name === 'Chris'; } catch (e) { return false; } })()"
    for attempt in range(4):
        p = Page()
        try:
            p.js(DEMO)
        except Exception:
            pass                                              # the page went away mid-answer: fine
        p.close()
        time.sleep(4)
        p = Page()
        p.wait(BOOTED, 120, "the demo game")
        time.sleep(2)
        if p.js(DEMO_IN):
            break
        p.close()
    assert p.js(DEMO_IN), "the demo game didn't stick"
    time.sleep(12)                                            # random art arrives
    shot("store_1_table.png")
    p.js("window.lifepiNative && (window.lifepiNative.removed = true)")   # no (sample) ad in a store shot
    p.js(ESCAPE)
    time.sleep(2.5)
    shot("store_2_menu.png")
    p.js(ESCAPE)
    p.close()

    # 4. offline first start (app data cleared): the bundled runtime
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

    network(True)

    log = adb("logcat", "-d", "-b", "crash", check=False)
    assert PKG not in log, log[-3000:]
    print("no crashes; screenshots in", SHOTS)


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except BaseException:
        print("--- on failure ---")
        print("focus:", focus())
        print("crash log:", adb("logcat", "-d", "-b", "crash", check=False)[-2500:])
        print("LifePi log:", adb("logcat", "-d", "-s", "LifePi:I", "chromium:E", "AndroidRuntime:E", check=False)[-2000:])
        raise

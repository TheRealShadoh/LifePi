"""End-to-end test of the phone / browser build (needs internet for pygbag's runtime CDN).

    python web/build.py && python tests/web_test.py

Serves build/web on 127.0.0.1 (not "localhost": pygbag treats localhost:8xxx as its own dev
server), drives headless Chrome over the DevTools protocol at phone size with real touch
input, and checks: boot without Python errors, taps count once (touch and mouse), holds,
a flipped seat, Scryfall + art download through the browser's fetch, reload without a
"Leave site?" prompt, the game surviving the reload, the service worker, rotation and
starting offline. Screenshots go to $LIFEPI_SHOTS (or a temp dir).
Needs: pip install websocket-client; Chrome/Chromium (or set $CHROME).
"""
import base64, functools, http.server, json, os, shutil, socket, subprocess, sys, tempfile, threading, time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "build" / "web"
SHOTS = Path(os.environ.get("LIFEPI_SHOTS") or tempfile.mkdtemp())
SHOTS.mkdir(parents=True, exist_ok=True)
W, H = 390, 844                                    # an iPhone-sized portrait screen
BOOT_TIMEOUT = 120


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_chrome():
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if os.path.exists(p):
            return p
    sys.exit("Chrome/Chromium not found - set $CHROME")


class Browser:
    def __init__(self):
        self.port = free_port()
        self.proc = subprocess.Popen([find_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
                                      "--no-first-run", f"--remote-debugging-port={self.port}",
                                      f"--user-data-dir={tempfile.mkdtemp()}", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json").read())
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("Chrome did not start")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True, timeout=30)
        self.n, self.dialogs = 0, []
        for m in ("Page.enable", "Runtime.enable", "Network.enable"):
            self.call(m)

    def call(self, method, **params):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Page.javascriptDialogOpening":      # record, then dismiss
                self.dialogs.append(msg["params"].get("type"))
                self.n += 1
                self.ws.send(json.dumps({"id": self.n, "method": "Page.handleJavaScriptDialog",
                                         "params": {"accept": True}}))
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def size(self, w, h, angle=0):
        """Phone screen w x h, turned angle degrees (screen.orientation.angle: 90 = the phone
        turned counter-clockwise into landscape)."""
        kind = {0: "portraitPrimary", 90: "landscapePrimary", 180: "portraitSecondary", 270: "landscapeSecondary"}[angle]
        self.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=2, mobile=True,
                  screenOrientation={"type": kind, "angle": angle})
        self.call("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)

    def touch(self, x, y, ms=80):
        pt = [{"x": x, "y": y, "radiusX": 6, "radiusY": 6, "force": 1, "id": 1}]
        self.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=pt)
        time.sleep(ms / 1000)
        self.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])

    def click(self, x, y):
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    def shot(self, name):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        (SHOTS / name).write_bytes(base64.b64decode(data))

    def close(self):
        try:
            self.ws.close()
        finally:
            self.proc.kill()


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    port = free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Quiet, directory=str(WEB)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/", httpd


def wait_for(b, cond, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        if b.js(cond):
            return
        time.sleep(0.5)
    term = b.js('((document.querySelector(".xterm-rows") || {}).innerText || "").slice(-3000)')
    raise AssertionError(f"timed out waiting for {what}\n--- pygbag terminal ---\n{term}")


BOOTED = ("(() => { const c = document.querySelector('#canvas');"
          " return !!c && c.width > 1 && !!localStorage['lifepi-state']; })()")
LIVES = "JSON.parse(localStorage['lifepi-state']).players.map(p => p.life)"
TRACEBACK = "((document.querySelector('.xterm-rows') || {}).innerText || '').includes('Traceback')"


def lives_after(b, action, settle=0.9):
    action()
    time.sleep(settle)                             # the game autosaves within 0.5 s
    return b.js(LIVES)


def main():
    assert (WEB / "index.html").exists(), "build first: python web/build.py"
    url, httpd = serve()
    b = Browser()
    try:
        b.size(W, H)
        b.call("Page.navigate", url=url + "?selftest")
        wait_for(b, BOOTED, BOOT_TIMEOUT, "the app to boot")
        assert not b.js(TRACEBACK), b.js("document.querySelector('.xterm-rows').innerText.slice(-3000)")
        canvas = b.js("[document.querySelector('#canvas').width, document.querySelector('#canvas').height]")
        assert canvas[1] > canvas[0] > 300, canvas                       # fills a portrait phone
        b.shot("web_boot.png")
        print("boot ok, canvas", canvas)

        # 4 players, table seating: far row flipped. Points well inside each half.
        start = b.js(LIVES)
        assert start == [40, 40, 40, 40], start
        L = lives_after(b, lambda: b.touch(W * 0.88, H * 0.75))          # bottom-right, + half
        assert L == [40, 40, 41, 40], f"one touch must count once: {L}"
        L = lives_after(b, lambda: b.touch(W * 0.12, H * 0.75))          # bottom-left, - half
        assert L == [40, 40, 41, 39], L
        L = lives_after(b, lambda: b.click(W * 0.88, H * 0.75))          # mouse, desktop browsers
        assert L == [40, 40, 42, 39], f"one click must count once: {L}"
        L = lives_after(b, lambda: b.touch(W * 0.88, H * 0.75, ms=1200))  # hold: steps of 10, no tap
        gained = L[2] - 42
        assert gained in (10, 20, 30) and L[3] == 39, f"hold should add 10s: {L}"
        L2 = lives_after(b, lambda: b.touch(W * 0.12, H * 0.25))         # flipped: screen-left is its +
        assert L2[0] == 41 and L2[1:] == L[1:], f"flipped seat: {L2}"
        print("taps / holds / flipped seat ok", L2)

        # Scryfall search + card art through the browser's fetch (the ?selftest hook)
        wait_for(b, "!!localStorage['lifepi-selftest']", 60, "the Scryfall self-test")
        st = json.loads(b.js("localStorage['lifepi-selftest']"))
        assert st.get("ok"), st
        print("scryfall + art download in the browser ok", st)

        # reload: no "Leave site?" prompt, the game is still there, service worker in charge
        b.call("Page.reload")
        wait_for(b, BOOTED + " && !!navigator.serviceWorker.controller", BOOT_TIMEOUT, "the reload")
        assert not b.dialogs, f"unexpected dialog on reload: {b.dialogs}"
        time.sleep(1.5)
        assert b.js(LIVES) == L2, (b.js(LIVES), L2)
        print("reload keeps the game, no prompt, service worker active")

        # turning the phone (auto-rotate is off): the canvas still covers the screen, unstretched,
        # but the layout stays fixed to the phone - a phone lying on the table doesn't flip
        b.size(H, W, angle=90)                    # turned 90 degrees counter-clockwise
        wait_for(b, "document.querySelector('#canvas').width > document.querySelector('#canvas').height",
                 10, "the canvas to follow a rotation")
        time.sleep(0.6)
        c = b.js("(() => { const c = document.querySelector('#canvas'), r = c.getBoundingClientRect();"
                 " return [c.width, c.height, r.width, r.height, innerWidth, innerHeight]; })()")
        assert abs(c[2] - c[4]) <= 2 and abs(c[3] - c[5]) <= 2, f"canvas must cover the screen: {c}"
        assert abs(c[0] / c[1] - c[2] / c[3]) < 0.02, f"canvas would be stretched: {c}"
        before = b.js(LIVES)
        # the spot that was the bottom-right player's "+" in portrait (x 0.88, y 0.75) is, after a
        # counter-clockwise quarter turn, at screen (y * H, W - x * W) - and must still be their "+"
        L3 = lives_after(b, lambda: b.touch(0.75 * H, W - 0.88 * W))
        assert L3[2] == before[2] + 1 and L3[:2] + L3[3:] == before[:2] + before[3:],             f"layout must stay fixed to the phone when it turns: {before} -> {L3}"
        b.shot("web_landscape.png")
        b.size(W, H, angle=0)
        time.sleep(1)
        L2 = b.js(LIVES)
        print("rotation ok", c)

        # offline: the pygbag runtime (CDN) is in the service worker's cache, and with the site
        # itself gone (server stopped) the app still starts from that cache. (DevTools' offline
        # emulation is not used: it does not apply to the service worker's own fetches.)
        cached = b.js("caches.keys().then(ks => Promise.all(ks.map(k => caches.open(k).then(c => c.keys()))))"
                      ".then(ls => ls.flat().map(r => r.url))")
        cdn = [u for u in cached if "pygame-web.github.io" in u]
        assert any(u.endswith(".wasm") for u in cdn) and any("pythons.js" in u for u in cdn), cdn[:20]
        print(f"service worker cache: {len(cached)} files, {len(cdn)} from the pygbag CDN")
        httpd.shutdown()
        httpd.server_close()
        b.call("Page.reload")
        wait_for(b, BOOTED, BOOT_TIMEOUT, "an offline start")
        time.sleep(1.5)
        assert not b.js(TRACEBACK)
        assert b.js(LIVES) == L2
        b.shot("web_offline.png")
        print("offline start ok")
    finally:
        b.close()
    print("web build ok; screenshots in", SHOTS)


if __name__ == "__main__":
    main()

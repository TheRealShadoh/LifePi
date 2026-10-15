"""End-to-end test of the phone page (Phones & TV / QR code) in headless Chrome.

    python tests/phone_test.py         (needs Chrome / Chromium and websocket-client)

Starts LifePi with its phone server, opens the page at a small phone size with real touch
input while another "phone" changes a life total every 150 ms (a busy table), and checks:
the Game sheet keeps working (it isn't rebuilt under the finger), player count / seating /
life changes from the phone, a swipe that starts on a button scrolls instead of tapping,
the Lifetap-style commander damage sheet (tap a life total), and force-landscape.
"""
import json, os, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from web_test import Browser, SHOTS, free_port   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
W, H = 360, 640                                   # a small phone, so sheets have to scroll


def main():
    tmp = tempfile.mkdtemp()
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
               XDG_CONFIG_HOME=f"{tmp}/c", XDG_CACHE_HOME=f"{tmp}/k", XDG_DATA_HOME=f"{tmp}/d")
    port = free_port()
    app = subprocess.Popen([sys.executable, str(ROOT / "lifepi.py"), "--windowed", "800x480",
                            "--companion-port", str(port), "--no-random-art"], env=env)
    base, stop = f"http://127.0.0.1:{port}", threading.Event()
    b = None
    try:
        for _ in range(100):
            try:
                pin = json.load(open(f"{tmp}/c/lifepi/state.json"))["settings"]["companion_pin"]
                urllib.request.urlopen(f"{base}/api/state?k={pin}", timeout=2).read()
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("LifePi's phone server did not start")

        def state():
            return json.loads(urllib.request.urlopen(f"{base}/api/state?k={pin}", timeout=5).read())

        def busy():                                # another phone at the table
            k = 0
            while not stop.is_set():
                k += 1
                req = urllib.request.Request(f"{base}/api/act?k={pin}", method="POST",
                                             headers={"Content-Type": "application/json"},
                                             data=json.dumps({"op": "life", "seat": 0, "d": 1 if k % 2 else -1}).encode())
                try:
                    urllib.request.urlopen(req, timeout=2).read()
                except Exception:
                    pass
                time.sleep(0.15)
        threading.Thread(target=busy, daemon=True).start()

        b = Browser()
        b.size(W, H)
        if os.environ.get("CPU_THROTTLE"):          # e.g. 6: behave like a slow CI runner / old phone
            b.call("Emulation.setCPUThrottlingRate", rate=float(os.environ["CPU_THROTTLE"]))
        b.call("Page.navigate", url=f"{base}/?k={pin}")
        for _ in range(60):
            if b.js("document.querySelectorAll('.cell').length === 4"):
                break
            time.sleep(0.25)
        else:
            raise AssertionError("phone page did not load")

        def centre(sel, i=0):
            r = b.js(f"(() => {{ const e = document.querySelectorAll({json.dumps(sel)})[{i}]; if (!e) return null;"
                     f" const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }})()")
            assert r, f"not found: {sel}[{i}]"
            return r

        def still():
            """Wait until the sheet has stopped scrolling (a fling can glide on after a swipe, and
            a touch during it only stops the scroll - on a real phone too)."""
            last, same = None, 0
            for _ in range(60):
                top = b.js("(document.getElementById('box') || {}).scrollTop || 0")
                same = same + 1 if top == last else 0
                if same >= 3:
                    return
                last = top
                time.sleep(0.1)

        def tap(sel, i=0, ms=180, settle=0.45):   # a human-length tap, on screen, scrolling done
            still()
            # visible = inside the area that actually shows it: the sheet's scroll box (a button
            # scrolled half under the sheet's top edge has its centre over the backdrop), else the screen
            if not b.js(f"(() => {{ const e = document.querySelectorAll({json.dumps(sel)})[{i}]; if (!e) return true;"
                        f" const r = e.getBoundingClientRect(), box = e.closest('#box');"
                        f" const v = box ? box.getBoundingClientRect() : {{top: 0, bottom: innerHeight}};"
                        f" return r.top >= v.top && r.bottom <= v.bottom; }})()"):
                b.js(f"document.querySelectorAll({json.dumps(sel)})[{i}].scrollIntoView({{block: 'center'}})")
                still()
            x, y = centre(sel, i)
            b.touch(x, y, ms)
            time.sleep(settle)

        def swipe(x0, y0, x1, y1):
            b.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x0, "y": y0, "id": 1}])
            for k in range(1, 13):
                b.call("Input.dispatchTouchEvent", type="touchMove",
                       touchPoints=[{"x": x0 + (x1 - x0) * k / 12, "y": y0 + (y1 - y0) * k / 12, "id": 1}])
                time.sleep(0.016)
            b.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            time.sleep(0.6)

        # --- first join: the phone asks which seat is yours; your seat gets a blue outline
        for _ in range(20):
            if b.js("!!document.querySelector('#box .seatpick')"):
                break
            time.sleep(0.2)
        assert b.js("!document.getElementById('sheet').hidden && !!document.querySelector('#box .seatpick')"),             "the seat picker should open on first join"
        tap("#box .seatpick button", 1)
        assert b.js("document.getElementById('sheet').hidden"), "picking a seat closes the picker"
        me = b.js("(() => { const e = document.querySelectorAll('.cell .panel')[1]; return [e.className, getComputedStyle(e).boxShadow]; })()")
        assert "me" in me[0] and "74, 216, 255" in me[1], me
        b.js("location.reload()"); time.sleep(1)
        for _ in range(40):
            if b.js("document.querySelectorAll('.cell').length === 4"):
                break
            time.sleep(0.25)
        time.sleep(0.5)
        assert b.js("document.getElementById('sheet').hidden"), "the seat picker opens only the first time"
        assert b.js("document.querySelectorAll('.lifebtn').length") == 4, "the life numbers are buttons"
        print("first join asks for a seat once; your seat is outlined")

        # --- a drag that starts in a sheet and ends on the dimmed backdrop must not close it
        # (the browser sends that "click" to the backdrop; a sheet too short to scroll shows it)
        tap("#seatbtn")
        assert b.js("!document.getElementById('sheet').hidden"), "seat sheet did not open"
        bx = b.js("(() => { const r = document.getElementById('box').getBoundingClientRect(); return [r.left, r.top, r.width]; })()")
        swipe(bx[0] + bx[2] / 2, bx[1] + 40, bx[0] + bx[2] / 2, max(5, bx[1] - 60))
        assert b.js("!document.getElementById('sheet').hidden"), "dragging out of a sheet closed it"
        tap("#box .chipbtn.go")
        # ...but a real tap on the backdrop still closes it
        tap("#seatbtn")
        b.touch(W / 2, max(5, b.js("document.getElementById('box').getBoundingClientRect().top") - 30), 150)
        time.sleep(0.5)
        assert b.js("document.getElementById('sheet').hidden"), "tapping the backdrop should close the sheet"
        print("drag out of a sheet keeps it open; tap on the backdrop closes it")

        # --- Game sheet on a busy table ---------------------------------------------
        tap("#gamebtn")
        assert b.js("!document.getElementById('sheet').hidden"), "Game sheet did not open"
        b.js("window.__probe = document.querySelector('#box .chips.six button')")
        time.sleep(1.2)                            # ~8 updates from the other phone
        assert b.js("document.contains(window.__probe)"), "the open sheet is rebuilt on every update"
        tap("#box .chips.six button", 5)           # 6 players
        assert b.js("document.querySelector('#box .chips.six button.on').textContent") == "6"
        # a swipe that starts on a button scrolls the sheet and does not press the button
        x, y = centre("#box .chips.six button", 2)
        before_on = b.js("document.querySelector('#box .chips.six button.on').textContent")
        swipe(x, min(y + 220, H - 20), x, max(y - 200, 20))
        assert b.js("document.getElementById('box').scrollTop") > 0, "sheet did not scroll"
        assert b.js("document.querySelector('#box .chips.six button.on').textContent") == before_on
        tap("#box .big", 0)
        start = b.js("(() => { const e = document.querySelector('#box .big'), r = e.getBoundingClientRect();"
                     " return {text: e.textContent, top: r.top, bottom: r.bottom, view: innerHeight,"
                     " scroll: document.getElementById('box').scrollTop}; })()")
        assert "again" in start["text"], f"first tap on Start did not arm it: {start}"
        tap("#box .big", 0, settle=1.0)
        st = state()
        assert len(st["players"]) == 6, f"new game from the phone: {len(st['players'])} players"
        print("busy table: Game sheet stable, 6-player game started from the phone")
        tap("#gamebtn")
        tap("#box .chips.seat4 button", 1, settle=0.8)
        assert state()["seating"] == "ends"
        tap("#box .chips.seat4 button", 0, settle=0.8)
        tap("#box .chipbtn.go")
        print("seating changed from the phone")

        # --- Lifetap-style commander damage: tap a life total -------------------------
        target = 2
        l0 = state()["players"][target]["life"]
        # beside the life number is -/+; the number itself opens commander damage (like the Pi)
        lr = b.js(f"(() => {{ const r = document.querySelectorAll('.cell .life')[{target}].getBoundingClientRect(),"
                  f" n = document.querySelectorAll('.cell .lifebtn')[{target}].getBoundingClientRect();"
                  f" return [r.left, r.top, r.width, r.height, n.left, n.width]; }})()")
        b.touch(lr[0] + lr[2] * 0.02, lr[1] + lr[3] / 2, 120); time.sleep(0.5)
        assert state()["players"][target]["life"] == l0 - 1 and b.js("document.getElementById('sheet').hidden")
        b.touch(lr[0] + lr[2] * 0.98, lr[1] + lr[3] / 2, 120); time.sleep(0.5)
        assert state()["players"][target]["life"] == l0
        assert lr[5] < lr[2] * 0.75, lr                    # the number button leaves room for -/+
        tap(".cell .lifebtn", target)
        assert b.js("!!document.querySelector('#box .cmdgrid')"), "the life number did not open commander damage"
        assert state()["players"][target]["life"] == l0
        b.js("closeSheet()"); time.sleep(0.3)
        tap(".cell .strip.bot", target)              # "+ Cmdr dmg" opens commander damage
        assert b.js("!!document.querySelector('#box .cmdgrid')"), "the strip did not open commander damage"
        assert b.js("document.querySelectorAll('#box .ctile').length") == 6 + 1      # 6 commanders + poison
        b.shot("phone_cmd.png")
        tap("#box .ctile .half2.plus", 3)          # right half of seat 3's tile: +1
        p = state()["players"][target]
        assert p["cmd"][3] == 1 and p["life"] == l0 - 1, (p["cmd"], p["life"], l0)
        assert b.js("(document.querySelectorAll('#box .ctile')[3].querySelector('.cdelta') || {}).textContent") == "+1", \
            "the damage tile should show the running +1"
        tap("#box .ctile .half2.minus", 3)         # left half: -1
        p = state()["players"][target]
        assert p["cmd"][3] == 0 and p["life"] == l0, (p["cmd"], p["life"])
        halves = b.js("(() => { const t = document.querySelectorAll('#box .ctile')[3].getBoundingClientRect(),"
                      " m = document.querySelectorAll('#box .ctile .half2.minus')[3].getBoundingClientRect(),"
                      " p = document.querySelectorAll('#box .ctile .half2.plus')[3].getBoundingClientRect();"
                      " return [m.left - t.left, m.width / t.width, p.right - t.right, p.width / t.width]; })()")
        assert abs(halves[0]) < 4 and abs(halves[2]) < 4 and 0.45 < halves[1] < 0.55 and 0.45 < halves[3] < 0.55, halves
        x, y = centre("#box .ctile .half2.plus", 1)
        b.touch(x, y, 1300)                        # hold keeps adding
        time.sleep(0.6)
        held = state()["players"][target]["cmd"][1]
        assert held >= 3, f"hold should repeat: {held}"
        # partner commanders: switch one on for seat 1, its partner gets its own tile
        tap("#box .chipbtn", 0)                    # "Counters & more" -> that player's sheet
        tap("#box .chipbtn.go")
        tap(".cell .strip.top", 1)
        tap("#box .row.switch", 0, settle=0.8)     # "Partner commanders"
        assert state()["players"][1]["partner"], "partner switch from the phone"
        tap("#box .chipbtn.go")
        tap(".cell .strip.bot", target)
        assert b.js("document.querySelectorAll('#box .ctile').length") == 7 + 1  # 6 commanders + 1 partner + poison
        main_before = state()["players"][target]["cmd"][1]
        tap("#box .ctile .half2.plus", 2)          # seat 1's partner tile is right after seat 1's
        p = state()["players"][target]
        assert p["cmd2"][1] == 1 and p["cmd"][1] == main_before, (p["cmd"], p["cmd2"], main_before)
        b.shot("phone_cmd_partner.png")
        poison0 = state()["players"][target]["counters"]["poison"]
        tap("#box .ctile .half2.plus", 7)          # the poison tile
        assert state()["players"][target]["counters"]["poison"] == poison0 + 1
        tap("#box .chipbtn.go")
        print(f"commander damage sheet ok (left half -1, right half +1, hold +{held}, partner tile, strip)")

        # --- quick taps on the halves still change life --------------------------------
        l1 = state()["players"][4]["life"]
        tap(".cell .half.plus", 4, ms=90)
        assert state()["players"][4]["life"] == l1 + 1

        # --- undo from the phone (the other phone stops first: undo takes back the latest change)
        stop.set(); time.sleep(0.6)
        l3 = state()["players"][4]["life"]
        tap(".cell .half.plus", 4, ms=90)
        assert state()["players"][4]["life"] == l3 + 1
        for _ in range(20):
            if b.js("!document.getElementById('undobtn').hidden"):
                break
            time.sleep(0.1)
        tap("#undobtn")
        assert state()["players"][4]["life"] == l3, "Undo on the phone"
        assert "Undone" in b.js("document.getElementById('toast').textContent")
        tap("#gamebtn"); tap("#box .big", 2)          # Game > History
        assert b.js("document.querySelectorAll('#box .hist .row').length") >= 3
        b.shot("phone_history.png")
        tap("#box .chipbtn.go")
        b.js("onStatus('offline')")                   # lost the connection: a banner, a dimmed table
        assert b.js("!document.getElementById('offline').hidden && document.body.classList.contains('offline')")
        b.js("onStatus('online')")
        assert b.js("document.getElementById('offline').hidden")
        print("undo, history and the offline banner ok")

        # --- force landscape ------------------------------------------------------------
        tap("#rotbtn")
        assert b.js("document.documentElement.classList.contains('land90')")
        time.sleep(0.5)
        r = b.js("(() => { const r = document.body.getBoundingClientRect(); return [r.width, r.height]; })()")
        assert abs(r[0] - W) < 3 and abs(r[1] - H) < 3, f"turned page must still cover the screen: {r}"
        cell = b.js("(() => { const r = document.querySelector('.cell').getBoundingClientRect(); return [r.width, r.height]; })()")
        b.shot("phone_landscape.png")
        l2 = state()["players"][4]["life"]
        tap(".cell .half.plus", 4, ms=90)          # hit-testing in the turned page
        assert state()["players"][4]["life"] == l2 + 1, "taps in the turned page land on the wrong player"
        tap("#rotbtn"); tap("#rotbtn")
        assert not b.js("document.documentElement.className.includes('land')")
        print("force landscape ok", cell)

        # --- formats: 1 v 1 from the phone - no commander damage on the panels
        tap("#gamebtn")
        fmts = b.js("[...document.querySelectorAll('#box .chips')[0].querySelectorAll('button')].map(e => e.textContent)")
        assert fmts == ["Commander", "1 v 1", "Two-Headed Giant"], fmts
        tap("#box .chips button", 1)
        tap("#box .big", 0); tap("#box .big", 0, settle=1.0)       # a game in progress: two taps
        st = state()
        assert st["format"] == "duel" and len(st["players"]) == 2 and st["players"][0]["life"] == 20, st["format"]
        assert b.js("document.querySelector('.cell .pill.empty').textContent") == "+ Counters"
        tap(".cell .strip.bot", 0)
        assert b.js("!document.querySelector('#box .cmdgrid') && !!document.querySelector('#box .lifebig')")
        b.shot("phone_duel.png")
        tap("#box .chipbtn.go")
        print("formats from the phone ok")
    finally:
        stop.set()
        if b:
            b.close()
        app.terminate()
        try:
            app.wait(10)
        except subprocess.TimeoutExpired:
            app.kill()
    print("phone page ok; screenshots in", SHOTS)


if __name__ == "__main__":
    main()

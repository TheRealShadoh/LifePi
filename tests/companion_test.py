"""Phones / TV web server: SDL_VIDEODRIVER=dummy python3 tests/companion_test.py"""
import os, sys, tempfile, json, threading, time, urllib.request, urllib.error
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
tmp = tempfile.mkdtemp()
for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
    os.environ[k] = os.path.join(tmp, k)
os.environ.pop("LIFEPI_PUBLIC_URL", None)
shots = os.environ.get("LIFEPI_SHOTS", tmp); os.makedirs(shots, exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pygame, lifepi
from types import SimpleNamespace as NS

app = lifepi.App(NS(windowed="800x480", rotate=None, fps=30, companion_port=0))   # 0 = any free port
comp = app.companion
assert comp.running, comp.error
BASE, PIN = f"http://127.0.0.1:{comp.port}", app.settings["companion_pin"]
assert len(PIN) == 6 and PIN.isdigit()

def req(path, data=None, key="current"):
    key = PIN if key == "current" else key
    url = BASE + path + (("&" if "?" in path else "?") + "k=" + key if key is not None else "")
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()

def state():
    code, _, body = req("/api/state"); assert code == 200, code
    return json.loads(body)

def act(**a):
    code, _, _ = req("/api/act", a); assert code == 202, code
    app.tick_remote()                            # what the main loop does every frame

# pages are public, the API needs the PIN
for path in ("/", "/tv"):
    code, ctype, body = req(path, key=None)
    assert code == 200 and ctype.startswith("text/html") and b"LifePi" in body and b"/*CLIENT*/" not in body
assert req("/api/state", key=None)[0] == 401
assert req("/api/state", key="000000" if PIN != "000000" else "111111")[0] == 401
comp.fails.clear()
s = state()
assert [p["life"] for p in s["players"]] == [40] * 4 and s["players"][0]["color"].startswith("#")
assert s["join"].startswith("http://") and s["join"].endswith(f":{comp.port}")
print("auth / state ok")

# phones and the TV place every seat exactly where the device draws it
def device_rects(a):
    return [(s.box.x / a.W, s.box.y / a.H, s.box.w / a.W, s.box.h / a.H)
            for s in lifepi.layout(len(a.players), a.W, a.H, a.settings["seating"])]
def published(a):
    return [tuple(p["pos"]) for p in json.loads(comp.snap)["players"]]
def close(xs, ys): return all(abs(a - b) < 1e-3 for x, y in zip(xs, ys) for a, b in zip(x, y))
for seating in lifepi.SEATINGS:
    act(op="seating", value=seating); assert app.settings["seating"] == seating
    for n in range(1, lifepi.MAX_PLAYERS + 1):
        app.start_game(n, 40); app.tick_remote()
        assert close(published(app), device_rects(app)), (seating, n)
        snap = json.loads(comp.snap)
        assert snap["seating"] == seating and snap["max_players"] == 8
        for p in snap["players"]:
            if seating == "round" and n >= 3:     # wedge outline; the upright box sits inside it
                assert p["poly"] and len(p["poly"]) >= 3
                x, y, w, h = p["pos"]
                assert lifepi.point_in_poly((x + w / 2, y + h / 2), [tuple(q) for q in p["poly"]])
            else:
                assert p["poly"] is None
act(op="seating", value="nonsense"); assert app.settings["seating"] == "upright"
act(op="seating", value="table")
act(op="newgame", n=8, life=30, seating="ends")
assert len(app.players) == 8 and app.settings["seating"] == "ends" and len(state()["players"][7]["cmd"]) == 8
act(op="cmd", seat=0, src=7, d=3); assert app.players[0]["cmd"][7] == 3
act(op="newgame", n=99, life=40); assert len(app.players) == 8                     # clamped
# first player: hidden from phones while the device spins, revealed when it lands
g0 = state()["game"]
act(op="newgame", n=5, life=40)
s1 = state(); assert s1["game"] == g0 + 1 and s1["first"] == -1 and app.first_spinning() and 0 <= app.first < 5
app.first_anim["t0"] -= app.first_anim["land"] + 0.01; app.tick_first(); app.tick_remote()
assert state()["first"] == app.first
act(op="random_first", on=False); assert app.settings["random_first"] is False and state()["random_first"] is False
act(op="reset"); assert state()["first"] == -1
act(op="random_first", on=True); act(op="random_first", on="yes"); assert app.settings["random_first"] is True
act(op="seating", value="table")
for n in range(1, 7):
    app.start_game(n, 40); app.tick_remote()
    assert close(published(app), device_rects(app)), n
order = lambda: [p["seat"] for p in sorted(json.loads(comp.snap)["players"], key=lambda p: (p["pos"][1], p["pos"][0]))]
assert order() == [0, 1, 2, 5, 4, 3], order()        # clockwise: bottom row runs right to left
app.start_game(4, 40); app.tick_remote(); assert order() == [0, 1, 3, 2], order()
app.rotate_screen(); app.tick_remote()                  # portrait canvas: still the device's rectangles
assert close(published(app), device_rects(app))
for _ in range(3): app.rotate_screen()
app.tick_remote()
print("seat positions match the device ok")

# every action, applied by the main thread
act(op="life", seat=0, d=-3); assert app.players[0]["life"] == 37 and state()["players"][0]["life"] == 37
act(op="cmd", seat=1, src=1, d=2)               # own commander
assert app.players[1]["cmd"][1] == 2 and app.players[1]["life"] == 38
act(op="cmd", seat=1, src=2, d=21); assert state()["players"][1]["ko"] == "commander"
act(op="counter", seat=2, key="poison", d=4); assert app.players[2]["counters"]["poison"] == 4
act(op="name", seat=3, name="  Chris  "); assert app.players[3]["name"] == "Chris"
c0 = app.players[3]["color"]; act(op="color", seat=3); assert app.players[3]["color"] != c0
act(op="cmd_hits_life", on=False); assert app.settings["cmd_hits_life"] is False
act(op="cmd", seat=0, src=2, d=5); assert app.players[0]["life"] == 37    # no longer hits life
act(op="cmd_hits_life", on=True)
# bad input is ignored, never crashes the app
for bad in ({"op": "life", "seat": 9, "d": 1}, {"op": "life", "seat": 0, "d": "x"}, {"op": "life", "seat": True, "d": 1},
            {"op": "counter", "seat": 0, "key": "nope", "d": 1}, {"op": "cmd", "seat": 0, "src": 7, "d": 1},
            {"op": "newgame", "n": "6"}, {"op": "explode"}, {"op": "name", "seat": 0, "name": 5}):
    act(**bad)
assert app.players[0]["life"] == 37
assert req("/api/act", data=[1, 2])[0] == 400
act(op="life", seat=0, d=10 ** 9); assert app.players[0]["life"] == 37 + 999            # clamped
act(op="newgame", n=3, life=20); assert [p["life"] for p in app.players] == [20, 20, 20]
act(op="reset"); assert len(app.players) == 3
print("actions ok")

# live updates: the event stream pushes the state whenever it changes
events = []
def listen():
    with urllib.request.urlopen(f"{BASE}/api/events?k={PIN}", timeout=10) as r:
        for line in r:
            if line.startswith(b"data: "):
                events.append(json.loads(line[6:]))
                if len(events) == 2:
                    return
t = threading.Thread(target=listen, daemon=True); t.start()
for _ in range(100):
    if events: break
    time.sleep(0.02)
assert events and events[0]["players"][0]["life"] == 20 and comp.clients == 1
act(op="life", seat=1, d=-6)
t.join(5); assert len(events) == 2 and events[1]["players"][1]["life"] == 14
for _ in range(100):
    if comp.clients == 0: break
    time.sleep(0.02)
print("event stream ok")

# card art is served from the cache only
red = pygame.Surface((40, 30)); red.fill((200, 40, 40))
art = lifepi.CACHE_DIR / "x.png"; pygame.image.save(red, str(art))
app.set_art(0, str(art), "Card - illus. Someone"); app.tick_remote()
p0 = state()["players"][0]; assert p0["art"].startswith("/art/0?v=") and p0["credit"]
code, ctype, body = req(p0["art"]); assert code == 200 and ctype == "image/png" and body[:4] == b"\x89PNG"
outside = os.path.join(tmp, "secret.png"); pygame.image.save(red, outside)
app.players[1]["art"] = outside; assert req("/art/1")[0] == 404 and req("/art/9")[0] == 404
app.players[1]["art"] = None
print("art ok")

# the join screen on the device, with QR code when the qrcode package is installed
app.push("menu"); app.render(); pygame.image.save(app.canvas, os.path.join(shots, "menu_companion.png")); app.pop()
app.open_remote(); app.render(); pygame.image.save(app.canvas, os.path.join(shots, "remote.png"))
try:
    import qrcode
    assert app.qr_cache[1], "QR code not drawn"
except ImportError:
    pass
app.pop()

# new PIN locks old phones out; too many wrong PINs lock an address out
app.new_pin(); assert req("/api/state")[0] == 401
PIN = app.settings["companion_pin"]; comp.fails.clear(); assert req("/api/state")[0] == 200
for _ in range(lifepi.Companion.MAX_FAILS):
    req("/api/state", key="nope")
assert req("/api/state")[0] == 429, "lock-out"
comp.fails.clear()
print("pin / lock-out ok")

# switching off closes the port; the setting survives a restart
app.toggle_companion(); assert not comp.running and app.settings["companion"] is False
try:
    urllib.request.urlopen(BASE + "/", timeout=2); raise AssertionError("still serving")
except (urllib.error.URLError, ConnectionError, OSError):
    pass
app.toggle_companion(); assert comp.running
BASE = f"http://127.0.0.1:{comp.port}"
app.save_state()
app2 = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))       # no port: companion off
assert app2.companion is None and app2.settings["companion_pin"] == PIN

# phone actions also work through the real main loop
code, _, _ = req("/api/act", {"op": "life", "seat": 2, "d": 3}); assert code == 202
app.run(max_frames=3)
assert app.players[2]["life"] == 23 and not comp.running
print("companion ok"); print("SHOTS", shots)

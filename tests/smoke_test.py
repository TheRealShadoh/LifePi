"""Headless smoke test: SDL_VIDEODRIVER=dummy python3 tests/smoke_test.py"""
import os, sys, tempfile, time, json
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
tmp = tempfile.mkdtemp()
for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
    os.environ[k] = os.path.join(tmp, k)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pygame, lifepi
from types import SimpleNamespace as NS

# rotation math: a canvas pixel must round-trip through pygame's rotate
pygame.display.init()
for rot in (0, 90, 180, 270):
    c = pygame.Surface((80, 48)); c.fill((0, 0, 0)); c.set_at((7, 5), (255, 0, 0))
    out = pygame.transform.rotate(c, -rot)
    w, h = out.get_size()
    hits = [(x, y) for x in range(w) for y in range(h) if out.get_at((x, y))[0] == 255]
    assert lifepi.unrotate(*hits[0], w, h, rot) == (7, 5), rot
print("rotation mapping ok")

app = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
app.render()
def tap(pos, hold=0):
    app.pointer_down("t", pos)
    if hold:
        end = time.time() + hold
        while time.time() < end: app.tick_holds(); time.sleep(0.01)
    app.pointer_up("t", pos); app.render()

# 4 players, clockwise seats: 0 top-left, 1 top-right, 2 bottom-right, 3 bottom-left.
# Top row is flipped 180, so seat 0's "+" half is on the canvas LEFT.
tap((100, 120)); assert app.players[0]["life"] == 41
tap((300, 120)); tap((300, 120)); assert app.players[0]["life"] == 39
tap((700, 360)); assert app.players[2]["life"] == 41
tap((450, 360), hold=1.1); assert app.players[2]["life"] == 41 - 20, app.players[2]["life"]
print("tap / hold ok")

# rotate the screen 90: canvas becomes 480x800, a screen touch must land on the right panel
app.rotate_screen(); app.render()
assert (app.W, app.H) == (480, 800)
before = [p["life"] for p in app.players]
cx, cy = 360, 600            # canvas point: bottom-right seat (2), '+' half
sx, sy = 480 * 0 + (800 - 1 - cy), cx   # forward map for 90 cw on an 800x480 screen
assert app.to_canvas(sx, sy) == (cx, cy)
tap(app.to_canvas(sx, sy)); assert app.players[2]["life"] == before[2] + 1
for _ in range(3): app.rotate_screen()
app.render(); assert (app.W, app.H) == (800, 480)
print("screen rotation ok")

# player modal from a flipped seat: commander damage hits life
app.push("player", idx=0, angle=180); app.render()
plus = [z for z in app.zones if z[2]]           # zones with hold = -/+ buttons
life0 = app.players[0]["life"]
app.change_cmd(0, 1, 3); assert app.players[0]["life"] == life0 - 3 and app.players[0]["cmd"][1] == 3
assert len(plus) == 2 * (4 + 3)
app.pop()

# keyboard -> rename -> profile
app.push("player", idx=1, angle=0); app.render()
app.zones[1][1]()            # "Rename"
app.render(); m = app.modals[-1]; assert m["kind"] == "keyboard"
m["text"] = "Chris"; app.keyboard_ok(m); assert app.players[1]["name"] == "Chris" and "Chris" in app.profiles
app.modals.clear()

# mocked Scryfall search
red = pygame.Surface((62, 45)); red.fill((200, 40, 40)); pygame.image.save(red, os.path.join(tmp, "a.png"))
png = open(os.path.join(tmp, "a.png"), "rb").read()
def fake(url, timeout=10):
    if "cards/search" in url:
        return json.dumps({"data": [{"id": f"id{k}", "name": f"Card {k}", "set": "tst", "artist": "Some Artist",
                "image_uris": {"art_crop": "https://img/x.jpg"}} for k in range(8)]}).encode()
    return png
lifepi.http_get = fake
app.open_art_search(1, "card", 0)
grid = app.modals[-1]
for _ in range(100):
    if grid["status"] == "" : break
    time.sleep(0.05)
assert len(grid["items"]) == 8, grid
app.render(); pygame.image.save(app.canvas, os.path.join(tmp, "grid.png"))
app.grid_pick(grid, grid["items"][0]); assert app.players[1]["art"] and "Some Artist" in app.players[1]["credit"]
app.players[3]["counters"]["poison"] = 4; app.players[3]["life"] = 0
app.render(); pygame.image.save(app.canvas, os.path.join(tmp, "game.png"))
for kind, kw in (("menu", {}), ("newgame", dict(n=4, life=40)), ("dice", dict(result="17", sub="d20")),
                 ("player", dict(idx=1, angle=0)), ("keyboard", dict(title="Player name", text="Chr", cb=print))):
    app.push(kind, **kw); app.render(); pygame.image.save(app.canvas, os.path.join(tmp, kind + ".png")); app.pop()
for n in range(1, 7):
    app.start_game(n, 40); app.render()
app.save_state(); app2 = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert len(app2.players) == 6 and "Chris" in app2.profiles
print("modals / scryfall / persistence ok"); print("SHOTS", tmp)

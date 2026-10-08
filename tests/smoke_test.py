"""Headless smoke test: SDL_VIDEODRIVER=dummy python3 tests/smoke_test.py"""
import os, sys, tempfile, time, json
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
tmp = tempfile.mkdtemp()
for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
    os.environ[k] = os.path.join(tmp, k)
shots = os.environ.get("LIFEPI_SHOTS", tmp); os.makedirs(shots, exist_ok=True)
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
assert len(plus) == 2 * (4 + 4)                 # 4 counters + commander damage from all 4 seats
app.pop()
# a player's own commander counts too (e.g. after an opponent steals it)
life0 = app.players[0]["life"]
app.change_cmd(0, 0, 2); assert app.players[0]["cmd"][0] == 2 and app.players[0]["life"] == life0 - 2
app.change_cmd(0, 0, 19); assert app.ko_reason(app.players[0]) == "commander"
app.change_cmd(0, 0, -21); assert app.players[0]["cmd"][0] == 0 and app.players[0]["life"] == life0
app.players[2]["cmd"][:4] = [4, 9, 2, 21]       # several sources incl. self -> grid of mini tiles
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "cmd_tiles.png"))
assert app.ko_reason(app.players[2]) == "commander"
app.players[2]["cmd"] = [0] * 6; app.players[2]["life"] = 41 - 20

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
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "grid.png"))
app.grid_pick(grid, grid["items"][0]); assert app.players[1]["art"] and "Some Artist" in app.players[1]["credit"]
app.players[3]["counters"]["poison"] = 4; app.players[3]["life"] = 0
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "game.png"))
for kind, kw in (("menu", {}), ("newgame", dict(n=4, life=40)), ("dice", dict(result="17", sub="d20")),
                 ("player", dict(idx=1, angle=0)), ("keyboard", dict(title="Player name", text="Chr", cb=print))):
    app.push(kind, **kw); app.render(); pygame.image.save(app.canvas, os.path.join(shots, kind + ".png")); app.pop()

# search results are cached: repeating a search makes no API calls, and art isn't re-downloaded
calls = []
def counting(url, timeout=10):
    calls.append(url)
    if "cards/autocomplete" in url:
        return json.dumps({"object": "catalog", "data": ["Lightning Bolt", "Lightning Helix"]}).encode()
    return fake(url)
lifepi.http_get = counting
def wait_grid(g):
    for _ in range(100):
        if g["status"] in ("", "No cards found") or g["status"].startswith(("Offline", "Network")): return
        time.sleep(0.05)
app.open_art_search(1, "  CARD ", 0); grid = app.modals[-1]; wait_grid(grid)
assert len(grid["items"]) == 8 and calls == [], calls
app.pop()
# stale cache + no network -> saved results with a note
cache = lifepi.search_cache_path("card"); d = json.loads(cache.read_text()); d["t"] = 0; cache.write_text(json.dumps(d))
def offline(url, timeout=10): raise lifepi.urllib.error.URLError("no network")
lifepi.http_get = offline
app.open_art_search(1, "card", 0); grid = app.modals[-1]; wait_grid(grid)
assert len(grid["items"]) == 8 and grid["status"].startswith("Offline"), grid["status"]
app.pop(); lifepi.http_get = counting
print("search cache ok")

# guess-as-you-type: Card art keyboard asks Scryfall autocomplete after a pause, cached
def typed(m, text):
    app.set_text(m, text); time.sleep(lifepi.SUGGEST_DELAY + 0.05); app.tick_suggest()
    for _ in range(50):
        if m["sugg"] and m["sugg"][0] == "Lightning Bolt": break
        time.sleep(0.02)
app.push("player", idx=1, angle=0); app.render()
app.zones[2][1]()            # "Card art"
m = app.modals[-1]; assert m["kind"] == "keyboard" and "pick" in m
assert app.local_card_names("car")[0] == "Card 0"       # instant local guess from the cached search
app.set_text(m, "L"); app.set_text(m, "Li"); typed(m, "Lig")
assert m["sugg"][:2] == ["Lightning Bolt", "Lightning Helix"], m["sugg"]
assert len([c for c in calls if "autocomplete" in c]) == 1, calls   # debounced: one request
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "keyboard_suggest.png"))
calls.clear(); typed(m, "x"); typed(m, "Lig"); assert not [c for c in calls if "autocomplete" in c]
assert "Lightning Bolt" in app.local_card_names("bolt")
app.keyboard_pick(m, "Lightning Bolt"); grid = app.modals[-1]; wait_grid(grid)
assert grid["title"] == "Card art: Lightning Bolt"
assert any("cards/search" in c and "%21%22Lightning+Bolt%22" in c for c in calls), calls  # exact-name search
app.modals.clear()
# rename keyboard suggests saved profiles; picking one loads it
app.push("player", idx=2, angle=0); app.render(); app.zones[1][1]()
m = app.modals[-1]; assert "Chris" in m["sugg"]
app.keyboard_pick(m, "Chris"); assert app.players[2]["name"] == "Chris" and app.players[2]["art"]
app.players[2]["name"] = "Player 3"; app.modals.clear()
print("suggestions ok")

# long-press a name strip and drag it onto another seat to swap
app.render()
app.players[0]["cmd"][2], app.players[2]["cmd"][0] = 5, 7
n0, n2, life0 = app.players[0]["name"], app.players[2]["name"], app.players[0]["life"]
app.pointer_down("d", (200, 220))                    # seat 0 (flipped) name strip
end = time.time() + lifepi.HOLD_DELAY + 0.1
while time.time() < end: app.tick_holds(); time.sleep(0.01)
app.pointer_move("d", (600, 400)); app.render()      # over seat 2
pygame.image.save(app.canvas, os.path.join(shots, "drag.png"))
assert not app.modals                                # a hold must not open the sheet
app.pointer_up("d", (600, 400)); app.render()
assert app.players[2]["name"] == n0 and app.players[0]["name"] == n2
assert app.players[2]["life"] == life0
assert app.players[2]["cmd"][0] == 5 and app.players[0]["cmd"][2] == 7   # damage follows the players
tap((200, 220)); assert app.modals and app.modals[-1]["kind"] == "player" and app.modals[-1]["idx"] == 0
app.modals.clear()
print("seat drag ok")
for n in range(1, 7):
    app.start_game(n, 40); app.render()
app.save_state(); app2 = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert len(app2.players) == 6 and "Chris" in app2.profiles
assert "Lightning Helix" in app2.local_card_names("helix")   # autocomplete cache persisted
print("modals / scryfall / persistence ok"); print("SHOTS", shots)

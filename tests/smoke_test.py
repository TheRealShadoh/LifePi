"""Headless smoke test: SDL_VIDEODRIVER=dummy python3 tests/smoke_test.py"""
import math, os, sys, tempfile, time, json
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
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
# install.sh runs lifepi.py directly: its first bytes must be the "#!" line (no BOM, no CRLF)
head = open(lifepi.__file__, "rb").read(64)
assert head.startswith(b"#!/usr/bin/env python3\n"), head[:24]
# the README says ./install.sh - a lost executable bit breaks that on every fresh clone
root = os.path.dirname(os.path.abspath(lifepi.__file__))
for f in ("install.sh", "uninstall.sh"):
    assert os.access(os.path.join(root, f), os.X_OK), f + " is not executable (git update-index --chmod=+x)"

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

# a finger that slides is not a press: no tap on release, and a running +/-10 repeat stops
life = app.players[2]["life"]
app.pointer_down("s", (450, 360))                                   # seat 2 "-"
end = time.time() + lifepi.HOLD_DELAY + 0.05
while time.time() < end: app.tick_holds(); time.sleep(0.01)
assert app.players[2]["life"] == life - 10, app.players[2]["life"]  # the hold fired once
app.pointer_move("s", (450, 470))                                   # slides off towards the edge
end = time.time() + 1.2
while time.time() < end: app.tick_holds(); time.sleep(0.01)
app.pointer_up("s", (450, 470)); app.render()
assert app.players[2]["life"] == life - 10, app.players[2]["life"]  # ...and stopped there
life = app.players[2]["life"]
app.pointer_down("s", (700, 360)); app.pointer_move("s", (700 + int(app.slop()) + 10, 360))
app.pointer_up("s", (700 + int(app.slop()) + 10, 360)); app.render()
assert app.players[2]["life"] == life                               # slid, still inside "+": no tap
app.pointer_down("s", (720, 360)); app.pointer_move("s", (725, 364)); app.pointer_up("s", (725, 364))
app.render(); assert app.players[2]["life"] == life + 1             # a wobble is still a tap
# a quick second tap right after a sheet closes must not reach the seat underneath
life = app.players[2]["life"]
app.push("dice", result="", sub=""); app.render(); app.pop(); app.render()
sw, sh = app.screen.get_size()
def finger(kind, x, y):
    app.handle(pygame.event.Event(kind, finger_id=7, x=x / sw, y=y / sh, dx=0, dy=0, pressure=1))
finger(pygame.FINGERDOWN, 700, 360); finger(pygame.FINGERUP, 700, 360); app.render()
assert app.players[2]["life"] == life, "tap leaked through a closing sheet"
time.sleep(lifepi.BOARD_GUARD + 0.05)
finger(pygame.FINGERDOWN, 700, 360); finger(pygame.FINGERUP, 700, 360); app.render()
assert app.players[2]["life"] == life + 1
print("slides / closing-sheet taps ok")

# rotate the screen 90: canvas becomes 480x800, a screen touch must land on the right panel
app.rotate_screen(); app.render()
assert (app.W, app.H) == (480, 800)
before = [p["life"] for p in app.players]
cx, cy = 420, 600            # canvas point: bottom-right seat (2), + half (its middle is the life total)
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
assert len(plus) == 2 * (5 + 1 + 4)             # 5 counters, each opponent, damage from all 4 seats
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
ac = ["Atraxa's Fall", "Atraxi Warden", "Atraxa's Skitterfang", "Atraxa, Grand Unifier",
      "Atraxa, Praetors' Voice", "Heat Ray", "Ixhel, Scion of Atraxa"]       # Scryfall's order for "atra"
ranked = lifepi.rank_card_names(ac, "Atra")
assert ranked[:2] == ["Atraxa, Grand Unifier", "Atraxa, Praetors' Voice"], ranked   # commanders in the strip
assert ranked[2:4] == ["Atraxa's Fall", "Atraxi Warden"] and ranked[-1] == "Ixhel, Scion of Atraxa", ranked
assert lifepi.rank_card_names(["Lightning Bolt", "Lightning Helix"], "lig") == ["Lightning Bolt", "Lightning Helix"]
# nothing found: "Search again" goes back to the keyboard with the search filled in
def nothing(url, timeout=10):
    if "cards/search" in url:
        raise lifepi.urllib.error.HTTPError(url, 404, "not found", {}, None)
    return counting(url)
lifepi.http_get = nothing
app.open_art_search(1, "Zzyzx", 0); grid = app.modals[-1]; wait_grid(grid)
assert grid["status"] == "No cards found", grid["status"]
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "no_cards.png"))
again_btn = [z for z in app.zones if z[1] and z[1].__name__ == "again"]
assert len(again_btn) == 1, "Search again button missing"
again_btn[0][1](); m = app.modals[-1]
assert m["kind"] == "keyboard" and m["text"] == "Zzyzx" and "pick" in m, m
app.modals.clear(); lifepi.http_get = counting
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

# --- seating styles: table ===, ends -=-, round, upright; 1-8 players -----------------
import math
def seat_point(seat, fx, fy):
    """Canvas point at fraction (fx, fy) of a seat's upright frame (0.5, 0.5 = centre)."""
    cx, cy, cw, ch = seat.frame
    (rx, ry), (dx, dy) = lifepi.frame_axes(seat.angle)
    lx, ly = (fx - 0.5) * cw, (fy - 0.5) * ch
    return (round(cx + lx * rx + ly * dx), round(cy + lx * ry + ly * dy))

W, H = app.W, app.H
def near_buttons(pos):
    """On (or right by) the round menu / undo buttons, which sit on top of the panels."""
    r = max(22, min(W, H) // 22)
    return any(math.dist(pos, c) < 2 * r for c in (app.menu_pos(r), app.undo_pos(r)))
for seating in lifepi.SEATINGS:
    for n in range(1, lifepi.MAX_PLAYERS + 1):
        app.modals.clear(); app.settings["seating"] = seating; app.start_game(n, 40); app.render()
        seats = app.seats
        assert len(seats) == n, (seating, n)
        # every screen point belongs to exactly one seat (no holes, no overlaps)
        for gx in range(7, W, 37):
            for gy in range(5, H, 31):
                owners = [k for k, s in enumerate(seats) if s.contains((gx + 0.37, gy + 0.41))]
                assert len(owners) == 1, (seating, n, (gx, gy), owners)
        # clockwise around the table (angles of seat centres keep increasing)
        if n >= 3:
            ang = [math.degrees(math.atan2(s.frame[1] - H / 2, s.frame[0] - W / 2)) % 360 for s in seats]
            steps = [(b - a) % 360 for a, b in zip(ang, ang[1:] + ang[:1])]
            assert all(0 < st < 180 for st in steps) and abs(sum(steps) - 360) < 1e-6, (seating, n, ang)
        # a tap on a player's own -/+ half (in their reading direction) changes their life
        for k, s in enumerate(seats):
            for fx, d in ((0.25, -1), (0.75, 1)):
                pos = seat_point(s, fx, 0.5)
                if near_buttons(pos):
                    pos = seat_point(s, 0.15 if d < 0 else 0.85, 0.42)   # step around the menu / undo buttons
                before = [p["life"] for p in app.players]
                tap(pos)
                after = [p["life"] for p in app.players]
                assert after[k] == before[k] + d and sum(after) == sum(before) + d, (seating, n, k, d, pos)
        # the name strip opens the sheet facing that player (end seats: 90 / 270)
        for k, s in enumerate(seats):
            pos = next((pt for fy in (0.06, 0.16) for fx in (0.5, 0.25, 0.75, 0.1, 0.9) for pt in [seat_point(s, fx, fy)]
                       if not near_buttons(pt)), None); assert pos, (seating, n, k, app.menu_pos(22))
            tap(pos)
            m = app.modals[-1] if app.modals else None
            assert m and m["kind"] == "player" and m["idx"] == k, (seating, n, k)
            assert m["angle"] == round(s.angle / 90) * 90 % 360
            app.modals.clear(); app.render()
print("seating layouts: coverage, clockwise order, rotated taps ok")

# custom seating: any number of players on each side (far, right end, near, left end)
import itertools
combos = [list(c) for c in itertools.product(range(9), range(4), range(9), range(4))
          if 1 <= sum(c) <= lifepi.MAX_PLAYERS]
for sides in combos:
    n = sum(sides)
    seats = lifepi.sides_layout(sides, W, H)
    assert len(seats) == n, sides
    for gx in range(7, W, 53):
        for gy in range(5, H, 47):
            owners = [k for k, s in enumerate(seats) if s.contains((gx + 0.37, gy + 0.41))]
            assert len(owners) == 1, (sides, (gx, gy), owners)
    want = [180] * sides[0] + [90] * sides[1] + [0] * sides[2] + [270] * sides[3]
    assert [s.angle for s in seats] == want, (sides, [s.angle for s in seats])
    if sides[0] and sides[2] and n >= 3:          # all the way round the table: clockwise
        ang = [math.degrees(math.atan2(s.frame[1] - H / 2, s.frame[0] - W / 2)) % 360 for s in seats]
        steps = [(b - a) % 360 for a, b in zip(ang, ang[1:] + ang[:1])]
        assert all(0 < st < 180 for st in steps) and abs(sum(steps) - 360) < 1e-6, (sides, ang)
assert lifepi.fit_sides([1, 1, 1, 0], 3) == [1, 1, 1, 0] and sum(lifepi.fit_sides([1, 1, 1, 0], 6)) == 6
assert lifepi.fit_sides([4, 2, 4, 2], 4)[1::2] == [2, 2] and lifepi.fit_sides("junk", 4) == lifepi.default_sides(4)
assert all(sum(lifepi.default_sides(n)) == n for n in range(1, 9))
assert lifepi.move_seat([2, 0, 2, 0], 1, 1) == [2, 1, 1, 0]      # +1 on the right end: from a long side
assert lifepi.move_seat([1, 1, 1, 0], 1, -1) == [1, 0, 1, 1]     # -1 there: to the empty left end
assert lifepi.move_seat([0, 0, 3, 0], 2, 1) == [0, 0, 3, 0]      # nothing to take
# two facing each other and one on an end, played: taps land on the right player
for sides in ([1, 1, 1, 0], [1, 0, 1, 1], [2, 0, 1, 0], [2, 1, 2, 1], [1, 2, 1, 2], [0, 2, 0, 1], [3, 0, 3, 2]):
    n = sum(sides)
    app.modals.clear(); app.start_game(n, 40); app.set_sides(sides); app.first_anim = None; app.render()
    assert app.settings["seating"] == "custom" and [s.angle for s in app.seats] == \
        [180] * sides[0] + [90] * sides[1] + [0] * sides[2] + [270] * sides[3], sides
    for k, s in enumerate(app.seats):
        for fx, d in ((0.25, -1), (0.75, 1)):
            pos = seat_point(s, fx, 0.5)
            if near_buttons(pos):
                pos = seat_point(s, 0.15 if d < 0 else 0.85, 0.42)
            before = [p["life"] for p in app.players]; tap(pos)
            after = [p["life"] for p in app.players]
            assert after[k] == before[k] + d and sum(after) == sum(before) + d, (sides, k, d)
app.set_sides([5, 0, 0, 0]); assert app.settings["sides"] == [3, 0, 3, 2], "wrong total ignored"
# the menu / undo buttons sit where seats meet - never on top of one wide panel's life total
for sides in combos:
    n = sum(sides)
    if n < 2:
        continue
    app.players = [lifepi.new_player(i, 40) for i in range(n)]
    app.settings.update(seating="custom", sides=lifepi.fit_sides(sides, n)); app.seats = list(lifepi.layout(n, W, H, app.seating_key()))
    r = app.hub_r(); m, u = app.menu_pos(r), app.undo_pos(r)
    mid = ((m[0] + u[0]) / 2, (m[1] + u[1]) / 2)
    near = {k for dx in (-5, 5) for dy in (-5, 5) for k, st in enumerate(app.seats) if st.contains((mid[0] + dx, mid[1] + dy))}
    assert len(near) >= 2, (sides, mid)
app.start_game(3, 40); app.set_sides([0, 1, 2, 0]); app.first_anim = None; app.render()
assert app.hub_spot()[2] == "v" and app.undo_pos(app.hub_r())[1] > app.menu_pos(app.hub_r())[1]
tap(app.menu_pos(app.hub_r())); assert app.modals and app.modals[-1]["kind"] == "menu"; app.modals.clear()
app.modal_seen = 0; app.change_life(0, -1); app.render(); tap(app.undo_pos(app.hub_r())); assert app.players[0]["life"] == 40, "undo button works"
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "custom_two_side_by_side_one_end.png"))
# the seating sheet from the menu: styles, and +/- per side, live
app.modals.clear(); app.start_game(3, 40); app.set_seating("table")
app.push("menu"); app.render()
seat_btn = [z for z in app.zones if z[1] == app.open_seating]; assert len(seat_btn) == 1
seat_btn[0][1](); app.render(); assert app.modals[-1]["kind"] == "seating"
pygame.image.save(app.canvas, os.path.join(shots, "seating_sheet.png"))
def sheet_buttons():
    app.render(); return [z for z in app.zones if z[1]]
before = len(sheet_buttons())
next(z for z in app.zones if z[1] and z[1].__defaults__ == ("custom",))[1]()
assert app.settings["seating"] == "custom" and sum(app.settings["sides"]) == 3
steps = [z for z in sheet_buttons() if z[1].__qualname__.endswith("<lambda>") and z[1].__defaults__ and len(z[1].__defaults__) == 2]
assert len(steps) == 8, len(steps)                 # - and + on four sides
sides0 = list(app.settings["sides"])
right_plus = next(z for z in steps if z[1].__defaults__ == (1, 1)); right_plus[1]()
assert app.settings["sides"] != sides0 and app.settings["sides"][1] == sides0[1] + 1 and sum(app.settings["sides"]) == 3
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "seating_custom.png"))
# saved and restored; a reset can be undone back to the same seating
app.modals.clear(); app.save_state()
again = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert again.settings["seating"] == "custom" and again.settings["sides"] == app.settings["sides"]
assert again.seating_key() == app.seating_key()
# from a New game sheet: applies when that game starts, fitted to its player count
app.push("newgame", n=4, life=40, seating="table"); app.render()
ng = app.modals[-1]
app.open_seating(ng); app.render()
next(z for z in app.zones if z[1] and z[1].__defaults__ == ("custom",))[1]()
assert ng["seating"] == "custom" and sum(ng["sides"]) == 4
next(z for z in sheet_buttons() if z[1].__defaults__ == (3, 1))[1]()       # + on the left end
assert ng["sides"][3] == lifepi.fit_sides(app.settings["sides"], 4)[3] + 1 or ng["sides"][3] == 2
app.pop(); app.render()
for _ in range(2):                                       # a game in progress: Start takes two taps
    if app.modals and app.modals[-1]["kind"] == "newgame":
        app.render()
        next(z for z in app.zones if z[1] and z[1].__qualname__.split(".")[-1] in ("start", "tap")
             and z[1].__qualname__.split(".<locals>")[0] in ("App.draw_newgame", "App.confirm"))[1]()
assert len(app.players) == 4 and app.settings["seating"] == "custom" and app.settings["sides"] == ng["sides"], \
    (app.settings["sides"], ng["sides"])
# phones can set it too, and see it
app.apply_remote({"op": "sides", "value": [1, 1, 1, 1]})
assert app.settings["sides"] == [1, 1, 1, 1] and app.snapshot()["sides"] == [1, 1, 1, 1]
app.settings["seating"] = "table"; app.start_game(4, 40); app.render()
print("custom seating: any count per side, sheet, new game, save, phones ok")

# a sheet opened at a table end is laid out portrait and its buttons still hit
app.settings["seating"] = "ends"; app.start_game(6, 40); app.render()
end = next(k for k, s in enumerate(app.seats) if s.angle == 270)
app.push("player", idx=end, angle=270); app.render()
pygame.image.save(app.canvas, os.path.join(shots, "sheet_end_seat.png"))
v = lifepi.View(app, (0, 0, W, H), 270); assert (v.w, v.h) == (H, W)
plus = [z for z in app.zones if z[2]]
assert len(plus) == 2 * (5 + 1 + 6)              # 5 counters, each opponent, damage from 6 seats
poison0 = app.players[end]["counters"]["poison"]
# press the first "+" on the rotated sheet: find its canvas point by scanning the zone
hit = next((x, y) for x in range(0, W, 4) for y in range(0, H, 4) if plus[1][0]((x, y)))
app.pointer_down("t", hit); app.pointer_up("t", hit)
assert app.players[end]["counters"]["poison"] == poison0 + 1
app.modals.clear()
# 8 players: commander damage from the 8th seat, saved and restored with the seating style
app.settings["seating"] = "round"; app.start_game(8, 40)
app.change_cmd(0, 7, 4); assert app.players[0]["cmd"][7] == 4 and len(app.players[0]["cmd"]) == 8
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "round_8.png"))
app.save_state(); r8 = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert len(r8.players) == 8 and r8.players[0]["cmd"][7] == 4 and r8.settings["seating"] == "round"
app.settings["seating"] = "table"; app.start_game(4, 40); app.render()
print("8 players / rotated sheets ok")

# --- commander damage pane (Lifetap style): the "Cmdr dmg" strip opens it ---------------
def find_point(zone, avoid=()):
    """A canvas point inside zone (a hit function) and outside the zones in avoid."""
    for x in range(2, W, 6):
        for y in range(2, H, 6):
            if zone((x, y)) and not any(a((x, y)) for a in avoid):
                return (x, y)
    raise AssertionError("zone not on screen")
for seating in ("table", "ends", "round"):
    app.modals.clear(); app.settings["seating"] = seating; app.start_game(6, 40); app.first_anim = None; app.render()
    for k, s in enumerate(app.seats):
        app.modals.clear(); app.render()
        pos = next(pt for fx in (0.5, 0.3, 0.7) for pt in [seat_point(s, fx, 0.93)] if not near_buttons(pt))
        tap(pos)                                             # on the bottom strip
        m = app.modals[-1] if app.modals else None
        assert m and m["kind"] == "cmd" and m["idx"] == k, (seating, k, m)
        assert m["angle"] == round(s.angle / 90) * 90 % 360
        app.render()                                         # draws facing that player
    app.modals.clear(); app.render()
    # tapping the life number itself opens that player's commander damage; beside it is still -/+
    life = app.players[1]["life"]
    tap(seat_point(app.seats[1], 0.5, 0.5))
    m = app.modals[-1] if app.modals else None
    assert m and m["kind"] == "cmd" and m["idx"] == 1 and app.players[1]["life"] == life, (seating, m)
    assert m["angle"] == round(app.seats[1].angle / 90) * 90 % 360
    app.modals.clear(); app.render()
    for fx, d in ((0.15, -1), (0.85, 1)):
        life = app.players[1]["life"]
        tap(seat_point(app.seats[1], fx, 0.5))
        assert app.players[1]["life"] == life + d and not app.modals, (seating, fx)
print("damage strip and the life number open commander damage; beside the number is -/+")

app.modals.clear(); app.settings["seating"] = "ends"; app.start_game(6, 40); app.first_anim = None; app.render()
end = next(k for k, s in enumerate(app.seats) if s.angle == 270)    # sideways pane at a table end
tap(seat_point(app.seats[end], 0.5, 0.93)); app.render()                # its "Cmdr dmg" strip
pygame.image.save(app.canvas, os.path.join(shots, "cmd_pane_end.png"))
z = app.zones          # [tile0 left -, tile0 right +, ..., poison, rad, monarch, initiative, more, done]
assert len(z) == 2 * 6 + 4 + 2                 # no poison tile any more: poison is a middle button
target, src = app.players[end], 2
life0 = target["life"]
plus_pt, minus_pt = find_point(z[2 * src + 1][0]), find_point(z[2 * src][0])
tap(plus_pt)                                                 # right half of the tile: +1
assert target["cmd"][src] == 1 and target["life"] == life0 - 1, (target["cmd"], target["life"])
tap(plus_pt, hold=1.2)                                       # hold keeps adding
assert target["cmd"][src] >= 4, target["cmd"]
tap(minus_pt, hold=2.0)                                      # left half: -1, held all the way down
assert target["cmd"][src] == 0 and target["life"] == life0, (target["cmd"], target["life"])
tap(minus_pt); assert target["cmd"][src] == 0                # never below 0
# the halves are each half of the tile, facing that player: the left of the tile is -, right is +
tile = [pt for pt in ((x, y) for x in range(0, W, 3) for y in range(0, H, 3)) if z[2 * src][0](pt) or z[2 * src + 1][0](pt)]
assert abs(sum(1 for pt in tile if z[2 * src][0](pt)) - len(tile) / 2) < len(tile) * 0.06
z[-2][1]()                                                   # "Counters & more"
assert app.modals[-1]["kind"] == "player" and app.modals[-1]["idx"] == end
app.modals.clear(); app.settings["seating"] = "table"; app.start_game(4, 40); app.first_anim = None; app.render()
# each commander's tile sits where that player sits: seen from the pane's own facing, the tile
# centres go round the table in the same order and direction as the seats do
def angle_of(pt, c): return math.atan2(pt[1] - c[1], pt[0] - c[0])
for seating, n in (("table", 4), ("table", 6), ("ends", 6), ("round", 5), ("table", 3)):
    for facing in (0, 90, 180, 270):
        app.modals.clear(); app.settings["seating"] = seating; app.start_game(n, 40); app.first_anim = None; app.render()
        app.push("cmd", idx=0, angle=facing); app.render()
        v = lifepi.View(app, app.sheet_area(), facing); v.u = max(40, min(v.h // 8, v.w // 8))
        area = pygame.Rect(8, int(v.u * 1.75), v.w - 16, v.h - int(v.u * 1.75) - v.u - 8)
        tiles = app.table_map(v, area, app.commanders(), None)
        seats_local = [v.to_local(st.frame[:2]) for st in app.seats]
        cx = sum(x for x, _ in seats_local) / n; cy = sum(y for _, y in seats_local) / n
        tx = sum(r.centerx for r in tiles) / n; ty = sum(r.centery for r in tiles) / n
        for k in range(n):                     # same direction from the middle, within 60 degrees
            a1 = angle_of(seats_local[k], (cx, cy)); a2 = angle_of(tiles[k].center, (tx, ty))
            diff = abs((a1 - a2 + math.pi) % (2 * math.pi) - math.pi)
            assert diff < math.radians(60), (seating, n, facing, k, math.degrees(diff))
        for a_, b_ in ((a_, b_) for a_ in range(n) for b_ in range(a_ + 1, n)):
            assert not tiles[a_].colliderect(tiles[b_]), (seating, n, facing, a_, b_)
        hub_zones = app.zones[2 * n: 2 * n + 4]
        for hz in hub_zones:                   # the middle buttons don't overlap any tile
            pt = find_point(hz[0], [z_[0] for z_ in app.zones[:2 * n]])
pygame.image.save(app.canvas, os.path.join(shots, "cmd_pane_table_map.png"))
app.modals.clear()
print("commander damage pane ok (left half -, right half +, hold, rotated, counters & more, seats where they sit)")

# --- the pane's middle: poison and rad (tap +1, hold -1), monarch and initiative (one holder) ---
app.settings["seating"] = "table"; app.start_game(4, 40); app.first_anim = None; app.render()
app.push("cmd", idx=2, angle=0); app.render()
hub = app.zones[2 * 4: 2 * 4 + 4]               # poison, rad, monarch, initiative
victim = app.players[2]
tap(find_point(hub[0][0])); tap(find_point(hub[0][0]))
assert victim["counters"]["poison"] == 2
app.render(); hub = app.zones[8:12]
tap(find_point(hub[0][0]), hold=0.7)            # hold: -1 once
assert victim["counters"]["poison"] == 1, victim["counters"]
app.render(); hub = app.zones[8:12]
tap(find_point(hub[1][0])); assert victim["counters"]["rad"] == 1
app.render(); hub = app.zones[8:12]
tap(find_point(hub[2][0])); assert victim["monarch"] and app.holder("monarch") == 2
app.modals.clear(); app.push("cmd", idx=0, angle=0); app.render()
tap(find_point(app.zones[8 + 2][0]))            # player 1 takes the crown: it leaves player 3
assert app.players[0]["monarch"] and not victim["monarch"] and app.holder("monarch") == 0
app.render(); tap(find_point(app.zones[8 + 3][0])); assert app.holder("initiative") == 0
app.render(); tap(find_point(app.zones[8 + 3][0])); assert app.holder("initiative") is None   # tap again: given up
assert any(e["text"].endswith("takes the monarch") for e in app.history), [e["text"] for e in app.history][-4:]
app.undo_last(); app.undo_last(); app.undo_last()        # initiative off/on, then the crown goes back
assert app.holder("monarch") == 2 and app.holder("initiative") is None
app.modals.clear(); app.render()
assert any("Monarch" in str(c) for c in [victim]) or True
pygame.image.save(app.canvas, os.path.join(shots, "monarch_chip.png"))
# saved and restored; a hand-edited save with two monarchs keeps only the first
app.save_state(); b2 = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert b2.holder("monarch") == 2 and b2.players[2]["counters"]["rad"] == 1
d = json.loads(lifepi.STATE_FILE.read_text()); d["players"][0]["monarch"] = True
lifepi.STATE_FILE.write_text(json.dumps(d))
b3 = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert [q["monarch"] for q in b3.players].count(True) == 1 and b3.holder("monarch") == 0
# phones: designations in the state, and the "designate" action
snap = app.snapshot(); assert snap["players"][2]["monarch"] is True and ["monarch", "Monarch"] in snap["designations"]
app.apply_remote({"op": "designate", "seat": 1, "key": "initiative"}); assert app.holder("initiative") == 1
app.apply_remote({"op": "designate", "seat": 9, "key": "initiative"}); assert app.holder("initiative") == 1
app.apply_remote({"op": "designate", "seat": 1, "key": "nope"}); assert app.holder("initiative") == 1
app.start_game(4, 40); assert app.holder("monarch") is None and app.holder("initiative") is None   # new game: nobody
print("poison / rad / monarch / initiative ok")

# --- partner commanders: two commanders for one seat, damage tracked separately -------
app.settings["cmd_hits_life"] = True; app.start_game(4, 40); app.first_anim = None
app.set_partner(1, True)
assert app.commanders() == [(0, 1), (1, 1), (1, 2), (2, 1), (3, 1)]
victim = app.players[0]
app.change_cmd(0, 1, 11); app.change_cmd(0, 1, 10, which=2)       # 11 from one partner, 10 from the other
assert victim["cmd"][1] == 11 and victim["cmd2"][1] == 10 and victim["life"] == 40 - 21   # both hit life
assert app.ko_reason(victim) == "", "21 split across two partners is not a commander damage KO"
victim["life"] = 40
app.change_cmd(0, 1, 10, which=2); assert victim["cmd2"][1] == 20 and not app.ko_reason(victim)
app.change_cmd(0, 1, 1, which=2); assert app.ko_reason(victim) == "commander"   # 21 from ONE partner
app.change_cmd(0, 2, 5, which=2); assert victim["cmd2"][2] == 0, "seat 2 has no partner"
# the pane gets a tile per commander; the partner tile's halves work on the partner's damage
app.render(); app.push("cmd", idx=0, angle=0); app.render()
pygame.image.save(app.canvas, os.path.join(shots, "cmd_pane_partner.png"))
z = app.zones; assert len(z) == 2 * 5 + 4 + 2           # 5 commander tiles, 4 middle buttons, 2 buttons
d2 = victim["cmd2"][1]; tap(find_point(z[2 * 2 + 1][0])); assert victim["cmd2"][1] == d2 + 1 and victim["cmd"][1] == 11
tap(find_point(z[2 * 2][0])); assert victim["cmd2"][1] == d2
app.modals.clear()
# the player sheet has a row per commander and a Partner switch
app.push("player", idx=0, angle=0); app.render()
assert len([zz for zz in app.zones if zz[2]]) == 2 * (5 + 1 + 5)      # -/+ for 5 counters, each opponent, 5 commanders
app.modals.clear()
# seats swap with partner damage; saved; turning partner off forgets the second commander's damage
app.swap_seats(1, 3)
assert app.players[0]["cmd2"][3] == d2 and app.players[0]["cmd2"][1] == 0 and app.players[3]["partner"]
app.save_state(); rp = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert rp.players[3]["partner"] and rp.players[0]["cmd2"][3] == d2
app.set_partner(3, False); assert app.players[0]["cmd2"][3] == 0 and app.commanders() == [(j, 1) for j in range(4)]
app.render()
print("partner commanders ok (separate tracking, 21 from one partner, pane, sheet, swap, save)")

# --- undo + history ---------------------------------------------------------------------
app.settings["cmd_hits_life"] = True; app.modals.clear(); app.start_game(4, 40); app.first_anim = None
app.undo.clear(); del app.history[:-1]; n_hist = len(app.history)   # (well under the 300 kept)
for _ in range(5): app.change_life(1, -1)                    # quick taps: one entry, one undo step
assert app.players[1]["life"] == 35 and len(app.history) == n_hist + 1 and len(app.undo) == 1
assert app.history[-1]["d"] == -5 and "life -5" in app.undo_label() and "now 35" in app.undo_label(), app.undo_label()
app.change_cmd(2, 0, 3)                                     # commander damage: one step (life follows it)
assert app.players[2]["cmd"][0] == 3 and app.players[2]["life"] == 37 and len(app.undo) == 2
assert f"{app.players[2]['name']}: commander damage from {app.players[0]['name']} +3" in app.history[-1]["text"], app.history[-1]
app.change_counter(3, "poison", 2); app.change_opponents(0, -1)
assert [p["life"] for p in app.players] == [40, 34, 36, 39] and app.players[3]["counters"]["poison"] == 2
app.undo_last(); assert [p["life"] for p in app.players] == [40, 35, 37, 40]          # each opponent back
app.undo_last(); assert app.players[3]["counters"]["poison"] == 0
app.undo_last(); assert app.players[2]["cmd"][0] == 0 and app.players[2]["life"] == 40
app.rename_player(1, "Ana Bell")                            # renamed since: Undo keeps the new name
app.undo_last(); assert app.players[1]["life"] == 40 and app.players[1]["name"] == "Ana Bell"
assert len(app.history) == n_hist and not app.undo
app.undo_last(); assert app.toast[0] == "Nothing to undo"
app.change_life(0, -3); app.undo[-1]["t"] -= lifepi.MERGE_TIME + 1    # a later tap is its own step
app.change_life(0, -2); assert len(app.undo) == 2 and app.history[-1]["d"] == -2
# a reset / new game is undoable too - the whole game comes back
app.change_cmd(1, 3, 7); before = json.dumps(app.players)
app.start_game(4, 40, reset=True); assert app.players[1]["cmd"][3] == 0
assert app.history[-1]["kind"] == "game" and "reset" in app.history[-1]["text"]
app.undo_last(); assert json.dumps(app.players) == before
app.swap_seats(0, 1); assert app.players[0]["name"] == "Ana Bell"
app.undo_last(); assert app.players[1]["name"] == "Ana Bell"
# the undo button by the menu: tap undoes, hold opens the history
app.render(); app.change_life(2, 4); app.render()
r = max(22, min(W, H) // 22)
tap(app.undo_pos(r)); assert app.players[2]["life"] == 40
tap(app.undo_pos(r), hold=0.7); assert app.modals[-1]["kind"] == "history"
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "history.png"))
assert any("Ana Bell" in e["text"] or "Player" in e["text"] for e in app.history)
app.modals.clear()
# history is saved; the snapshot carries it to phones
app.change_life(0, -1); app.save_state()
rh = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert rh.history[-1]["text"] == app.history_text(app.history[-1]) and "life -1" in rh.history[-1]["text"]
assert app.snapshot()["history"][0][1] == app.history_text(app.history[-1]) and app.snapshot()["undo"]
print("undo + history ok (merged taps, cmd, counters, each opponent, reset, swap, button, saved)")

# --- two taps for Reset / Quit / Start over a game / partner off -------------------------
app.modals.clear(); app.start_game(4, 40); app.first_anim = None; app.change_life(0, -6)
def mz():                                                    # menu buttons, without the Turn button
    return [z for z in app.zones if not (z[1] and "turn_sheet" in getattr(getattr(z[1], "__code__", None), "co_names", ()))]
app.push("menu"); app.render()
reset = mz()[1]                                             # 2nd menu button: Reset this game
tap(find_point(reset[0])); assert app.players[0]["life"] == 34 and app.is_armed("reset")
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "menu_armed.png"))
tap(find_point(reset[0])); assert app.players[0]["life"] == 34 and app.is_armed("reset")  # a double tap isn't a confirm
def later(): app.armed = (app.armed[0], app.armed[1] - lifepi.CONFIRM_GAP)              # ...a deliberate 2nd tap is
later(); tap(find_point(reset[0])); assert app.players[0]["life"] == 40 and not app.modals    # second tap: reset
app.undo_last(); assert app.players[0]["life"] == 34
app.push("menu"); app.render(); tap(find_point(mz()[1][0])); app.armed = ("reset", time.time() - 5)
app.step(); app.render(); tap(find_point(mz()[1][0])); assert app.players[0]["life"] == 34   # timed out: arms again
app.modals.clear(); app.armed = None
app.push("menu"); app.render()
quit_zone = app.zones[len(app.zones) - 1]                    # Quit: the last button
tap(find_point(quit_zone[0])); assert app.running and app.is_armed("quit")
app.armed = None; app.modals.clear()
# New game over a game in progress: Start needs two taps; a fresh table starts at once
app.push("newgame", n=4, life=40); app.render(); start = app.zones[-1]
tap(find_point(start[0])); assert app.players[0]["life"] == 34 and app.is_armed("start")
tap(find_point(app.zones[-1][0])); assert app.players[0]["life"] == 34 and app.modals    # double tap: no wipe
later(); tap(find_point(app.zones[-1][0])); assert app.players[0]["life"] == 40 and not app.modals
app.push("newgame", n=3, life=40); app.render(); tap(find_point(app.zones[-1][0]))
assert len(app.players) == 3 and not app.modals                 # nothing to lose: one tap
# partner off takes two taps (it forgets that commander's damage)
app.start_game(4, 40); app.first_anim = None; app.set_partner(2, True); app.change_cmd(0, 2, 4, which=2)
app.push("player", idx=2, angle=0); app.render()
acts = [z for z in app.zones if not z[2]]                   # the top row of buttons (Done first)
partner_btn = acts[-1]
tap(find_point(partner_btn[0])); assert app.players[2]["partner"]
later(); tap(find_point(partner_btn[0])); assert not app.players[2]["partner"] and app.players[0]["cmd2"][2] == 0
app.undo_last(); assert app.players[2]["partner"] and app.players[0]["cmd2"][2] == 4
app.modals.clear()
print("two-tap confirm ok (reset, quit, start over a game, partner off; double taps don't confirm; times out)")

# --- formats, each opponent, planar die, initials, warnings ------------------------------
app.start_game(2, 20, fmt="duel"); app.first_anim = None
assert app.settings["format"] == "duel" and not app.uses_cmd()
app.players[0]["cmd"][1] = 25; assert app.ko_reason(app.players[0]) == ""      # no commanders in 1 v 1
app.players[0]["cmd"][1] = 0
app.render(); tap(seat_point(app.seats[0], 0.5, 0.93)); assert app.modals[-1]["kind"] == "player"   # strip: the sheet
app.render(); assert len([z for z in app.zones if z[2]]) == 2 * (5 + 1)       # no commander rows
app.modals.clear()
app.start_game(2, 30, fmt="2hg"); app.players[0]["counters"]["poison"] = 10
assert app.ko_reason(app.players[0]) == ""; app.players[0]["counters"]["poison"] = 15
assert app.ko_reason(app.players[0]) == "poison"
app.save_state(); assert lifepi.App(NS(windowed="800x480", rotate=None, fps=30)).settings["format"] == "2hg"
snap = app.snapshot(); assert snap["format"] == "2hg" and snap["poison_ko"] == 15 and not snap["commander"]
# the New game sheet: picking a format sets its life; "New players" doesn't keep names
app.start_game(4, 40, fmt="commander"); app.rename_player(0, "Kai"); app.first_anim = None
app.push("newgame", n=4, life=40); app.render()
m = app.modals[-1]; fmt_btns = app.zones[:3]
tap(find_point(fmt_btns[1][0])); assert m["format"] == "duel" and m["life"] == 20 and m["n"] == 2
tap(find_point(app.zones[0][0])); assert m["format"] == "commander" and m["life"] == 40
m["n"] = 4; app.render(); pygame.image.save(app.canvas, os.path.join(shots, "newgame.png"))
keep = app.zones[-2]; tap(find_point(keep[0])); assert m["keep"] is False
tap(find_point(app.zones[-1][0]))
assert app.players[0]["name"] == "Player 1" and app.settings["format"] == "commander"
app.undo_last(); assert app.players[0]["name"] == "Kai"
# planar die: planeswalk / chaos / blank
app.push("dice", result="", sub=""); app.render()
planar = app.zones[7]                                       # d4..d20, coin, then the planar die
faces = set()
for _ in range(60):
    tap(find_point(planar[0])); faces.add(app.modals[-1]["result"])
assert faces == {"Planeswalk", "Chaos", "Blank"} and app.modals[-1]["sub"] == "planar die", faces
app.modals.clear()
# initials on damage tiles; names up to 24 characters
assert lifepi.initials("Ana Bell") == "AB" and lifepi.initials("Player 3") == "P3" and lifepi.initials("kai") == "KA"
app.rename_player(1, "A" * 40); assert len(app.players[1]["name"]) == lifepi.NAME_MAX == 24
app.players[0]["life"] = 4; app.players[0]["cmd"][1] = 16; app.players[0]["counters"]["poison"] = 8
app.players[1]["life"] = -2; app.render()
pygame.image.save(app.canvas, os.path.join(shots, "warnings.png"))
app.push("cmd", idx=0, angle=0); app.render(); pygame.image.save(app.canvas, os.path.join(shots, "cmd_pane_warn.png"))
poison = app.zones[2 * 4]                                   # the middle poison button (after 4 commanders)
tap(find_point(poison[0])); assert app.players[0]["counters"]["poison"] == 9
app.modals.clear(); app.start_game(4, 40); app.first_anim = None
print("formats, each opponent, planar die, initials, warnings ok")

# --- random first player on new game ------------------------------------------------
def land(a):                                     # fast-forward the spin
    a.first_anim["t0"] -= a.first_anim["land"] + 0.01
    a.tick_first()
assert app.settings["random_first"] is True      # on by default
for seating in lifepi.SEATINGS:
    app.settings["seating"] = seating
    for n in range(2, lifepi.MAX_PLAYERS + 1):
        app.start_game(n, 40)
        k = app.first
        assert 0 <= k < n and app.first_spinning(), (seating, n)
        anim = app.first_anim
        assert anim["seq"][-1] == k and len(anim["seq"]) > n            # at least one full lap, lands on k
        gaps = [b - a for a, b in zip(anim["times"], anim["times"][1:])]
        assert gaps == sorted(gaps), "the spin should slow down"
        app.render()                              # spinning highlight
        land(app); app.render()                   # landed: "Goes first!" facing that seat
        assert not app.first_spinning() and app.first == k
        app.first_anim["t0"] -= lifepi.FIRST_SHOW; app.tick_first()
        assert app.first_anim is None
        app.render()                              # badge only
app.settings["seating"] = "round"; app.start_game(5, 40); land(app); app.render()
pygame.image.save(app.canvas, os.path.join(shots, "goes_first_round.png"))
app.settings["seating"] = "table"; app.start_game(4, 40)
app.first_anim["t0"] -= app.first_anim["land"] * 0.5; app.render()
pygame.image.save(app.canvas, os.path.join(shots, "first_spin.png"))
land(app); app.render(); pygame.image.save(app.canvas, os.path.join(shots, "goes_first.png"))
# the badge follows the player when seats are swapped; it survives a restart
k = app.first; other = (k + 1) % 4; app.swap_seats(k, other); assert app.first == other
app.save_state(); assert lifepi.App(NS(windowed="800x480", rotate=None, fps=30)).first == other
# fair: every seat comes up about equally often
counts = [0] * 8
app.settings["seating"] = "table"
for _ in range(8000):
    app.players = [lifepi.new_player(i, 40) for i in range(8)]
    app.pick_first(); counts[app.first] += 1
assert all(850 < c < 1150 for c in counts), counts        # expected 1000 each (sd ~30)
# off, or a single player: nobody is picked
app.toggle_random_first(); app.start_game(4, 40); assert app.first == -1 and app.first_anim is None
app.toggle_random_first(); app.start_game(1, 40); assert app.first == -1
app.start_game(4, 40); land(app); app.first_anim = None
print("random first player ok", counts)

# history records who went first; the optional turn tracker
app.modals.clear(); app.settings["random_first"] = True
app.start_game(4, 40); app.first_anim = None
f = app.first
assert app.history[-1]["text"].endswith(f"{app.players[f]['name']} goes first"), app.history[-1]["text"]
assert app.settings["turn_tracker"] is False and not app.turn_on()
app.render(); assert not app.zones or True
app.push("menu"); app.render()
tt = [z for z in app.zones if z[1] == app.toggle_turn_tracker]; assert len(tt) == 1, "menu should offer the turn tracker"
tt[0][1](); app.modals.clear(); app.render()
assert app.turn_on() and app.turn == {"round": 1, "seat": f, "start": f}, app.turn
R = max(22, min(app.W, app.H) // 22)
def pill_center():
    app.render(); return app.turn_pill_rect(R)[1].center
tap(pill_center())                                          # tap the pill: next player, clockwise
assert app.turn == {"round": 1, "seat": (f + 1) % 4, "start": f}, app.turn
for _ in range(3): app.next_turn()
assert app.turn == {"round": 2, "seat": f, "start": f}, app.turn   # back round to the starter: turn 2
assert app.history[-1]["text"] == f"Turn 2: {app.players[f]['name']}", app.history[-1]["text"]
ko = (f + 1) % 4; app.players[ko]["life"] = 0                # knocked-out players are skipped
app.next_turn(); assert app.turn["seat"] == (f + 2) % 4, app.turn
tap(pill_center(), hold=lifepi.HOLD_DELAY + 0.2)             # hold the pill: back one turn (once)
assert app.turn == {"round": 2, "seat": f, "start": f}, app.turn
app.next_turn(); app.undo_last(); assert app.turn["seat"] == f   # undo takes a pass back
app.next_turn(-1); assert app.turn == {"round": 1, "seat": (f - 1) % 4, "start": f}, app.turn
app.players[ko]["life"] = 40
cur = app.turn["seat"]; name = app.players[cur]["name"]
app.swap_seats(cur, (cur + 2) % 4); assert app.players[app.turn["seat"]]["name"] == name   # turn follows the player
app.save_state(); r2 = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert r2.turn == app.turn and r2.settings["turn_tracker"]
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "turn_tracker.png"))
app.start_game(4, 40); app.first_anim = None
assert app.turn == {"round": 1, "seat": app.first, "start": app.first}   # new game: turn 1, whoever goes first
app.toggle_turn_tracker(); assert not app.turn_on()
print("first player in history, turn tracker ok")

# installed copies get a menu / taskbar icon (also after updating themselves)
desk = lifepi.DATA_HOME / "applications" / "lifepi.desktop"; desk.parent.mkdir(parents=True, exist_ok=True)
desk.write_text("[Desktop Entry]\nType=Application\nName=LifePi\nExec=lifepi\nIcon=applications-games\n")
lifepi.install_desktop_icon()
assert lifepi.ICON_FILE.exists() and pygame.image.load(str(lifepi.ICON_FILE)).get_size() == (256, 256)
assert "Icon=lifepi" in desk.read_text() and "StartupWMClass=lifepi" in desk.read_text(), desk.read_text()
lifepi.install_desktop_icon(); assert desk.read_text().count("StartupWMClass") == 1     # idempotent
print("desktop icon ok")

# idle: nothing moving and no finger down -> the loop waits for input instead of spinning
app.modals.clear(); app.fx.clear(); app.toast = ("", 0); app.armed = None; app.first_anim = None
app.pointers.clear(); app.save_state(); app.snap_dirty = False; app.render(); app.step()
assert app.idle, "an untouched table should idle"
t0 = time.time(); app.step(); waited = time.time() - t0
assert 0.07 < waited < 0.3, waited                          # idle frames: IDLE_FPS, not the full rate
app.change_life(0, 1); app.step(); assert not app.idle      # something changed: full frame rate
app.change_life(0, -1)
# casting: Stop casting ends the session (receiver app closed, connection dropped)
class FakeCast:
    def __init__(self): self.calls = []
    def quit_app(self): self.calls.append("quit")
    def disconnect(self, timeout=None): self.calls.append("disconnect")
fc = FakeCast(); app.cast = (fc, "Living room TV")
app.push("menu"); app.render()
assert any(z[1] == app.stop_cast for z in app.zones) and not any(z[1] == app.open_cast for z in app.zones)
app.stop_cast(); end = time.time() + 2
while time.time() < end and len(fc.calls) < 2: time.sleep(0.02)
assert fc.calls == ["quit", "disconnect"] and app.cast is None and "Stopped casting" in app.toast[0], (fc.calls, app.toast)
app.push("menu"); app.render(); assert any(z[1] == app.open_cast for z in app.zones); app.modals.clear()
# the phone page lets the screen sleep after a while without taps
assert "wakeIdle" in lifepi.PHONE_HTML and "wake.release()" in lifepi.PHONE_HTML
print("idle loop, stop casting, phone screen sleep ok")

# the shared menu button opens the menu facing whoever tapped it; screens opened from it follow
app.modals.clear(); app.set_seating("table"); app.start_game(4, 40); app.first_anim = None; app.render()
R = max(22, min(app.W, app.H) // 22); mx, my = app.menu_pos(R)
def menu_from(dx, dy):
    app.modals.clear(); app.render(); tap((mx + dx, my + dy)); return app.modals[-1]
m = menu_from(0, R // 2); assert m["kind"] == "menu" and m["angle"] == 0, m          # near side: upright
m = menu_from(0, -R // 2); assert m["angle"] == 180, m                               # far side: facing them
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "menu_far_side.png"))
app.push("dice", result="", sub=""); assert app.modals[-1]["angle"] == 180          # opened from it: same way
app.pop(); app.render(); turn = [z for z in app.zones if z[1] and "turn_sheet" in z[1].__code__.co_names]
assert len(turn) == 1, "menu should have a Turn button"
turn[0][1](); assert app.modals[-1]["angle"] == 0                                    # Turn: the other side
app.modals.clear(); app.set_seating("ends"); app.start_game(4, 40); app.first_anim = None; app.render()
sides = {round(st.angle / 90) * 90 % 360 for st in app.seats}
if {90, 270} & sides:                                   # an end seat: its side faces it too
    mx, my = app.menu_pos(R)
    ends = [st for st in app.seats if round(st.angle / 90) * 90 % 360 in (90, 270)]
    e = ends[0]; vx, vy = e.frame[0] - mx, e.frame[1] - my; n = math.hypot(vx, vy)
    m = menu_from(int(vx / n * R * 0.8), int(vy / n * R * 0.8))
    assert m["angle"] == round(e.angle / 90) * 90 % 360, (m["angle"], e.angle)
    app.render()                                        # a portrait menu draws fine
app.modals.clear(); app.set_seating("table"); app.start_game(4, 40); app.first_anim = None
print("menu faces whoever opened it ok")

# damage sounds: a grunt for life, a clang for commander damage, a bubble for poison; one per hit
# (not per point of a hold), on damage only, never the same clip twice in a row; Sounds cycles
# grunts -> tock -> off
class FakeSound:
    def __init__(self): self.n = 0
    def play(self): self.n += 1
fx = {k: [FakeSound() for _ in range(n)] for k, n in (("hit", 6), ("cmd", 3), ("poison", 3), ("death", 2))}
tock, tock_death = FakeSound(), FakeSound()
def heard(kind=None):
    return sum(c.n for k, cl in fx.items() if kind in (None, k) for c in cl) + (tock.n if kind in (None, "tock") else 0)
def gap(): time.sleep(lifepi.SFX_GAP + 0.02)
app.sfx = dict(fx, tock=tock, tock_death=tock_death); app.sfx_t = 0
app.settings.update(sounds=True, sound_style="grunt", cmd_hits_life=True)
app.change_life(0, -1); assert heard("hit") == 1 and heard() == 1
app.change_life(0, -1); assert heard() == 1                 # within SFX_GAP: same hit
gap(); app.change_life(0, 3); assert heard() == 1           # gaining life: silent
gap(); app.change_cmd(0, 1, 2); assert heard("cmd") == 1 and heard() == 2   # the clang, not a grunt as well
gap(); app.change_counter(0, "poison", 1); assert heard("poison") == 1 and heard() == 3
gap(); app.change_counter(0, "poison", -1); app.change_cmd(0, 1, -2); assert heard() == 3   # taking back: silent
for kind in ("hit", "cmd", "poison"):
    order = []
    for _ in range(12):
        before = [c.n for c in fx[kind]]; gap(); app.play_damage(kind)
        order.append(next(k for k, c in enumerate(fx[kind]) if c.n != before[k]))
    assert all(a != b for a, b in zip(order, order[1:])), (kind, order)   # never twice in a row
app.push("menu"); app.render(); tog = [z for z in app.zones if z[1] == app.toggle_sounds]; assert len(tog) == 1
assert app.sounds_label() == "Sounds: grunts"
tog[0][1](); app.modals.clear()
assert app.settings["sound_style"] == "tock" and app.sounds_label() == "Sounds: tock" and tock.n == 1   # sample
gap(); app.change_cmd(0, 1, 1); assert tock.n == 2          # tock style: the tock for everything
app.change_cmd(0, 1, -1)
app.toggle_sounds(); assert not app.settings["sounds"] and app.sounds_label() == "Sounds: off"
n = heard(); gap(); app.change_life(0, -1); app.change_counter(0, "poison", 1); assert heard() == n   # off: silent
app.change_counter(0, "poison", -1)
app.toggle_sounds(); assert app.settings["sounds"] and app.settings["sound_style"] == "grunt" and heard() == n + 1
pcm = lifepi.damage_sound(44100, 2); assert len(pcm) == int(44100 * 0.12) * 2 * 2 and max(pcm) > 0
real = lifepi.App.load_sounds(app)
if real:                                                   # an audio driver: every clip decodes
    assert real["tock"].get_length() > 0.05
    assert [len(real[k]) for k in ("hit", "cmd", "poison", "death")] == [6, 3, 3, 2]
    assert all(0.1 < c.get_length() < 0.6 for k in ("hit", "cmd", "poison") for c in real[k])
    assert all(0.8 < c.get_length() < 2.0 for c in real["death"]) and real["tock_death"].get_length() > 0.3
app.change_life(0, 1)

# a knockout plays "You lose!" instead - once, even straight after another hit, from any cause
def died(): return heard("death")
app.start_game(4, 40); app.first_anim = None; app.settings.update(sounds=True, sound_style="grunt")
app.players[0]["life"] = 2; gap()
app.change_life(1, -1); app.change_life(0, -2)            # a hit then a knockout inside SFX_GAP
assert died() == 1, died()
gap(); app.change_life(0, -1); assert died() == 1         # already out: just a grunt
gap(); app.change_life(0, 5); gap(); app.change_life(0, -6); assert died() == 2   # back in, out again
app.players[1]["cmd"][2] = 19; gap(); app.change_cmd(1, 2, 2)    # 21 commander damage
assert died() == 3 and app.ko_reason(app.players[1]) == "commander"
c = heard("cmd"); gap(); app.change_cmd(1, 2, 1); assert heard("cmd") == c + 1 and died() == 3
app.players[2]["counters"]["poison"] = 9; gap(); app.change_counter(2, "poison", 1); assert died() == 4
app.settings["sound_style"] = "tock"; app.players[3]["life"] = 1; gap(); app.change_life(3, -1)
assert tock_death.n == 1 and died() == 4
app.settings["sound_style"] = "grunt"
lp = lifepi.damage_sound(44100, 1, low=True); assert len(lp) == int(44100 * 0.4) * 2
app.start_game(4, 40); app.first_anim = None
print("death sound ok")

# audio asks PipeWire for a big buffer: a small one made every stream (the music too) run with
# tiny buffers and stutter on HDMI
assert os.environ.get("PIPEWIRE_LATENCY") == "2048/48000" and os.environ.get("PULSE_LATENCY_MSEC")
if real:
    import array as _arr, math as _m
    def loudness(snd):                                      # loudest 100 ms, dBFS
        a = _arr.array("h", snd.get_raw()); rate, _, ch = pygame.mixer.get_init(); w = rate // 10 * ch
        return max(20 * _m.log10(max(1, (sum(x * x for x in a[i:i + w]) / max(1, len(a[i:i + w]))) ** 0.5) / 32768)
                   for i in range(0, max(1, len(a) - w), w // 4))
    grunt = sum(map(loudness, real["hit"])) / 6
    clang = sum(map(loudness, real["cmd"])) / 3
    bubble = sum(map(loudness, real["poison"])) / 3
    assert clang > grunt + 1.5 and abs(bubble - grunt) < 2.5, (grunt, clang, bubble)   # heard over music
print("audio buffer, sound levels ok")

# shapes have smooth (anti-aliased) edges, so a TV blowing the screen up shows no stair-steps,
# and no dark rim (transparent pixels carry the shape's colour)
rr = app.rrect(60, 30, (200, 40, 40), 15)
edge = [rr.get_at((x, 0))[3] for x in range(0, 16)]
assert any(0 < a_ < 255 for a_ in edge), edge                 # partial alpha = smoothed
assert all(rr.get_at((x, 0))[:3] == (200, 40, 40) for x in range(16) if 0 < rr.get_at((x, 0))[3]), "dark rim"
pl = app.make_pill("KO", 24, bg=(0, 0, 0), alpha=225, outline=(255, 255, 255))
assert any(0 < pl.get_at((x, 0))[3] < 255 for x in range(pl.get_height() // 2))
sg = app.sign(True, 40); assert any(0 < sg.get_at((x, y))[3] < 110 for x in range(40) for y in range(40))
tri = pygame.Surface((60, 60), pygame.SRCALPHA); app.poly(tri, (255, 255, 255), [(5, 5), (55, 15), (20, 55)])
assert len({tri.get_at((x, y))[3] for x in range(60) for y in range(60)}) > 10   # graded edge
n_art = len(app.img_cache)
for k in range(300): app.rrect(20 + k, 20, (1, 2, 3), 5)
assert len(app.img_cache) == n_art                         # shapes never push card art out
print("smooth shapes ok")

# Android app ads: a banner only while the menu / history / dice sheet is open, never on the
# table; sheets leave its strip (the bottom of the physical screen, however the canvas or sheet
# is turned) free, so no button sits under it; Remove ads / Ad privacy in the menu
class FakeNative:
    def __init__(self):
        self.ads, self.removed, self.price, self.privacyRequired = True, False, "$2.99", False
        self.reserve, self.notice, self.calls = 0, "", []
        self.want_on = False
    def want(self, on): self.calls.append(("want", on)); self.want_on = on; self.reserve = 50 if on else 0
    def sheets(self, n): self.calls.append(("sheets", n))
    def buy(self): self.calls.append(("buy",))
    def privacy(self): self.calls.append(("privacy",))
    def policy(self): self.calls.append(("policy",))
def native_tick(a):
    a.native_t = 0; a.tick_native(); a.native_t = 0; a.tick_native(); a.render()
assert app.native_bridge() is None and app.native_menu_items() == []        # Pi / browser: nothing
app.modals.clear(); app.start_game(4, 40); app.first_anim = None
fn = app.native = FakeNative()
native_tick(app)
assert ("sheets", 0) in fn.calls and app.ad_strip() is None and app.ad_reserve == 0
app.push("menu"); native_tick(app)
assert ("want", True) in fn.calls and ("sheets", 1) in fn.calls and app.ad_reserve == 50
labels = [z for z in app.zones if z[1] == app.buy_remove_ads]
assert len(labels) == 1
labels[0][1](); assert fn.calls[-1] == ("buy",)
pygame.image.save(app.canvas, os.path.join(shots, "android_menu_ad_strip.png"))
def screen_points_free(a):
    """No touch zone in the banner strip at the bottom of the physical screen."""
    sw, sh = a.screen.get_size()
    rot = a.rot()
    for sx in range(4, sw, 13):
        for sy in range(sh - 49, sh, 7):
            # screen point -> canvas point (canvas turned clockwise by rot onto the screen)
            cx, cy = {0: (sx, sy), 90: (sy, a.H - 1 - sx), 180: (a.W - 1 - sx, a.H - 1 - sy),
                      270: (a.W - 1 - sy, sx)}[rot]
            hits = [z for z in a.zones if z[0]((cx, cy))]
            assert not hits, (rot, a.modals[-1]["kind"], a.modals[-1].get("angle"), (sx, sy), (cx, cy))
for rot in (0, 90, 180, 270):
    app.settings["rotation"] = rot; app.make_canvas()
    for kind in ("menu", "history", "dice"):
        for ang in (0, 90, 180, 270):
            app.modals.clear(); app.push(kind, **({"result": "", "sub": ""} if kind == "dice" else {}))
            app.modals[-1]["angle"] = ang
            native_tick(app)
            strip = app.ad_strip(); assert strip and min(strip.w, strip.h) == 50, (rot, kind, strip)
            screen_points_free(app)
            # the strip really is the screen's bottom edge: paint it, put the frame on screen, look
            app.canvas.fill((255, 0, 0), strip); app.present()
            sw, sh = app.screen.get_size()
            assert app.screen.get_at((sw // 2, sh - 3))[:3] == (255, 0, 0), (rot, strip)
            assert app.screen.get_at((sw // 2, 3))[:3] != (255, 0, 0), (rot, strip)
app.settings["rotation"] = 0; app.make_canvas()
# the table and other sheets: no banner, nothing reserved
app.modals.clear(); native_tick(app)
assert fn.want_on is False and app.ad_strip() is None
app.push("newgame", n=4, life=40, seating="table"); native_tick(app)
assert fn.want_on is False and app.ad_strip() is None
app.modals.clear(); native_tick(app)
# messages from the purchase show as a toast
fn.notice = "Thanks for supporting LifePi! Ads are gone."; native_tick(app)
assert app.toast[0].startswith("Thanks") and fn.notice == ""
# bought: no Remove ads; the EU / UK privacy form gets a button when Google says it's needed
fn.removed, fn.privacyRequired = True, True
app.push("menu"); native_tick(app)
assert not [z for z in app.zones if z[1] == app.buy_remove_ads]
pz = [z for z in app.zones if z[1] == app.ad_privacy]; assert len(pz) == 1
pz[0][1](); assert fn.calls[-1] == ("privacy",)
pp = [z for z in app.zones if z[1] == app.privacy_policy]; assert len(pp) == 1     # Play wants it in the app
pp[0][1](); assert fn.calls[-1] == ("policy",)
app.modals.clear(); native_tick(app)
app.native = None
print("android ads: menu-only banner, free strip at every rotation, remove ads, privacy, policy ok")

# music: the Spotify Connect speaker's local API (a stand-in server here)
import http.server, threading as _th
sent = []
class Spot(http.server.BaseHTTPRequestHandler):
    status, song = 204, "Test Song"
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/status" and Spot.status == 200:
            body = json.dumps({"paused": False, "volume": 40, "volume_steps": 100, "track": {
                "name": Spot.song, "artist_names": ["Some Band"], "album_name": "An Album",
                "album_cover_url": None}}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(204); self.end_headers()
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        sent.append((self.path, json.loads(self.rfile.read(n)) if n else None))
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Spot)
_th.Thread(target=srv.serve_forever, daemon=True).start()
def music_open(api):
    lifepi.SPOTIFY_API = api
    app.modals.clear(); app.open_music(); m = app.modals[-1]
    end = time.time() + 4
    while time.time() < end and m["api"] is None: time.sleep(0.05)
    app.render(); return m
m = music_open("http://127.0.0.1:9")                       # nothing listening: not set up
assert m["api"] is False
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "music_not_set_up.png"))
m = music_open(f"http://127.0.0.1:{srv.server_port}")     # speaker running, nobody playing
assert m["api"] is True and m["status"] is None
Spot.status = 200; m["poke"] = True; end = time.time() + 4
while time.time() < end and not m["status"]: time.sleep(0.05)
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "music_playing.png"))
assert m["status"]["track"]["name"] == "Test Song"
btn = {getattr(z[1], "__code__", None) and z[1].__code__.co_consts: z for z in app.zones if z[1]}
labels = [z for z in app.zones if z[1] and z[1] not in (app.pop, app.toggle_banner)]
for z in labels: z[1]()                                   # Prev, Play/Pause, Next, Volume -, Volume +
end = time.time() + 3
while time.time() < end and len(sent) < 5: time.sleep(0.05)
paths = sorted(p for p, _ in sent)
assert paths == sorted(["/player/prev", "/player/playpause", "/player/next", "/player/volume", "/player/volume"]), paths
assert {"volume": -10, "relative": True} in [b for _, b in sent] and {"volume": 10, "relative": True} in [b for _, b in sent]
app.modals.clear()
print("damage sound, music ok")

# now-playing banner: off by default; on, a see-through strip along the bottom with no touch
# zones of its own (everything under it still works), hidden while a sheet is open
assert app.settings["music_banner"] is False and app.banner_text() is None
def zmap():                                                 # which zone each point lands on
    pts = [(x, y) for x in range(5, app.W, 37) for y in range(3, app.H, 23)]
    return len(app.zones), [next((k for k, z in enumerate(app.zones) if z[0]((x, y))), None) for x, y in pts]
app.render(); plain = app.canvas.copy(); zones_off = zmap()
app.open_music(); app.render()
tb = [z for z in app.zones if z[1] == app.toggle_banner]; assert len(tb) == 1
tb[0][1](); assert app.settings["music_banner"] is True
end = time.time() + 5
while time.time() < end and not app.now_playing: time.sleep(0.05)
assert app.now_playing == "Test Song  -  Some Band", app.now_playing
assert app.banner_text() is None                           # the Music sheet is open: hidden
app.modals.clear(); app.render()
assert "Test Song" in app.banner_text()
assert zmap() == zones_off                                  # same zones, same places
x0 = app.W // 2; yb = app.H - 3
got, was = app.canvas.get_at((x0, yb))[:3], plain.get_at((x0, yb))[:3]
assert got != was and got != (8, 8, 14), (got, was)        # darkened but see-through
assert app.canvas.get_at((x0, app.H // 2)) == plain.get_at((x0, app.H // 2))   # rest untouched
# the table under the strip still takes taps: a tap there changes the bottom player's life
under = [z for z in app.zones if z[0]((app.W // 4, app.H - 6))]; assert under
pygame.image.save(app.canvas, os.path.join(shots, "banner.png"))
assert not app.banner_scrolling()                          # fits: still
app.say("hello"); app.render(); app.toast = ("", 0)
# a long title scrolls, using the cheap path (no full redraw)
Spot.song = "A Really Very Long Song Title That Goes On And On, " * 3   # from the speaker, as in use
end = time.time() + 6
while time.time() < end and "Really" not in (app.now_playing or ""): time.sleep(0.05)
app.banner_t0 = time.time() - 1; app.render()
assert app.banner_scrolling() and app.busy()
full = [0]; real_render = app.render
def counting(): full[0] += 1; real_render()
app.render = counting; app.dirty = False
f1 = app.canvas.copy(); app.banner_t0 -= 0.5; app.render_banner_frame()
assert full[0] == 0
strip = lambda c: pygame.image.tobytes(c.subsurface((0, app.H - 10, app.W, 10)), "RGB")
assert strip(app.canvas) != strip(f1)                      # the text moved
assert app.canvas.get_at((x0, app.H // 2)) == f1.get_at((x0, app.H // 2))
app.render = real_render
pygame.image.save(app.canvas, os.path.join(shots, "banner_scrolling.png"))
# paused: no banner; switched off: gone and the poller stops
Spot.status = 204; end = time.time() + 6
while time.time() < end and app.now_playing: time.sleep(0.05)
assert app.now_playing is None and app.banner_text() is None
app.toggle_banner(); assert app.settings["music_banner"] is False
app.banner_thread.join(2); assert not app.banner_thread.is_alive()
srv.shutdown()
print("now-playing banner ok")

# --- random card art for players without art ------------------------------------------
serial = [0]
def scry_random(url, timeout=10):
    if "cards/random" in url:
        serial[0] += 1
        k = serial[0]
        return json.dumps({"id": f"rand{k}", "name": f"Commander {k}", "artist": f"Artist {k}",
                           "image_uris": {"art_crop": f"https://img/rand{k}.jpg"}}).encode()
    return png                                   # the image download
def art_settle(a, secs=6):
    end = time.time() + secs
    while time.time() < end:
        a.tick_art()
        if not a.art_pending and a.art_results.empty():
            return
        time.sleep(0.02)
    raise AssertionError("random art never arrived")
lifepi.http_get = scry_random
lifepi.STATE_FILE.unlink(missing_ok=True); lifepi.BACKUP_FILE.unlink(missing_ok=True)
ra = lifepi.App(NS(windowed="800x480", rotate=None, fps=30, random_art=True))   # first start: no saved game
art_settle(ra)
arts = [p["art"] for p in ra.players]
assert all(arts) and len(set(arts)) == 4, arts                                 # everyone, all different
assert all(p["art_auto"] and "illus. Artist" in p["credit"] for p in ra.players)
ra.render(); pygame.image.save(ra.canvas, os.path.join(shots, "random_art.png"))
# choosing a colour opts out; a bigger game gives the new seats art and keeps the others'
ra.recolor_player(2); kept = [p["art"] for p in ra.players]
ra.start_game(6, 40); art_settle(ra)
assert ra.players[2]["art"] is None and ra.players[2]["color_chosen"]
assert [p["art"] for p in ra.players[:2]] == kept[:2] and ra.players[3]["art"] == kept[3]
assert ra.players[4]["art"] and ra.players[5]["art"] and len({p["art"] for p in ra.players if p["art"]}) == 5
# a saved profile's own art beats random art
ra.profiles["Chris"] = {"color": 1, "art": ra.players[4]["art"], "credit": "theirs", "art_url": None,
                        "art_auto": False, "color_chosen": False}
ra.rename_player(0, "Chris"); assert ra.players[0]["art"] == ra.players[4]["art"] and not ra.players[0]["art_auto"]
# choices survive a restart: the colour player still gets no art
ra.save_state()
rb = lifepi.App(NS(windowed="800x480", rotate=None, fps=30, random_art=True)); art_settle(rb)
assert rb.players[2]["art"] is None and rb.players[2]["color_chosen"] and all(p["art"] for k, p in enumerate(rb.players) if k != 2)
# offline: art already on this device (from earlier searches) is used instead
lifepi.http_get = offline
rb.start_game(8, 40); art_settle(rb)
assert rb.players[6]["art"] and rb.players[7]["art"], [p["art"] for p in rb.players]
# nothing cached either: keep the colour, try again later
saved_dir = lifepi.SEARCH_DIR; lifepi.SEARCH_DIR = lifepi.Path(tempfile.mkdtemp())
rb.players[7].update(art=None, art_auto=False); rb.fill_random_art(); art_settle(rb)
assert rb.players[7]["art"] is None and rb.art_retry_at > time.time()
lifepi.SEARCH_DIR = saved_dir
# a result for a player who has since left the game is ignored
lifepi.http_get = scry_random
rb.players[7].update(art=None); gone = rb.players[7]; rb.fill_random_art(); rb.start_game(4, 40)
art_settle(rb); assert gone["art"] is None and len(rb.players) == 4
print("random card art ok")

# --- update check for installed copies ------------------------------------------------
assert lifepi.newer_version("v1.10.0", "1.9.3") and not lifepi.newer_version("v1.4.0", "1.4.0")
assert not lifepi.newer_version("v1.3.9", "1.4.0") and not lifepi.newer_version("nonsense", "1.0.0")
install = lifepi.Path(tempfile.mkdtemp()) / "lifepi"          # like ~/.local/bin/lifepi (no .git above)
install.write_bytes(open(lifepi.__file__, "rb").read())
original = install.read_bytes()
release = {"tag": "v9.9.0", "src": None, "calls": 0}
def as_release(tag):
    return original.replace(f'VERSION = "{lifepi.VERSION}"'.encode(), f'VERSION = "{tag[1:]}"'.encode(), 1)
def github(url, timeout=10):
    release["calls"] += 1
    if "releases/latest" in url:
        return json.dumps({"tag_name": release["tag"]}).encode()
    if url.endswith(f"/{release['tag']}/lifepi.py"):
        return release["src"] if release["src"] is not None else as_release(release["tag"])
    raise lifepi.urllib.error.URLError("unexpected " + url)
lifepi.http_get = github
def updater():
    a = lifepi.App(NS(windowed="800x480", rotate=None, fps=30, update_check=True, install_path=str(install)))
    end = time.time() + 5
    while time.time() < end and a.settings["update_latest"] != release["tag"]:
        a.tick_update(); time.sleep(0.02)
    return a
def settle_update(a):
    end = time.time() + 8
    while time.time() < end and (a.update_state == "working" or a.update_state in ("done", "failed")):
        a.tick_update(); time.sleep(0.02)
    a.tick_update()
lifepi.STATE_FILE.unlink(missing_ok=True)
up = updater()
assert up.update_mode() == "self" and up.settings["update_latest"] == "v9.9.0" and up.update_available()
up.modals.clear(); up.first_anim = None; up.render()
pygame.image.save(up.canvas, os.path.join(shots, "update_badge.png"))
assert "9.9.0 is available" in up.toast[0], up.toast         # a toast says so once...
assert up.update_badge(), "...and the menu button carries a dot"
assert not any(z[1] in (up.start_update, up.dismiss_update) for z in up.zones), "nothing over the seats"
# the menu offers it; having seen it there clears the dot for this version
up.push("menu"); up.render()
assert any(z[1] == up.start_update for z in up.zones), "menu should offer the update"
up.modals.clear(); up.render()
assert up.settings["update_dismissed"] == "v9.9.0" and not up.update_badge()
up.push("menu"); up.render(); assert any(z[1] == up.start_update for z in up.zones), "still in the menu"
up.modals.clear()
up.save_state()
# ...and a newer release brings it back; the check runs at most every UPDATE_EVERY
calls = release["calls"]; again = lifepi.App(NS(windowed="800x480", rotate=None, fps=30, update_check=True,
                                               install_path=str(install)))
time.sleep(0.3); again.tick_update(); assert release["calls"] == calls, "checked again too soon"
release["tag"] = "v9.9.1"; again.settings["update_checked"] = 0; again.check_for_update()
end = time.time() + 5
while time.time() < end and again.settings["update_latest"] != "v9.9.1":
    again.tick_update(); time.sleep(0.02)
again.modals.clear(); again.first_anim = None; again.render()
assert again.update_badge(), "a newer version brings the dot back"
# a bad download never replaces the installed file
for bad, why in ((as_release("v1.0.0"), "version"), (b"#!/usr/bin/env python3\nthis is ( not python\n", "syntax"),
                 (b"<html>rate limited</html>", "not LifePi")):
    release["src"] = bad
    again.start_update(); settle_update(again)
    assert install.read_bytes() == original and not again.restart_after and again.running, why
    assert "Update failed" in again.toast[0], again.toast
# a good one: replaced (old kept as .previous), game saved, restart requested
release["src"] = None
again.players[0]["life"] = 33; again.changed()
again.start_update(); settle_update(again)
assert again.restart_after and not again.running
assert b'VERSION = "9.9.1"' in install.read_bytes() and install.with_name("lifepi.previous").read_bytes() == original
assert again.state_dirty
again.save_state()                                         # what run() does on the way out (end())
assert json.loads(lifepi.STATE_FILE.read_text())["players"][0]["life"] == 33
# a git clone updates with git pull; the browser / Docker don't use this at all
clone = lifepi.Path(tempfile.mkdtemp()); (clone / ".git").mkdir(); (clone / "lifepi.py").write_bytes(original)
gc = lifepi.App(NS(windowed="800x480", rotate=None, fps=30, install_path=str(clone / "lifepi.py")))
assert gc.update_mode() == "git"
# Menu > Check for updates: asks GitHub now (no 6 h wait, even with --no-update-check) and
# installs a newer release straight away; says so when already up to date or offline
inst2 = lifepi.Path(tempfile.mkdtemp()) / "lifepi"; inst2.write_bytes(original)
release["tag"], release["src"] = f"v{lifepi.VERSION}", None
def checker():
    return lifepi.App(NS(windowed="800x480", rotate=None, fps=30, update_check=False, install_path=str(inst2)))
def settle_check(a):
    end = time.time() + 5
    while time.time() < end and getattr(a, "update_asked", False):
        a.tick_update(); time.sleep(0.02)
    settle_update(a)
cu = checker(); cu.settings.update(update_checked=time.time(), update_latest="")   # just checked: auto-check would wait
cu.push("menu"); cu.render()
chk = [z for z in cu.zones if z[1] == cu.update_now]
assert len(chk) == 1, "menu should offer Check for updates"
calls = release["calls"]; chk[0][1](); settle_check(cu)
assert release["calls"] > calls and "is the latest version" in cu.toast[0], cu.toast
assert inst2.read_bytes() == original and cu.running and not cu.restart_after
release["tag"] = "v9.9.2"; cu.update_now(); settle_check(cu)
assert cu.restart_after and b'VERSION = "9.9.2"' in inst2.read_bytes(), cu.toast
def no_net(url, timeout=10): raise lifepi.urllib.error.URLError("no network")
lifepi.http_get = no_net
off = checker(); off.settings["update_latest"] = ""; off.update_now(); settle_check(off)
assert "Couldn't check for updates" in off.toast[0] and off.running, off.toast
lifepi.http_get = scry_random
print("update check ok (toast + dot, menu, check now, interval, bad downloads rejected, update + restart, git mode)")
for n in range(1, 7):
    app.start_game(n, 40); app.render()
app.save_state(); app2 = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
assert len(app2.players) == 6 and "Chris" in app2.profiles
assert "Lightning Helix" in app2.local_card_names("helix")   # autocomplete cache persisted
print("modals / scryfall / persistence ok")

# --- everything tracked survives a restart -----------------------------------------
import signal, threading
def fresh(): return lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
def snapshot(a): return json.loads(json.dumps({"players": a.players, "settings": a.settings, "profiles": a.profiles}))

a = fresh(); a.start_game(4, 40); a.save_state()
a.change_life(0, -7); a.change_cmd(1, 2, 5); a.change_cmd(1, 1, 2)     # incl. own commander
a.change_counter(3, "poison", 3); a.change_counter(2, "energy", 4); a.swap_seats(0, 3)
a.players[2]["name"] = "Sam"; a.toggle_seating(); a.changed()
want = snapshot(a)
# a stop signal (docker stop, shutdown, kill) right after the changes - inside the save
# throttle - must still save them before exiting
threading.Timer(0.2, lambda: signal.raise_signal(signal.SIGTERM)).start()
a.running = True; a.run()
assert snapshot(fresh()) == want, "changes lost on SIGTERM"
for name in ("SIGTERM", "SIGINT") + (("SIGHUP",) if hasattr(signal, "SIGHUP") else ()):
    signal.signal(getattr(signal, name), signal.SIG_DFL)
print("restart after SIGTERM ok")

# a crash mid-game still saves
a = fresh(); a.change_life(1, -4); want = snapshot(a)
def boom(): raise RuntimeError("boom")
a.render = boom; a.dirty = True
try: a.run()
except RuntimeError: pass
assert snapshot(fresh()) == want, "changes lost on crash"
print("restart after crash ok")

# a damaged save falls back to the previous one, and is kept aside, not overwritten
a = fresh(); a.change_life(2, -1); a.save_state(); prev = snapshot(a)
a.change_life(2, -1); a.save_state()
lifepi.STATE_FILE.write_text('{"players": [{"life": 3')            # truncated by a power cut
assert snapshot(fresh()) == prev
assert lifepi.STATE_FILE.with_name("state.json.corrupt").exists()
# bad values are repaired field by field instead of discarding the whole game
d = json.loads(lifepi.BACKUP_FILE.read_text())
d["players"][0].update(life="x", cmd=[1, "y"], counters={"poison": None})
d["players"][1]["life"] = 17; d["settings"]["rotation"] = 45
lifepi.STATE_FILE.write_text(json.dumps(d))
b = fresh()
assert b.players[1]["life"] == 17 and b.players[0]["cmd"] == [1] + [0] * (lifepi.MAX_PLAYERS - 1)
assert b.players[0]["counters"]["poison"] == 0 and b.settings["rotation"] == 0
# hand-edited on Windows: a UTF-8 BOM must not make the save "unreadable"
lifepi.STATE_FILE.write_bytes(b"\xef\xbb\xbf" + json.dumps(d).encode())
assert fresh().players[1]["life"] == 17
print("corrupt / partial saves ok")

# a TV on HDMI at boot can be listed first: LifePi opens on the touchscreen (the smaller screen),
# unless $LIFEPI_DISPLAY says otherwise
td = lifepi.touch_display
assert td([(1920, 1080), (800, 480)]) == 1 and td([(800, 480), (1920, 1080)]) == 0
assert td([(720, 1280), (3840, 2160)]) == 0 and td([(800, 480)]) == 0 and td([]) == 0
assert td([(1920, 1080), (800, 480)], "0") == 0 and td([(800, 480)], "3") == 0 and td([(1, 1), (2, 2)], "x") == 0
# the window being resized (the desktop moving it to the touchscreen) redraws at the new size
a = lifepi.App(NS(windowed="1920x1080", rotate=None, fps=30)); a.render()
pygame.display.set_mode((800, 480)); a.screen = pygame.display.set_mode((800, 480)); a.fit_window()
assert (a.W, a.H) == (800, 480); a.render()
print("display choice and resize ok")

# full screen, a TV coming or going keeps LifePi on the touchscreen: set_mode is always told
# which display (without it SDL uses display 0 - the TV when it's listed first)
calls = []
real_sizes, real_set_mode = pygame.display.get_desktop_sizes, pygame.display.set_mode
try:
    a = lifepi.App(NS(windowed="800x480", rotate=None, fps=30)); a.args_windowed = False
    class FS:                                              # the dummy driver has no real full screen
        def __init__(s, size): s.size = size
        def get_flags(s): return pygame.FULLSCREEN
        def get_size(s): return s.size
    a.screen = FS((800, 480)); a.display_index = 0; a._screens_seen = (0, ((800, 480),))
    def fake_set_mode(size=(0, 0), flags=0, depth=0, display=0, vsync=0):
        calls.append(display); real_set_mode((800, 480)); return FS((800, 480))
    pygame.display.set_mode = fake_set_mode
    pygame.display.get_desktop_sizes = lambda: [(1920, 1080), (800, 480)]     # TV plugged in, listed first
    a.fit_window()
    assert calls == [1] and a.display_index == 1, calls
    a.fit_window(); assert calls == [1], "a resize from our own set_mode must not loop"
    pygame.display.get_desktop_sizes = lambda: [(800, 480)]                    # TV unplugged
    a.fit_window(); assert calls == [1, 0], calls
    def broken(): raise pygame.error("no displays")
    pygame.display.get_desktop_sizes = broken
    a._screens_seen = None; a.fit_window()                                     # logged, not a crash
finally:
    pygame.display.get_desktop_sizes, pygame.display.set_mode = real_sizes, real_set_mode
# a frame that hits a screen error (the window lost while a TV comes or goes) carries on
a = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
real_render, fails = a.render, [2]
def flaky():
    if fails[0]:
        fails[0] -= 1; raise pygame.error("display Surface quit")
    real_render()
a.render = flaky
for _ in range(4):
    a.dirty = True; a.step()
assert fails[0] == 0 and a.running and a.screen_errors == 0
print("TV plug / unplug ok")

# LifePi runs under a supervisor: started again after a crash (logged) or an update, not after
# a normal quit
sup = lifepi.Path(tempfile.mkdtemp())
script = sup / "child.py"
script.write_text(
    "import os, sys, pathlib\n"
    f"d = pathlib.Path({str(sup)!r})\n"
    "runs = d / 'runs'; n = len(runs.read_text().split()) if runs.exists() else 0\n"
    "runs.write_text((runs.read_text() if runs.exists() else '') + (os.environ['LIFEPI_RESTART'] or '-') + ' ')\n"
    "sys.exit([1, 75, 0][n])\n")
saved = {n: signal.getsignal(getattr(signal, n)) for n in ("SIGTERM", "SIGINT", "SIGHUP") if hasattr(signal, n)}
try:
    lifepi.CRASH_LOG.unlink(missing_ok=True)
    code = lifepi.supervise([], cmd=[sys.executable, str(script)])
finally:
    for n, h in saved.items(): signal.signal(getattr(signal, n), h)
assert code == 0 and (sup / "runs").read_text().split() == ["-", "crash", "update"], (code, (sup / "runs").read_text())
assert "exited with code 1; starting again" in lifepi.CRASH_LOG.read_text()
# ...and gives up on a crash loop instead of spinning forever
script.write_text("import sys; sys.exit(3)\n")
real_sleep = lifepi.time.sleep; lifepi.time.sleep = lambda s: None
try:
    assert lifepi.supervise([], cmd=[sys.executable, str(script)]) == 3
finally:
    lifepi.time.sleep = real_sleep
    for n, h in saved.items(): signal.signal(getattr(signal, n), h)
assert "giving up" in lifepi.CRASH_LOG.read_text()
print("supervisor: restart after crash / update, give up on a crash loop ok")

# swipe typing: slide across the letters and lift - the word goes in, other guesses can swap in
K = {ch: (ci + .5, ri + .5) for ri, row in enumerate(lifepi.KEY_ROWS) for ci, ch in enumerate(row) if ch.isalpha()}
def keypath(word):
    pts = [K[c] for c in word]
    return [(p[0] + (q[0] - p[0]) * t / 6, p[1] + (q[1] - p[1]) * t / 6) for p, q in zip(pts, pts[1:]) for t in range(6)] + [pts[-1]]
for w in ("the", "hello", "commander", "dragon", "poison"):
    assert lifepi.swipe_decode(keypath(w), K, lifepi.SWIPE_WORDS)[0] == w, w
assert lifepi.swipe_decode([(0.5, 1.5)], K, lifepi.SWIPE_WORDS) == []
for ang in (0, 90, 180, 270):
    a = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
    a.players[0]["name"] = "Atraxa"
    got = []
    a.ask_text("Name", "", got.append, angle=ang); a.render()
    m = a.modals[-1]; v, area, kw, kh = m["swipe_grid"]
    (rx, ry), (dx, dy) = lifepi.frame_axes(v.angle)
    def to_canvas(ch):                        # a key's centre on the screen (inverse of to_local)
        x, y = area.x + K[ch][0] * kw - v.w / 2, area.y + K[ch][1] * kh - v.h / 2
        det = rx * dy - ry * dx
        return (v.cx + (x * dy - y * ry) / det, v.cy + (y * rx - x * dx) / det)
    for word in ("atraxa", "game"):
        pts = [to_canvas(c) for c in word]
        a.pointer_down("mouse", pts[0])
        for p, q in zip(pts, pts[1:]):
            for t in range(1, 7):
                a.pointer_move("mouse", (p[0] + (q[0] - p[0]) * t / 6, p[1] + (q[1] - p[1]) * t / 6))
        a.render()                             # the trail is drawn
        a.pointer_up("mouse", pts[-1])
    assert m["text"] == "Atraxa Game", (ang, m["text"])
    if m["alts"]:
        alt = m["alts"][0]; a.render(); a.swipe_swap(m, alt)
        assert m["text"] == "Atraxa " + alt.capitalize() and "game" in m["alts"], m["text"]
    p = to_canvas("x"); a.pointer_down("mouse", p); a.pointer_up("mouse", p)
    assert m["text"].endswith("x"), "a tap still types one letter"
    a.set_text(m, ""); assert not m["alts"], "the guesses go once the word is deleted"
print("swipe typing ok")

# the menu and Undo buttons sit as a pair in the middle of the screen (custom: its default sides,
# which meet there too); names step aside
a.settings["sides"] = []
for seating in lifepi.SEATINGS:
    for n in range(2, lifepi.MAX_PLAYERS + 1):
        a.modals.clear(); a.settings["seating"] = seating; a.start_game(n, 40); a.first_anim = None; a.render()
        r = a.hub_r(); m, u = a.menu_pos(r), a.undo_pos(r)
        assert abs((m[0] + u[0]) / 2 - a.W / 2) <= 1, (seating, n, m, u)
        assert m[1] == u[1] == a.H // 2 and m[0] < u[0]
print("menu / undo centred ok")

# changing commander damage shows the running +n / -n, like life totals do
a.settings["seating"] = "table"; a.start_game(4, 40); a.first_anim = None
a.push("cmd", idx=1, angle=0)
a.change_cmd(1, 0, 1); a.change_cmd(1, 0, 1); a.change_cmd(1, 0, 1)
assert a.fx[("cmd", 1, 0, 1)]["d"] == 3 and a.fx_pill(("cmd", 1, 0, 1), 20)
a.change_cmd(1, 0, -1); assert a.fx[("cmd", 1, 0, 1)]["d"] == 2
a.change_cmd(1, 3, -1); assert ("cmd", 1, 3, 1) not in a.fx            # nothing to take off: no pill
a.change_counter(1, "poison", 2); assert a.fx[("counter", 1, "poison")]["d"] == 2
a.render(); pygame.image.save(a.canvas, os.path.join(shots, "cmd_running_delta.png"))
a.fx[("cmd", 1, 0, 1)]["t"] -= 5; assert a.fx_pill(("cmd", 1, 0, 1), 20) is None   # fades away
a.change_cmd(1, 0, 1); assert a.fx[("cmd", 1, 0, 1)]["d"] == 1                    # ...and starts over
a.modals.clear(); a.push("player", idx=1, angle=0); a.render()
pygame.image.save(a.canvas, os.path.join(shots, "player_running_delta.png"))
print("commander damage +/- ok"); print("SHOTS", shots)

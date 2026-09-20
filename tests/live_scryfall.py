"""Live end-to-end check of the Scryfall art search (needs internet).
   SDL_VIDEODRIVER=dummy python3 tests/live_scryfall.py"""
import os, sys, tempfile, time
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
tmp = tempfile.mkdtemp()
for k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
    os.environ[k] = os.path.join(tmp, k)
shots = os.environ.get("LIFEPI_SHOTS", tmp)
os.makedirs(shots, exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pygame, lifepi
from types import SimpleNamespace as NS

app = lifepi.App(NS(windowed="800x480", rotate=None, fps=30))
app.open_art_search(0, "lightning bolt", 0)
grid = app.modals[-1]
deadline = time.time() + 90
while grid["status"] and time.time() < deadline:
    time.sleep(0.2)
print("status:", repr(grid["status"]), "items:", len(grid["items"]))
assert grid["status"] == "" and len(grid["items"]) >= 3, grid["status"]
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "live_grid.png"))
it = grid["items"][0]
assert app.cover(it["img"], 266, 160) is not None, "downloaded art did not decode"
app.grid_pick(grid, it)
assert app.players[0]["art"] and "illus." in app.players[0]["credit"]
app.render(); pygame.image.save(app.canvas, os.path.join(shots, "live_game.png"))
print("live scryfall ok:", it["label"], "|", app.players[0]["credit"])

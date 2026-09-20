#!/usr/bin/env python3
"""
LifePi - a touch-first Magic: The Gathering life counter that runs natively on
a Raspberry Pi (or any Linux box) with nothing but Python 3 and pygame/SDL2.

  * 1-6 players, table seating (top row flipped 180 deg) or upright seating
  * tap = +/-1, hold = +/-10, running delta indicator
  * poison / commander tax / energy / experience, commander damage per opponent
  * card-art backgrounds from Scryfall (cached on disk) or your own local images
  * saved player profiles, dice / coin / random player
  * MANUAL SCREEN ROTATION (0/90/180/270) done in-app - no OS or compositor
    tricks needed; touch input is remapped to match
  * crash-safe: state is autosaved to ~/.config/lifepi/state.json

Unofficial fan content. Not affiliated with Wizards of the Coast, Scryfall or
any other life counter app.  MIT licensed - see LICENSE.
"""
import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pygame

VERSION = "1.0.0"
APP = "lifepi"
HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / APP
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / APP / "art"
IMAGE_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share")) / APP / "images"
STATE_FILE = CONFIG_DIR / "state.json"
USER_AGENT = f"LifePi/{VERSION} (+https://github.com/TheRealShadoh/LifePi)"

WHITE = (255, 255, 255)
GREY = (170, 170, 180)
DARK = (18, 18, 24)
BTN = (58, 60, 74)
BTN_ON = (70, 120, 200)
BTN_BAD = (150, 50, 50)
BTN_OK = (50, 130, 80)
PALETTE = [(46, 78, 140), (150, 42, 42), (38, 108, 62), (176, 150, 84),
           (62, 52, 76), (118, 58, 140), (196, 106, 38), (36, 116, 126)]
COUNTERS = [("poison", "Poison", "PSN"), ("tax", "Cmdr tax", "TAX"),
            ("energy", "Energy", "NRG"), ("exp", "Experience", "EXP")]
ROWS = {1: [1], 2: [1, 1], 3: [2, 1], 4: [2, 2], 5: [3, 2], 6: [3, 3]}
HOLD_DELAY = 0.45


# --------------------------------------------------------------------------- helpers
def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def unrotate(px, py, disp_w, disp_h, rot):
    """Map a point on a surface displayed rotated clockwise by `rot` degrees
    (displayed size disp_w x disp_h) back to the unrotated surface."""
    if rot == 90:
        return py, disp_w - 1 - px
    if rot == 180:
        return disp_w - 1 - px, disp_h - 1 - py
    if rot == 270:
        return disp_h - 1 - py, px
    return px, py


def new_player(i, life):
    return {"name": f"Player {i + 1}", "life": life, "color": i % len(PALETTE),
            "art": None, "credit": "",
            "counters": {k: 0 for k, _, _ in COUNTERS}, "cmd": [0] * 6}


def layout(n, w, h, seating):
    """Return [(rect, angle)] in clockwise seat order."""
    rows = ROWS[n]
    rh = h / len(rows)
    out = []
    for ri, cnt in enumerate(rows):
        y0, y1 = round(ri * rh), round((ri + 1) * rh)
        cells = []
        for ci in range(cnt):
            x0, x1 = round(ci * w / cnt), round((ci + 1) * w / cnt)
            flipped = seating == "table" and len(rows) > 1 and ri == 0
            cells.append((pygame.Rect(x0, y0, x1 - x0, y1 - y0), 180 if flipped else 0))
        if ri == 1:
            cells.reverse()          # clockwise: top L->R, then bottom R->L
        out += cells
    return out


class View:
    """An off-screen surface drawn upright, then placed on the canvas at 0/180 deg.
    Touch zones registered through it are transformed to canvas coordinates."""

    def __init__(self, app, rect, angle=0):
        self.app, self.rect, self.angle = app, pygame.Rect(rect), angle
        self.surf = pygame.Surface(self.rect.size)
        self.w, self.h = self.rect.size

    def zone(self, r, tap=None, hold=None, repeat=0.15):
        r = pygame.Rect(r)
        if self.angle == 180:
            r = pygame.Rect(self.w - r.right, self.h - r.bottom, r.w, r.h)
        r.move_ip(self.rect.topleft)
        self.app.zones.append((r, tap, hold, repeat))

    def finish(self):
        s = self.surf if self.angle == 0 else pygame.transform.flip(self.surf, True, True)
        self.app.canvas.blit(s, self.rect.topleft)


# --------------------------------------------------------------------------- app
class App:
    def __init__(self, args):
        self.args = args
        for d in (CONFIG_DIR, CACHE_DIR, IMAGE_DIR):
            d.mkdir(parents=True, exist_ok=True)
        self.settings = {"rotation": 0, "seating": "table", "cmd_hits_life": True,
                         "start_life": 40}
        self.players = [new_player(i, 40) for i in range(4)]
        self.profiles = {}
        self.load_state()
        if args.rotate is not None:
            self.settings["rotation"] = args.rotate % 360 // 90 * 90

        pygame.display.init()      # no audio needed; skips ALSA start-up
        pygame.font.init()
        pygame.display.set_caption("LifePi")
        if args.windowed:
            w, h = (int(v) for v in args.windowed.lower().split("x"))
            self.screen = pygame.display.set_mode((w, h))
        else:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.clock = pygame.time.Clock()
        self.fonts, self.img_cache = {}, {}
        self.zones, self.pointers, self.modals, self.fx = [], {}, [], {}
        self.dirty, self.state_dirty, self.last_save = True, False, 0.0
        self.running = True
        self.make_canvas()

    # ------------------------------------------------------------------ state
    def load_state(self):
        try:
            data = json.loads(STATE_FILE.read_text())
            self.settings.update(data.get("settings", {}))
            self.profiles = data.get("profiles", {})
            players = data.get("players") or []
            if 1 <= len(players) <= 6:
                base = [new_player(i, self.settings["start_life"]) for i in range(len(players))]
                for b, p in zip(base, players):
                    b.update({k: p[k] for k in b if k in p and k not in ("counters", "cmd")})
                    b["counters"].update(p.get("counters", {}))
                    b["cmd"] = (list(p.get("cmd", [])) + [0] * 6)[:6]
                self.players = base
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save_state(self):
        data = {"settings": self.settings, "players": self.players, "profiles": self.profiles}
        tmp = STATE_FILE.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=1))
            os.replace(tmp, STATE_FILE)
        except OSError as e:
            print("save failed:", e, file=sys.stderr)
        self.state_dirty, self.last_save = False, time.time()

    def changed(self):
        self.dirty = self.state_dirty = True

    # ------------------------------------------------------------------ drawing utils
    def make_canvas(self):
        sw, sh = self.screen.get_size()
        rot = self.settings["rotation"]
        self.canvas = pygame.Surface((sw, sh) if rot in (0, 180) else (sh, sw))
        self.W, self.H = self.canvas.get_size()
        self.img_cache.clear()
        self.dirty = True

    def font(self, size):
        size = max(10, int(size))
        if size not in self.fonts:
            self.fonts[size] = pygame.font.Font(None, size)
        return self.fonts[size]

    def text(self, surf, s, size, color=WHITE, maxw=None, alpha=255, shadow=True, **anchor):
        size = max(10, int(size))
        f = self.font(size)
        while maxw and size > 10 and f.size(s)[0] > maxw:
            size = int(size * 0.9)
            f = self.font(size)
        img = f.render(s, True, color)
        r = img.get_rect(**anchor)
        if shadow:
            sh = f.render(s, True, (0, 0, 0))
            sh.set_alpha(min(alpha, 170))
            off = max(1, size // 28)
            surf.blit(sh, r.move(off, off))
        if alpha < 255:
            img.set_alpha(alpha)
        surf.blit(img, r)
        return r

    def cover(self, path, w, h, dim=0):
        """Load an image, scale+crop it to fill w x h, optionally darken. Cached."""
        key = (path, w, h, dim)
        if key in self.img_cache:
            return self.img_cache[key]
        if len(self.img_cache) > 80:
            self.img_cache.clear()
        out = None
        try:
            img = pygame.image.load(path).convert()
            iw, ih = img.get_size()
            sc = max(w / iw, h / ih)
            img = pygame.transform.smoothscale(img, (max(w, round(iw * sc)), max(h, round(ih * sc))))
            out = pygame.Surface((w, h))
            out.blit(img, ((w - img.get_width()) // 2, (h - img.get_height()) // 2))
            if dim:
                shade = pygame.Surface((w, h))
                shade.set_alpha(dim)
                out.blit(shade, (0, 0))
        except (pygame.error, OSError, TypeError):
            out = None
        self.img_cache[key] = out
        return out

    def button(self, v, rect, label, tap, hold=None, bg=BTN, fg=WHITE, size=None, repeat=0.15):
        rect = pygame.Rect(rect).inflate(-6, -6)
        pygame.draw.rect(v.surf, bg, rect, border_radius=max(4, rect.h // 6))
        self.text(v.surf, label, size or rect.h * 0.5, fg, maxw=rect.w - 10,
                  shadow=False, center=rect.center)
        v.zone(rect, tap, hold, repeat)

    # ------------------------------------------------------------------ game actions
    def change_life(self, i, d):
        self.players[i]["life"] += d
        now = time.time()
        fx = self.fx.setdefault(i, {"d": 0, "t": 0})
        if now - fx["t"] > 1.8:
            fx["d"] = 0
        fx["d"] += d
        fx["t"] = now
        self.changed()

    def change_counter(self, i, key, d):
        c = self.players[i]["counters"]
        c[key] = max(0, c[key] + d)
        self.changed()

    def change_cmd(self, i, src, d):
        p = self.players[i]
        new = max(0, p["cmd"][src] + d)
        applied = new - p["cmd"][src]
        p["cmd"][src] = new
        if applied and self.settings["cmd_hits_life"]:
            self.change_life(i, -applied)
        self.changed()

    def is_dead(self, p):
        return p["life"] <= 0 or p["counters"]["poison"] >= 10 or max(p["cmd"]) >= 21

    def start_game(self, n, life):
        self.settings["start_life"] = life
        old = self.players
        self.players = []
        for i in range(n):
            p = new_player(i, life)
            if i < len(old):
                p.update({k: old[i][k] for k in ("name", "color", "art", "credit")})
            self.players.append(p)
        self.fx.clear()
        self.modals.clear()
        self.changed()

    def rotate_screen(self):
        self.settings["rotation"] = (self.settings["rotation"] + 90) % 360
        self.make_canvas()
        self.changed()

    def remember_profile(self, p):
        if not p["name"].startswith("Player "):
            self.profiles[p["name"]] = {k: p[k] for k in ("color", "art", "credit")}

    # ------------------------------------------------------------------ modal plumbing
    def push(self, kind, **kw):
        self.modals.append(dict(kind=kind, **kw))
        self.dirty = True

    def pop(self, *_):
        if self.modals:
            self.modals.pop()
        self.dirty = True

    def ask_text(self, title, initial, cb, angle=0):
        self.push("keyboard", title=title, text=initial, cb=cb, angle=angle)

    # ------------------------------------------------------------------ player panel
    def draw_panel(self, i, rect, angle):
        p = self.players[i]
        v = View(self, rect, angle)
        s, w, h = v.surf, v.w, v.h
        bg = self.cover(p["art"], w, h, dim=105) if p["art"] else None
        if bg:
            s.blit(bg, (0, 0))
        else:
            s.fill(PALETTE[p["color"] % len(PALETTE)])
        top, bot = int(h * 0.2), int(h * 0.22)
        self.text(s, p["name"], top * 0.6, maxw=w * 0.85, center=(w // 2, top // 2))

        size = int(min(h * 0.56, w * 0.5))
        self.text(s, str(p["life"]), size, maxw=w * 0.62, center=(w // 2, h // 2))
        self.text(s, "-", size * 0.5, alpha=110, center=(int(w * 0.09), h // 2))
        self.text(s, "+", size * 0.5, alpha=110, center=(int(w * 0.91), h // 2))

        fx = self.fx.get(i)
        if fx and fx["d"]:
            age = time.time() - fx["t"]
            if age < 1.8:
                a = 255 if age < 1.2 else int(255 * (1.8 - age) / 0.6)
                self.text(s, f"{fx['d']:+d}", size * 0.3, (255, 230, 120), alpha=a,
                          center=(w // 2, h // 2 - int(size * 0.47)))

        chips = [f"{short} {p['counters'][k]}" for k, _, short in COUNTERS if p["counters"][k]]
        if max(p["cmd"]):
            chips.append(f"CMD {max(p['cmd'])}")
        self.text(s, "   ".join(chips) if chips else "counters", bot * 0.42,
                  WHITE if chips else GREY, alpha=255 if chips else 120, maxw=w * 0.9,
                  center=(w // 2, h - bot // 2 - bot // 8))
        if bg and p["credit"]:
            self.text(s, p["credit"], max(11, h * 0.05), GREY, maxw=w * 0.95,
                      midbottom=(w // 2, h - 3))
        if self.is_dead(p):
            shade = pygame.Surface((w, h))
            shade.set_alpha(165)
            s.blit(shade, (0, 0))
            self.text(s, "KO", size * 0.35, (230, 80, 80), center=(w // 2, h // 2 + int(size * 0.45)))
        pygame.draw.rect(s, (0, 0, 0), s.get_rect(), 2)

        mid = h - top - bot
        v.zone((0, top, w // 2, mid), lambda: self.change_life(i, -1),
               lambda: self.change_life(i, -10), 0.5)
        v.zone((w // 2, top, w - w // 2, mid), lambda: self.change_life(i, 1),
               lambda: self.change_life(i, 10), 0.5)
        opener = lambda: self.push("player", idx=i, angle=angle)
        v.zone((0, 0, w, top), opener)
        v.zone((0, h - bot, w, bot), opener)
        v.finish()

    # ------------------------------------------------------------------ modals
    def modal_view(self, m, title):
        v = View(self, (0, 0, self.W, self.H), m.get("angle", 0))
        v.surf.fill(DARK)
        v.u = max(40, self.H // 8)               # basic row height
        self.text(v.surf, title, v.u * 0.55, maxw=self.W - v.u * 3, shadow=False,
                  midleft=(16, v.u // 2))
        return v

    def draw_menu(self, m):
        v = self.modal_view(m, f"LifePi {VERSION}")
        u, W = v.u, self.W
        st = self.settings
        items = [
            ("New game", lambda: self.push("newgame", n=len(self.players), life=st["start_life"]), BTN_OK),
            ("Reset this game", lambda: self.start_game(len(self.players), st["start_life"]), BTN),
            ("Dice / coin", lambda: self.push("dice", result="", sub=""), BTN),
            (f"Rotate screen ({st['rotation']} deg)", self.rotate_screen, BTN_ON),
            (f"Seating: {st['seating']}", self.toggle_seating, BTN),
            (f"Cmdr dmg hits life: {'on' if st['cmd_hits_life'] else 'off'}", self.toggle_cmd, BTN),
            ("Back to game", self.pop, BTN),
            ("Quit", self.quit, BTN_BAD),
        ]
        rh = (self.H - u - u // 2) // 4
        for k, (label, fn, bg) in enumerate(items):
            r = (k % 2 * W // 2, u + k // 2 * rh, W // 2, rh)
            self.button(v, r, label, fn, bg=bg, size=min(rh * 0.42, W * 0.035))
        self.text(v.surf, "Unofficial fan content - not affiliated with Wizards of the Coast. "
                  "Card art via Scryfall.", u * 0.3, GREY, maxw=W - 20, shadow=False,
                  midbottom=(W // 2, self.H - 4))
        v.finish()

    def toggle_seating(self):
        self.settings["seating"] = "upright" if self.settings["seating"] == "table" else "table"
        self.changed()

    def toggle_cmd(self):
        self.settings["cmd_hits_life"] = not self.settings["cmd_hits_life"]
        self.changed()

    def quit(self):
        self.running = False

    def draw_newgame(self, m):
        v = self.modal_view(m, "New game")
        u, W = v.u, self.W
        rh = (self.H - u) // 4

        def setv(k, val):
            m[k] = val
            self.dirty = True

        self.text(v.surf, "Players", rh * 0.3, GREY, shadow=False, midleft=(16, u + 10))
        for n in range(1, 7):
            self.button(v, ((n - 1) * W // 6, u + rh // 5, W // 6, rh - rh // 5), str(n),
                        lambda n=n: setv("n", n), bg=BTN_ON if m["n"] == n else BTN)
        y = u + rh
        self.text(v.surf, "Starting life", rh * 0.3, GREY, shadow=False, midleft=(16, y + 10))
        for k, life in enumerate((20, 30, 40)):
            self.button(v, (k * W // 4, y + rh // 5, W // 4, rh - rh // 5), str(life),
                        lambda life=life: setv("life", life), bg=BTN_ON if m["life"] == life else BTN)
        custom = m["life"] not in (20, 30, 40)

        def set_custom(t):
            if t.strip().isdigit() and int(t) > 0:
                setv("life", min(9999, int(t)))
        self.button(v, (3 * W // 4, y + rh // 5, W // 4, rh - rh // 5),
                    f"Custom: {m['life']}" if custom else "Custom",
                    lambda: self.ask_text("Starting life", "", set_custom),
                    bg=BTN_ON if custom else BTN)
        y = self.H - rh
        self.button(v, (0, y, W // 2, rh), "Cancel", self.pop)
        self.button(v, (W // 2, y, W // 2, rh), "Start", lambda: self.start_game(m["n"], m["life"]), bg=BTN_OK)
        v.finish()

    def draw_dice(self, m):
        v = self.modal_view(m, "Dice / coin")
        u, W, H = v.u, self.W, self.H
        rh = max(u, H // 6)

        def roll(sides):
            m["result"], m["sub"] = str(random.randint(1, sides)), f"d{sides}"
            self.dirty = True

        def coin():
            m["result"], m["sub"] = random.choice(("Heads", "Tails")), "coin flip"
            self.dirty = True

        def who():
            m["result"], m["sub"] = random.choice(self.players)["name"], "random player"
            self.dirty = True

        dice = (4, 6, 8, 10, 12, 20)
        for k, d in enumerate(dice):
            self.button(v, (k * W // 6, u, W // 6, rh), f"d{d}", lambda d=d: roll(d))
        self.button(v, (0, H - rh, W // 3, rh), "Coin", coin)
        self.button(v, (W // 3, H - rh, W // 3, rh), "Random player", who)
        self.button(v, (2 * W // 3, H - rh, W // 3, rh), "Close", self.pop, bg=BTN_ON)
        mid = (u + rh + H - rh) // 2
        if m["result"]:
            self.text(v.surf, m["result"], (H - u - 2 * rh) * 0.7, (255, 230, 120), maxw=W * 0.9,
                      shadow=False, center=(W // 2, mid))
            self.text(v.surf, m["sub"], u * 0.4, GREY, shadow=False, midtop=(W // 2, u + rh + 6))
        v.finish()

    def draw_player(self, m):
        i = m["idx"]
        if i >= len(self.players):
            return self.pop()
        p, ang = self.players[i], m.get("angle", 0)
        v = self.modal_view(m, p["name"])
        u, W, H = v.u, self.W, self.H
        self.button(v, (W - 2 * u, 0, 2 * u, u), "Done", self.pop, bg=BTN_ON)

        def rename(t):
            if t.strip():
                p["name"] = t.strip()[:18]
                prof = self.profiles.get(p["name"])
                if prof and not p["art"]:
                    p.update(prof)
                self.remember_profile(p)
                self.changed()

        def search(t):
            if t.strip():
                self.open_art_search(i, t.strip(), ang)

        def recolor():
            p["color"] = (p["color"] + 1) % len(PALETTE)
            p["art"], p["credit"] = None, ""
            self.remember_profile(p)
            self.changed()

        acts = [("Rename", lambda: self.ask_text("Player name", "" if p["name"].startswith("Player ") else p["name"], rename, ang)),
                ("Card art", lambda: self.ask_text("Search Scryfall for a card", "", search, ang)),
                ("Local image", lambda: self.open_local_images(i, ang)),
                ("Colour", recolor),
                ("Profiles", lambda: self.open_profiles(i, ang))]
        for k, (label, fn) in enumerate(acts):
            self.button(v, (k * W // len(acts), u, W // len(acts), u), label, fn, size=u * 0.38)

        others = [j for j in range(len(self.players)) if j != i]
        nrows = max(len(COUNTERS), len(others), 1)
        rh = (H - 2 * u - u // 3) // (nrows + 1)
        y0 = 2 * u + u // 6
        cols = 2 if others else 1
        cw = W // cols

        def row(x, y, label, value, minus, plus, warn=False):
            b = min(rh, cw // 5)
            self.text(v.surf, label, rh * 0.42, maxw=cw - 3 * b - 24, shadow=False, midleft=(x + 14, y + rh // 2))
            self.button(v, (x + cw - 3 * b - 8, y, b, rh), "-", minus, hold=minus, size=rh * 0.7)
            self.text(v.surf, str(value), rh * 0.6, (255, 110, 110) if warn else WHITE, shadow=False,
                      center=(x + cw - 2 * b - 8 + b // 2, y + rh // 2))
            self.button(v, (x + cw - b - 8, y, b, rh), "+", plus, hold=plus, size=rh * 0.7)

        self.text(v.surf, "Counters", rh * 0.4, GREY, shadow=False, midleft=(14, y0 + rh // 2))
        for k, (key, label, _) in enumerate(COUNTERS):
            row(0, y0 + (k + 1) * rh, label, p["counters"][key],
                lambda key=key: self.change_counter(i, key, -1),
                lambda key=key: self.change_counter(i, key, 1),
                warn=key == "poison" and p["counters"][key] >= 10)
        if others:
            self.text(v.surf, "Commander damage from", rh * 0.4, GREY, shadow=False,
                      midleft=(cw + 14, y0 + rh // 2))
            for k, j in enumerate(others):
                row(cw, y0 + (k + 1) * rh, self.players[j]["name"], p["cmd"][j],
                    lambda j=j: self.change_cmd(i, j, -1), lambda j=j: self.change_cmd(i, j, 1),
                    warn=p["cmd"][j] >= 21)
        v.finish()

    def draw_keyboard(self, m):
        v = self.modal_view(m, m["title"])
        u, W, H = v.u, self.W, self.H
        box = pygame.Rect(12, u, W - 24, u)
        pygame.draw.rect(v.surf, (36, 38, 50), box, border_radius=8)
        self.text(v.surf, m["text"] + "|", u * 0.6, maxw=box.w - 20, shadow=False, midleft=(box.x + 12, box.centery))
        rows = ["1234567890", "qwertyuiop", "asdfghjkl'", "zxcvbnm,.-"]
        kh = (H - 2 * u - 8) // 5
        y = 2 * u + 8

        def add(ch):
            t = m["text"]
            m["text"] = (t + (ch.upper() if not t or t.endswith(" ") else ch))[:40]
            self.dirty = True

        def back():
            m["text"] = m["text"][:-1]
            self.dirty = True

        for ri, keys in enumerate(rows):
            for ci, ch in enumerate(keys):
                self.button(v, (ci * W // 10, y + ri * kh, W // 10, kh), ch.upper(), lambda ch=ch: add(ch),
                            size=kh * 0.55)
        yb = y + 4 * kh
        self.button(v, (0, yb, W // 5, kh), "Cancel", self.pop, bg=BTN_BAD, size=kh * 0.4)
        self.button(v, (W // 5, yb, 2 * W // 5, kh), "space", lambda: add(" "), size=kh * 0.4)
        self.button(v, (3 * W // 5, yb, W // 5, kh), "DEL", back, hold=back, size=kh * 0.4)
        self.button(v, (4 * W // 5, yb, W // 5, kh), "OK", lambda: self.keyboard_ok(m), bg=BTN_OK, size=kh * 0.4)
        v.finish()

    def keyboard_ok(self, m):
        self.pop()
        m["cb"](m["text"])

    def draw_grid(self, m):
        """Generic 3x2 paged picker. items: {label, img, value}; may grow from a thread."""
        v = self.modal_view(m, m["title"])
        u, W, H = v.u, self.W, self.H
        items = m["items"]
        per = 6
        pages = max(1, (len(items) + per - 1) // per)
        m["page"] = min(m["page"], pages - 1)
        gh = H - 2 * u
        cw, ch = W // 3, gh // 2
        for k, it in enumerate(items[m["page"] * per:(m["page"] + 1) * per]):
            r = pygame.Rect(k % 3 * cw, u + k // 3 * ch, cw, ch).inflate(-8, -8)
            img = self.cover(it["img"], r.w, r.h) if it.get("img") else None
            if img:
                v.surf.blit(img, r)
            else:
                pygame.draw.rect(v.surf, it.get("bg", BTN), r, border_radius=8)
            strip = pygame.Surface((r.w, max(18, r.h // 5)))
            strip.set_alpha(170)
            v.surf.blit(strip, (r.x, r.bottom - strip.get_height()))
            self.text(v.surf, it["label"], strip.get_height() * 0.75, maxw=r.w - 8, shadow=False,
                      center=(r.centerx, r.bottom - strip.get_height() // 2))
            v.zone(r, lambda it=it: self.grid_pick(m, it))
        if m.get("status"):
            self.text(v.surf, m["status"], u * 0.45, GREY, maxw=W * 0.9, shadow=False,
                      center=(W // 2, u + gh // 2) if not items else (W // 2, u // 2))

        def page(d):
            m["page"] = (m["page"] + d) % pages
            self.dirty = True
        y = H - u
        self.button(v, (0, y, W // 4, u), "< Prev", lambda: page(-1))
        self.text(v.surf, f"{m['page'] + 1} / {pages}", u * 0.4, GREY, shadow=False, center=(W * 3 // 8, y + u // 2))
        self.button(v, (W // 2, y, W // 4, u), "Next >", lambda: page(1))
        self.button(v, (3 * W // 4, y, W // 4, u), "Cancel", self.pop, bg=BTN_BAD)
        v.finish()

    def grid_pick(self, m, it):
        self.pop()
        m["on_pick"](it)

    # ------------------------------------------------------------------ art / profiles
    def set_art(self, i, path, credit):
        p = self.players[i]
        p["art"], p["credit"] = path, credit
        self.remember_profile(p)
        self.changed()

    def open_art_search(self, i, query, angle):
        grid = dict(title=f"Card art: {query}", items=[], page=0, status="Searching Scryfall...", angle=angle,
                    on_pick=lambda it: self.set_art(i, it["img"], it["value"]))
        self.push("grid", **grid)
        grid = self.modals[-1]

        def work():
            try:
                url = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
                    {"q": query, "unique": "art", "order": "edhrec"})
                cards = json.loads(http_get(url)).get("data", [])[:18]
                for c in cards:
                    uris = c.get("image_uris") or (c.get("card_faces") or [{}])[0].get("image_uris") or {}
                    if not uris.get("art_crop"):
                        continue
                    path = CACHE_DIR / f"{c['id']}.jpg"
                    if not path.exists():
                        time.sleep(0.1)                       # be polite to the API
                        tmp = path.with_suffix(".part")
                        tmp.write_bytes(http_get(uris["art_crop"]))
                        os.replace(tmp, path)
                    credit = f"{c.get('name', '')} - illus. {c.get('artist', 'unknown')} - (c) Wizards of the Coast"
                    grid["items"].append({"label": f"{c.get('name', '?')} ({c.get('set', '').upper()})",
                                          "img": str(path), "value": credit})
                    grid["status"] = "Loading..."
                    self.dirty = True
                grid["status"] = "" if grid["items"] else "No cards found"
            except urllib.error.HTTPError as e:
                grid["status"] = "No cards found" if e.code == 404 else f"Scryfall error {e.code}"
            except Exception as e:                            # offline, DNS, timeout...
                grid["status"] = f"Network problem: {e}"
            self.dirty = True
        threading.Thread(target=work, daemon=True).start()

    def open_local_images(self, i, angle):
        exts = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
        files = sorted(f for f in IMAGE_DIR.iterdir() if f.suffix.lower() in exts)
        items = [{"label": f.stem, "img": str(f), "value": ""} for f in files]
        self.push("grid", title="Local images", items=items, page=0, angle=angle,
                  status="" if items else f"Put images in {IMAGE_DIR}",
                  on_pick=lambda it: self.set_art(i, it["img"], ""))

    def open_profiles(self, i, angle):
        items = [{"label": name, "img": pr.get("art"), "value": name,
                  "bg": PALETTE[pr.get("color", 0) % len(PALETTE)]}
                 for name, pr in sorted(self.profiles.items())]

        def pick(it):
            p = self.players[i]
            p["name"] = it["value"]
            p.update(self.profiles[it["value"]])
            self.changed()
        self.push("grid", title="Load profile", items=items, page=0, angle=angle,
                  status="" if items else "No profiles yet - rename a player to create one", on_pick=pick)

    # ------------------------------------------------------------------ render
    def render(self):
        self.zones = []
        self.canvas.fill(DARK)
        n = len(self.players)
        for i, (rect, angle) in enumerate(layout(n, self.W, self.H, self.settings["seating"])):
            self.draw_panel(i, rect, angle)
        r = max(22, min(self.W, self.H) // 22)
        c = (self.W // 2, self.H // 2) if n > 1 else (self.W - r - 10, self.H - r - 10)
        pygame.draw.circle(self.canvas, (12, 12, 16), c, r)
        pygame.draw.circle(self.canvas, GREY, c, r, 2)
        for dy in (-r // 3, 0, r // 3):
            pygame.draw.line(self.canvas, WHITE, (c[0] - r // 2, c[1] + dy), (c[0] + r // 2, c[1] + dy), 2)
        hit = pygame.Rect(0, 0, int(r * 2.6), int(r * 2.6))
        hit.center = c
        self.zones.append((hit, lambda: self.push("menu"), None, 0))

        if self.modals:
            self.zones = []
            m = self.modals[-1]
            getattr(self, "draw_" + m["kind"])(m)

        rot = self.settings["rotation"]
        out = self.canvas if rot == 0 else pygame.transform.rotate(self.canvas, -rot)
        self.screen.blit(out, (0, 0))
        pygame.display.flip()
        self.dirty = False

    # ------------------------------------------------------------------ input
    def to_canvas(self, sx, sy):
        sw, sh = self.screen.get_size()
        return unrotate(int(sx), int(sy), sw, sh, self.settings["rotation"])

    def pointer_down(self, pid, pos):
        for z in reversed(self.zones):
            if z[0].collidepoint(pos):
                self.pointers[pid] = {"zone": z, "t": time.time(), "held": False, "next": 0}
                return

    def pointer_up(self, pid, pos):
        ptr = self.pointers.pop(pid, None)
        if ptr and not ptr["held"] and ptr["zone"][1] and ptr["zone"][0].collidepoint(pos):
            ptr["zone"][1]()
            self.dirty = True

    def tick_holds(self):
        now = time.time()
        for ptr in list(self.pointers.values()):
            rect, _, hold, repeat = ptr["zone"]
            if not hold:
                continue
            if not ptr["held"] and now - ptr["t"] >= HOLD_DELAY:
                ptr["held"], ptr["next"] = True, now
            if ptr["held"] and now >= ptr["next"]:
                hold()
                ptr["next"] = now + repeat
                self.dirty = True

    def handle(self, e):
        sw, sh = self.screen.get_size()
        if e.type == pygame.QUIT:
            self.running = False
        elif e.type == pygame.FINGERDOWN:
            pygame.mouse.set_visible(False)
            self.pointer_down(("f", e.finger_id), self.to_canvas(e.x * sw, e.y * sh))
        elif e.type == pygame.FINGERUP:
            self.pointer_up(("f", e.finger_id), self.to_canvas(e.x * sw, e.y * sh))
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and not getattr(e, "touch", False):
            self.pointer_down("mouse", self.to_canvas(*e.pos))
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and not getattr(e, "touch", False):
            self.pointer_up("mouse", self.to_canvas(*e.pos))
        elif e.type == pygame.KEYDOWN:
            m = self.modals[-1] if self.modals else None
            if m and m["kind"] == "keyboard":
                if e.key == pygame.K_RETURN:
                    self.keyboard_ok(m)
                elif e.key == pygame.K_ESCAPE:
                    self.pop()
                elif e.key == pygame.K_BACKSPACE:
                    m["text"] = m["text"][:-1]
                elif e.unicode and e.unicode.isprintable():
                    m["text"] = (m["text"] + e.unicode)[:40]
                self.dirty = True
            elif e.key == pygame.K_ESCAPE:
                self.pop() if self.modals else self.push("menu")
            elif e.key == pygame.K_r:
                self.rotate_screen()
            elif e.key == pygame.K_q and e.mod & pygame.KMOD_CTRL:
                self.running = False
        elif e.type in (pygame.VIDEORESIZE, getattr(pygame, "WINDOWSIZECHANGED", -1)):
            self.make_canvas()

    # ------------------------------------------------------------------ main loop
    def run(self, max_frames=None):
        frames = 0
        while self.running:
            for e in pygame.event.get():
                self.handle(e)
            self.tick_holds()
            now = time.time()
            if any(now - fx["t"] < 1.9 for fx in self.fx.values()):
                self.dirty = True
            if self.dirty:
                self.render()
            if self.state_dirty and now - self.last_save > 1.0:
                self.save_state()
            self.clock.tick(self.args.fps)
            frames += 1
            if max_frames and frames >= max_frames:
                break
        if self.state_dirty:
            self.save_state()
        pygame.quit()


def main():
    ap = argparse.ArgumentParser(description="LifePi - native MTG life counter for Raspberry Pi")
    ap.add_argument("--windowed", metavar="WxH", help="run in a window (e.g. 800x480) instead of fullscreen")
    ap.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), help="set screen rotation and remember it")
    ap.add_argument("--fps", type=int, default=30)
    App(ap.parse_args()).run()


if __name__ == "__main__":
    main()

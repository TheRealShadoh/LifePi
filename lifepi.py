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
import hashlib
import hmac
import http.server
import json
import os
import queue
import random
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from math import ceil
from pathlib import Path

import pygame

VERSION = "1.0.0"
APP = "lifepi"
HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / APP
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / APP / "art"
IMAGE_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share")) / APP / "images"
STATE_FILE = CONFIG_DIR / "state.json"
BACKUP_FILE = CONFIG_DIR / "state.json.bak"         # the save before the latest one
SAVE_INTERVAL = 0.5                                 # max seconds of play a power cut can lose
COMPANION_PORT = 8765                               # phones + TV view, on the local network
SEARCH_DIR = CACHE_DIR.parent / "search"            # cached Scryfall search results
SUGGEST_FILE = CACHE_DIR.parent / "autocomplete.json"
SEARCH_TTL = 7 * 86400                              # re-ask Scryfall after a week
SUGGEST_TTL = 30 * 86400
SUGGEST_DELAY = 0.3                                 # pause in typing before asking Scryfall
USER_AGENT = f"LifePi/{VERSION} (+https://github.com/TheRealShadoh/LifePi)"

WHITE = (255, 255, 255)
GREY = (170, 170, 180)
DARK = (18, 18, 24)
HEADER = (30, 31, 42)
GOOD = (40, 150, 80)
BAD = (190, 50, 50)
BTN = (58, 60, 74)
BTN_ON = (70, 120, 200)
BTN_BAD = (150, 50, 50)
BTN_OK = (50, 130, 80)
PALETTE = [(46, 78, 140), (150, 42, 42), (38, 108, 62), (176, 150, 84),
           (62, 52, 76), (118, 58, 140), (196, 106, 38), (36, 116, 126)]
COUNTERS = [("poison", "Poison", "Poison"), ("tax", "Cmdr tax", "Tax"),
            ("energy", "Energy", "Energy"), ("exp", "Experience", "Exp")]
CHIP_COLORS = {"poison": (40, 130, 70), "tax": (150, 110, 40), "energy": (175, 135, 20),
               "exp": (80, 85, 175), "cmd": (165, 45, 45)}
ROWS = {1: [1], 2: [1, 1], 3: [2, 1], 4: [2, 2], 5: [3, 2], 6: [3, 3]}
HOLD_DELAY = 0.45


# --------------------------------------------------------------------------- helpers
def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_api_lock, _api_last = threading.Lock(), [0.0]


def scryfall(path, **params):
    """GET a Scryfall API endpoint as JSON, at most ~10 requests/s as Scryfall asks."""
    with _api_lock:
        wait = _api_last[0] + 0.1 - time.time()
        if wait > 0:
            time.sleep(wait)
        _api_last[0] = time.time()
    return json.loads(http_get("https://api.scryfall.com" + path + "?" + urllib.parse.urlencode(params)))


def search_cache_path(query):
    key = hashlib.sha1(" ".join(query.lower().split()).encode()).hexdigest()[:16]
    return SEARCH_DIR / f"{key}.json"


def write_atomic(path, text, durable=False):
    """Write via a temp file + rename, so a crash never leaves a half-written file.
    durable=True also fsyncs, so the data survives a power cut (Pi SD cards)."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        if durable:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, path)
    if durable:
        fsync_dir(path.parent)


def fsync_dir(d):
    if hasattr(os, "O_DIRECTORY"):           # POSIX: make the rename itself durable
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def as_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


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


def lan_ip():
    """This machine's address on the local network: the interface of the default route
    (where phones on the same Wi-Fi are). UDP connect() sends no packets."""
    for target in ("192.0.2.1", "10.255.255.255"):    # TEST-NET-1 follows the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 1))
            return s.getsockname()[0]
        except OSError:
            pass
        finally:
            s.close()
    return "127.0.0.1"


def qr_matrix(text):
    """QR code modules for text, or None without the optional qrcode package."""
    try:
        import qrcode
    except ImportError:
        return None
    q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(text)
    q.make(fit=True)
    return q.get_matrix()


class Companion:
    """Web server for phones (/) and TVs (/tv) on the local network.

    Runs on its own threads; it never touches game state directly. Requests that change
    the game are queued and applied by the main loop (App.tick_remote), and the main loop
    publishes a JSON snapshot that is served to /api/state and streamed to /api/events."""

    MAX_FAILS = 10                         # wrong PINs per address before a lock-out...
    LOCKOUT = 60                           # ...of this many seconds

    def __init__(self, app, port):
        self.app, self.port = app, port
        self.actions = queue.Queue()
        self.cond = threading.Condition()
        self.snap, self.version = b"{}", 0
        self.clients, self.fails = 0, {}
        self.httpd, self.error = None, ""

    @property
    def running(self):
        return self.httpd is not None

    def start(self):
        try:
            self.httpd = http.server.ThreadingHTTPServer(("", self.port), make_handler(self))
        except OSError as e:
            self.error = f"Port {self.port} is in use ({e.strerror})" if e.errno in (48, 98, 10048) \
                else f"Could not start: {e}"
            return False
        self.httpd.daemon_threads = True
        self.port, self.error = self.httpd.server_address[1], ""
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return True

    def stop(self):
        httpd, self.httpd = self.httpd, None
        if httpd:
            with self.cond:
                self.cond.notify_all()             # let event streams finish
            httpd.shutdown()
            httpd.server_close()

    def publish(self, data):
        body = json.dumps(data, separators=(",", ":")).encode()
        with self.cond:
            if body != self.snap:
                self.snap, self.version = body, self.version + 1
                self.cond.notify_all()

    def check_key(self, addr, key):
        now = time.time()
        n, until = self.fails.get(addr, (0, 0))
        if until > now:
            return "locked"
        if key and hmac.compare_digest(key.encode(), self.app.settings["companion_pin"].encode()):
            self.fails.pop(addr, None)
            return "ok"
        n += 1
        self.fails[addr] = (0, now + self.LOCKOUT) if n >= self.MAX_FAILS else (n, 0)
        return "bad"


def make_handler(comp):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = f"LifePi/{VERSION}"

        def log_message(self, *_):
            pass

        def send(self, code, body=b"", ctype="application/json", extra=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in dict([("Cache-Control", "no-store")] + list(extra)).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def authed(self, q):
            verdict = comp.check_key(self.client_address[0], (q.get("k") or [""])[0])
            if verdict != "ok":
                self.send(429 if verdict == "locked" else 401,
                          json.dumps({"error": verdict}).encode())
            return verdict == "ok"

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                return self.send(200, PHONE_HTML.encode(), "text/html; charset=utf-8")
            if url.path in ("/tv", "/tv/"):
                return self.send(200, TV_HTML.encode(), "text/html; charset=utf-8")
            if not self.authed(q):
                return
            if url.path == "/api/state":
                with comp.cond:
                    body = comp.snap
                return self.send(200, body)
            if url.path == "/api/events":
                return self.events()
            if url.path.startswith("/art/"):
                return self.art(url.path[5:])
            self.send(404, b'{"error":"not found"}')

        def do_POST(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path != "/api/act":
                return self.send(404, b'{"error":"not found"}')
            if not self.authed(urllib.parse.parse_qs(url.query)):
                return
            n = as_int(self.headers.get("Content-Length"), 0)
            try:
                if not 0 < n <= 4096:
                    raise ValueError
                action = json.loads(self.rfile.read(n))
                if not isinstance(action, dict):
                    raise ValueError
            except ValueError:
                return self.send(400, b'{"error":"bad request"}')
            comp.actions.put(action)
            self.send(202, b'{"ok":true}')

        def events(self):
            """Server-sent events: the full state whenever it changes, plus keep-alives."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with comp.cond:
                comp.clients += 1
            comp.app.dirty = True
            last = -1
            try:
                while comp.running:
                    with comp.cond:
                        # keep-alives every 5 s also notice phones that left (for the count)
                        comp.cond.wait_for(lambda: comp.version != last or not comp.running, timeout=5)
                        version, body = comp.version, comp.snap
                    if not comp.running:
                        break
                    if version == last:
                        self.wfile.write(b": ping\n\n")
                    else:
                        self.wfile.write(b"data: " + body + b"\n\n")
                        last = version
                    self.wfile.flush()
            except OSError:                     # phone went away
                pass
            finally:
                with comp.cond:
                    comp.clients -= 1
                comp.app.dirty = True

        def art(self, seat):
            players = comp.app.players
            i = as_int(seat.split("?")[0], -1)
            path = players[i]["art"] if 0 <= i < len(players) else None
            try:
                f = Path(path).resolve()
                if not any(f.is_relative_to(d.resolve()) for d in (CACHE_DIR, IMAGE_DIR)):
                    raise ValueError
                body = f.read_bytes()
            except (TypeError, ValueError, OSError):
                return self.send(404, b'{"error":"no art"}')
            ctype = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
                     ".bmp": "image/bmp"}.get(f.suffix.lower(), "image/jpeg")
            self.send(200, body, ctype, [("Cache-Control", "max-age=86400")])

    return Handler


class View:
    """An off-screen surface drawn upright, then placed on the canvas at 0/180 deg.
    Touch zones registered through it are transformed to canvas coordinates."""

    def __init__(self, app, rect, angle=0):
        self.app, self.rect, self.angle = app, pygame.Rect(rect), angle
        self.surf = pygame.Surface(self.rect.size)
        self.w, self.h = self.rect.size

    def zone(self, r, tap=None, hold=None, repeat=0.15, drag=None):
        r = pygame.Rect(r)
        if self.angle == 180:
            r = pygame.Rect(self.w - r.right, self.h - r.bottom, r.w, r.h)
        r.move_ip(self.rect.topleft)
        self.app.zones.append((r, tap, hold, repeat, drag))

    def finish(self):
        s = self.surf if self.angle == 0 else pygame.transform.flip(self.surf, True, True)
        self.app.canvas.blit(s, self.rect.topleft)


# --------------------------------------------------------------------------- app
class App:
    def __init__(self, args):
        self.args = args
        for d in (CONFIG_DIR, CACHE_DIR, SEARCH_DIR, IMAGE_DIR):
            d.mkdir(parents=True, exist_ok=True)
        self.settings = {"rotation": 0, "seating": "table", "cmd_hits_life": True,
                         "start_life": 40, "companion": True, "companion_pin": ""}
        self.players = [new_player(i, 40) for i in range(4)]
        self.profiles = {}
        self.load_state()
        self.state_dirty = self.snap_dirty = False
        if not self.settings["companion_pin"]:
            self.new_pin()
        self.cache_lock = threading.Lock()
        self.ac_cache, self.ac_dirty, self.known_names = {}, False, set()
        self.load_caches()
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
        self.dirty, self.last_save = True, 0.0
        self.seats = []
        self.toast = ("", 0.0)
        self.qr_cache = (None, None)
        self.running = True
        self.make_canvas()                  # before publish(): phones get the seat layout from it
        # phones / TV: off unless a port is given (main() always gives one)
        self.companion = None
        port = getattr(args, "companion_port", None)
        if port is not None:
            self.companion = Companion(self, port)
            if self.settings["companion"]:
                self.companion.start()
        self.publish()

    # ------------------------------------------------------------------ state
    def load_state(self):
        """Restore the saved game. Falls back to the previous save if the latest is damaged;
        a damaged file is kept aside (state.json.corrupt) rather than silently overwritten."""
        for path in (STATE_FILE, BACKUP_FILE):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))   # -sig: tolerate a BOM from Windows editors
                settings, players, profiles = self.parse_state(data)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                print(f"ignoring unreadable {path.name}: {e}", file=sys.stderr)
                if path == STATE_FILE:
                    try:
                        os.replace(STATE_FILE, STATE_FILE.with_name("state.json.corrupt"))
                    except OSError:
                        pass
                continue
            self.settings.update(settings)
            self.profiles = profiles
            if players:
                self.players = players
            return

    def parse_state(self, data):
        """Validate saved JSON field by field, so one bad value can't sink the whole game."""
        s = data.get("settings", {})
        settings = {}
        if as_int(s.get("rotation"), -1) in (0, 90, 180, 270):
            settings["rotation"] = as_int(s["rotation"])
        if s.get("seating") in ("table", "upright"):
            settings["seating"] = s["seating"]
        if isinstance(s.get("cmd_hits_life"), bool):
            settings["cmd_hits_life"] = s["cmd_hits_life"]
        if as_int(s.get("start_life"), 0) > 0:
            settings["start_life"] = as_int(s["start_life"])
        if isinstance(s.get("companion"), bool):
            settings["companion"] = s["companion"]
        pin = s.get("companion_pin")
        if isinstance(pin, str) and len(pin) == 6 and pin.isdigit():
            settings["companion_pin"] = pin
        start = settings.get("start_life", self.settings["start_life"])

        players = []
        raw = data.get("players") or []
        if 1 <= len(raw) <= 6:
            for i, p in enumerate(raw):
                b = new_player(i, start)
                if isinstance(p.get("name"), str) and p["name"].strip():
                    b["name"] = p["name"][:18]
                b["life"] = as_int(p.get("life"), start)
                b["color"] = as_int(p.get("color"), b["color"]) % len(PALETTE)
                if isinstance(p.get("art"), str):
                    b["art"] = p["art"]
                if isinstance(p.get("credit"), str):
                    b["credit"] = p["credit"]
                c = p.get("counters") or {}
                b["counters"] = {k: max(0, as_int(c.get(k))) for k, _, _ in COUNTERS}
                b["cmd"] = [max(0, as_int(x)) for x in (list(p.get("cmd") or []) + [0] * 6)[:6]]
                players.append(b)

        profiles = {}
        for name, pr in (data.get("profiles") or {}).items():
            if isinstance(name, str) and isinstance(pr, dict):
                profiles[name] = {"color": as_int(pr.get("color")) % len(PALETTE),
                                  "art": pr["art"] if isinstance(pr.get("art"), str) else None,
                                  "credit": pr["credit"] if isinstance(pr.get("credit"), str) else ""}
        return settings, players, profiles

    def save_state(self):
        """Durably save the game. The previous save is kept as state.json.bak."""
        data = {"settings": self.settings, "players": self.players, "profiles": self.profiles}
        try:
            tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=1))
                f.flush()
                os.fsync(f.fileno())
            if STATE_FILE.exists():
                os.replace(STATE_FILE, BACKUP_FILE)
            os.replace(tmp, STATE_FILE)
            fsync_dir(STATE_FILE.parent)
            self.state_dirty = False            # only once it's really on disk
        except OSError as e:
            print("save failed:", e, file=sys.stderr)
        self.last_save = time.time()
        self.save_caches()

    def load_caches(self):
        """Autocomplete answers + card names from earlier searches (for offline guesses)."""
        try:
            data = json.loads(SUGGEST_FILE.read_text())
            if isinstance(data, dict):
                self.ac_cache = data
        except (OSError, ValueError):
            pass
        for entry in self.ac_cache.values():
            try:
                self.known_names.update(entry[1])
            except (IndexError, TypeError):
                pass
        for f in SEARCH_DIR.glob("*.json"):
            try:
                self.known_names.update(c["name"] for c in json.loads(f.read_text())["cards"])
            except (OSError, ValueError, KeyError, TypeError):
                pass

    def save_caches(self):
        with self.cache_lock:
            if not self.ac_dirty:
                return
            keep = sorted(self.ac_cache.items(), key=lambda kv: kv[1][0])[-1000:]
            self.ac_cache, self.ac_dirty = dict(keep), False
            text = json.dumps(self.ac_cache)
        try:
            write_atomic(SUGGEST_FILE, text)
        except OSError as e:
            print("cache save failed:", e, file=sys.stderr)

    # ------------------------------------------------------------------ phones / TV
    def new_pin(self):
        """A fresh 6-digit PIN; phones using the old one are locked out."""
        self.settings["companion_pin"] = f"{secrets.randbelow(10 ** 6):06d}"
        self.state_dirty = True
        self.qr_cache = (None, None)

    def companion_base(self, refresh=False):
        """Address phones use to reach us (LIFEPI_PUBLIC_URL overrides, e.g. in Docker)."""
        if refresh or not getattr(self, "_base", None):
            env = os.environ.get("LIFEPI_PUBLIC_URL", "").rstrip("/")
            self._base = env or f"http://{lan_ip()}:{self.companion.port}"
        return self._base

    def snapshot(self):
        n = len(self.players)
        # each seat's rectangle on the device screen (fractions of the canvas), so phones and
        # the TV place players exactly where the device does (clockwise, seating, rotation)
        W, H = self.W, self.H
        rects = [[round(r.x / W, 4), round(r.y / H, 4), round(r.w / W, 4), round(r.h / H, 4)]
                 for r, _ in layout(n, W, H, self.settings["seating"])]
        out = []
        for i, p in enumerate(self.players):
            art = None
            if p["art"] and os.path.isfile(p["art"]):
                art = f"/art/{i}?v={hashlib.sha1(p['art'].encode()).hexdigest()[:10]}"
            out.append({"seat": i, "name": p["name"], "life": p["life"],
                        "color": "#%02x%02x%02x" % PALETTE[p["color"] % len(PALETTE)],
                        "art": art, "credit": p["credit"] if art else "",
                        "counters": dict(p["counters"]), "cmd": p["cmd"][:n], "ko": self.ko_reason(p),
                        "pos": rects[i]})
        return {"players": out, "start_life": self.settings["start_life"],
                "cmd_hits_life": self.settings["cmd_hits_life"],
                "counters": [[k, label] for k, label, _ in COUNTERS],
                "join": self.companion_base() if self.companion and self.companion.running else ""}

    def publish(self):
        if self.companion and self.companion.running:
            self.companion.publish(self.snapshot())

    def tick_remote(self):
        """Apply actions queued by phones (main thread only), then publish the new state."""
        if not self.companion:
            return
        acted = False
        while True:
            try:
                a = self.companion.actions.get_nowait()
            except queue.Empty:
                break
            try:
                self.apply_remote(a)
                acted = True
            except (KeyError, TypeError, ValueError, IndexError) as e:
                print("ignored phone action", a, e, file=sys.stderr)
        if acted or self.snap_dirty:
            self.snap_dirty = False
            self.publish()

    def apply_remote(self, a):
        def num(key, lo, hi):
            v = a.get(key)
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError(key)
            return max(lo, min(hi, v))

        n, op = len(self.players), a.get("op")
        if op in ("life", "counter", "cmd", "name", "color"):
            seat = num("seat", -1, 99)
            if not 0 <= seat < n:
                return
        if op == "life":
            self.change_life(seat, num("d", -999, 999))
        elif op == "counter" and a.get("key") in self.players[0]["counters"]:
            self.change_counter(seat, a["key"], num("d", -99, 99))
        elif op == "cmd":
            src = num("src", -1, 99)
            if 0 <= src < n:
                self.change_cmd(seat, src, num("d", -99, 99))
        elif op == "name" and isinstance(a.get("name"), str):
            self.rename_player(seat, a["name"])
        elif op == "color":
            self.recolor_player(seat)
        elif op == "newgame":
            self.start_game(num("n", 1, 6), num("life", 1, 9999))
        elif op == "reset":
            self.start_game(n, self.settings["start_life"])
        elif op == "cmd_hits_life" and isinstance(a.get("on"), bool):
            self.settings["cmd_hits_life"] = a["on"]
            self.changed()

    def toggle_companion(self):
        if self.companion.running:
            self.companion.stop()
            self.settings["companion"] = False
        else:
            self.settings["companion"] = self.companion.start()
        self.companion_base(refresh=True)
        self.qr_cache = (None, None)
        self.changed()
        self.publish()

    def say(self, text):
        self.toast = (text, time.time())
        self.dirty = True

    def changed(self):
        self.dirty = self.state_dirty = self.snap_dirty = True

    # ------------------------------------------------------------------ drawing utils
    def make_canvas(self):
        sw, sh = self.screen.get_size()
        rot = self.settings["rotation"]
        self.canvas = pygame.Surface((sw, sh) if rot in (0, 180) else (sh, sw))
        self.W, self.H = self.canvas.get_size()
        self.img_cache.clear()
        self.dirty = self.snap_dirty = True       # phones mirror the layout

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
            off = max(1, size // 36)
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
        rad = max(4, min(rect.h // 6, 14))
        pygame.draw.rect(v.surf, bg, rect, border_radius=rad)
        pygame.draw.rect(v.surf, tuple(min(255, c + 24) for c in bg), rect, 1, border_radius=rad)
        self.text(v.surf, label, size or rect.h * 0.5, fg, maxw=rect.w - 10,
                  shadow=False, center=rect.center)
        v.zone(rect, tap, hold, repeat)

    def shade(self, w, h, top, mid, bot):
        """Cached black vertical gradient overlay: alpha top -> mid (centre) -> bottom."""
        key = ("shade", w, h, top, mid, bot)
        if key not in self.img_cache:
            col = pygame.Surface((1, 3), pygame.SRCALPHA)
            for y, a in enumerate((top, mid, bot)):
                col.set_at((0, y), (0, 0, 0, a))
            self.img_cache[key] = pygame.transform.smoothscale(col, (w, h))
        return self.img_cache[key]

    def corners(self, w, h, rad):
        """Cached mask that paints DARK outside a rounded rect (rounds a panel's corners)."""
        key = ("corners", w, h, rad)
        if key not in self.img_cache:
            m = pygame.Surface((w, h))
            m.fill(DARK)
            m.set_colorkey((255, 0, 255))
            pygame.draw.rect(m, (255, 0, 255), m.get_rect(), border_radius=rad)
            self.img_cache[key] = m
        return self.img_cache[key]

    def sign(self, plus, size):
        """Translucent -/+ hint glyph."""
        size = max(8, int(size))
        key = ("sign", plus, size)
        if key not in self.img_cache:
            g = pygame.Surface((size, size), pygame.SRCALPHA)
            th = max(3, size // 6)
            pygame.draw.rect(g, (255, 255, 255, 110), (0, (size - th) // 2, size, th), border_radius=th // 2)
            if plus:
                pygame.draw.rect(g, (255, 255, 255, 110), ((size - th) // 2, 0, th, (size - th) // 2),
                                 border_radius=th // 2)
                pygame.draw.rect(g, (255, 255, 255, 110), ((size - th) // 2, (size + th) // 2, th,
                                 size - (size + th) // 2), border_radius=th // 2)
            self.img_cache[key] = g
        return self.img_cache[key]

    def make_pill(self, label, size, fg=WHITE, bg=(0, 0, 0), alpha=170, outline=None):
        f = self.font(size)
        img = f.render(label, True, fg)
        ph = int(f.get_height() * 1.3)
        pill = pygame.Surface((img.get_width() + ph, ph), pygame.SRCALPHA)
        pygame.draw.rect(pill, (*bg, alpha), pill.get_rect(), border_radius=ph // 2)
        if outline:
            pygame.draw.rect(pill, outline, pill.get_rect(), 2, border_radius=ph // 2)
        pill.blit(img, img.get_rect(center=pill.get_rect().center))
        return pill

    def cmd_tile(self, j, dmg, side, number=True):
        """Mini portrait of seat j's commander (art, else colour) with the damage it dealt."""
        side = max(12, int(side))
        src = self.players[j]
        col = PALETTE[src["color"] % len(PALETTE)]
        key = ("tile", src["art"], col, side)
        if key not in self.img_cache:
            t = pygame.Surface((side, side), pygame.SRCALPHA)
            art = self.cover(src["art"], side, side) if src["art"] else None
            if art:
                t.blit(art, (0, 0))
                t.blit(self.shade(side, side, 60, 90, 150), (0, 0))
            else:
                t.fill((*col, 255))
                t.blit(self.shade(side, side, 0, 30, 90), (0, 0))
            rad = max(3, side // 5)
            mask = pygame.Surface((side, side), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=rad)
            t.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            pygame.draw.rect(t, tuple(min(255, c + 70) for c in col), t.get_rect(), max(1, side // 18),
                             border_radius=rad)
            self.img_cache[key] = t
        t = self.img_cache[key].copy()
        if dmg >= 21:
            pygame.draw.rect(t, (255, 70, 70), t.get_rect(), max(2, side // 10), border_radius=max(3, side // 5))
        if number:
            self.text(t, str(dmg), side * 0.62, (255, 120, 120) if dmg >= 21 else WHITE,
                      maxw=side * 0.9, center=(side // 2, side // 2 + 1))
        return t

    def draw_strip(self, surf, area, extra, cmds, chips):
        """Panel bottom strip: commander-damage tiles [(seat, dmg)], then counter pills
        [(label, colour)]. One line if that fits at >= 75% of the strip height; otherwise the
        strip grows `extra` px upward and the tiles get their own grid (1 or 2 rows, whichever
        gives bigger tiles) with the pills on a line below."""
        def tiles(side):
            return [self.cmd_tile(j, d, side) for j, d in cmds]

        def pills(rh):
            return [self.make_pill(label, rh * 0.5, bg=c, alpha=225) for label, c in chips]

        def width(items, gap):
            return sum(it.get_width() for it in items) + gap * (len(items) - 1)

        rh = area.h
        while rh >= area.h * 0.75:
            items, gap = tiles(rh) + pills(rh), max(3, rh // 6)
            if width(items, gap) <= area.w:
                return self.place(surf, area, [(items, rh, gap)])
            rh = int(rh * 0.92)

        area = pygame.Rect(area.x, area.y - extra, area.w, area.h + extra)
        pill_h = int(area.h * 0.36) if chips else 0
        lines = []
        if cmds:
            th, n, gap = area.h - (pill_h + 3 if chips else 0), len(cmds), 4
            side, rows = max(((min((th - 3 * (r - 1)) // r, (area.w - gap * (-(-n // r) - 1)) // -(-n // r)), r)
                              for r in (1, 2)), key=lambda sr: (sr[0], -sr[1]))
            cols = -(-n // rows)
            ts = tiles(side)
            lines += [(ts[k:k + cols], side, gap) for k in range(0, n, cols)]
        if chips:
            ph = pill_h
            while True:
                ps, gap = pills(ph), max(3, ph // 6)
                if width(ps, gap) <= area.w or ph <= 12:
                    break
                ph = int(ph * 0.9)
            lines.append((ps, ph, gap))
        self.place(surf, area, lines)

    def place(self, surf, area, lines):
        """Blit lines [(surfaces, row_height, gap)] centred in area."""
        y = area.centery - (sum(rh for _, rh, _ in lines) + 3 * (len(lines) - 1)) // 2
        for items, rh, gap in lines:
            x = area.centerx - (sum(it.get_width() for it in items) + gap * (len(items) - 1)) // 2
            for it in items:
                surf.blit(it, it.get_rect(midleft=(x, y + rh // 2)))
                x += it.get_width() + gap
            y += rh + 3

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

    def ko_reason(self, p):
        if p["counters"]["poison"] >= 10:
            return "poison"
        if max(p["cmd"]) >= 21:
            return "commander"
        if p["life"] <= 0:
            return "life"
        return ""

    def is_dead(self, p):
        return bool(self.ko_reason(p))

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

    def rename_player(self, i, t):
        p = self.players[i]
        if t.strip():
            p["name"] = t.strip()[:18]
            prof = self.profiles.get(p["name"])
            if prof and not p["art"]:
                p.update(prof)
            self.remember_profile(p)
            self.changed()

    def recolor_player(self, i):
        p = self.players[i]
        p["color"] = (p["color"] + 1) % len(PALETTE)
        p["art"], p["credit"] = None, ""
        self.remember_profile(p)
        self.changed()

    def swap_seats(self, a, b):
        """Swap two players' seats; commander damage is indexed by seat, so it follows."""
        P = self.players
        P[a], P[b] = P[b], P[a]
        for p in P:
            p["cmd"][a], p["cmd"][b] = p["cmd"][b], p["cmd"][a]
        fa, fb = self.fx.pop(a, None), self.fx.pop(b, None)
        if fa:
            self.fx[b] = fa
        if fb:
            self.fx[a] = fb
        self.changed()

    def seat_at(self, pos):
        for k, (rect, _) in enumerate(self.seats):
            if rect.collidepoint(pos):
                return k
        return None

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

    def ask_text(self, title, initial, cb, angle=0, **kw):
        """On-screen keyboard. Optional kw: local(text) -> instant suggestions,
        suggest(text) -> slower suggestions (run on a thread after a pause in typing),
        pick(suggestion) -> called when a suggestion is tapped, hint -> shown when none."""
        self.push("keyboard", title=title, text="", cb=cb, angle=angle, sugg=[], sugg_due=None, **kw)
        self.set_text(self.modals[-1], initial)

    def set_text(self, m, t):
        m["text"] = t[:40]
        if m.get("local"):
            m["sugg"] = m["local"](m["text"])
        if m.get("suggest"):
            m["sugg_due"] = time.time() + SUGGEST_DELAY
        self.dirty = True

    def tick_suggest(self):
        m = self.modals[-1] if self.modals else None
        if not m or m["kind"] != "keyboard" or not m.get("sugg_due") or time.time() < m["sugg_due"]:
            return
        m["sugg_due"], t = None, m["text"]

        def work():
            names = m["suggest"](t)
            if m["text"] == t:                       # still what's on screen?
                m["sugg"] = names
                self.dirty = True
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ player panel
    def draw_panel(self, i, rect, angle):
        p = self.players[i]
        gap = max(2, min(self.W, self.H) // 160)
        v = View(self, pygame.Rect(rect).inflate(-2 * gap, -2 * gap), angle)
        s, w, h = v.surf, v.w, v.h
        bg = self.cover(p["art"], w, h) if p["art"] else None
        if bg:
            s.blit(bg, (0, 0))
            s.blit(self.shade(w, h, 210, 150, 220), (0, 0))   # darker where the text sits
        else:
            s.fill(PALETTE[p["color"] % len(PALETTE)])
            s.blit(self.shade(w, h, 0, 25, 95), (0, 0))
        top, bot = int(h * 0.2), int(h * 0.22)
        self.text(s, p["name"], top * 0.6, maxw=w * 0.85, center=(w // 2, top // 2))

        size = int(min(h * 0.56, w * 0.5))
        self.text(s, str(p["life"]), size, maxw=w * 0.62, center=(w // 2, h // 2))
        for plus, x in ((False, w * 0.08), (True, w * 0.92)):
            g = self.sign(plus, min(size * 0.2, w * 0.07, 44))
            s.blit(g, g.get_rect(center=(int(x), h // 2)))

        fx = self.fx.get(i)
        if fx and fx["d"]:
            age = time.time() - fx["t"]
            if age < 1.8:
                pl = self.make_pill(f"{fx['d']:+d}", size * 0.24, bg=GOOD if fx["d"] > 0 else BAD, alpha=235)
                if age > 1.2:
                    pl.set_alpha(int(255 * (1.8 - age) / 0.6))
                s.blit(pl, pl.get_rect(center=(w // 2, h // 2 - int(size * 0.5))))

        chips = [(f"{short} {p['counters'][k]}", CHIP_COLORS[k]) for k, _, short in COUNTERS if p["counters"][k]]
        cmds = [(j, d) for j, d in enumerate(p["cmd"][:len(self.players)]) if d]   # includes self
        cy = h - bot // 2 - bot // 8
        if chips or cmds:
            credit_h = int(max(11, h * 0.05)) + 3 if bg and p["credit"] else 0
            area = pygame.Rect(int(w * 0.04), h - bot, int(w * 0.92), bot - credit_h - 3)
            self.draw_strip(s, area, int(bot * 0.3), cmds, chips)
        else:
            pl = self.make_pill("counters", bot * 0.34, fg=(200, 200, 210), alpha=70,
                                outline=(255, 255, 255, 50))
            s.blit(pl, pl.get_rect(center=(w // 2, cy)))
        if bg and p["credit"]:
            self.text(s, p["credit"], max(11, h * 0.05), GREY, maxw=w * 0.9,
                      midbottom=(w // 2, h - 3))
        why = self.ko_reason(p)
        if why:
            shade = pygame.Surface((w, h))
            shade.fill((25, 0, 0))
            shade.set_alpha(170)
            s.blit(shade, (0, 0))
            pl = self.make_pill("KO" if why == "life" else f"KO - {why}", size * 0.2, bg=BAD, alpha=240)
            s.blit(pl, pl.get_rect(center=(w // 2, h // 2 + int(size * 0.47))))
        s.blit(self.corners(w, h, max(6, min(w, h) // 14)), (0, 0))

        mid = h - top - bot
        v.zone((0, top, w // 2, mid), lambda: self.change_life(i, -1),
               lambda: self.change_life(i, -10), 0.5)
        v.zone((w // 2, top, w - w // 2, mid), lambda: self.change_life(i, 1),
               lambda: self.change_life(i, 10), 0.5)
        opener = lambda: self.push("player", idx=i, angle=angle)
        v.zone((0, 0, w, top), opener, drag=i)       # hold + drag the name to swap seats
        v.zone((0, h - bot, w, bot), opener)
        v.finish()

    # ------------------------------------------------------------------ modals
    def modal_view(self, m, title, accent=HEADER):
        v = View(self, (0, 0, self.W, self.H), m.get("angle", 0))
        v.surf.fill(DARK)
        v.u = max(40, self.H // 8)               # basic row height
        pygame.draw.rect(v.surf, accent, (0, 0, self.W, v.u))
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
            ("Phones & TV", self.open_remote, BTN_ON),
            ("Cast to TV", self.open_cast, BTN),
            ("Back to game", self.pop, BTN),
            ("Quit", self.quit, BTN_BAD),
        ]
        rh = (self.H - u - u // 2) // ((len(items) + 1) // 2)
        for k, (label, fn, bg) in enumerate(items):
            r = (k % 2 * W // 2, u + k // 2 * rh, W // 2, rh)
            self.button(v, r, label, fn, bg=bg, size=min(rh * 0.42, W * 0.035))
        self.text(v.surf, "Unofficial fan content - not affiliated with Wizards of the Coast. "
                  "Card art via Scryfall.", u * 0.3, GREY, maxw=W - 20, shadow=False,
                  midbottom=(W // 2, self.H - 4))
        v.finish()

    def open_remote(self):
        if self.companion and self.companion.running:
            self.companion_base(refresh=True)          # the Wi-Fi address may have changed
        self.push("remote")

    def draw_remote(self, m):
        v = self.modal_view(m, "Phones & TV")
        u, W, H = v.u, self.W, self.H
        comp = self.companion
        on = bool(comp and comp.running)
        bh = u
        body = pygame.Rect(0, u, W, H - u - bh)
        side = min(body.h - 24, int(W * 0.42))
        qr_box = pygame.Rect(16, body.y + (body.h - side) // 2, side, side)
        x0, tw = qr_box.right + 24, W - qr_box.right - 40
        lines = []                                  # (text, size, colour)
        if on:
            base = self.companion_base()
            join = f"{base}/?k={self.settings['companion_pin']}"
            if self.qr_cache[0] != join:
                self.qr_cache = (join, qr_matrix(join))
            mat = self.qr_cache[1]
            if mat:
                pygame.draw.rect(v.surf, WHITE, qr_box, border_radius=8)
                cells = len(mat) + 4                 # quiet zone of 2 modules each side
                cell = side / cells
                for y, row in enumerate(mat):
                    for x, dark in enumerate(row):
                        if dark:
                            pygame.draw.rect(v.surf, (0, 0, 0), (qr_box.x + round((x + 2) * cell),
                                             qr_box.y + round((y + 2) * cell), ceil(cell), ceil(cell)))
            else:
                pygame.draw.rect(v.surf, HEADER, qr_box, border_radius=8)
                self.text(v.surf, "Install python3-qrcode", u * 0.32, GREY, maxw=side - 16, shadow=False,
                          center=(qr_box.centerx, qr_box.centery - u * 0.25))
                self.text(v.surf, "to show a QR code here", u * 0.32, GREY, maxw=side - 16, shadow=False,
                          center=(qr_box.centerx, qr_box.centery + u * 0.25))
            n = comp.clients
            lines += [("Scan with a phone camera, or open", u * 0.32, GREY),
                      (base, u * 0.5, WHITE),
                      (f"PIN  {self.settings['companion_pin']}", u * 0.5, (255, 230, 120)),
                      ("", u * 0.2, GREY),
                      ("TV / big screen: open", u * 0.32, GREY),
                      (f"{base}/tv?k={self.settings['companion_pin']}", u * 0.36, WHITE),
                      ("Roku: mirror a phone that has that page open", u * 0.28, GREY),
                      ("", u * 0.2, GREY),
                      (f"{n} device{'s' if n != 1 else ''} connected", u * 0.34, GOOD if n else GREY)]
            if os.path.exists("/.dockerenv") and not os.environ.get("LIFEPI_PUBLIC_URL"):
                lines.append(("In Docker: set LIFEPI_PUBLIC_URL to the host's address", u * 0.28, BAD))
        else:
            pygame.draw.rect(v.surf, HEADER, qr_box, border_radius=8)
            why = ("Started with --no-companion" if not comp else comp.error or "Phones & TV are switched off")
            lines += [(why, u * 0.4, WHITE),
                      ("Turn it on to control LifePi from phones", u * 0.32, GREY),
                      ("on the same Wi-Fi and show it on a TV.", u * 0.32, GREY)]
        y = body.y + (body.h - sum(int(s * 1.25) for _, s, _ in lines)) // 2
        for text, size, col in lines:
            if text:
                self.text(v.surf, text, size, col, maxw=tw, shadow=False, topleft=(x0, y))
            y += int(size * 1.25)

        y = H - bh
        if comp:
            self.button(v, (0, y, W // 4, bh), "Turn off" if on else "Turn on", self.toggle_companion,
                        bg=BTN if on else BTN_OK)
        if on:
            self.button(v, (W // 4, y, W // 4, bh), "New PIN", lambda: (self.new_pin(), self.say(
                "New PIN - phones must scan again")))
            self.button(v, (W // 2, y, W // 4, bh), "Cast to TV", self.open_cast)
        self.button(v, (3 * W // 4, y, W // 4, bh), "Close", self.pop, bg=BTN_ON)
        v.finish()

    def open_cast(self):
        """Find Google Cast devices (Chromecast, Google TV, Nvidia Shield...) and cast /tv."""
        if not (self.companion and self.companion.running):
            return self.say("Turn on Phones & TV first (menu > Phones & TV)")
        grid = dict(title="Cast the TV view to...", items=[], page=0, angle=0,
                    status="Looking for Chromecast / Google TV / Shield...",
                    on_pick=lambda it: self.cast_to(it["value"], it["label"]))
        self.push("grid", **grid)
        grid = self.modals[-1]

        def work():
            try:
                import pychromecast
            except ImportError:
                grid["status"] = "Casting needs pychromecast: sudo apt install python3-pychromecast"
                self.dirty = True
                return
            try:
                casts, browser = pychromecast.get_chromecasts(timeout=6)
                try:
                    browser.stop_discovery()
                except AttributeError:                  # pychromecast < 10
                    pychromecast.discovery.stop_discovery(browser)
                for c in casts:
                    info = getattr(c, "cast_info", None)
                    name = getattr(info, "friendly_name", None) or getattr(c, "name", None) or "Cast device"
                    grid["items"].append({"label": name, "value": c, "bg": BTN})
                grid["status"] = "" if casts else ("No Cast devices found. Roku / Fire TV / smart TV: "
                                                   "open the TV address from Phones & TV instead.")
            except Exception as e:
                grid["status"] = f"Cast search failed: {e}"
            self.dirty = True
        threading.Thread(target=work, daemon=True).start()

    def cast_to(self, cast, name):
        url = f"{self.companion_base()}/tv?k={self.settings['companion_pin']}"
        self.say(f"Casting to {name}...")

        def work():
            try:
                from pychromecast.controllers.dashcast import DashCastController
                cast.wait(timeout=10)
                dash = DashCastController()
                cast.register_handler(dash)
                # force: open the page directly - the DashCast receiver is https, and an
                # http LAN page inside its iframe would be blocked as mixed content
                dash.load_url(url, force=True)
                self.say(f"Showing LifePi on {name}")
            except Exception as e:
                self.say(f"Cast failed: {e}")
        threading.Thread(target=work, daemon=True).start()

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
        v = self.modal_view(m, p["name"], tuple(c * 3 // 4 for c in PALETTE[p["color"] % len(PALETTE)]))
        u, W, H = v.u, self.W, self.H
        self.button(v, (W - 2 * u, 0, 2 * u, u), "Done", self.pop, bg=BTN_ON)

        def rename(t):
            self.rename_player(i, t)

        def search(t):
            if t.strip():
                self.open_art_search(i, t.strip(), ang)

        def recolor():
            self.recolor_player(i)

        def pick_name(name):
            if name in self.profiles:
                self.load_profile(i, name)
            else:
                rename(name)

        acts = [("Rename", lambda: self.ask_text(
                    "Player name", "" if p["name"].startswith("Player ") else p["name"], rename, ang,
                    local=self.profile_names, pick=pick_name, hint="Saved profiles appear here")),
                ("Card art", lambda: self.ask_text(
                    "Search Scryfall for a card", "", search, ang,
                    local=self.local_card_names, suggest=self.suggest_cards,
                    hint="Type 2+ letters for suggestions",
                    pick=lambda name: self.open_art_search(i, f'!"{name}"', ang, label=name))),
                ("Local image", lambda: self.open_local_images(i, ang)),
                ("Colour", recolor),
                ("Profiles", lambda: self.open_profiles(i, ang))]
        for k, (label, fn) in enumerate(acts):
            self.button(v, (k * W // len(acts), u, W // len(acts), u), label, fn, size=u * 0.38)

        sources = range(len(self.players))          # commander damage, own commander included
        nrows = max(len(COUNTERS), len(sources))
        rh = (H - 2 * u - u // 3) // (nrows + 1)
        y0 = 2 * u + u // 6
        cw = W // 2

        def row(x, y, label, value, minus, plus, warn=False, tile=None):
            b = min(rh, cw // 5)
            lx = x + 14
            if tile is not None:
                t = self.cmd_tile(tile, value, rh * 0.82, number=False)
                v.surf.blit(t, t.get_rect(midleft=(lx, y + rh // 2)))
                lx += t.get_width() + 10
            self.text(v.surf, label, rh * 0.42, maxw=x + cw - 3 * b - 10 - lx, shadow=False,
                      midleft=(lx, y + rh // 2))
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
        self.text(v.surf, "Commander damage from", rh * 0.4, GREY, shadow=False,
                  midleft=(cw + 14, y0 + rh // 2))
        for k, j in enumerate(sources):
            name = self.players[j]["name"] + (" (you)" if j == i else "")
            row(cw, y0 + (k + 1) * rh, name, p["cmd"][j],
                lambda j=j: self.change_cmd(i, j, -1), lambda j=j: self.change_cmd(i, j, 1),
                warn=p["cmd"][j] >= 21, tile=j)
        v.finish()

    def draw_keyboard(self, m):
        v = self.modal_view(m, m["title"])
        u, W, H = v.u, self.W, self.H
        box = pygame.Rect(12, u, W - 24, u)
        pygame.draw.rect(v.surf, (36, 38, 50), box, border_radius=8)
        self.text(v.surf, m["text"] + "|", u * 0.6, maxw=box.w - 20, shadow=False, midleft=(box.x + 12, box.centery))
        rows = ["1234567890", "qwertyuiop", "asdfghjkl'", "zxcvbnm,.-"]
        y = 2 * u + 8
        if "pick" in m:                               # suggestion strip
            sh = int(u * 0.75)
            cols = 3 if W < 1000 else 4
            sugg = m.get("sugg") or []
            for k, name in enumerate(sugg[:cols]):
                self.button(v, (k * W // cols, y, W // cols, sh), name,
                            lambda name=name: self.keyboard_pick(m, name),
                            bg=BTN_ON if k == 0 else BTN, size=sh * 0.5)
            if not sugg and m.get("hint"):
                self.text(v.surf, m["hint"], sh * 0.4, GREY, shadow=False, center=(W // 2, y + sh // 2))
            y += sh
        kh = (H - y) // 5

        def add(ch):
            t = m["text"]
            self.set_text(m, t + (ch.upper() if not t or t.endswith(" ") else ch))

        def back():
            self.set_text(m, m["text"][:-1])

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
        self.save_caches()

    def keyboard_pick(self, m, choice):
        self.pop()
        m["pick"](choice)
        self.save_caches()

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
        if pages > 1:
            self.button(v, (0, y, W // 4, u), "< Prev", lambda: page(-1))
            self.text(v.surf, f"{m['page'] + 1} / {pages}", u * 0.4, GREY, shadow=False,
                      center=(W * 3 // 8, y + u // 2))
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

    def search_cards(self, query):
        """Scryfall art search, cached on disk for SEARCH_TTL so repeating a search costs
        no API calls. Falls back to saved (even stale) results when offline.
        Returns (cards, note); raises on network errors with nothing cached."""
        path = search_cache_path(query)
        try:
            cached = json.loads(path.read_text())
            if time.time() - cached["t"] < SEARCH_TTL:
                return cached["cards"], ""
        except (OSError, ValueError, KeyError, TypeError):
            cached = None
        try:
            data = scryfall("/cards/search", q=query, unique="art", order="edhrec").get("data", [])
        except Exception as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                data = []                              # Scryfall's "no cards matched"
            elif cached:
                return cached["cards"], "Offline - showing saved results"
            else:
                raise
        cards = []
        for c in data[:18]:
            uris = c.get("image_uris") or (c.get("card_faces") or [{}])[0].get("image_uris") or {}
            if uris.get("art_crop"):
                cards.append({"id": c["id"], "name": c.get("name", "?"), "set": c.get("set", ""),
                              "artist": c.get("artist", "unknown"), "art": uris["art_crop"]})
        try:
            write_atomic(path, json.dumps({"q": query, "t": time.time(), "cards": cards}))
        except OSError:
            pass
        with self.cache_lock:
            self.known_names.update(c["name"] for c in cards)
        return cards, ""

    def suggest_cards(self, text):
        """Card-name guesses from Scryfall autocomplete (cached); local guesses if offline.
        Runs on a worker thread."""
        q = " ".join(text.lower().split())
        if len(q) < 2:
            return []
        with self.cache_lock:
            hit = self.ac_cache.get(q)
        if hit and time.time() - hit[0] < SUGGEST_TTL:
            return hit[1]
        try:
            names = scryfall("/cards/autocomplete", q=q).get("data", [])[:20]
        except Exception:
            return hit[1] if hit else self.local_card_names(text)
        with self.cache_lock:
            self.ac_cache[q] = [time.time(), names]
            self.ac_dirty = True
            self.known_names.update(names)
        return names

    def local_card_names(self, text):
        """Instant guesses from card names seen before (no network)."""
        q = " ".join(text.lower().split())
        if len(q) < 2:
            return []
        with self.cache_lock:
            names = list(self.known_names)
        starts = sorted(n for n in names if n.lower().startswith(q))
        inside = sorted(n for n in names if q in n.lower() and not n.lower().startswith(q))
        return (starts + inside)[:12]

    def profile_names(self, text):
        t = text.strip().lower()
        return [n for n in sorted(self.profiles) if n.lower().startswith(t)]

    def open_art_search(self, i, query, angle, label=None):
        grid = dict(title=f"Card art: {label or query}", items=[], page=0, status="Searching Scryfall...",
                    angle=angle, on_pick=lambda it: self.set_art(i, it["img"], it["value"]))
        self.push("grid", **grid)
        grid = self.modals[-1]

        def work():
            try:
                cards, note = self.search_cards(query)
                for c in cards:
                    path = CACHE_DIR / f"{c['id']}.jpg"
                    if not path.exists():                 # art is cached on disk by card id
                        try:
                            tmp = path.with_suffix(".part")
                            tmp.write_bytes(http_get(c["art"]))
                            os.replace(tmp, path)
                            time.sleep(0.05)              # be polite to the image CDN
                        except Exception:
                            continue                      # offline: show what we have
                    credit = f"{c['name']} - illus. {c['artist']} - (c) Wizards of the Coast"
                    grid["items"].append({"label": f"{c['name']} ({c['set'].upper()})",
                                          "img": str(path), "value": credit})
                    grid["status"] = "Loading..."
                    self.dirty = True
                grid["status"] = note if grid["items"] else "No cards found"
            except urllib.error.HTTPError as e:
                grid["status"] = f"Scryfall error {e.code}"
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

        self.push("grid", title="Load profile", items=items, page=0, angle=angle,
                  status="" if items else "No profiles yet - rename a player to create one",
                  on_pick=lambda it: self.load_profile(i, it["value"]))

    def load_profile(self, i, name):
        p = self.players[i]
        p["name"] = name
        p.update(self.profiles[name])
        self.changed()

    # ------------------------------------------------------------------ render
    def render(self):
        self.zones = []
        self.canvas.fill(DARK)
        n = len(self.players)
        self.seats = layout(n, self.W, self.H, self.settings["seating"])
        for i, (rect, angle) in enumerate(self.seats):
            self.draw_panel(i, rect, angle)
        self.draw_drags()
        r = max(22, min(self.W, self.H) // 22)
        c = self.menu_pos(r)
        pygame.draw.circle(self.canvas, (0, 0, 0), (c[0], c[1] + 2), r + 2)
        pygame.draw.circle(self.canvas, (24, 24, 32), c, r)
        pygame.draw.circle(self.canvas, (210, 210, 220), c, r, 2)
        for dy in (-r // 3, 0, r // 3):
            pygame.draw.line(self.canvas, WHITE, (c[0] - r // 2, c[1] + dy), (c[0] + r // 2, c[1] + dy), 2)
        hit = pygame.Rect(0, 0, int(r * 2.6), int(r * 2.6))
        hit.center = c
        self.zones.append((hit, lambda: self.push("menu"), None, 0, None))

        if self.modals:
            self.zones = []
            m = self.modals[-1]
            getattr(self, "draw_" + m["kind"])(m)
        text, t = self.toast
        age = time.time() - t
        if text and age < 3.2:
            pl = self.make_pill(text, max(16, min(self.W, self.H) // 22), bg=(30, 31, 42), alpha=235,
                                outline=(255, 255, 255, 60))
            if age > 2.6:
                pl.set_alpha(int(255 * (3.2 - age) / 0.6))
            self.canvas.blit(pl, pl.get_rect(midbottom=(self.W // 2, self.H - 12)))

        rot = self.settings["rotation"]
        out = self.canvas if rot == 0 else pygame.transform.rotate(self.canvas, -rot)
        self.screen.blit(out, (0, 0))
        pygame.display.flip()
        self.dirty = False

    def menu_pos(self, r):
        """Menu button spot on the row divider, as clear of the (centred) names as possible."""
        n = len(self.players)
        if n == 1:
            return (self.W - r - 10, self.H - r - 10)
        xs = [(ci + 0.5) * self.W / cnt for cnt in ROWS[n] for ci in range(cnt)]
        clear = self.W * 0.12 + r
        x = min(range(self.W // 2 % 2, self.W, 2), key=lambda x: (
            -min(clear, min(abs(x - c) for c in xs)), abs(x - self.W // 2)))
        return (x, self.H // 2)

    def draw_drags(self):
        """Seat-swap drag feedback: dim the source, outline the target, float the name."""
        for ptr in self.pointers.values():
            src = ptr.get("drag")
            if src is None or src >= len(self.seats):
                continue
            srect, sang = self.seats[src]
            shade = pygame.Surface(srect.size)
            shade.set_alpha(140)
            self.canvas.blit(shade, srect)
            dst = self.seat_at(ptr["pos"])
            if dst is not None and dst != src:
                pygame.draw.rect(self.canvas, (255, 230, 120), self.seats[dst][0], 6)
            p = self.players[src]
            size = max(20, min(self.W, self.H) // 12)
            img = self.font(size).render(p["name"], True, WHITE)
            chip = pygame.Surface((img.get_width() + size, int(size * 1.4)))
            chip.fill(PALETTE[p["color"] % len(PALETTE)])
            pygame.draw.rect(chip, (255, 230, 120), chip.get_rect(), 3)
            chip.blit(img, img.get_rect(center=chip.get_rect().center))
            if sang == 180:
                chip = pygame.transform.flip(chip, True, True)
            self.canvas.blit(chip, chip.get_rect(center=ptr["pos"]))

    # ------------------------------------------------------------------ input
    def to_canvas(self, sx, sy):
        sw, sh = self.screen.get_size()
        return unrotate(int(sx), int(sy), sw, sh, self.settings["rotation"])

    def pointer_down(self, pid, pos):
        for z in reversed(self.zones):
            if z[0].collidepoint(pos):
                self.pointers[pid] = {"zone": z, "t": time.time(), "held": False, "next": 0, "pos": pos}
                return

    def pointer_move(self, pid, pos):
        ptr = self.pointers.get(pid)
        if ptr:
            ptr["pos"] = pos
            if ptr.get("drag") is not None:
                self.dirty = True

    def pointer_up(self, pid, pos):
        ptr = self.pointers.pop(pid, None)
        if ptr and ptr.get("drag") is not None:
            dst = self.seat_at(pos)
            if dst is not None and dst != ptr["drag"] and not self.modals:
                self.swap_seats(ptr["drag"], dst)
            self.dirty = True
        elif ptr and not ptr["held"] and ptr["zone"][1] and ptr["zone"][0].collidepoint(pos):
            ptr["zone"][1]()
            self.dirty = True

    def tick_holds(self):
        now = time.time()
        for ptr in list(self.pointers.values()):
            rect, _, hold, repeat, drag = ptr["zone"]
            if drag is not None:
                if not ptr["held"] and now - ptr["t"] >= HOLD_DELAY:
                    ptr["held"], ptr["drag"] = True, drag    # long-press on a name: start drag
                    self.dirty = True
                continue
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
        elif e.type == pygame.FINGERMOTION:
            self.pointer_move(("f", e.finger_id), self.to_canvas(e.x * sw, e.y * sh))
        elif e.type == pygame.FINGERUP:
            self.pointer_up(("f", e.finger_id), self.to_canvas(e.x * sw, e.y * sh))
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and not getattr(e, "touch", False):
            self.pointer_down("mouse", self.to_canvas(*e.pos))
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and not getattr(e, "touch", False):
            self.pointer_up("mouse", self.to_canvas(*e.pos))
        elif e.type == pygame.MOUSEMOTION and not getattr(e, "touch", False):
            self.pointer_move("mouse", self.to_canvas(*e.pos))
        elif e.type == pygame.KEYDOWN:
            m = self.modals[-1] if self.modals else None
            if m and m["kind"] == "keyboard":
                if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.keyboard_ok(m)
                elif e.key == pygame.K_ESCAPE:
                    self.pop()
                elif e.key == pygame.K_TAB and m.get("sugg") and "pick" in m:
                    self.keyboard_pick(m, m["sugg"][0])   # accept the first guess
                elif e.key == pygame.K_BACKSPACE:
                    self.set_text(m, m["text"][:-1])
                elif e.unicode and e.unicode.isprintable():
                    self.set_text(m, m["text"] + e.unicode)
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
    def stop(self, *_):
        self.running = False

    def run(self, max_frames=None):
        # docker stop / systemd / shutdown send SIGTERM, a closed terminal SIGHUP, Ctrl+C
        # SIGINT: leave the loop normally so the game is saved instead of dying mid-throttle
        for name in ("SIGTERM", "SIGINT", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), self.stop)
        frames = 0
        try:
            while self.running:
                for e in pygame.event.get():
                    self.handle(e)
                self.tick_holds()
                self.tick_suggest()
                self.tick_remote()
                now = time.time()
                if self.toast[0] and now - self.toast[1] < 3.2:
                    self.dirty = True
                if any(now - fx["t"] < 1.9 for fx in self.fx.values()):
                    self.dirty = True
                if self.dirty:
                    self.render()
                if self.state_dirty and now - self.last_save > SAVE_INTERVAL:
                    self.save_state()
                self.clock.tick(self.args.fps)
                frames += 1
                if max_frames and frames >= max_frames:
                    break
        finally:                                # also on a crash
            if self.state_dirty:
                self.save_state()
            if self.companion:
                self.companion.stop()
            pygame.quit()


# --------------------------------------------------------------------------- web pages
# Served by Companion. Kept in this file so LifePi stays a single file to install.
CLIENT_JS = r"""
const qs = new URLSearchParams(location.search);
let KEY = qs.get("k") || "";
try { if (KEY) localStorage.setItem("lifepi-k", KEY); else KEY = localStorage.getItem("lifepi-k") || ""; } catch (e) {}
let state = null, onState = () => {}, onStatus = () => {}, es = null;
const api = p => p + (p.includes("?") ? "&" : "?") + "k=" + encodeURIComponent(KEY);

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style") Object.assign(e.style, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c instanceof Node ? c : String(c));
  return e;
}
function cardBg(p, shade) {
  const img = p.art ? `linear-gradient(${shade}), url("${api(p.art)}")` : `linear-gradient(${shade})`;
  return { backgroundImage: img, backgroundColor: p.color };
}
async function act(a) {
  try {
    const r = await fetch(api("/api/act"), { method: "POST", headers: { "Content-Type": "application/json" },
                                             body: JSON.stringify(a) });
    if (r.status === 401 || r.status === 429) onStatus("auth", r.status);
  } catch (e) { onStatus("offline"); }
}
async function start() {
  if (es) { es.close(); es = null; }
  try {
    const r = await fetch(api("/api/state"));
    if (r.status === 401 || r.status === 429) return onStatus("auth", r.status);
    state = await r.json(); trackDeltas(state); onState(state);
  } catch (e) { onStatus("offline"); setTimeout(start, 2000); return; }
  es = new EventSource(api("/api/events"));
  es.onopen = () => onStatus("online");
  es.onmessage = (e) => { state = JSON.parse(e.data); trackDeltas(state); onStatus("online"); onState(state); };
  es.onerror = () => {                          // the browser retries by itself unless refused
    onStatus("offline");
    if (es && es.readyState === 2) { es = null; setTimeout(start, 2000); }
  };
}
function setKey(k) { KEY = k.trim(); try { localStorage.setItem("lifepi-k", KEY); } catch (e) {} start(); }

// running "+3" / "-7" per seat, like the device shows
const prev = {}, deltas = {};
function trackDeltas(s) {
  const now = Date.now();
  for (const p of s.players) {
    const old = prev[p.seat];
    if (old && old.name === p.name && old.life !== p.life) {
      const d = deltas[p.seat];
      deltas[p.seat] = { d: (d && now - d.t < 1800 ? d.d : 0) + p.life - old.life, t: now };
      setTimeout(() => onState(state), 1850);
    }
    prev[p.seat] = { name: p.name, life: p.life };
  }
}
function deltaOf(seat) {
  const d = deltas[seat];
  return d && d.d && Date.now() - d.t < 1800 ? d.d : 0;
}
const fmt = (d) => (d > 0 ? "+" : "−") + Math.abs(d);
"""

PHONE_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<meta name="theme-color" content="#121218">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>LifePi</title>
<style>
  :root { --bg:#121218; --card:#1d1e29; --line:#2c2e3d; --text:#f2f2f6; --dim:#9a9cae;
          --accent:#4678c8; --good:#32a050; --bad:#c23b3b; --warn:#ffe678; --gap:5px; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  /* one fixed screen, like the device: nothing outside the sheets scrolls or zooms */
  html, body { position:fixed; inset:0; margin:0; overflow:hidden; overscroll-behavior:none; touch-action:none;
               background:var(--bg); color:var(--text); user-select:none; -webkit-user-select:none;
               -webkit-touch-callout:none; font:16px/1.3 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  body { display:flex; flex-direction:column; padding:env(safe-area-inset-top) env(safe-area-inset-right)
         env(safe-area-inset-bottom) env(safe-area-inset-left); }
  button { font:inherit; color:inherit; cursor:pointer; touch-action:manipulation; }
  header { flex:none; display:flex; align-items:center; gap:8px; height:48px; padding:0 10px; }
  header b { font-size:17px; }
  #dot { width:9px; height:9px; border-radius:50%; background:var(--bad); flex:none; }
  #dot.on { background:var(--good); }
  .hbtn { margin-left:auto; height:36px; padding:0 14px; border-radius:999px; border:1px solid var(--line);
          background:#22232f; font-size:14px; font-weight:600; max-width:46vw; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .hbtn + .hbtn { margin-left:0; }
  #table { position:relative; flex:1; min-height:0; margin:0 var(--gap) var(--gap); }

  .cell { position:absolute; padding:var(--gap); container-type:size; }
  .panel { position:relative; width:100%; height:100%; border-radius:16px; overflow:hidden; background-size:cover;
           background-position:center; box-shadow:0 3px 12px rgba(0,0,0,.35); }
  .panel.me { box-shadow:0 0 0 3px var(--warn), 0 3px 12px rgba(0,0,0,.35); }
  .strip { position:absolute; left:0; right:0; border:0; background:none; padding:0 6%; display:flex;
           align-items:center; justify-content:center; text-align:center; }
  .strip.top { top:0; height:20%; font-weight:700; font-size:min(10cqh, 8cqw); text-shadow:0 1px 3px rgba(0,0,0,.7); }
  .strip.top span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .strip.top small { color:var(--warn); font-weight:600; margin-left:.35em; flex:none; }
  .strip.bot { bottom:0; height:22%; gap:min(2cqh, 1.6cqw); flex-wrap:wrap; align-content:center; }
  .half { position:absolute; top:20%; bottom:22%; width:50%; border:0; background:none; padding:0;
          touch-action:none; display:flex; align-items:center; }
  .half.minus { left:0; justify-content:flex-start; padding-left:6%; }
  .half.plus { right:0; justify-content:flex-end; padding-right:6%; }
  .half i { font-style:normal; font-weight:700; font-size:min(16cqh, 11cqw); opacity:.45; line-height:1; }
  .half:active { background:rgba(255,255,255,.07); }
  .life { position:absolute; left:15%; right:15%; top:50%; transform:translateY(-50%); text-align:center; pointer-events:none;
          font-weight:800; line-height:1; font-variant-numeric:tabular-nums;
          font-size:min(46cqh, 34cqw); text-shadow:0 2px 8px rgba(0,0,0,.6); }
  .delta { position:absolute; left:50%; top:21%; transform:translateX(-50%); pointer-events:none; padding:.1em .6em;
           border-radius:999px; font-weight:800; font-size:min(9cqh, 7cqw); background:var(--good); }
  .delta.neg { background:var(--bad); }
  .ko { position:absolute; left:50%; bottom:24%; transform:translateX(-50%); pointer-events:none; padding:.1em .7em;
        border-radius:999px; background:var(--bad); font-weight:800; font-size:min(8cqh, 6cqw); white-space:nowrap; }
  .panel.dead > :not(.ko):not(.strip) { opacity:.45; }
  .panel.dead { filter:saturate(.4); }
  .pill { padding:.15em .55em; border-radius:999px; font-weight:700; font-size:min(7cqh, 5.2cqw); white-space:nowrap; }
  .pill.empty { color:#cfd0dc; background:rgba(0,0,0,.28); border:1px solid rgba(255,255,255,.2); font-weight:600; }
  .tile { width:min(14cqh, 11cqw); aspect-ratio:1; border-radius:22%; background-size:cover; background-position:center;
          border:2px solid rgba(255,255,255,.45); display:flex; align-items:center; justify-content:center;
          font-weight:800; font-size:min(8cqh, 6cqw); text-shadow:0 1px 4px #000; }
  .tile.lethal { border-color:#ff4a4a; color:#ff8a8a; }

  /* sheets: the only places that scroll */
  .sheet { position:fixed; inset:0; z-index:10; display:flex; align-items:flex-end; justify-content:center;
           background:rgba(6,6,10,.6); touch-action:auto; }
  .sheet[hidden] { display:none; }
  .box { width:100%; max-width:560px; max-height:88%; overflow-y:auto; overscroll-behavior:contain; touch-action:pan-y;
         -webkit-overflow-scrolling:touch; background:var(--card); border:1px solid var(--line); border-bottom:0;
         border-radius:22px 22px 0 0; padding:10px 16px calc(18px + env(safe-area-inset-bottom)); }
  .grab { width:44px; height:5px; border-radius:3px; background:#4a4c5e; margin:2px auto 10px; }
  .shead { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
  .shead h3 { flex:1; margin:0; font-size:21px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .chipbtn { height:38px; padding:0 14px; border-radius:999px; border:1px solid var(--line); background:#2a2c3b; font-size:14px; font-weight:600; }
  .chipbtn.go { background:var(--accent); border-color:var(--accent); }
  h4 { font-size:13px; font-weight:600; color:var(--dim); margin:18px 2px 8px; text-transform:uppercase; letter-spacing:.6px; }
  .steps { display:grid; grid-template-columns:repeat(4, 1fr); gap:8px; }
  .steps button, .stepper button { height:54px; border-radius:14px; border:1px solid var(--line); background:#2a2c3b;
                                   font-size:22px; font-weight:700; }
  .steps button:active, .stepper button:active, .chips button:active { transform:scale(.96); background:#3a3d52; }
  .lifebig { text-align:center; font-size:64px; font-weight:800; line-height:1; margin:4px 0 10px; font-variant-numeric:tabular-nums; }
  .rows { border-radius:14px; overflow:hidden; border:1px solid var(--line); background:#191a24; }
  .row { display:flex; align-items:center; gap:10px; padding:8px 10px; border-top:1px solid var(--line); min-height:62px; }
  .row:first-child { border-top:0; }
  .row .label { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:600; }
  .row .label small { color:var(--dim); font-weight:500; }
  .row .stile { width:40px; height:40px; border-radius:9px; flex:none; background-size:cover; background-position:center;
                border:2px solid rgba(255,255,255,.35); }
  .stepper { display:flex; align-items:center; gap:6px; }
  .stepper button { width:50px; height:46px; }
  .stepper .v { min-width:40px; text-align:center; font-size:21px; font-weight:800; font-variant-numeric:tabular-nums; }
  .stepper .v.warn { color:#ff7a7a; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; }
  .chips.six { display:grid; grid-template-columns:repeat(6, 1fr); }
  .chips button { min-width:0; height:48px; border-radius:12px; border:1px solid var(--line); background:#2a2c3b;
                  font-size:17px; font-weight:700; padding:0 12px; }
  .chips button.on { background:var(--accent); border-color:var(--accent); }
  .chips input { width:100px; height:48px; border-radius:12px; border:1px solid var(--line); background:#2a2c3b;
                 color:var(--text); font-size:17px; padding:0 12px; user-select:text; -webkit-user-select:text; }
  .big { width:100%; height:54px; border-radius:14px; border:0; font-size:17px; font-weight:700; margin-top:10px; background:#2a2c3b; }
  .big.go { background:var(--good); } .big.danger { background:var(--bad); }
  .switch { width:100%; background:none; border:0; text-align:left; }
  .switch .label { white-space:normal; }
  .switch i { width:50px; height:30px; border-radius:15px; background:#3a3c4c; position:relative; flex:none; }
  .switch i::after { content:""; position:absolute; top:3px; left:3px; width:24px; height:24px; border-radius:50%;
                     background:#fff; transition:left .15s; }
  .switch.on i { background:var(--good); } .switch.on i::after { left:23px; }
  .seatpick { display:grid; grid-template-columns:repeat(auto-fill, minmax(140px, 1fr)); gap:8px; }
  .seatpick button { height:54px; border-radius:12px; border:2px solid transparent; font-weight:700; font-size:16px;
                     background-size:cover; background-position:center; text-shadow:0 1px 3px rgba(0,0,0,.7); }
  .seatpick button.on { border-color:var(--warn); }
  .muted { color:var(--dim); font-size:14px; margin:8px 2px; }
  a { color:#8fb4ff; }
  .overlay { position:fixed; inset:0; z-index:20; background:rgba(8,8,12,.88); display:flex; align-items:center;
             justify-content:center; padding:24px; touch-action:auto; }
  .overlay[hidden] { display:none; }
  .panel2 { width:100%; max-width:380px; background:var(--card); border:1px solid var(--line); border-radius:20px; padding:20px; }
  .panel2 h3 { margin:0 0 6px; font-size:20px; }
  .panel2 input { width:100%; height:54px; margin:14px 0 4px; border-radius:14px; border:1px solid var(--line);
                  background:#2a2c3b; color:var(--text); font-size:22px; padding:0 14px; user-select:text; -webkit-user-select:text; }
  .panel2 .acts { display:flex; gap:10px; } .panel2 .acts .big { flex:1; }
</style>
</head><body>
<header>
  <span id="dot"></span><b>LifePi</b>
  <button class="hbtn" id="seatbtn" type="button">Pick your seat</button>
  <button class="hbtn" id="gamebtn" type="button">Game</button>
</header>
<main id="table"></main>
<div class="sheet" id="sheet" hidden><div class="box" id="box"></div></div>
<div class="overlay" id="pin" hidden><form class="panel2">
  <h3>Join the game</h3>
  <div class="muted" id="pinwhy">Enter the PIN shown on LifePi under Menu &gt; Phones &amp; TV.</div>
  <input inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6-digit PIN">
  <button class="big go">Join</button>
</form></div>
<div class="overlay" id="ask" hidden><form class="panel2">
  <h3 id="asktitle">Rename</h3>
  <input id="askin" maxlength="18" autocomplete="off">
  <div class="acts"><button type="button" class="big" id="askno">Cancel</button><button class="big go">Save</button></div>
</form></div>
<script>
/*CLIENT*/
if (qs.get("k")) history.replaceState(null, "", location.pathname);   // keep the PIN out of the address bar
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
                set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
let me = +store.get("lifepi-seat", "-1"), myName = store.get("lifepi-name", "");
let sheet = null;                      // {kind: "player", seat} | {kind: "game"} | {kind: "seat"}
let confirmAt = "";
const ng = { n: null, life: null };
const CHIP = { poison: "#2f8a4a", tax: "#97702a", energy: "#ae8716", exp: "#5459ad" };
const SHORT = { poison: "Poison", tax: "Tax", energy: "Energy", exp: "Exp" };

function setMe(seat) {
  me = seat; myName = seat >= 0 && state && state.players[seat] ? state.players[seat].name : "";
  store.set("lifepi-seat", String(me)); store.set("lifepi-name", myName);
}
function followMe(s) {                 // seats can be swapped on the device: follow "me" by name
  if (me < 0) return;
  const at = s.players[me];
  if (at && at.name === myName) return;
  const moved = s.players.findIndex((p) => p.name === myName);
  if (moved >= 0) setMe(moved);
  else if (!at) setMe(-1);
  else { myName = at.name; store.set("lifepi-name", myName); }     // renamed in place
}

// presses: like the device - tap = 1 step, hold = big steps repeating. Tracked per pointer on
// window, because a live update can replace the element under a finger mid-press.
const presses = new Map();
function press(e, tap, hold, every) {
  e.preventDefault();
  const pr = { held: false, timer: null, tap };
  pr.timer = setTimeout(() => {
    pr.held = true;
    if (hold) { hold(); pr.timer = setInterval(hold, every); }
  }, 450);
  presses.set(e.pointerId, pr);
}
function release(e, cancel) {
  const pr = presses.get(e.pointerId);
  if (!pr) return;
  presses.delete(e.pointerId); clearTimeout(pr.timer); clearInterval(pr.timer);
  if (!pr.held && !cancel) pr.tap();
}
addEventListener("pointerup", (e) => release(e, false));
addEventListener("pointercancel", (e) => release(e, true));
function pressable(tag, attrs, tap, hold, every, ...kids) {
  return el(tag, Object.assign({}, attrs, { type: "button", oncontextmenu: (e) => e.preventDefault(),
    onpointerdown: (e) => press(e, tap, hold, every) }), ...kids);
}
const stepBtn = (label, fn) => pressable("button", {}, fn, fn, 150, label);   // sheets: hold repeats the step
function stepper(value, minus, plus, warn) {
  return el("div", { class: "stepper" }, stepBtn("−", minus),
            el("span", { class: "v" + (warn ? " warn" : "") }, value), stepBtn("+", plus));
}
function ask(title, value, cb) {
  const box = document.getElementById("ask"), input = document.getElementById("askin");
  document.getElementById("asktitle").textContent = title;
  input.value = value; box.hidden = false; setTimeout(() => input.focus(), 50);
  box.querySelector("form").onsubmit = (e) => { e.preventDefault(); box.hidden = true; if (input.value.trim()) cb(input.value.trim()); };
  document.getElementById("askno").onclick = () => { box.hidden = true; };
}

// ------------------------------------------------------------------ the table (mirrors the device)
function panel(p) {
  const seat = p.seat, d = deltaOf(seat), bottom = [];
  state.players.forEach((src) => {
    const v = p.cmd[src.seat];
    if (v) bottom.push(el("span", { class: "tile" + (v >= 21 ? " lethal" : ""),
      style: Object.assign(cardBg(src, "rgba(0,0,0,.2), rgba(0,0,0,.45)"), { borderColor: v >= 21 ? null : src.color }) }, v));
  });
  for (const [key] of state.counters)
    if (p.counters[key]) bottom.push(el("span", { class: "pill", style: { background: CHIP[key] || "#444" } },
                                        `${SHORT[key] || key} ${p.counters[key]}`));
  if (!bottom.length) bottom.push(el("span", { class: "pill empty" }, "counters"));
  const open = () => openSheet({ kind: "player", seat });
  const [x, y, w, h] = p.pos;
  return el("div", { class: "cell", style: { left: x * 100 + "%", top: y * 100 + "%", width: w * 100 + "%", height: h * 100 + "%" } },
    el("div", { class: "panel" + (seat === me ? " me" : "") + (p.ko ? " dead" : ""),
                style: cardBg(p, "rgba(0,0,0,.6), rgba(0,0,0,.32) 45%, rgba(0,0,0,.66)") },
      el("button", { class: "strip top", type: "button", onclick: open },
         el("span", {}, p.name), seat === me ? el("small", {}, "you") : null),
      pressable("button", { class: "half minus", "aria-label": "minus" }, () => act({ op: "life", seat, d: -1 }),
                () => act({ op: "life", seat, d: -10 }), 500, el("i", {}, "−")),
      pressable("button", { class: "half plus", "aria-label": "plus" }, () => act({ op: "life", seat, d: 1 }),
                () => act({ op: "life", seat, d: 10 }), 500, el("i", {}, "+")),
      el("div", { class: "life" }, p.life),
      d ? el("div", { class: "delta" + (d < 0 ? " neg" : "") }, fmt(d)) : null,
      p.ko ? el("div", { class: "ko" }, p.ko === "life" ? "KO" : "KO · " + p.ko) : null,
      el("button", { class: "strip bot", type: "button", onclick: open }, bottom)));
}

// ------------------------------------------------------------------ sheets
function openSheet(s) { sheet = s; render(); document.getElementById("box").scrollTop = 0; }
function closeSheet() { sheet = null; confirmAt = ""; render(); }
document.getElementById("sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });

function playerSheet(seat) {
  const s = state, p = s.players[seat];
  return [
    el("div", { class: "shead" }, el("h3", {}, p.name),
       el("button", { class: "chipbtn", type: "button", onclick: () => ask("Rename", p.name.startsWith("Player ") ? "" : p.name,
                                                                         (t) => act({ op: "name", seat, name: t })) }, "Rename"),
       el("button", { class: "chipbtn", type: "button", onclick: () => act({ op: "color", seat }) }, "Colour"),
       el("button", { class: "chipbtn go", type: "button", onclick: closeSheet }, "Done")),
    el("div", { class: "lifebig" }, p.life),
    el("div", { class: "steps" }, [-5, -1, 1, 5].map((d) => stepBtn(fmt(d), () => act({ op: "life", seat, d })))),
    el("h4", {}, "Counters"),
    el("div", { class: "rows" }, s.counters.map(([key, label]) =>
      el("div", { class: "row" }, el("span", { class: "label" }, label),
         stepper(p.counters[key], () => act({ op: "counter", seat, key, d: -1 }), () => act({ op: "counter", seat, key, d: 1 }),
                 key === "poison" && p.counters[key] >= 10)))),
    el("h4", {}, "Commander damage taken"),
    el("div", { class: "rows" }, s.players.map((src) =>
      el("div", { class: "row" },
         el("span", { class: "stile", style: cardBg(src, "rgba(0,0,0,.1), rgba(0,0,0,.1)") }),
         el("span", { class: "label" }, src.name, src.seat === seat ? el("small", {}, " (own)") : null),
         stepper(p.cmd[src.seat], () => act({ op: "cmd", seat, src: src.seat, d: -1 }),
                 () => act({ op: "cmd", seat, src: src.seat, d: 1 }), p.cmd[src.seat] >= 21)))),
  ];
}

function seatSheet() {
  return [
    el("div", { class: "shead" }, el("h3", {}, "Which seat are you?"),
       el("button", { class: "chipbtn go", type: "button", onclick: closeSheet }, "Done")),
    el("div", { class: "seatpick" }, state.players.map((p) =>
      el("button", { type: "button", class: p.seat === me ? "on" : "", style: cardBg(p, "rgba(0,0,0,.3), rgba(0,0,0,.5)"),
                     onclick: () => { setMe(p.seat); closeSheet(); } }, p.name)),
      el("button", { type: "button", class: me < 0 ? "on" : "", onclick: () => { setMe(-1); closeSheet(); } }, "Just watching")),
    el("p", { class: "muted" }, "Your seat gets a yellow outline. Everyone can still adjust any player."),
  ];
}

function confirmBtn(id, label, cls, fn) {
  const armed = confirmAt === id;
  return el("button", { class: "big " + cls, type: "button", onclick: () => {
    if (armed) { confirmAt = ""; fn(); closeSheet(); return; }
    confirmAt = id; setTimeout(() => { if (confirmAt === id) { confirmAt = ""; render(); } }, 3000); render();
  } }, armed ? "Tap again to confirm" : label);
}

function gameSheet() {
  const s = state;
  if (ng.n === null) { ng.n = s.players.length; ng.life = s.start_life; }
  const lifeOpts = [20, 30, 40];
  return [
    el("div", { class: "shead" }, el("h3", {}, "Game"),
       el("button", { class: "chipbtn go", type: "button", onclick: closeSheet }, "Done")),
    el("h4", {}, "Players"),
    el("div", { class: "chips six" }, [1, 2, 3, 4, 5, 6].map((n) =>
      el("button", { type: "button", class: ng.n === n ? "on" : "", onclick: () => { ng.n = n; render(); } }, n))),
    el("h4", {}, "Starting life"),
    el("div", { class: "chips" }, lifeOpts.map((l) =>
      el("button", { type: "button", class: ng.life === l ? "on" : "", onclick: () => { ng.life = l; render(); } }, l)),
      el("input", { type: "number", inputmode: "numeric", min: "1", max: "9999", placeholder: "Other",
                    value: lifeOpts.includes(ng.life) ? null : String(ng.life),
                    onchange: (e) => { const v = parseInt(e.target.value, 10); if (v > 0) ng.life = Math.min(9999, v); render(); } })),
    confirmBtn("new", `Start ${ng.n}-player game at ${ng.life}`, "go", () => act({ op: "newgame", n: ng.n, life: ng.life })),
    confirmBtn("reset", "Reset this game", "danger", () => act({ op: "reset" })),
    el("h4", {}, "Rules"),
    el("div", { class: "rows" }, el("button", { type: "button", class: "row switch" + (s.cmd_hits_life ? " on" : ""),
        onclick: () => act({ op: "cmd_hits_life", on: !s.cmd_hits_life }) },
      el("span", { class: "label" }, "Commander damage also reduces life"), el("i"))),
    el("h4", {}, "Big screen"),
    el("p", { class: "muted" }, "Open the TV view on a TV browser, cast this tab, or mirror this phone to a Roku. ",
       el("a", { href: "/tv?k=" + encodeURIComponent(KEY), target: "_blank" }, "Open TV view")),
  ];
}

// ------------------------------------------------------------------ render
function render() {
  if (!state) return;
  followMe(state);
  document.getElementById("seatbtn").textContent = me >= 0 && state.players[me] ? "You: " + state.players[me].name : "Pick your seat";
  document.getElementById("table").replaceChildren(...state.players.map(panel));
  const sh = document.getElementById("sheet"), box = document.getElementById("box");
  if (sheet && sheet.kind === "player" && !state.players[sheet.seat]) sheet = null;
  sh.hidden = !sheet;
  if (sheet) {
    const top = box.scrollTop;
    box.replaceChildren(el("div", { class: "grab" }),
      ...(sheet.kind === "player" ? playerSheet(sheet.seat) : sheet.kind === "seat" ? seatSheet() : gameSheet()));
    box.scrollTop = top;                 // live updates must not jump the sheet back to the top
  }
}
document.getElementById("seatbtn").onclick = () => openSheet({ kind: "seat" });
document.getElementById("gamebtn").onclick = () => { ng.n = null; openSheet({ kind: "game" }); };

onState = render;
onStatus = (what, code) => {
  document.getElementById("dot").classList.toggle("on", what === "online");
  const pin = document.getElementById("pin");
  if (what === "auth") {
    pin.hidden = false;
    document.getElementById("pinwhy").textContent = code === 429
      ? "Too many wrong PINs - wait a minute, then try again."
      : (KEY ? "That PIN didn't work (it may have changed). " : "") + "Enter the PIN shown on LifePi under Menu > Phones & TV.";
  } else if (what === "online") pin.hidden = true;
};
document.querySelector("#pin form").onsubmit = (e) => {
  e.preventDefault(); const v = e.target.querySelector("input").value; if (v.trim()) setKey(v);
};
start();
</script>
</body></html>
""".replace("/*CLIENT*/", CLIENT_JS)

TV_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LifePi - TV</title>
<style>
  :root { --gap:.6vmin; }
  * { box-sizing:border-box; }
  html, body { margin:0; height:100%; background:#0d0d12; color:#fff; overflow:hidden; cursor:none;
               font-family:system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
  #grid { position:fixed; inset:var(--gap); }
  body.hinted #grid { bottom:calc(var(--gap) + max(26px, 4.4vmin)); }   /* room for the join hint */
  .cell { position:absolute; padding:var(--gap); container-type:size; }
  .card { position:relative; width:100%; height:100%; border-radius:2.2vmin; overflow:hidden; background-size:cover;
          background-position:center; display:flex; flex-direction:column; align-items:center; justify-content:center;
          text-align:center; box-shadow:0 1vmin 3vmin rgba(0,0,0,.45); }
  .card.dead { filter:saturate(.3) brightness(.55); }
  .name { position:absolute; top:5%; left:6%; right:6%; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
          font-size:4vmin; font-size:min(10cqh, 7cqw); text-shadow:0 .3vmin 1vmin rgba(0,0,0,.7); }
  .life { font-weight:800; line-height:1; font-variant-numeric:tabular-nums; letter-spacing:-.02em;
          font-size:18vmin; font-size:min(44cqh, 33cqw); text-shadow:0 .6vmin 2vmin rgba(0,0,0,.6); }
  .delta { position:absolute; top:20%; padding:.2em .7em; border-radius:999px; font-weight:800; background:#32a050;
           font-size:3vmin; font-size:min(8cqh, 6cqw); }
  .delta.neg { background:#c23b3b; }
  .ko { margin-top:2%; padding:.15em .8em; border-radius:999px; background:#c23b3b; font-weight:800;
        font-size:2.6vmin; font-size:min(7cqh, 5cqw); }
  .bottom { position:absolute; bottom:5%; left:4%; right:4%; display:flex; flex-wrap:wrap; justify-content:center;
            align-items:center; gap:1vmin; gap:min(2.5cqh, 1.2vmin); }
  .pill { padding:.2em .7em; border-radius:999px; font-weight:700; font-size:2vmin; font-size:min(5.5cqh, 4cqw); }
  .tile { position:relative; width:5vmin; width:min(14cqh, 11cqw); aspect-ratio:1; border-radius:18%;
          background-size:cover; background-position:center; border:.35vmin solid rgba(255,255,255,.45);
          display:flex; align-items:center; justify-content:center; font-weight:800;
          font-size:2.6vmin; font-size:min(7.5cqh, 6cqw); text-shadow:0 .2vmin .8vmin #000; }
  .tile.lethal { border-color:#ff4a4a; color:#ff8a8a; }
  .credit { position:absolute; bottom:1.2%; left:0; right:0; font-size:max(9px, 1.2vmin); font-size:max(9px, 2.6cqh); color:rgba(255,255,255,.55); }
  #hint { position:fixed; left:50%; bottom:1.2vmin; transform:translateX(-50%); padding:.5vmin 1.6vmin; border-radius:999px;
          background:rgba(13,13,18,.82); color:#c8c9d6; font-size:max(12px, 1.7vmin); max-width:94vw; text-align:center; }
  @media (max-aspect-ratio: 1/1), (max-height: 520px) { .credit { display:none; } }
  #hint[hidden], #off[hidden] { display:none; }
  #off { position:fixed; inset:0; display:flex; align-items:center; justify-content:center; background:rgba(13,13,18,.75);
         font-size:3vmin; color:#c8c9d6; }
</style>
</head><body>
<div id="grid"></div>
<div id="hint" hidden></div>
<div id="off">Connecting to LifePi...</div>
<script>
/*CLIENT*/
const CHIP = { poison: "#2f8a4a", tax: "#97702a", energy: "#ae8716", exp: "#5459ad" };
const SHORT = { poison: "Poison", tax: "Tax", energy: "Energy", exp: "Exp" };
function render() {
  if (!state) return;
  const s = state;
  document.getElementById("grid").replaceChildren(...s.players.map((p) => {
    const d = deltaOf(p.seat);
    const bottom = [];
    s.players.forEach((src) => {
      const v = p.cmd[src.seat];
      if (v) bottom.push(el("div", { class: "tile" + (v >= 21 ? " lethal" : ""),
                                     style: Object.assign(cardBg(src, "rgba(0,0,0,.25), rgba(0,0,0,.45)"),
                                                          { borderColor: v >= 21 ? null : src.color }) }, v));
    });
    for (const [key] of s.counters)
      if (p.counters[key]) bottom.push(el("span", { class: "pill", style: { background: CHIP[key] || "#444" } },
                                          `${SHORT[key] || key} ${p.counters[key]}`));
    const [x, y, w, h] = p.pos;          // same place as on the device
    return el("div", { class: "cell", style: { left: x * 100 + "%", top: y * 100 + "%", width: w * 100 + "%", height: h * 100 + "%" } },
      el("div", { class: "card" + (p.ko ? " dead" : ""),
                  style: cardBg(p, "rgba(0,0,0,.62), rgba(0,0,0,.38) 45%, rgba(0,0,0,.72)") },
      el("div", { class: "name" }, p.name),
      d ? el("div", { class: "delta" + (d < 0 ? " neg" : "") }, fmt(d)) : null,
      el("div", { class: "life" }, p.life),
      p.ko ? el("div", { class: "ko" }, p.ko === "life" ? "KO" : "KO · " + p.ko) : null,
      el("div", { class: "bottom" }, bottom),
      p.credit ? el("div", { class: "credit" }, p.credit) : null));
  }));
  const hint = document.getElementById("hint");
  hint.hidden = !s.join || qs.get("hint") === "0";
  document.body.classList.toggle("hinted", !hint.hidden);
  hint.textContent = "Control LifePi from your phone: " + s.join + "  (PIN on the LifePi screen)";
}
onState = render;
onStatus = (what) => {
  const off = document.getElementById("off");
  off.hidden = what === "online";
  if (what === "auth") off.textContent = "This TV link's PIN is out of date - open it again from LifePi > Phones & TV.";
  else if (what === "offline") off.textContent = "Reconnecting to LifePi...";
};
addEventListener("resize", render);
start();
</script>
</body></html>
""".replace("/*CLIENT*/", CLIENT_JS)


def main():
    ap = argparse.ArgumentParser(description="LifePi - native MTG life counter for Raspberry Pi")
    ap.add_argument("--windowed", metavar="WxH", help="run in a window (e.g. 800x480) instead of fullscreen")
    ap.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), help="set screen rotation and remember it")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--companion-port", type=int, default=COMPANION_PORT, metavar="PORT",
                    help=f"port for the phone / TV web pages (default {COMPANION_PORT})")
    ap.add_argument("--no-companion", action="store_true", help="don't serve the phone / TV web pages")
    args = ap.parse_args()
    if args.no_companion:
        args.companion_port = None
    App(args).run()


if __name__ == "__main__":
    main()

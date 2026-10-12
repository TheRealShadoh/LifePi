#!/usr/bin/env python3
"""
LifePi - a touch-first Magic: The Gathering life counter that runs natively on
a Raspberry Pi (or any Linux box) with nothing but Python 3 and pygame/SDL2.

  * 1-8 players; table (===), ends (-=-), round table or upright seating
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
import asyncio
import functools
import hashlib
import hmac
import json
import os
import queue
import random
import secrets
import shutil
import signal
import subprocess
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import math
from math import ceil
from pathlib import Path

import pygame

VERSION = "1.8.0"
APP = "lifepi"
WEB = sys.platform == "emscripten"                  # running in a browser (pygbag / WebAssembly)
HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / APP
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / APP / "art"
IMAGE_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share")) / APP / "images"
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share"))
ICON_FILE = DATA_HOME / "icons" / "hicolor" / "256x256" / "apps" / "lifepi.png"   # menu / taskbar icon
STATE_FILE = CONFIG_DIR / "state.json"
BACKUP_FILE = CONFIG_DIR / "state.json.bak"         # the save before the latest one
SAVE_INTERVAL = 0.5                                 # max seconds of play a power cut can lose
COMPANION_PORT = 8765                               # phones + TV view, on the local network
FIRST_SHOW = 3.0                                    # seconds "Goes first!" stays up after the spin
RANDOM_ART_QUERY = "t:legendary t:creature game:paper"   # random art: commanders - big, characterful
RANDOM_ART_RETRY = 60                               # seconds before trying again when offline
REPO = "TheRealShadoh/LifePi"
UPDATE_EVERY = 6 * 3600                             # check GitHub for a new release at most this often
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
# player colours, picked to stay tellable apart with red/green colour blindness too
PALETTE = [(36, 84, 176), (196, 72, 20), (200, 80, 140), (78, 82, 96),
           (176, 150, 84), (38, 108, 62), (16, 128, 190), (96, 100, 30)]
AMBER = (255, 190, 60)                              # "getting close": low life, poison, commander damage
LETHAL = (255, 90, 90)
COUNTERS = [("poison", "Poison", "Poison"), ("tax", "Cmdr tax", "Tax"),
            ("energy", "Energy", "Energy"), ("exp", "Experience", "Exp")]
CHIP_COLORS = {"poison": (40, 130, 70), "tax": (150, 110, 40), "energy": (175, 135, 20),
               "exp": (80, 85, 175), "cmd": (165, 45, 45)}
MAX_PLAYERS = 8
ROWS = {1: [1], 2: [1, 1], 3: [2, 1], 4: [2, 2], 5: [3, 2], 6: [3, 3], 7: [4, 3], 8: [4, 4]}
SEATINGS = {"table": "Table  ===", "ends": "Ends  -=-", "round": "Round", "upright": "Upright"}
HOLD_DELAY = 0.45
BOARD_GUARD = 0.4                  # s after a sheet closes during which board taps are ignored
NAME_MAX = 24
CONFIRM_GAP = 0.35                                 # a confirming tap must come at least this late
ARM_TIME = 4.0
TURN = (110, 210, 255)                               # turn tracker colour (gold is "goes first")                                      # "Tap again to ..." stays armed this long
MERGE_TIME = 2.0                                    # taps this close together are one undo step
UNDO_MAX = 60
HISTORY_MAX = 300
# formats: label, starting life, poison that knocks a player out, commander damage in play
FORMATS = {"commander": ("Commander", 40, 10, True), "duel": ("1 v 1", 20, 10, False),
           "2hg": ("Two-Headed Giant", 30, 15, False)}


# --------------------------------------------------------------------------- helpers
def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_web_tmp = iter(range(10 ** 9))


async def fetch_bytes(url):
    """GET a URL. Desktop: urllib (background work runs on a thread there). Browser: the
    page's fetch(), saved into the in-browser filesystem by pygbag, then read back."""
    if not WEB:
        return http_get(url)
    import platform                               # pygbag's browser bridge
    tmp = f"/tmp/lifepi-fetch-{next(_web_tmp)}"
    flags = platform.ffi({"redirect": "follow", "credentials": "omit"})
    path = await platform.jsiter(platform.window.cross_file(url, tmp, flags))
    try:
        return Path(path).read_bytes()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def spawn(coro_fn):
    """Run background work without blocking the UI: a thread with its own event loop on
    desktop, an asyncio task in the browser (no threads there)."""
    if WEB:
        asyncio.get_event_loop().create_task(coro_fn())
    else:
        threading.Thread(target=lambda: asyncio.run(coro_fn()), daemon=True).start()


class ScryfallError(Exception):
    def __init__(self, status):
        super().__init__(f"Scryfall error {status}")
        self.status = status


_api_lock, _api_last = threading.Lock(), [0.0]


async def scryfall(path, **params):
    """GET a Scryfall API endpoint as JSON, at most ~10 requests/s as Scryfall asks."""
    with _api_lock:                               # reserve the next free slot
        now = time.time()
        slot = max(now, _api_last[0] + 0.1)
        _api_last[0] = slot
    if slot > now:
        await asyncio.sleep(slot - now)
    try:
        data = json.loads(await fetch_bytes("https://api.scryfall.com" + path + "?" + urllib.parse.urlencode(params)))
    except urllib.error.HTTPError as e:
        raise ScryfallError(e.code) from None
    if isinstance(data, dict) and data.get("object") == "error":     # browser fetch keeps error bodies
        raise ScryfallError(as_int(data.get("status"), 500))
    return data


def web_store(key, value=None):
    """Browser only: read (value None) or write a string in localStorage."""
    import platform
    ls = platform.window.localStorage
    if value is None:
        v = ls.getItem(f"lifepi-{key}")
        return None if v is None or str(v) in ("null", "undefined") else str(v)
    ls.setItem(f"lifepi-{key}", value)


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
            "art": None, "credit": "", "art_url": None, "art_auto": False, "color_chosen": False,
            "counters": {k: 0 for k, _, _ in COUNTERS}, "cmd": [0] * MAX_PLAYERS,
            # partner commanders: damage from each seat's second commander, tracked on its own
            "partner": False, "cmd2": [0] * MAX_PLAYERS}


def initials(name):
    """Up to two letters for a name: "Ana Bell" -> "AB", "Player 3" -> "P3", "Kai" -> "KA"."""
    words = [w for w in name.replace("-", " ").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def point_in_poly(pt, poly):
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def frame_axes(angle):
    """Screen directions of a frame's local right and down, for content rotated
    counter-clockwise by angle degrees (pygame.transform.rotate convention)."""
    a = math.radians(angle)
    return (math.cos(a), -math.sin(a)), (math.sin(a), math.cos(a))


class Seat:
    """Where one player sits: an outline on the canvas (rect or wedge polygon) and an upright
    frame - centre, size, reading angle - that the panel's content is drawn in and rotated by.
    angle is counter-clockwise degrees: 0 = reader at the bottom edge, 180 = top, 90 = right
    edge, 270 = left edge; anything else for a round table."""

    def __init__(self, rect=None, angle=0, poly=None, frame=None, box=None):
        self.angle = angle % 360
        if rect is not None:
            self.rect = pygame.Rect(rect)
            r = self.rect
            self.poly = [(r.left, r.top), (r.right, r.top), (r.right, r.bottom), (r.left, r.bottom)]
            cw, ch = (r.h, r.w) if self.angle in (90, 270) else (r.w, r.h)
            self.frame = (r.x + r.w / 2, r.y + r.h / 2, cw, ch)
            self.box = r                          # upright box for phones / TV
        else:
            self.poly = poly
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            self.rect = pygame.Rect(math.floor(min(xs)), math.floor(min(ys)),
                                    math.ceil(max(xs) - min(xs)), math.ceil(max(ys) - min(ys)))
            self.frame, self.box = frame, box
        self.is_rect = rect is not None

    def __iter__(self):                           # old (rect, angle) unpacking still works
        return iter((self.rect, self.angle))

    def contains(self, pos):
        return self.rect.collidepoint(pos) if self.is_rect else point_in_poly(pos, self.poly)


def rows_layout(n, w, h, flip):
    """=== : rows along the long sides; the far row faces the other way when flip."""
    rows = ROWS[n]
    rh = h / len(rows)
    out = []
    for ri, cnt in enumerate(rows):
        y0, y1 = round(ri * rh), round((ri + 1) * rh)
        cells = []
        for ci in range(cnt):
            x0, x1 = round(ci * w / cnt), round((ci + 1) * w / cnt)
            flipped = flip and len(rows) > 1 and ri == 0
            cells.append(Seat(pygame.Rect(x0, y0, x1 - x0, y1 - y0), 180 if flipped else 0))
        if ri == 1:
            cells.reverse()          # clockwise: top L->R, then bottom R->L
        out += cells
    return out


def ends_layout(n, w, h):
    """-=- : a player at each end of the table (one for 3 players), the rest along the sides.
    End seats face the short edges, so their content is turned 90 degrees."""
    ends = 2 if n >= 4 else 1
    top = (n - ends + 1) // 2
    bot = n - ends - top
    e = round(min(h / 2, w / (max(top, bot) + 2)))
    x0, mid = e, w - e * ends
    half = round(h / 2)

    def row(cnt, y, hh, angle):
        return [Seat(pygame.Rect(x0 + round(c * mid / cnt), y, round((c + 1) * mid / cnt) - round(c * mid / cnt), hh),
                     angle) for c in range(cnt)]
    out = row(top, 0, half, 180)                  # clockwise: far side L->R,
    if ends == 2:
        out.append(Seat(pygame.Rect(w - e, 0, e, h), 90))       # right end,
    out += row(bot, half, h - half, 0)[::-1]                    # near side R->L,
    out.append(Seat(pygame.Rect(0, 0, e, h), 270))              # left end
    return out


def fit_box(poly, centre, out_dir, angle, depth, aspects=(1.7, 1.4, 1.15, 0.95)):
    """Largest box (rotated by angle) inside poly, centred somewhere on the ray from centre
    along out_dir. Returns (cx, cy, w, h). Used for round-table wedges."""
    right, down = frame_axes(angle)
    best = (0, centre[0] + out_dir[0] * depth * 0.6, centre[1] + out_dir[1] * depth * 0.6, 10, 10)
    for hf in [x / 20 for x in range(16, 5, -1)]:
        bh = depth * hf
        for asp in aspects:
            bw = bh * asp
            if bw * bh <= best[0]:
                continue
            for df in [x / 20 for x in range(19, 4, -1)]:
                d = depth * df
                cx, cy = centre[0] + out_dir[0] * d, centre[1] + out_dir[1] * d
                # the panel's corners are empty, so they may overhang a little; its edge
                # middles (name, counters, -/+ hints) must be inside
                probes = [(sx * 0.8, sy * 0.8) for sx in (-1, 1) for sy in (-1, 1)] + \
                         [(0, -1), (0, 1), (-0.92, 0), (0.92, 0)] + \
                         [(sx * fx, sy) for sx in (-1, 1) for sy in (-1, 1) for fx in (0.45, 0.72)]
                pts = [(cx + px * bw / 2 * right[0] + py * bh / 2 * down[0],
                        cy + px * bw / 2 * right[1] + py * bh / 2 * down[1]) for px, py in probes]
                if all(point_in_poly(c, poly) for c in pts):
                    best = (bw * bh, cx, cy, bw, bh)
                    break
    _, cx, cy, bw, bh = best
    return cx, cy, bw, bh


def round_layout(n, w, h):
    """Round table: equal wedges from the centre (equal area: wedges are cut in a unit square,
    then stretched to the screen), each player's text facing their edge of the screen."""
    cx, cy = w / 2, h / 2
    step = 360 / n

    def edge(t):                                  # unit-square boundary point at angle t
        c, s = math.cos(math.radians(t)), math.sin(math.radians(t))
        m = max(abs(c), abs(s))
        return c / m, s / m

    def screen(u, v):
        return (cx + u * w / 2, cy + v * h / 2)

    # turn the wedges so their edges land on the screen corners where possible: 4 players
    # get one wedge per edge, 6 get two per long side and one per end
    offset = step / 2 if n == 8 else 0
    out = []
    for k in range(n):
        a0 = 180 - step / 2 + offset + k * step   # seat 0 on the left, then clockwise
        a1 = a0 + step
        pts = [edge(a0)]                          # in angle order (no sort: float wobble)
        for c in sorted(corner + 360 * m for corner in (45, 135, 225, 315) for m in (-1, 0, 1, 2)):
            if a0 + 1e-6 < c < a1 - 1e-6:
                pts.append(edge(c))
        pts.append(edge(a1))
        poly = [(cx, cy)] + [screen(*p) for p in pts]
        mid = edge(a0 + step / 2)
        ex, ey = screen(*mid)
        depth = math.hypot(ex - cx, ey - cy)
        out_dir = ((ex - cx) / depth, (ey - cy) / depth)
        theta = math.degrees(math.atan2(out_dir[1], out_dir[0]))
        angle = (90 - theta) % 360                # content "down" points at the player
        frame = fit_box(poly, (cx, cy), out_dir, angle, depth)
        bx, by, bw, bh = fit_box(poly, (cx, cy), out_dir, 0, depth, aspects=(1.3, 1.0, 0.8, 0.6))
        box = pygame.Rect(round(bx - bw / 2), round(by - bh / 2), round(bw), round(bh))
        out.append(Seat(angle=angle, poly=poly, frame=frame, box=box))
    return out


@functools.lru_cache(maxsize=32)
def layout(n, w, h, seating):
    """Seats in clockwise order for a seating style (see SEATINGS)."""
    if seating == "round" and n >= 3:
        return tuple(round_layout(n, w, h))
    if seating == "ends" and n >= 3:
        return tuple(ends_layout(n, w, h))
    return tuple(rows_layout(n, w, h, flip=seating != "upright"))


def app_icon(size, pad):
    """The LifePi icon (four seats around a life total), drawn with pygame - the same picture
    as the phone app's. Needs pygame's display + font modules initialised."""
    colours = [(46, 78, 140), (150, 42, 42), (38, 108, 62), (176, 150, 84)]
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.rect(s, (18, 18, 24), s.get_rect(), border_radius=size // 6)
    inner = size - 2 * pad
    gap = max(2, inner // 40)
    half = (inner - gap) // 2
    for k, c in enumerate(colours):
        r = pygame.Rect(pad + (k % 2) * (half + gap), pad + (k // 2) * (half + gap), half, half)
        pygame.draw.rect(s, c, r, border_radius=max(3, inner // 10))
    rad = inner // 4
    centre = (size // 2, size // 2)
    pygame.draw.circle(s, (18, 18, 24), centre, rad + gap * 2)
    pygame.draw.circle(s, (240, 240, 246), centre, rad, max(2, size // 64))
    t = pygame.font.Font(None, int(rad * 1.25)).render("40", True, (255, 255, 255))
    s.blit(t, t.get_rect(center=(centre[0], centre[1] + size // 90)))
    return s


def install_desktop_icon():
    """Installed copies: put the icon where the desktop finds it and point LifePi's menu and
    autostart entries at it. Runs on every start, so copies that update themselves (or were
    installed before there was an icon) get it too. Quietly does nothing it can't do."""
    try:
        if not ICON_FILE.exists():
            ICON_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = ICON_FILE.with_name("lifepi.tmp.png")   # pygame picks the format by extension
            pygame.image.save(app_icon(256, 14), str(tmp))
            os.replace(tmp, ICON_FILE)
    except (OSError, pygame.error) as e:
        print("icon not installed:", e, file=sys.stderr)
        return
    for f in (DATA_HOME / "applications" / "lifepi.desktop",
              Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "autostart" / "lifepi.desktop"):
        try:
            text = f.read_text()
        except OSError:
            continue
        new = text.replace("Icon=applications-games", "Icon=lifepi")
        if "StartupWMClass=" not in new:
            new = new.rstrip("\n") + "\nStartupWMClass=lifepi\n"
        if new != text:
            try:
                write_atomic(f, new)
            except OSError:
                pass


def rank_card_names(names, text):
    """Order card-name guesses for the 3-slot strip: commanders first. Scryfall lists "Atraxa's
    Fall" and "Atraxi Warden" before "Atraxa, Praetors' Voice"; legendary names read
    "Name, Title", so a match on the part before the comma goes first, then other names that
    start with what was typed, then the rest - each group in Scryfall's order."""
    q = " ".join(text.lower().split())

    def group(name):
        n = name.lower()
        if "," in n and n.split(",")[0].startswith(q):
            return 0
        return 1 if n.startswith(q) else 2
    return sorted(names, key=group)                     # stable: keeps the order within a group


def parse_version(v):
    """"v1.4.0" / "1.4.0" -> (1, 4, 0); None if it isn't a version."""
    parts = str(v).strip().lstrip("vV").split(".")
    if not 1 <= len(parts) <= 4 or not all(x.isdigit() for x in parts):
        return None
    return tuple(int(x) for x in parts) + (0,) * (4 - len(parts))


def newer_version(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


def check_release_source(src, tag):
    """A downloaded lifepi.py must look like the release before it replaces anything."""
    if not src.startswith(b"#!/usr/bin/env python3\n"):
        raise ValueError("download is not LifePi")
    text = src.decode("utf-8")
    want = tag.lstrip("vV")
    if f'VERSION = "{want}"' not in text:
        raise ValueError(f"download is not version {want}")
    compile(text, "lifepi.py", "exec")                    # a SyntaxError aborts the update


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
            import http.server
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
    import http.server
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
    """An off-screen surface drawn upright in a seat's frame, then rotated onto the canvas.
    Touch zones are given in the upright frame; touches are mapped back into it, so any
    angle works (flipped far row, table ends at 90/270, round-table wedges)."""

    def __init__(self, app, seat, angle=0, inset=0, alpha=False):
        if not isinstance(seat, Seat):
            seat = Seat(pygame.Rect(seat), angle)
        cx, cy, cw, ch = seat.frame
        self.app, self.seat, self.angle = app, seat, seat.angle
        self.cx, self.cy = cx, cy
        self.w, self.h = max(1, int(cw - 2 * inset)), max(1, int(ch - 2 * inset))
        self.surf = pygame.Surface((self.w, self.h), pygame.SRCALPHA if alpha else 0)

    def to_local(self, pos):
        """Canvas point -> upright frame coordinates (may fall outside the frame)."""
        (rx, ry), (dx, dy) = frame_axes(self.angle)
        px, py = pos[0] - self.cx, pos[1] - self.cy
        return px * rx + py * ry + self.w / 2, px * dx + py * dy + self.h / 2

    def zone(self, r, tap=None, hold=None, repeat=0.15, drag=None):
        r = pygame.Rect(r)
        seat = self.seat

        def hit(pos):
            if not seat.contains(pos):
                return False
            x, y = self.to_local(pos)
            return r.collidepoint(math.floor(x), math.floor(y))
        self.app.zones.append((hit, tap, hold, repeat, drag))

    def finish(self):
        a = self.angle
        if a % 90 == 0:
            s = pygame.transform.rotate(self.surf, a) if a else self.surf
        else:
            s = pygame.transform.rotozoom(self.surf, a, 1)     # smooth for round tables
        self.app.canvas.blit(s, s.get_rect(center=(round(self.cx), round(self.cy))))


# --------------------------------------------------------------------------- app
class App:
    def __init__(self, args):
        self.args = args
        for d in (CONFIG_DIR, CACHE_DIR, SEARCH_DIR, IMAGE_DIR):
            d.mkdir(parents=True, exist_ok=True)
        self.settings = {"rotation": 0, "seating": "table", "cmd_hits_life": True,
                         "start_life": 40, "format": "commander", "companion": True, "companion_pin": "", "random_first": True,
                         "turn_tracker": False,   # optional: whose turn it is + the turn number
                         "auto_rotate": False,    # phone app: follow the phone turning (off = stay put)
                         # installed copies: newest release seen, when we last asked, and the
                         # version whose "update available" banner was closed
                         "update_latest": "", "update_checked": 0, "update_dismissed": ""}
        self.players = [new_player(i, 40) for i in range(4)]
        self.profiles = {}
        self.first, self.first_anim, self.game_id = -1, None, 0    # who goes first (-1: not picked)
        self.turn = None          # turn tracker: {"round": n, "seat": whose turn, "start": who began}
        # what happened this game (saved), and the steps Undo can take back (this session)
        self.history, self.undo = [], []
        self.armed = None                         # (key, time): a button waiting for its second tap
        self.load_state()
        self.state_dirty = self.snap_dirty = False
        if not self.settings["companion_pin"]:
            self.new_pin()
        self.cache_lock = threading.Lock()
        self.ac_cache, self.ac_dirty, self.known_names = {}, False, set()
        self.load_caches()
        if args.rotate is not None:
            self.settings["rotation"] = args.rotate % 360 // 90 * 90

        if not WEB:                                # the window's app id matches lifepi.desktop
            os.environ.setdefault("SDL_VIDEO_WAYLAND_WMCLASS", "lifepi")
            os.environ.setdefault("SDL_VIDEO_X11_WMCLASS", "lifepi")
        pygame.display.init()      # no audio needed; skips ALSA start-up
        pygame.font.init()
        pygame.display.set_caption("LifePi")
        if not WEB:
            try:
                pygame.display.set_icon(app_icon(64, 2))
            except pygame.error:
                pass
            if getattr(args, "desktop_icon", False):   # main() only - tests leave $HOME alone
                install_desktop_icon()
        self.web_turn = 0                          # phone app: rotation undoing the phone's turn
        if WEB:                                   # the page decides: match its shape and size
            self.web_size = web_screen_size()
            self.screen = pygame.display.set_mode(self.web_size)
            self.web_checked = time.time()
            self.web_angle0 = web_screen_angle()  # the orientation to stay in
        elif args.windowed:
            w, h = (int(v) for v in args.windowed.lower().split("x"))
            self.screen = pygame.display.set_mode((w, h))
        else:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.clock = pygame.time.Clock()
        self.fonts, self.img_cache = {}, {}
        self.zones, self.pointers, self.modals, self.fx = [], {}, [], {}
        self.dirty, self.last_save = True, 0.0
        # random card art for artless players - only when launched as the app (tests stay offline)
        self.random_art = bool(getattr(args, "random_art", False))
        # update check: only when launched as the app (main()), never in tests or the browser
        self.update_check = bool(getattr(args, "update_check", False))
        self.install_path = Path(getattr(args, "install_path", None) or __file__).resolve()
        self.update_state, self.update_error, self.restart_after = "", "", False
        self.art_results, self.art_pending, self.art_retry_at = queue.Queue(), set(), 0
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
        if WEB:
            spawn(self.restore_art)
        self.fill_random_art()                    # players without art get a random card's art
        self.check_for_update()                   # a newer release on GitHub? (background)

    # ------------------------------------------------------------------ state
    def load_state(self):
        """Restore the saved game. Falls back to the previous save if the latest is damaged;
        a damaged file is kept aside (state.json.corrupt) rather than silently overwritten."""
        if WEB:                                   # the browser keeps saves in localStorage
            sources = [("browser save", lambda: web_store("state")), ("browser backup", lambda: web_store("state.bak"))]
        else:                                     # -sig: tolerate a BOM from Windows editors
            sources = [(f.name, lambda f=f: f.read_text(encoding="utf-8-sig")) for f in (STATE_FILE, BACKUP_FILE)]
        for name, read in sources:
            try:
                text = read()
                if text is None:
                    raise FileNotFoundError(name)
                data = json.loads(text)
                settings, players, profiles = self.parse_state(data)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                print(f"ignoring unreadable {name}: {e}", file=sys.stderr)
                if name == STATE_FILE.name:
                    try:
                        os.replace(STATE_FILE, STATE_FILE.with_name("state.json.corrupt"))
                    except OSError:
                        pass
                continue
            self.settings.update(settings)
            self.profiles = profiles
            if players:
                self.players = players
            first = as_int(data.get("first"), -1)
            self.first = first if 0 <= first < len(self.players) else -1
            self.turn = self.parse_turn(data.get("turn"))
            self.history = [e for e in (data.get("history") or [])[-HISTORY_MAX:]
                            if isinstance(e, dict) and isinstance(e.get("text"), str)
                            and isinstance(e.get("t"), (int, float))]
            return

    def parse_turn(self, t):
        """A saved turn tracker position, or None if it doesn't fit this game."""
        if not isinstance(t, dict):
            return None
        n = len(self.players)
        r, seat, start = as_int(t.get("round"), 0), as_int(t.get("seat"), -1), as_int(t.get("start"), -1)
        if r < 1 or not 0 <= seat < n or not 0 <= start < n:
            return None
        return {"round": r, "seat": seat, "start": start}

    def parse_state(self, data):
        """Validate saved JSON field by field, so one bad value can't sink the whole game."""
        s = data.get("settings", {})
        settings = {}
        if as_int(s.get("rotation"), -1) in (0, 90, 180, 270):
            settings["rotation"] = as_int(s["rotation"])
        if s.get("seating") in SEATINGS:
            settings["seating"] = s["seating"]
        if s.get("format") in FORMATS:
            settings["format"] = s["format"]
        if isinstance(s.get("cmd_hits_life"), bool):
            settings["cmd_hits_life"] = s["cmd_hits_life"]
        if as_int(s.get("start_life"), 0) > 0:
            settings["start_life"] = as_int(s["start_life"])
        if isinstance(s.get("companion"), bool):
            settings["companion"] = s["companion"]
        pin = s.get("companion_pin")
        if isinstance(pin, str) and len(pin) == 6 and pin.isdigit():
            settings["companion_pin"] = pin
        if isinstance(s.get("random_first"), bool):
            settings["random_first"] = s["random_first"]
        if isinstance(s.get("turn_tracker"), bool):
            settings["turn_tracker"] = s["turn_tracker"]
        if isinstance(s.get("auto_rotate"), bool):
            settings["auto_rotate"] = s["auto_rotate"]
        for key in ("update_latest", "update_dismissed"):
            if isinstance(s.get(key), str) and parse_version(s[key]):
                settings[key] = s[key]
        if isinstance(s.get("update_checked"), (int, float)):
            settings["update_checked"] = s["update_checked"]
        start = settings.get("start_life", self.settings["start_life"])

        players = []
        raw = data.get("players") or []
        if 1 <= len(raw) <= MAX_PLAYERS:
            for i, p in enumerate(raw):
                b = new_player(i, start)
                if isinstance(p.get("name"), str) and p["name"].strip():
                    b["name"] = p["name"][:NAME_MAX]
                b["life"] = as_int(p.get("life"), start)
                b["color"] = as_int(p.get("color"), b["color"]) % len(PALETTE)
                if isinstance(p.get("art"), str):
                    b["art"] = p["art"]
                if isinstance(p.get("credit"), str):
                    b["credit"] = p["credit"]
                if isinstance(p.get("art_url"), str) and p["art_url"].startswith("https://"):
                    b["art_url"] = p["art_url"]
                for flag in ("art_auto", "color_chosen"):
                    b[flag] = p.get(flag) is True
                c = p.get("counters") or {}
                b["counters"] = {k: max(0, as_int(c.get(k))) for k, _, _ in COUNTERS}
                b["cmd"] = [max(0, as_int(x)) for x in (list(p.get("cmd") or []) + [0] * MAX_PLAYERS)[:MAX_PLAYERS]]
                b["cmd2"] = [max(0, as_int(x)) for x in (list(p.get("cmd2") or []) + [0] * MAX_PLAYERS)[:MAX_PLAYERS]]
                b["partner"] = p.get("partner") is True
                players.append(b)

        profiles = {}
        for name, pr in (data.get("profiles") or {}).items():
            if isinstance(name, str) and isinstance(pr, dict):
                profiles[name] = {"color": as_int(pr.get("color")) % len(PALETTE),
                                  "art": pr["art"] if isinstance(pr.get("art"), str) else None,
                                  "credit": pr["credit"] if isinstance(pr.get("credit"), str) else "",
                                  "art_url": pr["art_url"] if isinstance(pr.get("art_url"), str)
                                  and pr["art_url"].startswith("https://") else None,
                                  "art_auto": pr.get("art_auto") is True, "color_chosen": pr.get("color_chosen") is True}
        return settings, players, profiles

    def save_state(self):
        """Durably save the game. The previous save is kept as state.json.bak."""
        data = {"settings": self.settings, "players": self.players, "profiles": self.profiles,
                "first": self.first, "turn": self.turn, "history": [self.history_entry(e) for e in self.history]}
        if WEB:
            try:
                prev = web_store("state")
                if prev:
                    web_store("state.bak", prev)
                web_store("state", json.dumps(data))
                self.state_dirty = False
            except Exception as e:                # storage full / blocked (private mode)
                print("save failed:", e, file=sys.stderr)
            self.last_save = time.time()
            self.save_caches()
            return
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
            data = json.loads((web_store("autocomplete") or "{}") if WEB else SUGGEST_FILE.read_text())
            if isinstance(data, dict):
                self.ac_cache = data
        except (OSError, ValueError, AttributeError):
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
            web_store("autocomplete", text) if WEB else write_atomic(SUGGEST_FILE, text)
        except Exception as e:
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
        # each seat's place on the device screen (fractions of the canvas), so phones and the
        # TV put players exactly where the device does (clockwise, seating style, rotation):
        # pos = upright box for the panel, poly = outline (a wedge on a round table)
        W, H = self.W, self.H
        seats = layout(n, W, H, self.settings["seating"])

        def frac(x, y):
            return [round(x / W, 4), round(y / H, 4)]
        rects = [frac(s.box.x, s.box.y) + frac(s.box.w, s.box.h) for s in seats]
        polys = [None if s.is_rect else [frac(x, y) for x, y in s.poly] for s in seats]
        out = []
        for i, p in enumerate(self.players):
            art = None
            if p["art"] and os.path.isfile(p["art"]):
                art = f"/art/{i}?v={hashlib.sha1(p['art'].encode()).hexdigest()[:10]}"
            out.append({"seat": i, "name": p["name"], "life": p["life"], "initials": initials(p["name"]),
                        "color": "#%02x%02x%02x" % PALETTE[p["color"] % len(PALETTE)],
                        "art": art, "credit": p["credit"] if art else "",
                        "counters": dict(p["counters"]), "cmd": p["cmd"][:n], "ko": self.ko_reason(p),
                        "cmd2": p["cmd2"][:n], "partner": bool(p.get("partner")),
                        "pos": rects[i], "poly": polys[i]})
        fmt = self.settings["format"]
        return {"players": out, "start_life": self.settings["start_life"],
                "format": fmt, "formats": [[k, f[0], f[1]] for k, f in FORMATS.items()],
                "poison_ko": FORMATS[fmt][2], "commander": FORMATS[fmt][3],
                "undo": self.undo_label(),
                "history": [[round(e["t"]), self.history_text(e)] for e in self.history[-40:]][::-1],
                # who goes first - hidden while the device is still spinning, so no spoilers
                "first": -1 if self.first_spinning() else self.first, "game": self.game_id,
                "random_first": self.settings["random_first"],
                "cmd_hits_life": self.settings["cmd_hits_life"], "seating": self.settings["seating"],
                "seatings": [[k, label] for k, label in SEATINGS.items()], "max_players": MAX_PLAYERS,
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
        if op in ("life", "counter", "cmd", "name", "color", "drain"):
            seat = num("seat", -1, 99)
            if not 0 <= seat < n:
                return
        if op == "life":
            self.change_life(seat, num("d", -999, 999))
        elif op == "drain":
            self.change_opponents(seat, num("d", -99, 99))
        elif op == "undo":
            self.undo_last()
        elif op == "counter" and a.get("key") in self.players[0]["counters"]:
            self.change_counter(seat, a["key"], num("d", -99, 99))
        elif op == "cmd":
            src = num("src", -1, 99)
            which = 2 if a.get("which") == 2 else 1
            if 0 <= src < n:
                self.change_cmd(seat, src, num("d", -99, 99), which)
        elif op == "partner" and isinstance(a.get("on"), bool):
            seat = num("seat", -1, 99)
            if 0 <= seat < n:
                self.set_partner(seat, a["on"])
        elif op == "name" and isinstance(a.get("name"), str):
            self.rename_player(seat, a["name"])
        elif op == "color":
            self.recolor_player(seat)
        elif op == "newgame":
            if a.get("seating") in SEATINGS:
                self.settings["seating"] = a["seating"]
            fmt = a.get("format") if a.get("format") in FORMATS else None
            self.start_game(num("n", 1, MAX_PLAYERS), num("life", 1, 9999), fresh=a.get("fresh") is True, fmt=fmt)
        elif op == "reset":
            self.start_game(n, self.settings["start_life"], reset=True)
        elif op == "seating":
            self.set_seating(a.get("value"))
        elif op == "random_first" and isinstance(a.get("on"), bool):
            self.settings["random_first"] = a["on"]
            self.changed()
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
    def rot(self):
        """Screen rotation in effect: the Rotate setting, plus - in the phone app with auto-rotate
        off - the opposite of however far the phone has turned since the app started, so the
        layout stays fixed to the device (a phone lying on the table) instead of flipping."""
        return (self.settings["rotation"] + self.web_turn) % 360

    def make_canvas(self):
        sw, sh = self.screen.get_size()
        rot = self.rot()
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

    def cmd_tile(self, j, dmg, side, number=True, partner=False):
        """Mini portrait of seat j's commander (art, else colour) with the damage it dealt;
        partner=True marks the seat's second (partner) commander with a "2" badge."""
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
        if dmg >= 15:                               # amber from 15, red (and thicker) at 21
            pygame.draw.rect(t, LETHAL if dmg >= 21 else AMBER, t.get_rect(), max(2, side // (8 if dmg >= 21 else 12)),
                             border_radius=max(3, side // 5))
        if number and side >= 26:                   # whose commander it is, under the damage
            self.text(t, str(dmg), side * 0.52, WHITE, maxw=side * 0.86, center=(side // 2, int(side * 0.4)))
            self.text(t, initials(src["name"]), side * 0.3, (230, 230, 236), maxw=side * 0.86,
                      center=(side // 2, int(side * 0.79)))
        elif number:
            self.text(t, str(dmg), side * 0.62, WHITE, maxw=side * 0.9, center=(side // 2, side // 2 + 1))
        if partner:
            self.partner_badge(t, (side - 1, 0), max(9, side // 3))
        return t

    def partner_badge(self, surf, topright, size):
        """A small gold "2" in a corner: the partner (second) commander."""
        b = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(b, (255, 220, 90, 255), (size // 2, size // 2), size // 2)
        img = self.font(int(size * 0.95)).render("2", True, (30, 24, 0))
        b.blit(img, img.get_rect(center=(size // 2, size // 2 + 1)))
        surf.blit(b, b.get_rect(topright=topright))

    def draw_strip(self, surf, area, extra, cmds, chips):
        """Panel bottom strip: commander-damage tiles [(seat, dmg)], then counter pills
        [(label, colour)]. One line if that fits at >= 75% of the strip height; otherwise the
        strip grows `extra` px upward and the tiles get their own grid (1 or 2 rows, whichever
        gives bigger tiles) with the pills on a line below."""
        def tiles(side):
            return [self.cmd_tile(j, d, side, partner=w == 2) for j, d, w in cmds]

        def pills(rh):
            return [self.make_pill(label, rh * 0.5, bg=c, alpha=225, outline=o) for label, c, o in chips]

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
    # ---- history + undo: every change first records itself. Taps on the same thing within
    # MERGE_TIME are one entry ("Ana life -5") and one undo step.
    def record(self, key, seat, kind, d, new, src=None, which=1, merge=True):
        now = time.time()
        u = self.undo[-1] if self.undo else None
        if merge and u and u["key"] == key and now - u["t"] < MERGE_TIME and len(self.history) > u["hist"]:
            u["t"] = now
            e = self.history[-1]
            e["d"], e["new"], e["t"] = e["d"] + d, new, now
            return
        self.undo.append({"key": key, "t": now, "hist": len(self.history), "game": self.game_copy()})
        del self.undo[:-UNDO_MAX]
        p = self.players[seat] if 0 <= seat < len(self.players) else None
        e = {"t": now, "kind": kind, "d": d, "new": new, "name": p["name"] if p else "",
             "src": self.players[src]["name"] if src is not None and src < len(self.players) else "", "which": which}
        self.history.append(e)
        e["text"] = self.history_text(e)
        if len(self.history) > HISTORY_MAX:
            cut = len(self.history) - HISTORY_MAX
            del self.history[:cut]
            for u in self.undo:
                u["hist"] = max(0, u["hist"] - cut)

    def history_text(self, e):
        k, d, new, name = e.get("kind"), e.get("d", 0), e.get("new"), e.get("name", "")
        if k == "life":
            return f"{name}: life {d:+d}  (now {new})"
        if k == "cmd":
            src = e.get("src", "") + ("'s partner" if e.get("which") == 2 else "")
            return f"{name}: commander damage from {src} {d:+d}  (now {new})"
        if k in dict((c, 1) for c, _, _ in COUNTERS):
            label = next(lab for c, lab, _ in COUNTERS if c == k)
            return f"{name}: {label.lower()} {d:+d}  (now {new})"
        if k == "drain":
            return f"{name}: each opponent {d:+d} life"
        if k == "partner":
            return f"{name}: partner commanders {'on' if new else 'off'}"
        if k == "swap":
            return f"{name} and {e.get('src', '')} swapped seats"
        if k == "turn":
            return f"Turn {new}: {name}" + ("  (back)" if d < 0 else "")
        if k in ("game", "reset"):
            return e.get("text") or "New game"
        return e.get("text", "")

    def history_entry(self, e):
        out = dict(e)
        out["text"] = self.history_text(e)
        return out

    def game_copy(self):
        return {"players": json.loads(json.dumps(self.players)), "first": self.first,
                "turn": dict(self.turn) if self.turn else None,
                "settings": {k: self.settings[k] for k in ("start_life", "format", "seating")},
                "game_id": self.game_id}

    def undo_label(self):
        if not self.undo:
            return ""
        u = self.undo[-1]
        return self.history_text(self.history[u["hist"]]) if u["hist"] < len(self.history) else "last change"

    def undo_last(self):
        """Take back the last change (a run of quick taps on one thing counts as one)."""
        if not self.undo:
            return self.say("Nothing to undo")
        label = self.undo_label()
        u = self.undo.pop()
        g = u["game"]
        players = g["players"]
        if len(players) == len(self.players) and u["key"][0] not in ("swap", "game"):
            for old, cur in zip(players, self.players):   # keep names / art changed since then
                for k in ("name", "color", "art", "credit", "art_url", "art_auto", "color_chosen"):
                    old[k] = cur.get(k)
        self.players = players
        self.first, self.game_id = g["first"], g["game_id"]
        self.turn = dict(g["turn"]) if g.get("turn") else None
        seating_changed = self.settings["seating"] != g["settings"]["seating"]
        self.settings.update(g["settings"])
        del self.history[u["hist"]:]
        self.fx.clear()
        self.first_anim = None
        self.modals = [m for m in self.modals if m.get("idx", 0) < len(self.players)]
        if seating_changed:
            self.make_canvas()
        self.say(f"Undone: {label}")
        self.changed()

    # ---- two-tap buttons: the first tap arms ("Tap again to ..."), the second acts
    def confirm(self, key, fn):
        def tap():
            if self.is_armed(key):
                if time.time() - self.armed[1] < CONFIRM_GAP:
                    return                        # the 2nd tap of a double tap is not a confirm
                self.armed = None
                fn()
            else:
                self.armed = (key, time.time())
            self.dirty = True
        return tap

    def is_armed(self, key):
        return bool(self.armed) and self.armed[0] == key and time.time() - self.armed[1] < ARM_TIME

    def game_in_progress(self):
        life = self.settings["start_life"]
        return any(p["life"] != life or any(p["counters"].values()) or any(p["cmd"]) or any(p["cmd2"])
                   for p in self.players)

    def format(self):
        return FORMATS[self.settings["format"]]

    def uses_cmd(self):
        return self.format()[3]

    def change_life(self, i, d, record=True):
        if record:
            self.record(("life", i), i, "life", d, self.players[i]["life"] + d)
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
        new = max(0, c[key] + d)
        if new != c[key]:
            self.record(("counter", i, key), i, key, new - c[key], new)
        c[key] = new
        self.changed()

    def change_opponents(self, i, d):
        """Each opponent of seat i gains / loses d life (drain effects, "each opponent loses 1")."""
        self.record(("drain", i), i, "drain", d, None)
        for j in range(len(self.players)):
            if j != i:
                self.change_life(j, d, record=False)
        self.changed()

    def commanders(self):
        """Every commander at the table, in seat order: (seat, which) - which 2 is a partner."""
        return [(j, w) for j, q in enumerate(self.players) for w in ((1, 2) if q.get("partner") else (1,))]

    def set_partner(self, i, on):
        """Seat i plays two partner commanders (or not). Turning it off forgets the damage the
        second one dealt (life already lost stays lost)."""
        if bool(self.players[i].get("partner")) != bool(on):
            self.record(("partner", i), i, "partner", 0, bool(on), merge=False)
        self.players[i]["partner"] = bool(on)
        if not on:
            for q in self.players:
                q["cmd2"][i] = 0
        self.changed()

    def change_cmd(self, i, src, d, which=1):
        """Commander damage to player i from seat src's commander (which=2: its partner)."""
        p = self.players[i]
        if which == 2 and not self.players[src].get("partner"):
            return
        track = p["cmd2"] if which == 2 else p["cmd"]
        new = max(0, track[src] + d)
        applied = new - track[src]
        if applied:
            self.record(("cmd", i, src, which), i, "cmd", applied, new, src=src, which=which)
        track[src] = new
        if applied and self.settings["cmd_hits_life"]:
            self.change_life(i, -applied, record=False)
        self.changed()

    def ko_reason(self, p):
        _, _, poison_ko, cmd = self.format()
        if p["counters"]["poison"] >= poison_ko:
            return "poison"
        if cmd and max(max(p["cmd"]), max(p["cmd2"])) >= 21:     # 21 from any one commander
            return "commander"
        if p["life"] <= 0:
            return "life"
        return ""

    def is_dead(self, p):
        return bool(self.ko_reason(p))

    def start_game(self, n, life, fresh=False, fmt=None, reset=False):
        """New game for n players at life. fresh: new players (names / art not kept)."""
        fmt = fmt or self.settings["format"]
        label = (f"Game reset - {n} players at {life}" if reset else
                 f"New game - {FORMATS[fmt][0]}, {n} player{'s' if n != 1 else ''} at {life}")
        self.record(("game",), -1, "game", 0, None, merge=False)
        self.history[-1]["text"] = label
        self.settings["start_life"], self.settings["format"] = life, fmt
        old = self.players
        self.players = []
        for i in range(n):
            p = new_player(i, life)
            if i < len(old) and not fresh:
                p.update({k: old[i].get(k) for k in ("name", "color", "art", "credit", "art_url", "art_auto",
                                                       "color_chosen", "partner")})
            self.players.append(p)
        self.fx.clear()
        self.modals.clear()
        self.game_id += 1
        self.pick_first()
        if 0 <= self.first < len(self.players):     # history keeps who started
            self.history[-1]["text"] = f"{label} - {self.players[self.first]['name']} goes first"
        self.reset_turn()
        self.fill_random_art()
        self.changed()

    def pick_first(self):
        """Choose who goes first (if enabled) and start the spin that reveals it."""
        n = len(self.players)
        self.first, self.first_anim = -1, None
        if n < 2 or not self.settings["random_first"]:
            return
        rng = random.SystemRandom()                 # fair, unpredictable
        k, start = rng.randrange(n), rng.randrange(n)
        seq = [(start + j) % n for j in range(2 * n + (k - start) % n + 1)]   # two laps, land on k
        times, t = [], 0.0
        for j in range(len(seq)):                   # slows down towards the end
            times.append(t)
            t += 0.05 + 0.32 * (j / len(seq)) ** 2.4
        self.first = k
        self.first_anim = {"t0": time.time(), "seq": seq, "times": times, "land": t}

    def first_spinning(self):
        a = self.first_anim
        return bool(a) and time.time() - a["t0"] < a["land"]

    def tick_first(self):
        """Keep the spin animating; publish the result to phones once it lands."""
        a = self.first_anim
        if not a:
            return
        age = time.time() - a["t0"]
        self.dirty = True
        if age >= a["land"] and not a.get("landed"):
            a["landed"] = True
            self.snap_dirty = True                 # phones/TV get the result now, not before
        if age >= a["land"] + FIRST_SHOW:
            self.first_anim = None

    def toggle_random_first(self):
        self.settings["random_first"] = not self.settings["random_first"]
        self.changed()

    # ---- optional turn tracker: whose turn it is and the turn number (a round of the table)
    def reset_turn(self):
        """Turn 1 for whoever goes first (seat 1 when nobody was picked)."""
        n = len(self.players)
        start = self.first if 0 <= self.first < n else 0
        self.turn = {"round": 1, "seat": start, "start": start} if n >= 2 else None

    def toggle_turn_tracker(self):
        self.settings["turn_tracker"] = not self.settings["turn_tracker"]
        if self.settings["turn_tracker"] and not self.turn:
            self.reset_turn()
        self.changed()

    def turn_on(self):
        return bool(self.settings["turn_tracker"] and self.turn and len(self.players) >= 2)

    def next_turn(self, d=1):
        """Pass the turn clockwise (d=1) or take it back (d=-1), skipping knocked-out players.
        The turn number goes up each time play comes back round to whoever started."""
        if not self.turn_on():
            return
        n, t = len(self.players), self.turn
        seat, rnd = t["seat"], t["round"]
        if d < 0 and seat == t["start"] and rnd == 1:
            return self.say("Already at the start of turn 1")
        for _ in range(n):
            if d > 0:
                seat = (seat + 1) % n
                rnd += seat == t["start"]
            else:
                rnd -= seat == t["start"]
                seat = (seat - 1) % n
            if not self.ko_reason(self.players[seat]) or seat == t["seat"]:
                break
        rnd = max(1, rnd)
        self.record(("turn",), seat, "turn", d, rnd, merge=False)
        t["seat"], t["round"] = seat, rnd
        self.changed()

    def turn_pill_rect(self, r):
        """Where the "Turn n - name" pill goes: beside the menu button, opposite Undo."""
        mx, my = self.menu_pos(r)
        ux, _ = self.undo_pos(r)
        pill = self.make_pill(self.turn_label(), max(20, int(r * 0.95)), fg=(10, 30, 40), bg=TURN, alpha=245)
        rect = pill.get_rect()
        if ux <= mx:
            rect.midleft = (mx + r + 10, my)
        else:
            rect.midright = (mx - r - 10, my)
        rect.clamp_ip(self.canvas.get_rect())
        return pill, rect

    def turn_label(self):
        name = self.players[self.turn["seat"]]["name"]
        name = name if len(name) <= 12 else name[:11] + "."
        return f"Turn {self.turn['round']} - {name}  >"

    def draw_turn(self, r):
        """Turn tracker: outline the active seat; the pill passes the turn (hold: back one)."""
        if not self.turn_on() or self.first_spinning():
            return
        seat = self.seats[self.turn["seat"]] if self.turn["seat"] < len(self.seats) else None
        if seat:
            pygame.draw.polygon(self.canvas, TURN, seat.poly, max(4, min(self.W, self.H) // 90))
        pill, rect = self.turn_pill_rect(r)
        self.canvas.blit(pill, rect)
        self.zones.append((rect.inflate(12, 14).collidepoint, lambda: self.next_turn(1),
                           lambda: self.next_turn(-1), 10 ** 6, None))

    def rotate_screen(self):
        self.settings["rotation"] = (self.settings["rotation"] + 90) % 360
        self.make_canvas()
        self.changed()

    def rename_player(self, i, t):
        p = self.players[i]
        if t.strip():
            p["name"] = t.strip()[:NAME_MAX]
            prof = self.profiles.get(p["name"])
            if prof and (not p["art"] or p.get("art_auto")):     # their saved look beats random art
                p.update(prof)
            self.remember_profile(p)
            self.changed()

    def recolor_player(self, i):
        p = self.players[i]
        p["color"] = (p["color"] + 1) % len(PALETTE)
        p["art"], p["credit"], p["art_url"] = None, "", None
        p["art_auto"], p["color_chosen"] = False, True   # a colour on purpose: no random art
        self.remember_profile(p)
        self.changed()

    def swap_seats(self, a, b):
        """Swap two players' seats; commander damage is indexed by seat, so it follows."""
        P = self.players
        self.record(("swap",), a, "swap", 0, None, src=b, merge=False)
        P[a], P[b] = P[b], P[a]
        for p in P:
            p["cmd"][a], p["cmd"][b] = p["cmd"][b], p["cmd"][a]
            p["cmd2"][a], p["cmd2"][b] = p["cmd2"][b], p["cmd2"][a]
        fa, fb = self.fx.pop(a, None), self.fx.pop(b, None)
        if fa:
            self.fx[b] = fa
        if fb:
            self.fx[a] = fb
        if self.first in (a, b):                    # "goes first" stays with the player
            self.first = b if self.first == a else a
        if self.turn:                               # ...and so does the turn
            for k in ("seat", "start"):
                if self.turn[k] in (a, b):
                    self.turn[k] = b if self.turn[k] == a else a
        self.changed()

    def seat_at(self, pos):
        for k, seat in enumerate(self.seats):
            if seat.contains(pos):
                return k
        return None

    def remember_profile(self, p):
        if not p["name"].startswith("Player "):
            self.profiles[p["name"]] = {k: p.get(k) for k in ("color", "art", "credit", "art_url", "art_auto",
                                                               "color_chosen")}

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

        async def work():
            names = await m["suggest"](t)
            if m["text"] == t:                       # still what's on screen?
                m["sugg"] = names
                self.dirty = True
        spawn(work)

    # ------------------------------------------------------------------ player panel
    def wedge_bg(self, p, seat, dead):
        """Round-table wedge filled with the player's art (darkened) or colour. Cached."""
        r = seat.rect
        key = ("wedge", p["art"], p["color"], tuple(seat.poly), dead)
        if key not in self.img_cache:
            surf = pygame.Surface(r.size, pygame.SRCALPHA)
            art = self.cover(p["art"], r.w, r.h) if p["art"] else None
            if art:
                surf.blit(art, (0, 0))
                surf.blit(self.shade(r.w, r.h, 150, 150, 150), (0, 0))
            else:
                surf.fill((*PALETTE[p["color"] % len(PALETTE)], 255))
            if dead:
                tint = pygame.Surface(r.size, pygame.SRCALPHA)
                tint.fill((25, 0, 0, 170))
                surf.blit(tint, (0, 0))
            mask = pygame.Surface(r.size, pygame.SRCALPHA)
            pygame.draw.polygon(mask, (255, 255, 255, 255), [(x - r.x, y - r.y) for x, y in seat.poly])
            surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            self.img_cache[key] = surf
        return self.img_cache[key]

    def draw_panel(self, i, seat):
        p = self.players[i]
        gap = max(2, min(self.W, self.H) // 160)
        wedge = not seat.is_rect
        why = self.ko_reason(p)
        if wedge:
            self.canvas.blit(self.wedge_bg(p, seat, bool(why)), seat.rect)
            v = View(self, seat, alpha=True)            # content only, over the wedge
        else:
            v = View(self, seat, inset=gap)
        s, w, h = v.surf, v.w, v.h
        bg = self.cover(p["art"], w, h) if p["art"] and not wedge else None
        if bg:
            s.blit(bg, (0, 0))
            s.blit(self.shade(w, h, 210, 150, 220), (0, 0))   # darker where the text sits
        elif not wedge:
            s.fill(PALETTE[p["color"] % len(PALETTE)])
            s.blit(self.shade(w, h, 0, 25, 95), (0, 0))
        top, bot = int(h * 0.2), int(h * 0.22)
        first = i == self.first and not self.first_spinning() and not why
        fade = 120 if why else 255                  # knocked out: text fades back (wedges too)
        nr = self.text(s, p["name"], max(20, top * 0.6), maxw=w * (0.68 if first else 0.85), alpha=fade,
                       center=(w // 2, top // 2))
        if first:                                   # who went first, for the rest of the game
            badge = self.make_pill("1st", max(16, top * 0.36), fg=(30, 24, 0), bg=(255, 220, 90), alpha=240)
            s.blit(badge, badge.get_rect(midleft=(nr.right + max(4, top // 8), top // 2)))

        size = int(min(h * 0.56, w * 0.5))
        low = p["life"] <= 5 and not why
        life_rect = self.text(s, str(p["life"]), size, AMBER if low else WHITE, maxw=w * 0.62, alpha=fade,
                              center=(w // 2, h // 2))
        # a short bar under the number: which way is up, so 6 / 9 / 66 read right from across the table
        bar = pygame.Rect(0, 0, max(8, int(life_rect.w * 0.42)), max(2, size // 28))
        bar.midtop = (w // 2, life_rect.bottom - int(size * 0.1))
        ul = pygame.Surface(bar.size, pygame.SRCALPHA)
        ul.fill((*(AMBER if low else WHITE), 90 if why else 150))
        s.blit(ul, bar)
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

        poison_ko = self.format()[2]
        chips = [(f"{short} {p['counters'][k]}", CHIP_COLORS[k],
                  AMBER if k == "poison" and p["counters"][k] >= poison_ko - 3 else None)
                 for k, _, short in COUNTERS if p["counters"][k]]
        cmds = [(j, d, w) for j, w in self.commanders()                         # includes self
                for d in [(p["cmd"] if w == 1 else p["cmd2"])[j]] if d] if self.uses_cmd() else []
        cy = h - bot // 2 - bot // 8
        if chips or cmds:
            credit_h = int(max(11, h * 0.05)) + 3 if bg and p["credit"] else 0
            area = pygame.Rect(int(w * 0.04), h - bot, int(w * 0.92), bot - credit_h - 3)
            self.draw_strip(s, area, int(bot * 0.3), cmds, chips)
        else:
            pl = self.make_pill("+ Cmdr dmg" if self.uses_cmd() else "+ Counters", max(15, bot * 0.34),
                                fg=(215, 215, 225), alpha=90, outline=(255, 255, 255, 80))
            s.blit(pl, pl.get_rect(center=(w // 2, cy)))
        if (bg or (wedge and p["art"])) and p["credit"]:
            self.text(s, p["credit"], max(11, h * 0.05), GREY, maxw=w * 0.9,
                      midbottom=(w // 2, h - 3))
        if why:
            if not wedge:                               # wedges are tinted in wedge_bg
                shade = pygame.Surface((w, h))
                shade.fill((25, 0, 0))
                shade.set_alpha(170)
                s.blit(shade, (0, 0))
            # said in words with a white outline - not by colour alone
            pl = self.make_pill("KO" if why == "life" else f"KO - {why}", max(20, size * 0.2), bg=(0, 0, 0),
                                alpha=225, outline=WHITE)
            s.blit(pl, pl.get_rect(center=(w // 2, h // 2 + int(size * 0.47))))
        if not wedge:
            s.blit(self.corners(w, h, max(6, min(w, h) // 14)), (0, 0))

        mid = h - top - bot
        # -/+ halves: for a wedge they cover the whole slice, split along the player's centre line
        far = 10 ** 5 if wedge else 0
        v.zone((-far, top - far, w // 2 + far, mid + 2 * far), lambda: self.change_life(i, -1),
               lambda: self.change_life(i, -10), 0.5)
        v.zone((w // 2, top - far, w - w // 2 + far, mid + 2 * far), lambda: self.change_life(i, 1),
               lambda: self.change_life(i, 10), 0.5)
        sheet_angle = round(seat.angle / 90) * 90 % 360   # sheets open facing that player
        opener = lambda: self.push("player", idx=i, angle=sheet_angle)
        cmd_pane = lambda: self.push("cmd", idx=i, angle=sheet_angle)
        v.zone((0, 0, w, top), opener, drag=i)       # hold + drag the name to swap seats
        # damage tiles / counters strip: the commander damage pane (no commanders: the sheet)
        v.zone((0, h - bot, w, bot), cmd_pane if self.uses_cmd() else opener)
        v.finish()

    # ------------------------------------------------------------------ modals
    def modal_view(self, m, title, accent=HEADER):
        """Full-screen modal facing m["angle"] (0/90/180/270). Draw with v.w x v.h - for a
        player at a table end that is portrait on a landscape screen."""
        v = View(self, (0, 0, self.W, self.H), m.get("angle", 0))
        v.surf.fill(DARK)
        v.u = max(40, min(v.h // 8, v.w // 8))  # basic row height (portrait: not taller)
        pygame.draw.rect(v.surf, accent, (0, 0, v.w, v.u))
        self.text(v.surf, title, v.u * 0.55, maxw=v.w - v.u * 3, shadow=False,
                  midleft=(16, v.u // 2))
        return v

    def draw_menu(self, m):
        if self.update_badge():
            self.dismiss_update()                 # seen: the menu offers it from here on
        v = self.modal_view(m, f"LifePi {VERSION}")
        u, W = v.u, v.w
        st = self.settings
        items = [
            ("New game", lambda: self.push("newgame", n=len(self.players), life=st["start_life"],
                                           seating=st["seating"]), BTN_OK),
            ("Reset this game", self.confirm("reset", lambda: self.start_game(len(self.players), st["start_life"],
                                                                         reset=True)), BTN),
            ("History / undo", lambda: self.push("history"), BTN),
            ("Dice / coin", lambda: self.push("dice", result="", sub=""), BTN),
            (f"Rotate screen ({st['rotation']} deg)", self.rotate_screen, BTN_ON),
            (f"Seating: {SEATINGS[st['seating']]}", self.toggle_seating, BTN),
            (f"Cmdr dmg hits life: {'on' if st['cmd_hits_life'] else 'off'}", self.toggle_cmd, BTN),
            (f"Turn tracker: {'on' if st['turn_tracker'] else 'off'}", self.toggle_turn_tracker,
             BTN_ON if st["turn_tracker"] else BTN),
            ("Phones & TV", self.open_remote, BTN_ON),
            ("Cast to TV", self.open_cast, BTN),
            ("Back to game", self.pop, BTN),
            ("Quit", self.confirm("quit", self.quit), BTN_BAD),
        ]
        armed = {"Reset this game": "reset", "Quit": "quit"}
        if WEB:                                   # no server, casting or quitting in a browser tab
            items = [it for it in items if it[0] not in ("Phones & TV", "Cast to TV", "Quit")]
            items.insert(5, (f"Auto-rotate: {'on' if st['auto_rotate'] else 'off'}", self.toggle_auto_rotate, BTN))
        if self.update_available():
            items.insert(len(items) - (0 if WEB else 1),
                         (f"Update to {self.settings['update_latest'].lstrip('v')}", self.start_update, BTN_OK))
        elif self.update_mode():                  # installed copies: check now, install if newer
            items.insert(len(items) - (0 if WEB else 1), ("Check for updates", self.update_now, BTN))
        rh = (v.h - u - u // 2) // ((len(items) + 1) // 2)
        for k, (label, fn, bg) in enumerate(items):
            r = (k % 2 * W // 2, u + k // 2 * rh, W // 2, rh)
            if label in armed and self.is_armed(armed[label]):
                label, bg = f"Tap again to {label.split()[0].lower()}", BTN_BAD
            self.button(v, r, label, fn, bg=bg, size=min(rh * 0.42, W * 0.035))
        self.text(v.surf, "Unofficial fan content - not affiliated with Wizards of the Coast. "
                  "Card art via Scryfall.", u * 0.3, GREY, maxw=W - 20, shadow=False,
                  midbottom=(W // 2, v.h - 4))
        v.finish()

    def open_remote(self):
        if self.companion and self.companion.running:
            self.companion_base(refresh=True)          # the Wi-Fi address may have changed
        self.push("remote")

    def draw_remote(self, m):
        v = self.modal_view(m, "Phones & TV")
        u, W, H = v.u, v.w, v.h
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
        order = list(SEATINGS)
        self.set_seating(order[(order.index(self.settings["seating"]) + 1) % len(order)])

    def set_seating(self, seating):
        if seating in SEATINGS:
            self.settings["seating"] = seating
            self.changed()

    def toggle_auto_rotate(self):
        """Phone app: follow the phone turning, or stay in the orientation it's in now."""
        self.settings["auto_rotate"] = not self.settings["auto_rotate"]
        if WEB and not self.settings["auto_rotate"]:
            self.web_angle0 = web_screen_angle()   # lock to how it's held right now
        self.web_checked = 0                       # re-check on the next frame
        self.changed()

    def toggle_cmd(self):
        self.settings["cmd_hits_life"] = not self.settings["cmd_hits_life"]
        self.changed()

    def quit(self):
        self.running = False

    def draw_newgame(self, m):
        v = self.modal_view(m, "New game")
        u, W = v.u, v.w
        rh = (v.h - u) // 5
        m.setdefault("seating", self.settings["seating"])
        m.setdefault("format", self.settings["format"])
        m.setdefault("keep", True)

        def setv(k, val):
            m[k] = val
            self.dirty = True

        def set_format(key):
            m["format"], m["life"] = key, FORMATS[key][1]
            if key != "commander":                  # 1 v 1, or two teams
                m["n"] = 2
            self.dirty = True

        y = u
        self.text(v.surf, "Format", rh * 0.3, GREY, shadow=False, midleft=(16, y + 10))
        for k, key in enumerate(FORMATS):
            self.button(v, (k * W // 3, y + rh // 5, W // 3, rh - rh // 5), FORMATS[key][0],
                        lambda key=key: set_format(key), bg=BTN_ON if m["format"] == key else BTN,
                        size=min((rh - rh // 5) * 0.42, W * 0.04))
        y += rh
        self.text(v.surf, "Players", rh * 0.3, GREY, shadow=False, midleft=(16, y + 10))
        for n in range(1, MAX_PLAYERS + 1):
            x0, x1 = (n - 1) * W // MAX_PLAYERS, n * W // MAX_PLAYERS
            self.button(v, (x0, y + rh // 5, x1 - x0, rh - rh // 5), str(n),
                        lambda n=n: setv("n", n), bg=BTN_ON if m["n"] == n else BTN)
        y += rh
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
        y += rh
        self.text(v.surf, "Seating", rh * 0.3, GREY, shadow=False, midleft=(16, y + 10))
        for k, key in enumerate(SEATINGS):
            self.button(v, (k * W // 4, y + rh // 5, W // 4, rh - rh // 5), SEATINGS[key],
                        lambda key=key: setv("seating", key), bg=BTN_ON if m["seating"] == key else BTN,
                        size=min((rh - rh // 5) * 0.4, W * 0.04))
        y = v.h - rh

        def start():
            self.settings["seating"] = m["seating"]
            self.start_game(m["n"], m["life"], fresh=not m["keep"], fmt=m["format"])
        rf, small = self.settings["random_first"], min(rh * 0.36, W * 0.032)
        self.button(v, (0, y, W // 4, rh), "Cancel", self.pop)
        self.button(v, (W // 4, y, W // 4, rh), f"Random first: {'on' if rf else 'off'}", self.toggle_random_first,
                    bg=BTN_ON if rf else BTN, size=small)
        self.button(v, (W // 2, y, W // 4, rh), "Same players" if m["keep"] else "New players",
                    lambda: setv("keep", not m["keep"]), bg=BTN_ON if m["keep"] else BTN, size=small)
        # a game in progress: Start needs a second tap (and Undo can still bring it back)
        busy = self.game_in_progress()
        armed = busy and self.is_armed("start")
        self.button(v, (3 * W // 4, y, W - 3 * W // 4, rh), "Tap again to start" if armed else "Start",
                    self.confirm("start", start) if busy else start, bg=BTN_BAD if armed else BTN_OK,
                    size=small if armed else None)
        v.finish()

    def draw_dice(self, m):
        v = self.modal_view(m, "Dice / coin")
        u, W, H = v.u, v.w, v.h
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
        def planar():                               # Planechase: 1 planeswalk, 1 chaos, 4 blank
            m["result"] = random.choice(("Planeswalk", "Chaos", "Blank", "Blank", "Blank", "Blank"))
            m["sub"] = "planar die"
            self.dirty = True

        for k, (label, fn) in enumerate((("Coin", coin), ("Planar die", planar), ("Random player", who))):
            self.button(v, (k * W // 4, H - rh, W // 4, rh), label, fn, size=min(rh * 0.42, W * 0.035))
        self.button(v, (3 * W // 4, H - rh, W - 3 * W // 4, rh), "Close", self.pop, bg=BTN_ON)
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
        u, W, H = v.u, v.w, v.h
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
                ("Card art", lambda: self.ask_card_art(i, ang)),
                ("Local image", lambda: self.open_local_images(i, ang)),
                ("Colour", recolor),
                ("Profiles", lambda: self.open_profiles(i, ang))]
        if self.uses_cmd():                       # turning partners off forgets that damage: two taps
            if p.get("partner"):
                acts.append(("Tap again: partner off" if self.is_armed(("partner", i)) else "Partner: on",
                             self.confirm(("partner", i), lambda: self.set_partner(i, False))))
            else:
                acts.append(("Partner: off", lambda: self.set_partner(i, True)))
        if WEB:                                   # no image folder in a browser
            acts = [a for a in acts if a[0] != "Local image"]
        for k, (label, fn) in enumerate(acts):
            self.button(v, (k * W // len(acts), u, W // len(acts), u), label, fn, size=u * 0.38)

        # commander damage, own commander(s) included - not in formats without commanders
        sources = self.commanders() if self.uses_cmd() else []
        nc = len(COUNTERS) + 1                      # the counters, then "each opponent"
        portrait = W < H or not sources             # opened at a table end: one column
        if portrait:
            nrows = nc + (len(sources) + 1 if sources else 0)
            cw = W
        else:
            nrows = max(nc, len(sources))
            cw = W // 2
        rh = min(int(u * 1.1), (H - 2 * u - u // 3) // (nrows + 1))
        y0 = 2 * u + u // 6
        cx, cy0 = (0, y0 + (nc + 1) * rh) if portrait else (cw, y0)   # damage column

        def row(x, y, label, value, minus, plus, warn=False, tile=None, partner=False):
            b = min(rh, cw // 5)
            lx = x + 14
            if tile is not None:
                t = self.cmd_tile(tile, value, rh * 0.82, number=False, partner=partner)
                v.surf.blit(t, t.get_rect(midleft=(lx, y + rh // 2)))
                lx += t.get_width() + 10
            self.text(v.surf, label, rh * 0.42, maxw=x + cw - 3 * b - 10 - lx, shadow=False,
                      midleft=(lx, y + rh // 2))
            self.button(v, (x + cw - 3 * b - 8, y, b, rh), "-", minus, hold=minus, size=rh * 0.7)
            self.text(v.surf, str(value), rh * 0.6, warn or WHITE, shadow=False,
                      center=(x + cw - 2 * b - 8 + b // 2, y + rh // 2))
            self.button(v, (x + cw - b - 8, y, b, rh), "+", plus, hold=plus, size=rh * 0.7)

        self.text(v.surf, "Counters", rh * 0.4, GREY, shadow=False, midleft=(14, y0 + rh // 2))
        poison_ko = self.format()[2]
        for k, (key, label, _) in enumerate(COUNTERS):
            val = p["counters"][key]
            row(0, y0 + (k + 1) * rh, label, val,
                lambda key=key: self.change_counter(i, key, -1),
                lambda key=key: self.change_counter(i, key, 1),
                warn=key == "poison" and (LETHAL if val >= poison_ko else AMBER if val >= poison_ko - 3 else None))
        row(0, y0 + nc * rh, "Each opponent", "", lambda: self.change_opponents(i, -1),
            lambda: self.change_opponents(i, 1))
        if sources:
            self.text(v.surf, "Commander damage from", rh * 0.4, GREY, shadow=False,
                      midleft=(cx + 14, cy0 + rh // 2))
        for k, (j, w) in enumerate(sources):
            name = self.players[j]["name"] + (" partner" if w == 2 else "") + (" (you)" if j == i else "")
            dmg = (p["cmd"] if w == 1 else p["cmd2"])[j]
            row(cx, cy0 + (k + 1) * rh, name, dmg,
                lambda j=j, w=w: self.change_cmd(i, j, -1, w), lambda j=j, w=w: self.change_cmd(i, j, 1, w),
                warn=LETHAL if dmg >= 21 else AMBER if dmg >= 15 else None, tile=j, partner=w == 2)
        v.finish()

    def draw_history(self, m):
        """What happened this game, newest first, with Undo for the latest change."""
        v = self.modal_view(m, "History")
        u, W, H = v.u, v.w, v.h
        self.button(v, (W - 2 * u, 0, 2 * u, u), "Done", self.pop, bg=BTN_ON)
        rh = max(26, u * 2 // 3)
        rows = (H - 2 * u - 8) // rh
        page = m.setdefault("page", 0)
        items = self.history[::-1]
        pages = max(1, -(-len(items) // rows))
        page = m["page"] = min(page, pages - 1)
        now = time.time()
        for k, e in enumerate(items[page * rows:(page + 1) * rows]):
            y = u + 4 + k * rh
            if k % 2:
                pygame.draw.rect(v.surf, (26, 27, 36), (0, y, W, rh))
            ago = now - e["t"]
            when = "now" if ago < 60 else f"{int(ago // 60)} min" if ago < 3600 else time.strftime("%H:%M", time.localtime(e["t"]))
            self.text(v.surf, when, rh * 0.5, GREY, shadow=False, midleft=(14, y + rh // 2))
            new_game = e.get("kind") in ("game", "reset")
            self.text(v.surf, self.history_text(e), rh * 0.56, (255, 220, 90) if new_game else WHITE,
                      maxw=W - u * 2 - 24, shadow=False, midleft=(u * 2, y + rh // 2))
        if not items:
            self.text(v.surf, "Nothing yet - life, counters and damage changes show up here.", u * 0.4, GREY,
                      maxw=W - 40, shadow=False, center=(W // 2, H // 2))
        y = H - u
        label = self.undo_label()

        def turn(d):
            m["page"] = max(0, min(pages - 1, m["page"] + d))
            self.dirty = True
        self.button(v, (0, y, W // 4, u), "Newer", lambda: turn(-1), bg=BTN if page else HEADER)
        self.button(v, (W // 4, y, W // 4, u), "Older", lambda: turn(1), bg=BTN if page < pages - 1 else HEADER)
        self.button(v, (W // 2, y, W - W // 2, u), f"Undo: {label}" if label else "Nothing to undo",
                    self.undo_last, bg=BTN_ON if label else HEADER, size=u * 0.36)
        v.finish()

    def cmd_layout(self, n, W, H):
        """Columns for n commander tiles in W x H: the arrangement with the biggest tiles."""
        best = (0, 1)
        for cols in range(1, n + 1):
            rows = -(-n // cols)
            tw, th = W / cols, H / rows
            size = min(tw, th * 1.35)               # tiles look best a little wider than tall
            if size > best[0]:
                best = (size, cols)
        return best[1]

    def draw_cmd(self, m):
        """Commander damage to one player: a tile per commander (their art; partners get their
        own tile). Like a life panel: left half -1, right half +1, hold repeats. Opened by
        tapping a player's life total."""
        i = m["idx"]
        if i >= len(self.players):
            return self.pop()
        p = self.players[i]
        v = self.modal_view(m, f"Commander damage to {p['name']}",
                            tuple(c * 3 // 4 for c in PALETTE[p["color"] % len(PALETTE)]))
        u, W, H = v.u, v.w, v.h
        why = self.ko_reason(p)
        info = f"Life {p['life']}" + (f"   KO - {why}" if why else "")
        hint = ("Left side -1, right side +1 - hold to repeat" +
                (" (damage also comes off life)" if self.settings["cmd_hits_life"] else ""))
        iy = u + u * 0.35
        r = self.text(v.surf, info, u * 0.5, (255, 120, 120) if why else WHITE, shadow=False, midleft=(16, iy))
        self.text(v.surf, hint, u * 0.3, GREY, maxw=W - r.right - 40, shadow=False, midleft=(r.right + 24, iy))

        sources = self.commanders() + [("poison", 0)]      # and a poison tile, like Lifetap
        n = len(sources)
        poison_ko = self.format()[2]
        area = pygame.Rect(0, int(u * 1.75), W, H - int(u * 1.75) - u)
        cols = self.cmd_layout(n, area.w, area.h)
        rows = -(-n // cols)
        tw, th = area.w // cols, area.h // rows
        for k, (j, w) in enumerate(sources):
            r = pygame.Rect(area.x + k % cols * tw, area.y + k // cols * th, tw, th).inflate(-10, -10)
            if j == "poison":
                src = {"name": "Poison", "color": 0, "art": None}
                dmg, col, limit = p["counters"]["poison"], CHIP_COLORS["poison"], poison_ko
            else:
                src = self.players[j]
                dmg, col, limit = (p["cmd"] if w == 1 else p["cmd2"])[j], PALETTE[src["color"] % len(PALETTE)], 21
            art = self.cover(src["art"], r.w, r.h) if src["art"] else None
            tile = pygame.Surface(r.size)
            if art:
                tile.blit(art, (0, 0))
                tile.blit(self.shade(r.w, r.h, 120, 140, 190), (0, 0))
            else:
                tile.fill(col)
                tile.blit(self.shade(r.w, r.h, 20, 40, 110), (0, 0))
            rad = max(6, min(r.w, r.h) // 10)
            v.surf.blit(tile, r)
            v.surf.blit(self.corners(r.w, r.h, rad), r)      # round the tile's corners
            lethal, close = dmg >= limit, dmg >= limit - (3 if j == "poison" else 6)
            edge = LETHAL if lethal else AMBER if close else tuple(min(255, c + 70) for c in col)
            pygame.draw.rect(v.surf, edge, r, max(2, min(r.w, r.h) // 40) + (2 if close else 0), border_radius=rad)
            name = src["name"] + (" partner" if w == 2 else "") + (" (own)" if j == i else "")
            self.text(v.surf, name, max(16, min(r.h * 0.13, r.w * 0.1)), maxw=r.w * 0.9,
                      midtop=(r.centerx, r.y + r.h * 0.06))
            self.text(v.surf, str(dmg), max(20, min(r.h * 0.45, r.w * 0.36)), WHITE,
                      center=(r.centerx, r.centery + r.h * 0.04))
            if close:                               # say it in words, not just with a colour
                note = "LETHAL" if lethal else f"{limit - dmg} more is lethal"
                self.text(v.surf, note, max(14, min(r.h * 0.11, r.w * 0.085)), LETHAL if lethal else AMBER,
                          maxw=r.w * 0.9, midbottom=(r.centerx, r.bottom - r.h * 0.05))
            if w == 2:
                self.partner_badge(v.surf, (r.right - 6, r.y + 6), max(14, int(min(r.w, r.h) * 0.16)))
            gs = min(r.h * 0.22, r.w * 0.12, 44)              # - / + hints on the two halves
            for plus, gx in ((False, r.x + r.w * 0.12), (True, r.right - r.w * 0.12)):
                g = self.sign(plus, gs)
                v.surf.blit(g, g.get_rect(center=(int(gx), r.centery)))
            if j == "poison":
                sub, add = (lambda: self.change_counter(i, "poison", -1)), (lambda: self.change_counter(i, "poison", 1))
            else:
                sub = lambda j=j, w=w: self.change_cmd(i, j, -1, w)
                add = lambda j=j, w=w: self.change_cmd(i, j, 1, w)
            v.zone((r.x, r.y, r.w // 2, r.h), sub, sub, 0.25)            # left half: -1
            v.zone((r.x + r.w // 2, r.y, r.w - r.w // 2, r.h), add, add, 0.25)   # right half: +1

        y = H - u
        angle = m.get("angle", 0)
        self.button(v, (0, y, W // 2, u), "Counters & more", lambda: (self.pop(), self.push("player", idx=i, angle=angle)))
        self.button(v, (W // 2, y, W - W // 2, u), "Done", self.pop, bg=BTN_ON)
        v.finish()

    def draw_keyboard(self, m):
        v = self.modal_view(m, m["title"])
        u, W, H = v.u, v.w, v.h
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
        u, W, H = v.u, v.w, v.h
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
        elif m.get("retry") and not items and not m.get("status", "").startswith("Searching"):
            def again():                        # nothing found (or offline): back to the keyboard
                self.pop()
                m["retry"]()
            self.button(v, (0, y, W // 2, u), "Search again", again, bg=BTN_ON)
        self.button(v, (3 * W // 4, y, W // 4, u), "Cancel", self.pop, bg=BTN_BAD)
        v.finish()

    def grid_pick(self, m, it):
        self.pop()
        m["on_pick"](it)

    # ------------------------------------------------------------------ art / profiles
    def set_art(self, i, path, credit, url=None):
        """url: where the art came from, so the browser build can fetch it again after a
        reload (its files don't survive one)."""
        p = self.players[i]
        p["art"], p["credit"], p["art_url"] = path, credit, url
        p["art_auto"], p["color_chosen"] = False, False
        self.remember_profile(p)
        self.changed()

    def wants_random_art(self, p):
        return self.random_art and not p.get("art") and not p.get("color_chosen")

    def fill_random_art(self):
        """Give every player without art (who didn't choose a colour) a random card's art.
        Runs in the background; results are applied by tick_art on the main thread."""
        if not self.random_art:
            return
        want = [p for p in self.players if self.wants_random_art(p) and id(p) not in self.art_pending]
        if not want:
            return
        self.art_pending.update(id(p) for p in want)
        taken = {q["art"] for q in self.players if q.get("art")}

        async def work():
            missed = False
            for p in want:
                got = await self.random_card_art(taken)
                if got:
                    taken.add(got[0])
                else:
                    missed = True
                self.art_results.put((p, got))
            if missed:                                  # offline (e.g. a Pi before its Wi-Fi is up)
                self.art_retry_at = time.time() + RANDOM_ART_RETRY
            self.dirty = True
        spawn(work)

    async def random_card_art(self, taken):
        """(path, credit, url) for a random commander's art: from Scryfall, else (offline) from
        art already cached on this device. None if neither is available."""
        for _ in range(4):                              # a few tries in case of a repeat
            try:
                c = await scryfall("/cards/random", q=RANDOM_ART_QUERY)
            except Exception:
                break
            uris = c.get("image_uris") or (c.get("card_faces") or [{}])[0].get("image_uris") or {}
            if not uris.get("art_crop") or not c.get("id"):
                continue
            path = CACHE_DIR / f"{c['id']}.jpg"
            if str(path) in taken:
                continue
            try:
                if not path.exists():
                    tmp = path.with_suffix(".part")
                    tmp.write_bytes(await fetch_bytes(uris["art_crop"]))
                    os.replace(tmp, path)
            except Exception:
                break
            credit = f"{c.get('name', '?')} - illus. {c.get('artist', 'unknown')} - (c) Wizards of the Coast"
            return str(path), credit, uris["art_crop"]
        return self.cached_card_art(taken)

    def cached_card_art(self, taken):
        """Offline fallback: a random card from earlier searches whose art is on disk."""
        pool = {}
        for f in SEARCH_DIR.glob("*.json"):
            try:
                for c in json.loads(f.read_text())["cards"]:
                    path = CACHE_DIR / f"{c['id']}.jpg"
                    if path.exists() and str(path) not in taken:
                        pool[str(path)] = (str(path), f"{c['name']} - illus. {c['artist']} - (c) Wizards of the Coast",
                                           c.get("art"))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return random.choice(list(pool.values())) if pool else None

    def tick_art(self):
        """Main thread: apply random art that arrived; retry later if we were offline."""
        while True:
            try:
                p, got = self.art_results.get_nowait()
            except queue.Empty:
                break
            self.art_pending.discard(id(p))
            # still in the game, still without art, still no colour chosen?
            if got and any(q is p for q in self.players) and self.wants_random_art(p):
                p["art"], p["credit"], p["art_url"] = got
                p["art_auto"] = True
                self.remember_profile(p)
                self.changed()
        if self.art_retry_at and time.time() >= self.art_retry_at:
            self.art_retry_at = 0
            self.fill_random_art()

    async def restore_art(self):
        """Browser: re-download card art whose cached file is gone (it lived in memory)."""
        wanted = {}
        for p in self.players + list(self.profiles.values()):
            if p.get("art") and p.get("art_url") and not os.path.exists(p["art"]):
                wanted[p["art"]] = p["art_url"]
        for path, url in wanted.items():
            try:
                data = await fetch_bytes(url)
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_bytes(data)
                self.img_cache.clear()
                self.dirty = True
            except Exception as e:
                print("art restore failed:", url, e, file=sys.stderr)

    async def search_cards(self, query):
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
            data = (await scryfall("/cards/search", q=query, unique="art", order="edhrec")).get("data", [])
        except Exception as e:
            if isinstance(e, ScryfallError) and e.status == 404:
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

    async def suggest_cards(self, text):
        """Card-name guesses from Scryfall autocomplete (cached); local guesses if offline.
        Runs in the background (see spawn)."""
        q = " ".join(text.lower().split())
        if len(q) < 2:
            return []
        with self.cache_lock:
            hit = self.ac_cache.get(q)
        if hit and time.time() - hit[0] < SUGGEST_TTL:
            return rank_card_names(hit[1], q)
        try:
            names = (await scryfall("/cards/autocomplete", q=q)).get("data", [])[:20]
        except Exception:
            return rank_card_names(hit[1], q) if hit else self.local_card_names(text)
        with self.cache_lock:
            self.ac_cache[q] = [time.time(), names]
            self.ac_dirty = True
            self.known_names.update(names)
        return rank_card_names(names, q)

    def local_card_names(self, text):
        return rank_card_names(self._local_card_names(text), text)

    def _local_card_names(self, text):
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

    def ask_card_art(self, i, angle, text=""):
        """The Card art keyboard: type a name (Scryfall suggests as you type), pick or search."""
        def search(t):
            if t.strip():
                self.open_art_search(i, t.strip(), angle)
        self.ask_text("Search Scryfall for a card", text, search, angle,
                      local=self.local_card_names, suggest=self.suggest_cards,
                      hint="Type 2+ letters for suggestions",
                      pick=lambda name: self.open_art_search(i, f'!"{name}"', angle, label=name))

    def open_art_search(self, i, query, angle, label=None):
        grid = dict(title=f"Card art: {label or query}", items=[], page=0, status="Searching Scryfall...",
                    angle=angle, on_pick=lambda it: self.set_art(i, it["img"], it["value"], it.get("url")),
                    retry=lambda: self.ask_card_art(i, angle, label or query))
        self.push("grid", **grid)
        grid = self.modals[-1]

        async def work():
            try:
                cards, note = await self.search_cards(query)
                for c in cards:
                    path = CACHE_DIR / f"{c['id']}.jpg"
                    if not path.exists():                 # art is cached on disk by card id
                        try:
                            tmp = path.with_suffix(".part")
                            tmp.write_bytes(await fetch_bytes(c["art"]))
                            os.replace(tmp, path)
                            await asyncio.sleep(0.05)     # be polite to the image CDN
                        except Exception:
                            continue                      # offline: show what we have
                    credit = f"{c['name']} - illus. {c['artist']} - (c) Wizards of the Coast"
                    grid["items"].append({"label": f"{c['name']} ({c['set'].upper()})",
                                          "img": str(path), "value": credit, "url": c["art"]})
                    grid["status"] = "Loading..."
                    self.dirty = True
                grid["status"] = note if grid["items"] else "No cards found"
            except ScryfallError as e:
                grid["status"] = str(e)
            except Exception as e:                            # offline, DNS, timeout...
                grid["status"] = f"Network problem: {e}"
            self.dirty = True
        spawn(work)

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
        if self.modals:
            self.modal_seen = time.time()          # see board_guard()
        self.zones = []
        self.canvas.fill(DARK)
        n = len(self.players)
        self.seats = layout(n, self.W, self.H, self.settings["seating"])
        for i, seat in enumerate(self.seats):
            self.draw_panel(i, seat)
        gap = max(2, min(self.W, self.H) // 160)
        for seat in self.seats:                   # round table: gaps between the wedges
            if not seat.is_rect:
                pygame.draw.polygon(self.canvas, DARK, seat.poly, 2 * gap)
        self.draw_first_spin()
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
        self.zones.append((hit.collidepoint, lambda: self.push("menu"), None, 0, None))
        self.draw_undo_button(r)
        self.draw_turn(r)
        self.draw_update_badge(c, r)

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

        rot = self.rot()
        out = self.canvas if rot == 0 else pygame.transform.rotate(self.canvas, -rot)
        self.screen.blit(out, (0, 0))
        pygame.display.flip()
        self.dirty = False

    def undo_pos(self, r):
        """The Undo button sits just beside the menu button (towards the screen's middle)."""
        x, y = self.menu_pos(r)
        return (x - int(r * 2.3), y) if x > self.W // 2 or len(self.players) == 1 else (x + int(r * 2.3), y)

    def draw_undo_button(self, r):
        """Round Undo button: a curved arrow; dim when there's nothing to take back.
        Tap: undo the last change. Hold: the history list."""
        c = self.undo_pos(r)
        rr = int(r * 0.8)
        on = bool(self.undo)
        col = (210, 210, 220) if on else (90, 90, 100)
        pygame.draw.circle(self.canvas, (0, 0, 0), (c[0], c[1] + 2), rr + 2)
        pygame.draw.circle(self.canvas, (24, 24, 32), c, rr)
        pygame.draw.circle(self.canvas, col, c, rr, 2)
        a = rr // 2                                  # arrow: a 3/4 arc with a head at its top-left
        box = pygame.Rect(c[0] - a, c[1] - a, 2 * a, 2 * a)
        pygame.draw.arc(self.canvas, col, box, math.radians(-120), math.radians(150), max(2, rr // 8))
        tip = (c[0] - int(a * math.cos(math.radians(30))), c[1] - int(a * math.sin(math.radians(30))))
        k = max(4, rr // 3)
        pygame.draw.polygon(self.canvas, col, [(tip[0] - k, tip[1] - k // 3), (tip[0] + k // 2, tip[1] - k),
                                               (tip[0] + k // 3, tip[1] + k // 2)])
        hit = pygame.Rect(0, 0, int(rr * 2.4), int(rr * 2.4))
        hit.center = c
        self.zones.append((hit.collidepoint, self.undo_last, lambda: self.push("history"), 10 ** 6, None))

    def menu_pos(self, r):
        """Menu button spot on the row divider, as clear of the (centred) names as possible."""
        n = len(self.players)
        if n == 1:
            return (self.W - r - 10, self.H - r - 10)
        if not all(s.is_rect for s in self.seats):
            return (self.W // 2, self.H // 2)     # round table: where the wedges meet
        # rows (and the sides of an ends layout) meet at H/2; avoid the end seats
        sides = [s.rect for s in self.seats if s.angle in (0, 180)]
        xs = [s.centerx for s in sides]
        lo, hi = min(s.left for s in sides) + r + 4, max(s.right for s in sides) - r - 4
        clear = (hi - lo) * 0.12 + r
        mid = (lo + hi) // 2
        x = min(range(lo, hi + 1, 2), key=lambda x: (-min(clear, min(abs(x - c) for c in xs)), abs(x - mid)))
        return (x, self.H // 2)

    # ------------------------------------------------------------------ updates (installed copies)
    def update_mode(self):
        """How this copy can update itself: "self" (replace the installed file), "git" (a clone:
        git pull), or "" (browser and Docker update their own way; or no write access).
        Worked out once - it can't change while the app runs."""
        if getattr(self, "_update_mode", None) is None:
            mode = ""
            if not WEB and not os.path.exists("/.dockerenv"):
                f = self.install_path
                if any((d / ".git").exists() for d in [f.parent, *f.parents]):
                    mode = "git"
                elif os.access(f, os.W_OK) and os.access(f.parent, os.W_OK):
                    mode = "self"
            self._update_mode = mode
        return self._update_mode

    def update_available(self):
        latest = self.settings["update_latest"]
        return bool(self.update_mode()) and newer_version(latest, VERSION)

    def check_for_update(self, force=False):
        """Ask GitHub for the latest release - at most every UPDATE_EVERY, in the background.
        force (the menu's Check for updates): ask now, even with --no-update-check."""
        if not self.update_mode() or not (self.update_check or force):
            return
        if not force and time.time() - self.settings["update_checked"] < UPDATE_EVERY:
            return

        async def work():
            try:
                rel = json.loads(await fetch_bytes(f"https://api.github.com/repos/{REPO}/releases/latest"))
                tag = rel.get("tag_name", "")
                if parse_version(tag):
                    self.update_found = tag
                else:
                    self.update_check_failed = "no release found"
            except Exception as e:                    # offline: try again next start
                print("update check failed:", e, file=sys.stderr)
                self.update_check_failed = "can't reach GitHub"
            self.dirty = True
        self.update_found, self.update_check_failed = None, None
        spawn(work)

    def update_now(self):
        """Menu > Check for updates: ask GitHub right away and, if there's a newer release,
        install it straight away (same checks as Update; the game is kept)."""
        if self.update_state == "working" or getattr(self, "update_asked", False):
            return
        self.modals.clear()
        self.update_asked = True
        self.say("Checking for updates...")
        self.check_for_update(force=True)

    def tick_update(self):
        """Main thread: record what the check found; finish an update (restart into it)."""
        found = getattr(self, "update_found", None)
        asked = getattr(self, "update_asked", False)
        if found:
            self.update_found = None
            self.settings["update_latest"], self.settings["update_checked"] = found, time.time()
            self.changed()
            if asked:                                 # Check for updates: act on the answer now
                self.update_asked = False
                if self.update_available():
                    self.dismiss_update()
                    self.start_update()
                else:
                    self.say(f"LifePi {VERSION} is the latest version")
            elif self.update_badge():
                self.say(f"LifePi {found.lstrip('v')} is available - open the menu to update")
        failed = getattr(self, "update_check_failed", None)
        if failed:
            self.update_check_failed = None
            if asked:
                self.update_asked = False
                self.say(f"Couldn't check for updates: {failed}")
        if self.update_state == "done":
            self.update_state = ""
            self.say(f"Updated to {self.settings['update_latest'].lstrip('v')} - restarting")
            self.restart_after, self.running = True, False   # main() starts the new version
        elif self.update_state == "failed":
            self.update_state = ""
            self.say(f"Update failed: {self.update_error} - still on {VERSION}")

    def dismiss_update(self):
        self.settings["update_dismissed"] = self.settings["update_latest"]
        self.changed()

    def start_update(self):
        """Install the latest release, then restart into it. The new file has to compile, carry
        the release's version and start like the old one before it replaces anything; the old
        file is kept as <file>.previous."""
        if self.update_state == "working" or not self.update_available():
            return
        tag, mode, f = self.settings["update_latest"], self.update_mode(), self.install_path
        self.modals.clear()
        self.update_state, self.update_error = "working", ""
        self.say(f"Updating to {tag.lstrip('v')}...")

        async def work():
            try:
                if mode == "git":
                    repo = next(d for d in [f.parent, *f.parents] if (d / ".git").exists())
                    r = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"], capture_output=True,
                                       text=True, timeout=120)
                    if r.returncode:
                        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1][:80])
                else:
                    src = await fetch_bytes(f"https://raw.githubusercontent.com/{REPO}/{tag}/lifepi.py")
                    check_release_source(src, tag)
                    tmp = f.with_name(f.name + ".new")
                    tmp.write_bytes(src)
                    os.chmod(tmp, os.stat(f).st_mode)
                    shutil.copy2(f, f.with_name(f.name + ".previous"))
                    os.replace(tmp, f)
                self.update_state = "done"
            except Exception as e:
                self.update_error = str(e) or type(e).__name__
                self.update_state = "failed"
            self.dirty = True
        spawn(work)

    def update_badge(self):
        """Show the gold dot on the menu button: there's an update the menu hasn't shown yet."""
        latest = self.settings["update_latest"]
        return (self.update_available() and self.settings["update_dismissed"] != latest
                and self.update_state != "working")

    def draw_update_badge(self, c, r):
        """Gold dot on the menu button's corner - nothing over the seats. Opening the menu (which
        offers "Update to ...") clears it for that version."""
        if not self.update_badge():
            return
        d = (c[0] + int(r * 0.72), c[1] - int(r * 0.72))
        k = max(6, int(r * 0.34))
        pygame.draw.circle(self.canvas, (0, 0, 0), d, k + 2)
        pygame.draw.circle(self.canvas, (255, 210, 70), d, k)

    def draw_first_spin(self):
        """Random first player: a highlight runs round the table, slows and lands; then
        "Goes first!" shows on that seat, facing its player."""
        a = self.first_anim
        if not a or not self.seats:
            return
        age = time.time() - a["t0"]
        gold = (255, 220, 90)
        if age < a["land"]:
            j = max(k for k, t in enumerate(a["times"]) if t <= age)
            seat = self.seats[a["seq"][j] % len(self.seats)]
            pygame.draw.polygon(self.canvas, gold, seat.poly, max(4, min(self.W, self.H) // 80))
            return
        if not 0 <= self.first < len(self.seats):
            return
        seat = self.seats[self.first]
        pulse = 0.5 + 0.5 * math.sin((age - a["land"]) * 9)
        pygame.draw.polygon(self.canvas, gold, seat.poly, max(4, min(self.W, self.H) // 80) + round(3 * pulse))
        cx, cy, cw, ch = seat.frame
        pill = self.make_pill("Goes first!", max(16, min(cw, ch) * 0.14), fg=(30, 24, 0), bg=gold, alpha=250)
        fade = a["land"] + FIRST_SHOW - age
        if fade < 0.6:
            pill.set_alpha(max(0, int(255 * fade / 0.6)))
        if seat.angle:
            pill = pygame.transform.rotozoom(pill, seat.angle, 1) if seat.angle % 90 else \
                pygame.transform.rotate(pill, seat.angle)
        self.canvas.blit(pill, pill.get_rect(center=(round(cx), round(cy))))

    def draw_drags(self):
        """Seat-swap drag feedback: dim the source, outline the target, float the name."""
        for ptr in self.pointers.values():
            src = ptr.get("drag")
            if src is None or src >= len(self.seats):
                continue
            seat = self.seats[src]
            r = seat.rect
            shade = pygame.Surface(r.size, pygame.SRCALPHA)
            pygame.draw.polygon(shade, (0, 0, 0, 140), [(x - r.x, y - r.y) for x, y in seat.poly])
            self.canvas.blit(shade, r)
            dst = self.seat_at(ptr["pos"])
            if dst is not None and dst != src:
                pygame.draw.polygon(self.canvas, (255, 230, 120), self.seats[dst].poly, 6)
            p = self.players[src]
            size = max(20, min(self.W, self.H) // 12)
            img = self.font(size).render(p["name"], True, WHITE)
            chip = pygame.Surface((img.get_width() + size, int(size * 1.4)))
            chip.fill(PALETTE[p["color"] % len(PALETTE)])
            pygame.draw.rect(chip, (255, 230, 120), chip.get_rect(), 3)
            chip.blit(img, img.get_rect(center=chip.get_rect().center))
            if seat.angle:                       # the chip faces whoever is dragging it
                chip = pygame.transform.rotate(chip, seat.angle)
            self.canvas.blit(chip, chip.get_rect(center=ptr["pos"]))

    # ------------------------------------------------------------------ input
    def to_canvas(self, sx, sy):
        sw, sh = self.screen.get_size()
        return unrotate(int(sx), int(sy), sw, sh, self.rot())

    def pointer_down(self, pid, pos):
        for z in reversed(self.zones):
            if z[0](pos):
                self.pointers[pid] = {"zone": z, "t": time.time(), "held": False, "next": 0, "pos": pos,
                                      "start": pos, "moved": False}
                return

    def pointer_move(self, pid, pos):
        ptr = self.pointers.get(pid)
        if ptr:
            ptr["pos"] = pos
            if ptr.get("drag") is not None:
                self.dirty = True
            elif not ptr["moved"] and math.dist(pos, ptr.get("start", pos)) > self.slop():
                ptr["moved"] = True          # a finger sliding (or a palm brushing) is not a press:
                                             # no tap on release, and a running +/-10 repeat stops

    def pointer_up(self, pid, pos):
        ptr = self.pointers.pop(pid, None)
        if ptr and ptr.get("drag") is not None:
            dst = self.seat_at(pos)
            if dst is not None and dst != ptr["drag"] and not self.modals:
                self.swap_seats(ptr["drag"], dst)
            self.dirty = True
        elif ptr and not ptr["held"] and not ptr.get("moved") and ptr["zone"][1] and ptr["zone"][0](pos):
            ptr["zone"][1]()
            self.dirty = True

    def slop(self):
        """How far a finger may wander and still count as a tap or a hold (6% of the short side)."""
        return max(18, min(self.W, self.H) * 0.06)

    def tick_holds(self):
        now = time.time()
        for ptr in list(self.pointers.values()):
            rect, _, hold, repeat, drag = ptr["zone"]
            if drag is not None:
                if not ptr["held"] and not ptr.get("moved") and now - ptr["t"] >= HOLD_DELAY:
                    ptr["held"], ptr["drag"] = True, drag    # long-press on a name: start drag
                    self.dirty = True
                continue
            if not hold or ptr.get("moved"):
                continue
            if not ptr["held"] and now - ptr["t"] >= HOLD_DELAY:
                ptr["held"], ptr["next"] = True, now
            if ptr["held"] and now >= ptr["next"]:
                hold()
                ptr["next"] = now + repeat
                self.dirty = True

    def board_guard(self):
        """True just after the last sheet closed: a quick second tap (a double tap on Start or
        Done) would otherwise land on whatever seat is underneath and change a life total."""
        return not self.modals and time.time() - getattr(self, "modal_seen", 0) < BOARD_GUARD

    def handle(self, e):
        sw, sh = self.screen.get_size()
        if e.type == pygame.QUIT:
            self.running = False
        elif e.type == pygame.FINGERDOWN:
            pygame.mouse.set_visible(False)
            if not self.board_guard():
                self.pointer_down(("f", e.finger_id), self.to_canvas(e.x * sw, e.y * sh))
        elif e.type == pygame.FINGERMOTION:
            self.pointer_move(("f", e.finger_id), self.to_canvas(e.x * sw, e.y * sh))
        elif e.type == pygame.FINGERUP:
            self.pointer_up(("f", e.finger_id), self.to_canvas(e.x * sw, e.y * sh))
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and not getattr(e, "touch", False):
            if not self.board_guard():
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
        """Desktop main loop."""
        self.begin()
        frames = 0
        try:
            while self.running:
                self.step()
                frames += 1
                if max_frames and frames >= max_frames:
                    break
        finally:                                # also on a crash
            self.end()

    async def run_async(self):
        """Browser main loop: the same frames, handing control back to the page after each."""
        self.begin()
        try:
            while self.running:
                self.step()
                await asyncio.sleep(0)
        finally:
            self.end()

    def begin(self):
        if WEB:
            return
        # docker stop / systemd / shutdown send SIGTERM, a closed terminal SIGHUP, Ctrl+C
        # SIGINT: leave the loop normally so the game is saved instead of dying mid-throttle
        for name in ("SIGTERM", "SIGINT", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), self.stop)

    def step(self):
        """One frame: input, timers, phones, drawing, autosave."""
        for e in pygame.event.get():
            self.handle(e)
        self.tick_holds()
        self.tick_suggest()
        self.tick_remote()
        self.tick_art()
        self.tick_update()
        now = time.time()
        if WEB and now - self.web_checked > 0.5:     # phone rotated / window resized
            self.web_checked = now
            size = web_screen_size()
            # When the phone turns counter-clockwise by a, the browser turns the page's drawing
            # clockwise by a to keep it upright (screen.orientation.angle = a). With auto-rotate
            # off, turn our drawing back counter-clockwise by a - our rotation is clockwise, so -a -
            # and the layout stays fixed to the phone.
            turn = 0 if self.settings["auto_rotate"] else (self.web_angle0 - web_screen_angle()) % 360
            if size != self.web_size or turn != self.web_turn:
                self.web_size, self.web_turn = size, turn
                self.screen = pygame.display.set_mode(size)
                self.make_canvas()
        if self.toast[0] and now - self.toast[1] < 3.2:
            self.dirty = True
        if self.armed and now - self.armed[1] >= ARM_TIME:   # "Tap again" went back to normal
            self.armed = None
            self.dirty = True
        if any(now - fx["t"] < 1.9 for fx in self.fx.values()):
            self.dirty = True
        self.tick_first()
        if self.dirty:
            self.render()
        if self.state_dirty and now - self.last_save > SAVE_INTERVAL:
            self.save_state()
        self.clock.tick(self.args.fps)

    def end(self):
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
    state = await r.json(); trackDeltas(state); trackFirst(state); onState(state);
  } catch (e) { onStatus("offline"); setTimeout(start, 2000); return; }
  es = new EventSource(api("/api/events"));
  es.onopen = () => onStatus("online");
  es.onmessage = (e) => { state = JSON.parse(e.data); trackDeltas(state); trackFirst(state); onStatus("online"); onState(state); };
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

// "Goes first!": flash once when a new game's first player is revealed (not on page load,
// not when seats are swapped later)
let revealedGame = null, firstFlashAt = 0;
const FIRST_FLASH = 3000;
function trackFirst(s) {
  if (s.first < 0) { if (revealedGame === null) revealedGame = -1; return; }
  if (revealedGame !== null && revealedGame !== s.game) {
    firstFlashAt = Date.now(); setTimeout(() => onState(state), FIRST_FLASH + 50);
  }
  revealedGame = s.game;
}
const firstFlash = (seat) => state && state.first === seat && Date.now() - firstFlashAt < FIRST_FLASH;

// every commander at the table: [player, which] - which 2 is a partner (second) commander
const commanders = (s) => s.players.flatMap((q) => q.partner ? [[q, 1], [q, 2]] : [[q, 1]]);
const dmgFrom = (p, src, which) => (which === 2 ? (p.cmd2 || []) : p.cmd)[src.seat] || 0;

// round table: each player's wedge as a clipped background, plus the gaps between wedges
const pct = (poly) => "polygon(" + poly.map(([x, y]) => `${x * 100}% ${y * 100}%`).join(",") + ")";
function wedgeLayers(players, shade, meSeat) {
  const out = [];
  for (const p of players) if (p.poly)
    out.push(el("div", { class: "wedge" + (p.ko ? " dead" : ""), style: Object.assign(cardBg(p, shade), { clipPath: pct(p.poly) }) }));
  if (!out.length) return out;
  const NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "gaps"); svg.setAttribute("viewBox", "0 0 1 1"); svg.setAttribute("preserveAspectRatio", "none");
  for (const p of players) if (p.poly) {
    const g = document.createElementNS(NS, "polygon");
    g.setAttribute("points", p.poly.map((xy) => xy.join(",")).join(" "));
    g.setAttribute("class", p.seat === meSeat ? "me" : "");
    svg.append(g);
  }
  out.push(svg);
  return out;
}
const placeAt = ([x, y, w, h]) => ({ left: x * 100 + "%", top: y * 100 + "%", width: w * 100 + "%", height: h * 100 + "%" });

// The device fits each wedge's upright box for its own (landscape) screen; on a phone that box
// would be stretched thin, so refit it here in this page's pixels (same rules as fit_box).
function inPoly([x, y], poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [x1, y1] = poly[i], [x2, y2] = poly[j];
    if ((y1 > y) !== (y2 > y) && x < x1 + (y - y1) * (x2 - x1) / (y2 - y1)) inside = !inside;
  }
  return inside;
}
const fitCache = new Map();
function wedgeBox(p, W, H) {
  const key = W + "x" + H + JSON.stringify(p.poly);
  if (fitCache.has(key)) return fitCache.get(key);
  const poly = p.poly.map(([x, y]) => [x * W, y * H]), [cx, cy] = poly[0];
  let mx = 0, my = 0;                                   // outward direction: through the wedge's far edge
  for (const [x, y] of poly.slice(1)) { mx += x; my += y; }
  mx = mx / (poly.length - 1) - cx; my = my / (poly.length - 1) - cy;
  const len = Math.hypot(mx, my) || 1, ux = mx / len, uy = my / len;
  let depth = 0;
  while (inPoly([cx + ux * (depth + 2), cy + uy * (depth + 2)], poly) && depth < W + H) depth += 2;
  const probes = [[-.8, -.8], [.8, -.8], [-.8, .8], [.8, .8], [0, -1], [0, 1], [-.92, 0], [.92, 0],
                  [-.6, 1], [.6, 1], [-.6, -1], [.6, -1]];
  let best = null;
  for (let hf = 0.8; hf >= 0.25; hf -= 0.05) {
    for (const asp of [1.3, 1.0, 0.8, 0.6]) {
      const bh = depth * hf, bw = bh * asp;
      if (best && bw * bh <= best[0]) continue;
      for (let df = 0.95; df >= 0.2; df -= 0.05) {
        const bx = cx + ux * depth * df, by = cy + uy * depth * df;
        if (probes.every(([px, py]) => inPoly([bx + px * bw / 2, by + py * bh / 2], poly))) {
          best = [bw * bh, bx - bw / 2, by - bh / 2, bw, bh]; break;
        }
      }
    }
  }
  const box = best ? [best[1] / W, best[2] / H, best[3] / W, best[4] / H] : p.pos;
  fitCache.set(key, box);
  return box;
}
const boxOf = (p, el) => (p.poly && el && el.clientWidth ? wedgeBox(p, el.clientWidth, el.clientHeight) : p.pos);
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
          --accent:#4678c8; --good:#32a050; --bad:#c23b3b; --warn:#ffe678; --amber:#ffbe3c; --me:#4ad8ff;
          --gap:5px; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  /* one fixed screen, like the device: nothing outside the sheets scrolls or zooms */
  html, body { position:fixed; inset:0; margin:0; overflow:hidden; overscroll-behavior:none; touch-action:none;
               background:var(--bg); color:var(--text); user-select:none; -webkit-user-select:none;
               -webkit-touch-callout:none; font:16px/1.3 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  body { display:flex; flex-direction:column; padding:env(safe-area-inset-top) env(safe-area-inset-right)
         env(safe-area-inset-bottom) env(safe-area-inset-left); }
  button { font:inherit; color:inherit; cursor:pointer; touch-action:manipulation; }
  header { flex:none; display:flex; align-items:center; gap:6px; height:54px; padding:0 8px; }
  header b { font-size:17px; }
  #dot { width:9px; height:9px; border-radius:50%; background:var(--bad); flex:none; }
  #dot.on { background:var(--good); }
  .hbtn.rot { margin-left:0; width:44px; padding:0; font-size:20px; flex:none; }
  .hbtn[hidden] { display:none; }
  #offline { flex:none; margin:0 var(--gap) var(--gap); padding:8px 12px; border-radius:12px; background:#5a1f1f;
             color:#fff; font-weight:700; font-size:15px; text-align:center; }
  #offline[hidden] { display:none; }
  body.offline #table { opacity:.45; filter:grayscale(.7); }
  #toast { position:fixed; left:50%; bottom:calc(16px + env(safe-area-inset-bottom)); transform:translateX(-50%); z-index:30;
           max-width:92vw; padding:10px 16px; border-radius:999px; background:#1e1f2a; border:1px solid #4a4c5e;
           font-weight:600; font-size:15px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; pointer-events:none; }
  #toast[hidden] { display:none; }
  .hbtn.rot.on { background:var(--accent); border-color:var(--accent); }
  /* force landscape: on a portrait screen, turn the whole page a quarter turn (works with the
     phone's rotation lock on, and on iPhones, where pages can't lock orientation themselves) */
  @media (orientation: portrait) {
    html.land90 body, html.land270 body { inset:auto; top:0; left:0; width:100vh; width:100dvh; height:100vw;
                                          transform-origin:top left; }
    html.land90 body { transform:rotate(90deg) translateY(-100%); }
    html.land270 body { transform:rotate(-90deg) translateX(-100%); }
  }
  .cmdto { display:flex; align-items:center; gap:10px; margin:2px 2px 6px; }
  .cmdto b { font-size:17px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .cmdto .life2 { margin-left:auto; font-size:34px; font-weight:800; font-variant-numeric:tabular-nums; }
  .cmdto .life2.dead { color:#ff7a7a; }
  .cmdgrid { display:grid; grid-template-columns:repeat(2, 1fr); gap:10px; }
  .ctile { position:relative; aspect-ratio:1.25; border-radius:16px; overflow:hidden; background-size:cover;
           background-position:center; border:3px solid rgba(255,255,255,.35); }
  .ctile.lethal { border-color:#ff4a4a; }
  .ctile.close:not(.lethal) { border-color:var(--amber); }
  .ctile .cnote { font-size:13px; font-weight:800; color:var(--amber); text-shadow:0 1px 3px #000; }
  .ctile.lethal .cnote { color:#ff8a8a; }
  .ctile .face { position:absolute; inset:0; padding:8px 8px 10px; pointer-events:none; color:#fff;
                 display:flex; flex-direction:column; align-items:center; justify-content:space-between; }
  .ctile .half2 { position:absolute; top:0; bottom:0; width:50%; border:0; background:none; color:#fff; padding:0 10px;
                  display:flex; align-items:center; font-size:26px; font-weight:800; text-shadow:0 1px 4px #000;
                  touch-action:manipulation; }
  .ctile .half2.minus { left:0; justify-content:flex-start; }
  .ctile .half2.plus { right:0; justify-content:flex-end; }
  .ctile .half2:active { background:rgba(255,255,255,.14); }
  .p2 { display:inline-flex; align-items:center; justify-content:center; width:1.35em; height:1.35em; border-radius:50%;
        background:#ffdc5a; color:#1e1800; font-weight:800; font-size:.7em; margin-left:.3em; text-shadow:none; vertical-align:middle; }
  .tile { position:relative; }
  .tile .p2 { position:absolute; top:-.35em; right:-.35em; margin:0; font-size:.55em; }
  .ctile .cname { font-weight:700; font-size:15px; text-shadow:0 1px 3px #000; max-width:100%; overflow:hidden;
                  text-overflow:ellipsis; white-space:nowrap; }
  .ctile .cname small { font-weight:500; opacity:.8; }
  .ctile .cval { font-size:46px; font-weight:800; line-height:1; text-shadow:0 2px 6px #000; font-variant-numeric:tabular-nums; }
  .ctile.lethal .cval { color:#ff8a8a; }
  .ctile .cplus { font-size:12px; font-weight:700; opacity:.75; text-shadow:0 1px 3px #000; }
  .hbtn { margin-left:auto; height:36px; padding:0 14px; border-radius:999px; border:1px solid var(--line);
          background:#22232f; font-size:14px; font-weight:600; max-width:46vw; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .hbtn + .hbtn { margin-left:0; }
  .hbtn { flex:none; height:44px; }
  #seatbtn { flex:0 1 auto; min-width:0; }       /* the one that gives way on a narrow phone */
  @media (max-width: 420px) { header b { display:none; } .hbtn { padding:0 11px; } }
  #table { position:relative; flex:1; min-height:0; margin:0 var(--gap) var(--gap); }

  .cell { position:absolute; padding:var(--gap); container-type:size; }
  .wedge { position:absolute; inset:0; background-size:cover; background-position:center; }
  .wedge.dead { filter:saturate(.35) brightness(.55); }
  .gaps { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
  .gaps polygon { fill:none; stroke:var(--bg); stroke-width:6px; vector-effect:non-scaling-stroke; stroke-linejoin:round; }
  .gaps polygon.me { stroke:var(--me); stroke-width:3px; }
  .cell.inwedge .panel { background:none !important; box-shadow:none; }
  .cell.inwedge .panel.me { box-shadow:none; }
  .panel { position:relative; width:100%; height:100%; border-radius:16px; overflow:hidden; background-size:cover;
           background-position:center; box-shadow:0 3px 12px rgba(0,0,0,.35); }
  .panel.me { box-shadow:0 0 0 3px var(--me), 0 3px 12px rgba(0,0,0,.35); }
  .strip { position:absolute; left:0; right:0; border:0; background:none; padding:0 6%; display:flex;
           align-items:center; justify-content:center; text-align:center; }
  .strip.top { top:0; height:20%; font-weight:700; font-size:max(13px, min(10cqh, 8cqw)); text-shadow:0 1px 3px rgba(0,0,0,.7); }
  .strip.top span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .strip.top small { color:var(--me); font-weight:700; margin-left:.35em; flex:none; }
  .first1 { flex:none; margin-left:.4em; padding:0 .45em; border-radius:999px; background:#ffdc5a; color:#1e1800;
            font-size:.72em; font-weight:800; line-height:1.5; text-shadow:none; }
  .goes { position:absolute; left:50%; top:50%; transform:translate(-50%, -50%); z-index:2; pointer-events:none;
          padding:.25em .8em; border-radius:999px; background:#ffdc5a; color:#1e1800; font-weight:800; white-space:nowrap;
          font-size:min(12cqh, 9cqw); box-shadow:0 0 0 4px rgba(255,220,90,.35); animation:goes 3s ease-out forwards; }
  @keyframes goes { 0% { transform:translate(-50%, -50%) scale(.6); opacity:0 } 12% { transform:translate(-50%, -50%) scale(1.08); opacity:1 }
                    20%, 80% { transform:translate(-50%, -50%) scale(1); opacity:1 } 100% { opacity:0 } }
  .panel.goesfirst { box-shadow:0 0 0 4px #ffdc5a, 0 3px 12px rgba(0,0,0,.35); }
  .strip.bot { bottom:0; height:22%; gap:min(2cqh, 1.6cqw); flex-wrap:wrap; align-content:center; }
  .half { position:absolute; top:20%; bottom:22%; width:50%; border:0; background:none; padding:0;
          touch-action:none; display:flex; align-items:center; }
  .half.minus { left:0; justify-content:flex-start; padding-left:6%; }
  .half.plus { right:0; justify-content:flex-end; padding-right:6%; }
  .half i { font-style:normal; font-weight:700; font-size:min(16cqh, 11cqw); opacity:.45; line-height:1; }
  .half:active { background:rgba(255,255,255,.07); }
  .life { position:absolute; left:15%; right:15%; top:50%; transform:translateY(-50%); text-align:center; pointer-events:none;
          font-weight:800; line-height:1; font-variant-numeric:tabular-nums;
          font-size:min(46cqh, 34cqw); text-shadow:0 2px 8px rgba(0,0,0,.6);
          /* a line under the number shows which way is up: 6 / 9 / 66 read right upside down */
          text-decoration:underline; text-decoration-color:rgba(255,255,255,.5); text-decoration-thickness:.035em;
          text-underline-offset:.06em; }
  .life.low { color:var(--amber); text-decoration-color:rgba(255,190,60,.6); }
  .delta { position:absolute; left:50%; top:21%; transform:translateX(-50%); pointer-events:none; padding:.1em .6em;
           border-radius:999px; font-weight:800; font-size:min(9cqh, 7cqw); background:var(--good); }
  .delta.neg { background:var(--bad); }
  .ko { position:absolute; left:50%; bottom:24%; transform:translateX(-50%); pointer-events:none; padding:.1em .7em;
        border-radius:999px; background:rgba(0,0,0,.82); border:2px solid #fff; font-weight:800;
        font-size:max(13px, min(8cqh, 6cqw)); white-space:nowrap; }
  .panel.dead > :not(.ko):not(.strip) { opacity:.45; }
  .panel.dead { filter:saturate(.4); }
  .pill { padding:.15em .55em; border-radius:999px; font-weight:700; font-size:max(12px, min(7cqh, 5.2cqw)); white-space:nowrap; }
  .pill.warn { box-shadow:0 0 0 2px var(--amber); }
  .pill.empty { color:#cfd0dc; background:rgba(0,0,0,.28); border:1px solid rgba(255,255,255,.2); font-weight:600; }
  .tile { width:max(26px, min(14cqh, 11cqw)); aspect-ratio:1; border-radius:22%; background-size:cover; background-position:center;
          border:2px solid rgba(255,255,255,.45); display:flex; flex-direction:column; align-items:center; justify-content:center;
          font-weight:800; font-size:max(12px, min(8cqh, 6cqw)); line-height:1; text-shadow:0 1px 4px #000; }
  .tile i { font-style:normal; font-size:.55em; font-weight:700; opacity:.9; margin-top:.1em; }
  .tile.close { border-color:var(--amber) !important; }
  .tile.lethal { border-color:#ff4a4a !important; border-width:3px; }

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
  .stepper .v.close { color:var(--amber); }
  .hist { font-size:14px; }
  .hist .row { min-height:40px; padding:6px 10px; }
  .hist .row .label { white-space:normal; font-weight:500; }
  .hist .row.game .label { color:var(--warn); font-weight:700; }
  .hist .row small { color:var(--dim); flex:none; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; }
  .chips.six { display:grid; grid-template-columns:repeat(4, 1fr); }
  .chips.seat4 { display:grid; grid-template-columns:repeat(2, 1fr); }
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
  .seatpick button.on { border-color:var(--me); }
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
  <button class="hbtn" id="undobtn" type="button" hidden>Undo</button>
  <button class="hbtn" id="seatbtn" type="button">Pick your seat</button>
  <button class="hbtn" id="gamebtn" type="button">Game</button>
  <button class="hbtn rot" id="rotbtn" type="button" aria-label="Turn the screen sideways">↻</button>
</header>
<div id="offline" hidden>Not connected to LifePi - reconnecting...</div>
<main id="table"></main>
<div id="toast" hidden></div>
<div class="sheet" id="sheet" hidden><div class="box" id="box"></div></div>
<div class="overlay" id="pin" hidden><form class="panel2">
  <h3>Join the game</h3>
  <div class="muted" id="pinwhy">Enter the PIN shown on LifePi under Menu &gt; Phones &amp; TV.</div>
  <input inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6-digit PIN">
  <button class="big go">Join</button>
</form></div>
<div class="overlay" id="ask" hidden><form class="panel2">
  <h3 id="asktitle">Rename</h3>
  <input id="askin" maxlength="24" autocomplete="off">
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
const ng = { n: null, life: null, format: null, fresh: false };
let toastTimer = null;
function toast(text) {
  const t = document.getElementById("toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 2500);
}
function undo() {
  if (!state || !state.undo) return;
  toast("Undone: " + state.undo); act({ op: "undo" });
}
// keep the screen on while the page is open (where the browser allows it)
let wake = null;
async function keepAwake() {
  try { if (!wake && navigator.wakeLock && document.visibilityState === "visible") {
    wake = await navigator.wakeLock.request("screen"); wake.addEventListener("release", () => { wake = null; }); } } catch (e) {}
}
addEventListener("pointerdown", keepAwake);
document.addEventListener("visibilitychange", keepAwake);
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
// Every button works this way (not "click"): when other phones are playing, the page redraws
// several times a second, and Safari drops a click if the element under the finger was
// replaced before it lifted. A swipe that turns into a scroll cancels (pointercancel / moved).
const presses = new Map();
function press(e, tap, hold, every) {
  e.preventDefault();
  const pr = { held: false, timer: null, tap, x: e.clientX, y: e.clientY };
  pr.timer = setTimeout(() => {
    if (!hold) return;                       // plain buttons: a long press is still a tap
    pr.held = true;
    hold(); pr.timer = setInterval(hold, every);
  }, 450);
  presses.set(e.pointerId, pr);
}
function release(e, cancel) {
  const pr = presses.get(e.pointerId);
  if (!pr) return;
  presses.delete(e.pointerId); clearTimeout(pr.timer); clearInterval(pr.timer);
  const moved = Math.hypot(e.clientX - pr.x, e.clientY - pr.y) > 16;
  if (!pr.held && !cancel && !moved) pr.tap();
}
addEventListener("pointerup", (e) => release(e, false));
addEventListener("pointercancel", (e) => release(e, true));
function pressable(tag, attrs, tap, hold, every, ...kids) {
  return el(tag, Object.assign({}, attrs, { type: "button", oncontextmenu: (e) => e.preventDefault(),
    onpointerdown: (e) => press(e, tap, hold, every) }), ...kids);
}
const stepBtn = (label, fn) => pressable("button", {}, fn, fn, 150, label);   // sheets: hold repeats the step
const tapBtn = (attrs, fn, ...kids) => pressable("button", attrs, fn, null, 0, ...kids);
function stepper(value, minus, plus, warn, close) {
  return el("div", { class: "stepper" }, stepBtn("−", minus),
            el("span", { class: "v" + (warn ? " warn" : close ? " close" : "") }, value), stepBtn("+", plus));
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
  const seat = p.seat, d = deltaOf(seat), bottom = [], cmdr = state.commander !== false;
  if (cmdr) commanders(state).forEach(([src, which]) => {
    const v = dmgFrom(p, src, which);
    if (v) bottom.push(el("span", { class: "tile" + (v >= 21 ? " lethal" : v >= 15 ? " close" : ""),
      style: Object.assign(cardBg(src, "rgba(0,0,0,.3), rgba(0,0,0,.55)"), { borderColor: src.color }) },
      v, el("i", {}, src.initials || ""), which === 2 ? el("span", { class: "p2" }, "2") : null));
  });
  const poisonKo = state.poison_ko || 10;
  for (const [key] of state.counters)
    if (p.counters[key]) bottom.push(el("span", { class: "pill" + (key === "poison" && p.counters[key] >= poisonKo - 3 ? " warn" : ""),
                                                  style: { background: CHIP[key] || "#444" } },
                                        `${SHORT[key] || key} ${p.counters[key]}`));
  if (!bottom.length) bottom.push(el("span", { class: "pill empty" }, cmdr ? "+ Cmdr dmg" : "+ Counters"));
  const open = () => openSheet({ kind: "player", seat });
  const openCmd = cmdr ? () => openSheet({ kind: "cmd", seat }) : open;
  return el("div", { class: "cell" + (p.poly ? " inwedge" : ""), style: placeAt(boxOf(p, document.getElementById("table"))) },
    el("div", { class: "panel" + (seat === me ? " me" : "") + (p.ko ? " dead" : "") + (firstFlash(seat) ? " goesfirst" : ""),
                style: p.poly ? {} : cardBg(p, "rgba(0,0,0,.6), rgba(0,0,0,.32) 45%, rgba(0,0,0,.66)") },
      tapBtn({ class: "strip top" }, open,
         el("span", {}, p.name), seat === me ? el("small", {}, "you") : null,
         seat === state.first && !p.ko ? el("span", { class: "first1" }, "1st") : null),
      firstFlash(seat) ? el("div", { class: "goes" }, "Goes first!") : null,
      pressable("button", { class: "half minus", "aria-label": "minus" }, () => act({ op: "life", seat, d: -1 }),
                () => act({ op: "life", seat, d: -10 }), 500, el("i", {}, "−")),
      pressable("button", { class: "half plus", "aria-label": "plus" }, () => act({ op: "life", seat, d: 1 }),
                () => act({ op: "life", seat, d: 10 }), 500, el("i", {}, "+")),
      el("div", { class: "life" + (p.life <= 5 && !p.ko ? " low" : "") }, p.life),
      d ? el("div", { class: "delta" + (d < 0 ? " neg" : "") }, fmt(d)) : null,
      p.ko ? el("div", { class: "ko" }, p.ko === "life" ? "KO" : "KO · " + p.ko) : null,
      tapBtn({ class: "strip bot" }, openCmd, bottom)));
}

// ------------------------------------------------------------------ sheets
let sheetSig = "";
function openSheet(s) { sheet = s; sheetSig = ""; render(); document.getElementById("box").scrollTop = 0; }
function closeSheet() { sheet = null; sheetSig = ""; confirmAt = ""; render(); }
function redrawSheet() { sheetSig = ""; render(); }
// tapping the dimmed backdrop closes a sheet - only if the touch also started there: a drag
// from inside the sheet that ends over the backdrop gets its "click" sent to the backdrop
let backdropDown = false;
document.getElementById("sheet").addEventListener("pointerdown", (e) => { backdropDown = e.target.id === "sheet"; });
document.getElementById("sheet").addEventListener("click", (e) => {
  if (e.target.id === "sheet" && backdropDown) closeSheet();
  backdropDown = false;
});

function playerSheet(seat) {
  const s = state, p = s.players[seat];
  return [
    el("div", { class: "shead" }, el("h3", {}, p.name),
       tapBtn({ class: "chipbtn" }, () => ask("Rename", p.name.startsWith("Player ") ? "" : p.name,
                                              (t) => act({ op: "name", seat, name: t })), "Rename"),
       tapBtn({ class: "chipbtn" }, () => act({ op: "color", seat }), "Colour"),
       tapBtn({ class: "chipbtn go" }, closeSheet, "Done")),
    el("div", { class: "lifebig" }, p.life),
    el("div", { class: "steps" }, [-5, -1, 1, 5].map((d) => stepBtn(fmt(d), () => act({ op: "life", seat, d })))),
    el("h4", {}, "Counters"),
    el("div", { class: "rows" }, s.counters.map(([key, label]) =>
      el("div", { class: "row" }, el("span", { class: "label" }, label),
         stepper(p.counters[key], () => act({ op: "counter", seat, key, d: -1 }), () => act({ op: "counter", seat, key, d: 1 }),
                 key === "poison" && p.counters[key] >= (s.poison_ko || 10),
                 key === "poison" && p.counters[key] >= (s.poison_ko || 10) - 3))),
      el("div", { class: "row" }, el("span", { class: "label" }, "Each opponent"),
         stepper("", () => act({ op: "drain", seat, d: -1 }), () => act({ op: "drain", seat, d: 1 })))),
    ...(s.commander === false ? [] : cmdSections(s, p, seat)),
  ];
}

function cmdSections(s, p, seat) {
  return [
    el("h4", {}, "Commander damage taken"),
    el("div", { class: "rows" }, commanders(s).map(([src, which]) =>
      el("div", { class: "row" },
         el("span", { class: "stile", style: cardBg(src, "rgba(0,0,0,.1), rgba(0,0,0,.1)") }),
         el("span", { class: "label" }, src.name, which === 2 ? el("span", { class: "p2" }, "2") : null,
            src.seat === seat ? el("small", {}, " (own)") : null),
         stepper(dmgFrom(p, src, which), () => act({ op: "cmd", seat, src: src.seat, which, d: -1 }),
                 () => act({ op: "cmd", seat, src: src.seat, which, d: 1 }), dmgFrom(p, src, which) >= 21,
                 dmgFrom(p, src, which) >= 15)))),
    el("h4", {}, "Commander"),
    el("div", { class: "rows" },
      tapBtn({ class: "row switch" + (p.partner ? " on" : "") }, () => {
          if (p.partner && confirmAt !== "partner") return arm("partner");
          confirmAt = ""; act({ op: "partner", seat, on: !p.partner });
        },
        el("span", { class: "label" }, p.partner && confirmAt === "partner"
          ? "Tap again to turn partners off (forgets the partner's damage)"
          : "Partner commanders (two commanders, damage tracked separately)"), el("i"))),
  ];
}

function cmdSheet(seat) {
  const s = state, p = s.players[seat];
  const hit = s.cmd_hits_life ? " Damage also comes off life." : "";
  return [
    el("div", { class: "shead" }, el("h3", {}, "Damage"),
       tapBtn({ class: "chipbtn" }, () => openSheet({ kind: "player", seat }), "Counters & more"),
       tapBtn({ class: "chipbtn go" }, closeSheet, "Done")),
    el("div", { class: "cmdto" }, el("span", { class: "stile", style: cardBg(p, "rgba(0,0,0,.1), rgba(0,0,0,.1)") }),
       el("b", {}, "to " + p.name), el("span", { class: "life2" + (p.ko ? " dead" : "") }, p.life)),
    el("p", { class: "muted" }, `Left side \u22121, right side +1, hold to repeat.${hit}`),
    el("div", { class: "cmdgrid" }, commanders(s).map(([src, which]) => {
      const v = dmgFrom(p, src, which);
      const add = () => act({ op: "cmd", seat, src: src.seat, which, d: 1 });
      const sub = () => act({ op: "cmd", seat, src: src.seat, which, d: -1 });
      return dmgTile(src.name, cardBg(src, "rgba(0,0,0,.25), rgba(0,0,0,.6)"), src.color, v, 21, 15,
                     [which === 2 ? el("span", { class: "p2" }, "2") : null, src.seat === seat ? el("small", {}, " (own)") : null],
                     which === 2 ? "partner" : "", sub, add);
    }), (() => {                                   // poison, like Lifetap
      const ko = s.poison_ko || 10, v = p.counters.poison;
      return dmgTile("Poison", { backgroundColor: CHIP.poison }, CHIP.poison, v, ko, ko - 3, [], "",
                     () => act({ op: "counter", seat, key: "poison", d: -1 }), () => act({ op: "counter", seat, key: "poison", d: 1 }));
    })()),
  ];
}

function dmgTile(name, bg, color, v, lethalAt, closeAt, extra, sub2, sub, add) {
  const lethal = v >= lethalAt, close = v >= closeAt;
  const note = lethal ? "LETHAL" : close ? `${lethalAt - v} more is lethal` : sub2;
  return el("div", { class: "ctile" + (lethal ? " lethal" : close ? " close" : ""),
                     style: Object.assign({}, bg, lethal || close ? {} : { borderColor: color }) },
    el("div", { class: "face" },
      el("span", { class: "cname" }, name, ...extra),
      el("span", { class: "cval" }, v), el("span", { class: close ? "cnote" : "cplus" }, note)),
    pressable("button", { class: "half2 minus", "aria-label": "less from " + name }, sub, sub, 220, "−"),
    pressable("button", { class: "half2 plus", "aria-label": "more from " + name }, add, add, 220, "+"));
}

function seatSheet() {
  return [
    el("div", { class: "shead" }, el("h3", {}, "Which seat are you?"), tapBtn({ class: "chipbtn go" }, closeSheet, "Done")),
    el("div", { class: "seatpick" }, state.players.map((p) =>
      tapBtn({ class: p.seat === me ? "on" : "", style: cardBg(p, "rgba(0,0,0,.3), rgba(0,0,0,.5)") },
             () => { setMe(p.seat); closeSheet(); }, p.name)),
      tapBtn({ class: me < 0 ? "on" : "" }, () => { setMe(-1); closeSheet(); }, "Just watching")),
    el("p", { class: "muted" }, "Your seat gets a blue outline. Everyone can still adjust any player."),
  ];
}

function arm(id) {
  confirmAt = id; setTimeout(() => { if (confirmAt === id) { confirmAt = ""; redrawSheet(); } }, 4000); redrawSheet();
}
function confirmBtn(id, label, cls, fn) {
  const armed = confirmAt === id;
  return tapBtn({ class: "big " + cls + (armed ? " armed" : "") }, () => {
    if (armed) { confirmAt = ""; fn(); closeSheet(); return; }
    arm(id);
  }, armed ? "Tap again to confirm" : label);
}
function historySheet() {
  const s = state;
  return [
    el("div", { class: "shead" }, el("h3", {}, "History"), tapBtn({ class: "chipbtn go" }, closeSheet, "Done")),
    s.undo ? tapBtn({ class: "big" }, undo, "Undo: " + s.undo) : el("p", { class: "muted" }, "Nothing to undo."),
    el("h4", {}, "Newest first"),
    historyRows(s),
  ];
}
function historyRows(s) {
  const now = Date.now() / 1000, ago = (t) => now - t < 60 ? "now" : now - t < 3600 ? Math.floor((now - t) / 60) + " min" : "";
  const items = (s.history || []).slice(0, 25);
  return el("div", { class: "rows hist" }, items.length ? items.map(([t, text]) =>
      el("div", { class: "row" + (/^(New game|Game reset)/.test(text) ? " game" : "") },
         el("span", { class: "label" }, text), el("small", {}, ago(t))))
    : el("div", { class: "row" }, el("span", { class: "label" }, "Nothing yet")));
}

function gameSheet() {
  const s = state;
  if (ng.n === null) { ng.n = s.players.length; ng.life = s.start_life; ng.format = s.format || "commander"; ng.fresh = false; }
  const lifeOpts = [20, 30, 40];
  const fmtName = ((s.formats || []).find(([k]) => k === ng.format) || [0, ""])[1];
  return [
    el("div", { class: "shead" }, el("h3", {}, "Game"), tapBtn({ class: "chipbtn go" }, closeSheet, "Done")),
    s.formats ? el("h4", {}, "Format") : null,
    s.formats ? el("div", { class: "chips" }, s.formats.map(([key, label, life]) =>
      tapBtn({ class: ng.format === key ? "on" : "" }, () => {
        ng.format = key; ng.life = life; if (key !== "commander") ng.n = 2; redrawSheet();
      }, label))) : null,
    el("h4", {}, "Players"),
    el("div", { class: "chips six" }, Array.from({ length: s.max_players || 6 }, (_, i) => i + 1).map((n) =>
      tapBtn({ class: ng.n === n ? "on" : "" }, () => { ng.n = n; redrawSheet(); }, n))),
    el("h4", {}, "Starting life"),
    el("div", { class: "chips" }, lifeOpts.map((l) =>
      tapBtn({ class: ng.life === l ? "on" : "" }, () => { ng.life = l; redrawSheet(); }, l)),
      el("input", { type: "number", inputmode: "numeric", min: "1", max: "9999", placeholder: "Other",
                    value: lifeOpts.includes(ng.life) ? null : String(ng.life),
                    onchange: (e) => { const v = parseInt(e.target.value, 10); if (v > 0) ng.life = Math.min(9999, v); redrawSheet(); } })),
    el("div", { class: "rows", style: { marginTop: "10px" } },
      tapBtn({ class: "row switch" + (ng.fresh ? "" : " on") }, () => { ng.fresh = !ng.fresh; redrawSheet(); },
        el("span", { class: "label" }, "Same players (keep names and art)"), el("i"))),
    confirmBtn("new", `Start ${fmtName ? fmtName + ", " : ""}${ng.n} player${ng.n === 1 ? "" : "s"} at ${ng.life}`, "go",
               () => act({ op: "newgame", n: ng.n, life: ng.life, format: ng.format, fresh: ng.fresh })),
    confirmBtn("reset", "Reset this game", "danger", () => act({ op: "reset" })),
    el("p", { class: "muted" }, "Pressed by mistake? Undo (at the top) brings the game back."),
    tapBtn({ class: "big" }, () => openSheet({ kind: "history" }), "History"),
    el("h4", {}, "Seating"),
    el("div", { class: "chips seat4" }, (s.seatings || []).map(([key, label]) =>
      tapBtn({ class: s.seating === key ? "on" : "" }, () => act({ op: "seating", value: key }), label))),
    el("p", { class: "muted" }, "Changes the current game's layout on LifePi and every screen."),
    el("h4", {}, "Rules"),
    el("div", { class: "rows" },
      tapBtn({ class: "row switch" + (s.random_first ? " on" : "") }, () => act({ op: "random_first", on: !s.random_first }),
        el("span", { class: "label" }, "Pick a random first player on new game"), el("i")),
      tapBtn({ class: "row switch" + (s.cmd_hits_life ? " on" : "") }, () => act({ op: "cmd_hits_life", on: !s.cmd_hits_life }),
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
  const ub = document.getElementById("undobtn");
  ub.hidden = !state.undo; ub.title = state.undo ? "Undo: " + state.undo : "";
  // the first time this phone joins: ask which seat is theirs
  if (me < 0 && !sheet && store.get("lifepi-asked", "") !== "1") { store.set("lifepi-asked", "1"); sheet = { kind: "seat" }; }
  document.getElementById("table").replaceChildren(
    ...wedgeLayers(state.players, "rgba(0,0,0,.5), rgba(0,0,0,.5)", me), ...state.players.map(panel));
  const sh = document.getElementById("sheet"), box = document.getElementById("box");
  if (sheet && (sheet.kind === "player" || sheet.kind === "cmd") && !state.players[sheet.seat]) sheet = null;
  sh.hidden = !sheet;
  if (!sheet) { sheetSig = ""; return; }
  // Rebuild the open sheet only when something it shows changed - not on every update
  // from the other phones - so buttons stay put under a finger and scrolling isn't disturbed.
  const sig = sheetKey();
  if (sig === sheetSig) return;
  sheetSig = sig;
  const top = box.scrollTop;
  box.replaceChildren(el("div", { class: "grab" }),
    ...(sheet.kind === "player" ? playerSheet(sheet.seat) : sheet.kind === "cmd" ? cmdSheet(sheet.seat)
        : sheet.kind === "seat" ? seatSheet() : sheet.kind === "history" ? historySheet() : gameSheet()));
  box.scrollTop = top;                   // live updates must not jump the sheet back to the top
}
function sheetKey() {
  const s = state, base = [sheet, confirmAt, ng.n, ng.life, me];
  if (sheet.kind === "game")
    return JSON.stringify([base, ng.format, ng.fresh, s.players.length, s.start_life, s.seating, s.random_first,
                           s.cmd_hits_life, s.max_players, s.format]);
  if (sheet.kind === "history") return JSON.stringify([base, s.undo, s.history]);
  const looks = s.players.map((p) => [p.name, p.color, p.art, p.partner]);
  if (sheet.kind === "seat") return JSON.stringify([base, looks]);
  const p = s.players[sheet.seat];
  return JSON.stringify([base, looks, s.cmd_hits_life, s.format, p.life, p.ko, p.counters, p.cmd, p.cmd2]);
}
document.getElementById("seatbtn").onclick = () => openSheet({ kind: "seat" });
document.getElementById("undobtn").addEventListener("pointerdown", (e) => press(e, undo, null, 0));
addEventListener("resize", () => render());          // wedge boxes depend on the screen shape
document.getElementById("gamebtn").onclick = () => { ng.n = null; openSheet({ kind: "game" }); };
// force landscape: off -> turned one way -> the other way -> off (remembered on this phone)
const TURNS = ["", "land90", "land270"];
let turn = store.get("lifepi-turn", "");
function applyTurn() {
  document.documentElement.classList.remove("land90", "land270");
  if (turn) document.documentElement.classList.add(turn);
  document.getElementById("rotbtn").classList.toggle("on", !!turn);
  render();
}
document.getElementById("rotbtn").onclick = () => {
  turn = TURNS[(TURNS.indexOf(turn) + 1) % TURNS.length]; store.set("lifepi-turn", turn); applyTurn();
};
applyTurn();

onState = render;
onStatus = (what, code) => {
  document.getElementById("dot").classList.toggle("on", what === "online");
  if (what === "online" || what === "offline") {
    document.getElementById("offline").hidden = what === "online";
    document.body.classList.toggle("offline", what === "offline");
  }
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
  .wedge { position:absolute; inset:0; background-size:cover; background-position:center; }
  .wedge.dead { filter:saturate(.3) brightness(.5); }
  .gaps { position:absolute; inset:0; width:100%; height:100%; }
  .gaps polygon { fill:none; stroke:#0d0d12; stroke-width:1.2vmin; vector-effect:non-scaling-stroke; stroke-linejoin:round; }
  .cell.inwedge .card { background:none !important; box-shadow:none; }
  .card { position:relative; width:100%; height:100%; border-radius:2.2vmin; overflow:hidden; background-size:cover;
          background-position:center; display:flex; flex-direction:column; align-items:center; justify-content:center;
          text-align:center; box-shadow:0 1vmin 3vmin rgba(0,0,0,.45); }
  .card.dead { filter:saturate(.3) brightness(.55); }
  .first1 { display:inline-block; margin-left:.35em; padding:0 .45em; border-radius:999px; background:#ffdc5a; color:#1e1800;
            font-size:.7em; font-weight:800; vertical-align:middle; text-shadow:none; }
  .goes { position:absolute; left:50%; top:50%; transform:translate(-50%, -50%); z-index:2; padding:.25em .8em;
          border-radius:999px; background:#ffdc5a; color:#1e1800; font-weight:800; white-space:nowrap;
          font-size:5vmin; font-size:min(12cqh, 9cqw); animation:goes 3s ease-out forwards; }
  @keyframes goes { 0% { transform:translate(-50%, -50%) scale(.6); opacity:0 } 12% { transform:translate(-50%, -50%) scale(1.08); opacity:1 }
                    20%, 80% { transform:translate(-50%, -50%) scale(1); opacity:1 } 100% { opacity:0 } }
  .card.goesfirst { box-shadow:0 0 0 .7vmin #ffdc5a, 0 1vmin 3vmin rgba(0,0,0,.45); }
  .name { position:absolute; top:5%; left:6%; right:6%; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
          font-size:4vmin; font-size:min(10cqh, 7cqw); text-shadow:0 .3vmin 1vmin rgba(0,0,0,.7); }
  .life { font-weight:800; line-height:1; font-variant-numeric:tabular-nums; letter-spacing:-.02em;
          font-size:18vmin; font-size:min(44cqh, 33cqw); text-shadow:0 .6vmin 2vmin rgba(0,0,0,.6); }
  .delta { position:absolute; top:20%; padding:.2em .7em; border-radius:999px; font-weight:800; background:#32a050;
           font-size:3vmin; font-size:min(8cqh, 6cqw); }
  .delta.neg { background:#c23b3b; }
  .ko { margin-top:2%; padding:.15em .8em; border-radius:999px; background:rgba(0,0,0,.82); border:.3vmin solid #fff; font-weight:800;
        font-size:2.6vmin; font-size:min(7cqh, 5cqw); }
  .bottom { position:absolute; bottom:5%; left:4%; right:4%; display:flex; flex-wrap:wrap; justify-content:center;
            align-items:center; gap:1vmin; gap:min(2.5cqh, 1.2vmin); }
  .pill { padding:.2em .7em; border-radius:999px; font-weight:700; font-size:2vmin; font-size:min(5.5cqh, 4cqw); }
  .tile { position:relative; width:5vmin; width:min(14cqh, 11cqw); aspect-ratio:1; border-radius:18%;
          background-size:cover; background-position:center; border:.35vmin solid rgba(255,255,255,.45);
          display:flex; align-items:center; justify-content:center; font-weight:800;
          font-size:2.6vmin; font-size:min(7.5cqh, 6cqw); text-shadow:0 .2vmin .8vmin #000; }
  .tile { flex-direction:column; line-height:1; }
  .tile i { font-style:normal; font-size:.55em; font-weight:700; margin-top:.1em; }
  .tile.close { border-color:#ffbe3c !important; }
  .tile.lethal { border-color:#ff4a4a !important; }
  .life.low { color:#ffbe3c; }
  .p2 { position:absolute; top:-.3em; right:-.3em; width:1.2em; height:1.2em; border-radius:50%; background:#ffdc5a;
        color:#1e1800; font-size:.5em; display:flex; align-items:center; justify-content:center; text-shadow:none; }
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
  document.getElementById("grid").replaceChildren(...wedgeLayers(s.players, "rgba(0,0,0,.45), rgba(0,0,0,.45)", -1),
                                                   ...s.players.map((p) => {
    const d = deltaOf(p.seat);
    const bottom = [];
    if (s.commander !== false) commanders(s).forEach(([src, which]) => {
      const v = dmgFrom(p, src, which);
      if (v) bottom.push(el("div", { class: "tile" + (v >= 21 ? " lethal" : v >= 15 ? " close" : ""),
                                     style: Object.assign(cardBg(src, "rgba(0,0,0,.3), rgba(0,0,0,.55)"),
                                                          { borderColor: src.color }) },
                            v, el("i", {}, src.initials || ""), which === 2 ? el("span", { class: "p2" }, "2") : null));
    });
    for (const [key] of s.counters)
      if (p.counters[key]) bottom.push(el("span", { class: "pill", style: { background: CHIP[key] || "#444" } },
                                          `${SHORT[key] || key} ${p.counters[key]}`));
    // same place as on the device; round tables: upright card inside the player's wedge
    return el("div", { class: "cell" + (p.poly ? " inwedge" : ""), style: placeAt(boxOf(p, document.getElementById("grid"))) },
      el("div", { class: "card" + (p.ko ? " dead" : "") + (firstFlash(p.seat) ? " goesfirst" : ""),
                  style: p.poly ? {} : cardBg(p, "rgba(0,0,0,.62), rgba(0,0,0,.38) 45%, rgba(0,0,0,.72)") },
      el("div", { class: "name" }, p.name, p.seat === s.first && !p.ko ? el("span", { class: "first1" }, "1st") : null),
      firstFlash(p.seat) ? el("div", { class: "goes" }, "Goes first!") : null,
      d ? el("div", { class: "delta" + (d < 0 ? " neg" : "") }, fmt(d)) : null,
      el("div", { class: "life" + (p.life <= 5 && !p.ko ? " low" : "") }, p.life),
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


def web_screen_angle():
    """Browser: how far the page's drawing is turned (clockwise) from the device's natural
    orientation - 90 when the phone is turned 90 degrees counter-clockwise
    (screen.orientation.angle; window.orientation on older iPhones)."""
    import platform
    w = platform.window
    try:
        a = w.screen.orientation.angle
    except Exception:
        a = None
    if a is None or str(a) in ("undefined", "null"):
        try:
            a = w.orientation
        except Exception:
            a = 0
    return as_int(a, 0) % 360 // 90 * 90


def web_screen_size():
    """Browser: framebuffer for the page's current shape (sharper on high-DPI phones, capped
    so drawing stays fast)."""
    import platform
    w, h = int(platform.window.innerWidth), int(platform.window.innerHeight)
    try:                                          # the canvas box: the page minus an iPhone's notch / home bar
        r = platform.document.getElementById("canvas").getBoundingClientRect()
        if int(r.width) >= 100 and int(r.height) >= 100:
            w, h = int(r.width), int(r.height)
    except Exception:
        pass
    scale = min(2.0, float(platform.window.devicePixelRatio or 1), 1600 / max(w, h, 1))
    return max(320, round(w * scale)), max(240, round(h * scale))


async def main_async():
    """Browser entry point (pygbag runs web/main.py, which awaits this)."""
    import types
    import platform
    app = App(types.SimpleNamespace(windowed=None, rotate=None, fps=30, companion_port=None, random_art=True))
    if "selftest" in str(platform.window.location.search):
        spawn(lambda: web_selftest(app))
    await app.run_async()


async def web_selftest(app):
    """?selftest in the page URL (used by tests/web_test.py): exercise Scryfall search and an
    image download through the browser's fetch, report into localStorage."""
    result = {"ok": False}
    try:
        cards, _ = await app.search_cards('!"Sol Ring"')
        art = await fetch_bytes(cards[0]["art"]) if cards else b""
        result = {"ok": bool(cards) and art[:2] == b"\xff\xd8", "cards": len(cards), "art_bytes": len(art),
                  "screen": list(app.screen.get_size())}
    except Exception as e:
        result["error"] = repr(e)
    web_store("selftest", json.dumps(result))


def main():
    ap = argparse.ArgumentParser(description="LifePi - native MTG life counter for Raspberry Pi")
    ap.add_argument("--windowed", metavar="WxH", help="run in a window (e.g. 800x480) instead of fullscreen")
    ap.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), help="set screen rotation and remember it")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--companion-port", type=int, default=COMPANION_PORT, metavar="PORT",
                    help=f"port for the phone / TV web pages (default {COMPANION_PORT})")
    ap.add_argument("--no-companion", action="store_true", help="don't serve the phone / TV web pages")
    ap.add_argument("--no-random-art", action="store_true",
                    help="don't give players without art a random card's art")
    ap.add_argument("--no-update-check", action="store_true",
                    help="don't check GitHub for new releases")
    ap.add_argument("--write-icon", action="store_true",
                    help="install the menu icon and point the menu entry at it, then exit (install.sh)")
    args = ap.parse_args()
    if args.write_icon:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        pygame.display.init()
        pygame.font.init()
        install_desktop_icon()
        print("icon:", ICON_FILE if ICON_FILE.exists() else "not written")
        return
    args.desktop_icon = True
    args.random_art = not args.no_random_art
    args.update_check = not args.no_update_check
    if args.no_companion:
        args.companion_port = None
    app = App(args)
    app.run()
    if app.restart_after:                         # updated: start the new version in this process
        os.execv(sys.executable, [sys.executable, str(app.install_path)] + sys.argv[1:])


if __name__ == "__main__":
    main()

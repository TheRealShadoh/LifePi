# LifePi

[![CI](https://github.com/TheRealShadoh/LifePi/actions/workflows/ci.yml/badge.svg)](https://github.com/TheRealShadoh/LifePi/actions/workflows/ci.yml)

A touch-first **Magic: The Gathering life counter that runs natively on a Raspberry Pi** —
no Android, no Waydroid, no browser. One Python file on top of pygame/SDL2, so it runs on
any ARM (or x86) Linux box.

Built because forcing an Android life counter into landscape under Waydroid is miserable.
LifePi is an **original, clean-room app** — it contains no code or assets from any other
life counter — released under the MIT licence so anyone can use, fork and improve it.

## Features

- 1–6 players; **table seating** (far row flipped 180° so everyone reads their own panel) or **upright** seating for a monitor on a stand
- Tap **−/+** for 1, **hold** for 10; running delta ("−7") while you tap; true multi-touch, so two players can tap at once
- Poison, commander tax, energy, experience; **commander damage per opponent** (optionally also subtracts life); automatic KO dimming at 0 life / 10 poison / 21 commander damage
- **Images:** search any card on Scryfall with the on-screen keyboard and use its art as your background (cached on disk, works offline afterwards), or drop your own PNG/JPGs in `~/.local/share/lifepi/images`
- **Player profiles** – name + art/colour are remembered and reloadable
- Dice (d4–d20), coin flip, random player
- **Manual screen rotation: 0 / 90 / 180 / 270°**, from the menu, the `R` key, or `--rotate`. It is done inside the app (render + touch remap), so it behaves identically on X11, Wayland (labwc/wayfire) and bare KMS/DRM, and doesn't touch the rest of the OS
- Crash-safe: game state autosaves to `~/.config/lifepi/state.json`

## Install (Raspberry Pi OS Bookworm or newer)

```bash
git clone https://github.com/TheRealShadoh/LifePi.git && cd LifePi
./install.sh              # add --autostart to launch at login
~/.local/bin/lifepi
```

Or without installing: `sudo apt install python3-pygame && python3 lifepi.py`.

| Option | Meaning |
|---|---|
| `--rotate 0\|90\|180\|270` | set (and remember) screen rotation |
| `--windowed 800x480` | run in a window for development |
| `--fps 30` | frame cap; the app only redraws when something changes, so idle CPU is near zero |

Keys (optional, a keyboard is never required): `Esc` menu/back · `R` rotate · `Ctrl+Q` quit.

**Pi OS Lite / no desktop:** SDL2's KMSDRM backend works from a console:
`SDL_VIDEODRIVER=kmsdrm python3 lifepi.py`. If touch lands in the wrong place because the
*OS* is also rotating the display, remove the OS rotation and use LifePi's instead.

## Run it in a browser (Docker)

No Pi handy, or want it on a tablet/phone? The container runs the real app on a virtual
display and streams it to any browser with noVNC. Images are multi-arch (amd64 + arm64).

```bash
docker run -d --name lifepi -p 8080:8080 -v lifepi-data:/data ghcr.io/therealshadoh/lifepi-web
# or, from a clone:  docker compose up -d --build
```

Open **http://localhost:8080**. Clicks and touches act as taps (one pointer at a time -
VNC has no multi-touch). `/data` keeps the saved game, profiles, cached art and
`share/lifepi/images` for your own backgrounds.

| Variable | Default | Meaning |
|---|---|---|
| `RESOLUTION` | `1280x720` | virtual screen size; `800x480` mimics the official 7" Pi display |
| `ROTATE` | *(unset)* | start rotated: 0 / 90 / 180 / 270 |
| `VNC_PASSWORD` | *(unset)* | **set this if anyone else can reach the port** - there is no auth otherwise |
| `PORT` | `8080` | HTTP/WebSocket port inside the container |

## Controls

- Middle of a panel: left half −, right half + (hold for ±10)
- Name strip or counter strip of a panel: opens that player's sheet (rename, card art, local image, colour, profiles, counters, commander damage) — oriented toward that player
- Round button in the centre: main menu (new game, reset, dice, rotate, seating)

## Tests

`SDL_VIDEODRIVER=dummy python3 tests/smoke_test.py` — headless; covers rotation/touch mapping, tap/hold, modals, persistence and a mocked Scryfall search. CI runs it on x86_64 and arm64 (the Pi's architecture), plus `tests/live_scryfall.py` against the real API.

## Legal

Unofficial fan content. Not approved/endorsed by Wizards of the Coast; Magic: The Gathering
and card art are © Wizards of the Coast LLC. Card data and images are fetched from the
[Scryfall API](https://scryfall.com/docs/api) on the user's request, rate-limited, cached
locally, never bundled in this repository, and shown with the artist credit next to the art.
LifePi is not affiliated with Scryfall or with any other life-counter app.

## Roadmap / good first issues

Turn timer · Planechase/Archenemy decks (Scryfall `t:plane` / `t:scheme`) · partner commanders (two damage tracks) · life history log · side-seat (90°) layouts for 5–6 players · monarch/initiative markers.

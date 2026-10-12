# LifePi

[![CI](https://github.com/TheRealShadoh/LifePi/actions/workflows/ci.yml/badge.svg)](https://github.com/TheRealShadoh/LifePi/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/TheRealShadoh/LifePi)](https://github.com/TheRealShadoh/LifePi/releases/latest)

A touch-first **Magic: The Gathering life counter that runs natively on a Raspberry Pi** —
no Android, no Waydroid, no browser. One Python file on top of pygame/SDL2, so it runs on
any ARM (or x86) Linux box.

**On a phone or tablet instead?** Open **https://therealshadoh.github.io/LifePi/** and
*Add to Home Screen* - the same app, installable and offline ([details](#on-a-phone-or-tablet-no-pi-needed)).

Built because forcing an Android life counter into landscape under Waydroid is miserable.
LifePi is an **original, clean-room app** — it contains no code or assets from any other
life counter — released under the MIT licence so anyone can use, fork and improve it.

## Features

- 1–8 players, four seating styles (menu > Seating, or pick one in New game):
  - **Table `===`** – players along both long sides, the far row flipped 180° so everyone reads their own panel
  - **Ends `-=-`** – one player at each short end of the table (panels turned 90° to face them), the rest along the sides
  - **Round** – a slice per player from the centre, each turned to face its player, for a round table
  - **Upright** – everything upright, for a monitor on a stand
- Tap **−/+** for 1, **hold** for 10 - anywhere on the left / right half of a panel, the life number included; running delta ("−7") while you tap; true multi-touch, so two players can tap at once
- **Undo + history:** the round ↶ button next to the menu takes back the last change (a run of quick taps counts as one step - even a reset or a new game); hold it, or menu > *History / undo*, for a list of everything that happened this game (saved with the game)
- **No accidents:** *Reset*, *Quit*, *Start* over a game in progress and turning *Partner* off need a second tap ("Tap again to ...")
- **Formats:** Commander (40), 1 v 1 (20, no commander damage) and Two-Headed Giant (30, 15 poison) - picked in New game
- Poison, commander tax, energy, experience; **commander damage per commander, your own included, partners tracked separately** - each source shows on your panel as a mini tile of that player's art (or colour) with the amount and their initials (optionally also subtracts life); warnings in amber before it's too late (life 5 or less, poison 3 from lethal, 15+ commander damage from one commander); automatic KO at 0 life / 10 poison / 21 commander damage, said in words and not just with colour
- **Each opponent ±1** in a player's sheet, for drain effects
- **Commander damage, Lifetap style:** tap the *Cmdr dmg* strip at the bottom of a panel and a tile for every commander pops up (their art, the damage they've dealt) plus a poison tile; left half of a tile −1, right half +1; "3 more is lethal" / "LETHAL" on the tile
- **Images:** search any card on Scryfall with the on-screen keyboard - card names are suggested as you type - and use its art as your background, or drop your own PNG/JPGs in `~/.local/share/lifepi/images`. Searches, suggestions and art are cached on disk, so repeat searches cost no API calls and work offline
- **Random card art:** players without art - at start-up, and new seats whenever a game grows - get a random commander's art from Scryfall (all different, credited). Art you pick is never replaced, a saved profile's art wins, and choosing **Colour** for a player keeps them on a colour. Offline it uses art already on the device, and tries again a minute later if there is none
- **Player profiles** – name + art/colour are remembered and reloadable
- **Random first player** on every new game (on by default; switch it in New game or on a phone): a highlight spins round the table and lands on who goes first, and a "1st" badge stays by their name all game
- Dice (d4–d20), coin flip, planar die, random player
- Player colours chosen to stay distinguishable with colour blindness; a short line under every life total shows which way is up (6 vs 9 from across the table)
- **Phones as controllers:** everyone on the same Wi-Fi scans a QR code and runs their own life, counters and commander damage from their phone - changes show up instantly on the Pi and on every other phone
- **TV view:** a live scoreboard for a big screen - cast it to a Chromecast / Google TV / Nvidia Shield from the menu, open it in a smart-TV browser, or mirror a phone to a Roku
- **Keeps itself up to date** (installed with `install.sh`): an *Update available* bar when there's a new release - one tap updates and restarts, or close it
- **Manual screen rotation: 0 / 90 / 180 / 270°**, from the menu, the `R` key, or `--rotate`. It is done inside the app (render + touch remap), so it behaves identically on X11, Wayland (labwc/wayfire) and bare KMS/DRM, and doesn't touch the rest of the OS
- Survives restarts: life, counters, commander damage, seats, names, art, profiles and settings autosave to `~/.config/lifepi/state.json` within half a second, are flushed to disk (safe against pulling the Pi's plug), saved on shutdown / `docker stop` / Ctrl+C / crashes, and the previous save is kept as `state.json.bak` in case the latest is damaged

## Install (Raspberry Pi OS Bookworm or newer)

```bash
git clone https://github.com/TheRealShadoh/LifePi.git && cd LifePi
./install.sh              # add --autostart to launch at login
~/.local/bin/lifepi
```

Or without installing: `sudo apt install python3-pygame && python3 lifepi.py`.

**Updates:** LifePi checks GitHub for a new release when it starts (at most every 6 hours; skipped when
offline). If there is one, a bar at the bottom says *Update available* - **Update** downloads that release,
checks it (it must compile and carry the release's version), swaps it in (the old file stays as
`lifepi.previous`) and restarts, keeping your game; **x** hides it until the next version (the menu still
offers *Update to ...*). Running from a git clone, Update does `git pull`. Only published releases are
offered. `--no-update-check` turns it off. (Versions before 1.5.0 don't have this: update those once with
`git pull && ./install.sh`.) The phone app and Docker update their own way.

| Option | Meaning |
|---|---|
| `--rotate 0\|90\|180\|270` | set (and remember) screen rotation |
| `--windowed 800x480` | run in a window for development |
| `--fps 30` | frame cap; the app only redraws when something changes, so idle CPU is near zero |
| `--companion-port 8765` | port for the phone / TV pages |
| `--no-companion` | don't serve the phone / TV pages at all |
| `--no-random-art` | leave players without art on their colour instead of a random card's art |
| `--no-update-check` | don't check GitHub for new releases |

Keys (optional, a keyboard is never required): `Esc` menu/back · `R` rotate · `Ctrl+Q` quit.

**Pi OS Lite / no desktop:** SDL2's KMSDRM backend works from a console:
`SDL_VIDEODRIVER=kmsdrm python3 lifepi.py`. If touch lands in the wrong place because the
*OS* is also rotating the display, remove the OS rotation and use LifePi's instead.

## Phones & TV

Open the menu (round button) > **Phones & TV**. It shows a QR code, the address and a 6-digit PIN.

- **Phones / tablets:** scan the QR code (or type the address and PIN). The phone shows the whole
  table, laid out exactly like the Pi (same seats, seating style and player count), on one screen
  with no scrolling: tap a panel's left / right side for −1 / +1, hold for ±10 (the life number too), tap the
  *Cmdr dmg* strip at the bottom for commander damage, tap a name for that player's counters and settings (rename, colour, partner commanders). **↻** turns
  the page sideways (landscape) while the phone stays upright - handy with the rotation lock on. **Pick your seat** (asked the first time a phone joins) gives your panel
  a blue outline; **Undo** takes back the last change; **Game** starts a new game (format, 1–8 players, starting life, same or new players) and holds
  the seating style, the rules (random first player, commander damage reduces life) and the game's history. The screen stays on while the page is open,
  and a banner says so if the phone loses its connection to LifePi. Changes show up instantly on the Pi
  and every other phone. Phones must be on the same network as LifePi; nothing is installed.
- **TV:** the **TV view** address shown there is a live, read-only scoreboard sized for a big screen.
  - *Chromecast, Google TV, Nvidia Shield:* menu > **Cast to TV**, pick the device (needs `python3-pychromecast`,
    which `install.sh` adds).
  - *Fire TV, smart TVs with a browser, a laptop on HDMI:* open the TV view address in the browser.
  - *Roku:* Roku has no web browser, so open the TV view on a phone or PC and use screen mirroring
    (Roku supports Miracast from Android / Windows and AirPlay from Apple devices on most models).
- **Security:** only devices with the PIN can see or change the game, and an address that gets the PIN wrong
  10 times is locked out for a minute - but the traffic is plain HTTP on your local network, so treat the PIN as
  "keeps the next table out", not as real security. **New PIN** disconnects every phone; **Turn off** closes the port.

The QR code needs `python3-qrcode` (also installed by `install.sh`); without it the address and PIN are still shown.

## On a phone or tablet, no Pi needed

The same `lifepi.py` also runs in a phone's browser (compiled to WebAssembly with
[pygbag](https://pypi.org/project/pygbag/)) and installs like an app on Android and iPhone.

1. Open **https://therealshadoh.github.io/LifePi/** on the phone (the first load fetches the
   Python runtime, a few MB).
2. **Add to Home Screen** - iPhone: Safari's Share menu; Android: the browser menu or the install prompt.
3. Open it from the home screen: full screen, and it works offline from then on.

The layout doesn't turn when the phone does (handy for a phone lying on the table); use the menu's
*Rotate screen* to turn it on purpose, or switch *Auto-rotate* on to follow the phone.

Everything on the Pi works there - seating styles, up to 8 players, card art from Scryfall, profiles,
random first player, commander damage and partners - except Phones & TV (a web page can't run a server), casting, and local image
folders. The game is saved in the browser; card art is fetched again if the browser clears its cache.

The site is published by each release (see [Releases](#releases)). To build or host it yourself:

```bash
pip install pygbag==0.9.3 pygame-ce
python web/build.py            # -> build/web/, a static site (also attached to every release as lifepi-web.zip)
python web/build.py --serve    # try it at http://127.0.0.1:8000 (use 127.0.0.1, not localhost: pygbag
                               # treats localhost:8xxx as its own dev server and pygame won't load)
```

Any HTTPS host works; installing and offline mode need HTTPS (or localhost).

## Run it in a browser (Docker)

No Pi handy? The container runs the real desktop app on a virtual display and streams it to any
browser with noVNC - including Phones & TV, which the phone app can't do. Images are multi-arch
(amd64 + arm64): `:latest` follows `main`; each release is also tagged, e.g. `:1.5.0` and `:1.5`.
To update: `docker compose pull && docker compose up -d` (or `--build` from a clone).

```bash
docker run -d --name lifepi -p 8080:8080 -p 8765:8765 -v lifepi-data:/data \
  -e LIFEPI_PUBLIC_URL=http://<this-machine's-LAN-IP>:8765 ghcr.io/therealshadoh/lifepi-web
# or, from a clone:  docker compose up -d --build   (LIFEPI_PORT=8090 to use another host port)
```

Open **http://localhost:8080** - the app fills the page (the full noVNC client is at `/vnc.html` if you need its toolbar). Clicks and touches act as taps (one pointer at a time -
VNC has no multi-touch). `/data` keeps the saved game, profiles, cached art and
`share/lifepi/images` for your own backgrounds.

### Zero-install: GitHub Codespaces

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/TheRealShadoh/LifePi)

Click the badge, wait for the build (~2 min the first time) and LifePi opens in a browser
tab. The forwarded URL (PORTS tab -> LifePi) also works from a **phone** signed in to the
same GitHub account. Stop the codespace when you're done - free accounts get a monthly quota.

| Variable | Default | Meaning |
|---|---|---|
| `RESOLUTION` | `1280x720` | virtual screen size; `800x480` mimics the official 7" Pi display |
| `ROTATE` | *(unset)* | start rotated: 0 / 90 / 180 / 270 |
| `VNC_PASSWORD` | *(unset)* | **set this if anyone else can reach the port** - there is no auth otherwise |
| `PORT` | `8080` | HTTP/WebSocket port inside the container |
| `LIFEPI_PUBLIC_URL` | *(unset)* | address phones should use for the companion, e.g. `http://192.168.1.20:8765` - a container can't see the host's LAN address. Compose publishes it on `LIFEPI_COMPANION_PORT` (default 8765) |

## Controls

- Middle of a panel: left side −, right side + (hold for ±10)
- **Cmdr dmg strip** at the bottom of a panel (or its damage tiles): commander damage, Lifetap style - a tile for every commander with their art, and one for poison; like a life panel, the tile's left half is −1 and its right half +1 (hold to repeat); *Counters & more* opens the full sheet. In formats without commanders the strip opens the player's sheet
- **↶ (next to the menu button):** undo the last change; hold it for the history
- **Partner commanders:** switch *Partner* on in a player's sheet and their second commander gets its own tile (gold "2" badge) - damage from each partner is tracked separately, and 21 from either one is a KO
- **Hold a player's name and drag it** onto another panel to swap seats (commander damage follows the players)
- **Name strip** of a panel: opens that player's sheet, turned toward them - rename, card art, local image, colour,
  profiles, partner on / off, counters, *Each opponent* ±1, and every commander's damage as a list
- Round button: main menu - new game, reset, history / undo, dice / coin, rotate, seating, commander damage rule,
  Phones & TV, Cast to TV, *Update to ...* (when there is one), quit; in the phone app also *Auto-rotate*
- **New game:** format (Commander / 1 v 1 / Two-Headed Giant), 1–8 players, starting life (20 / 30 / 40 / custom), seating style,
  random first player on / off, and *Same players* (keep names and art) or *New players*
- With a keyboard on the card-art search: Tab picks the first suggestion, Enter searches

## Releases

Cut a release by pushing a version tag (bump `VERSION` in `lifepi.py` first):

```bash
git tag v1.6.0 && git push origin v1.6.0
```

[`release.yml`](.github/workflows/release.yml) first runs **every** test - the whole CI suite below
plus the container tests - and publishes only if all of them pass:

- the container image `ghcr.io/therealshadoh/lifepi-web:1.6.0` and `:1.6` (amd64 + arm64)
- a [GitHub Release](https://github.com/TheRealShadoh/LifePi/releases) with release notes and the
  tested phone app attached (`lifepi-web.zip`)
- the phone app at https://therealshadoh.github.io/LifePi/ (GitHub Pages; controlled by the
  repository variable `DEPLOY_PAGES`) - installed phone apps pick it up the next time they're opened
- and from then on, installed Pis show *Update available*

Use tags rather than "Draft a new release" on GitHub - a release made on the website skips the
tests. `:latest` is published from `main` only after CI has passed there. Running the Release
workflow by hand on a branch is a dry run (tests only).

## Tests

All headless; CI runs them on every push and before every release.

| Test | Covers |
|---|---|
| `SDL_VIDEODRIVER=dummy python3 tests/smoke_test.py` | the app itself: rotation and touch mapping, tap / hold, every seating style for 1–8 players (each screen point belongs to one seat, clockwise order, taps on rotated seats), modals and rotated sheets, drag-to-swap, the commander damage pane (halves, hold, rotated, poison tile), partner commanders (separate tracking, KO rules, swaps, saves), undo + history (merged taps, resets, swaps, the button, saved), two-tap confirms (and their time-out), formats, each opponent, the planar die, warnings, random first player (incl. fairness), random card art, search cache and suggestions (mocked Scryfall), the update check (banner, close, bad downloads rejected, update + restart), saving / restart / crash / corrupt-save recovery |
| `SDL_VIDEODRIVER=dummy python3 tests/companion_test.py` | the Phones & TV server: PIN and lock-out, every phone action (undo, each opponent, formats included), bad input, live updates, seat positions matching the Pi, art serving |
| `python tests/phone_test.py` | the phone page (QR code) in headless Chrome on a busy table (another phone tapping every 150 ms): the seat picker on first join, the life number as -/+, starting games (two taps, formats) and changing seating from the phone, scrolling sheets, the commander damage sheet (halves, hold, partner and poison tiles), undo, history, the offline banner, force landscape (needs Chrome) |
| `python web/build.py && python tests/web_test.py` | the phone app end to end in headless Chrome at phone size: boot, real touch input, holds, Scryfall in the browser, reload, the layout staying put when the phone turns, starting offline (needs Chrome and internet) |
| `python3 tests/live_scryfall.py` | the real Scryfall API (non-blocking in CI) |

CI runs the first two on x86_64 and arm64 (the Pi's architecture) with Python 3.11 and 3.13, and
again with Pi OS's own `python3-pygame` package.

## Legal

Unofficial fan content. Not approved/endorsed by Wizards of the Coast; Magic: The Gathering
and card art are © Wizards of the Coast LLC. Card data and images are fetched from the
[Scryfall API](https://scryfall.com/docs/api) on the user's request, rate-limited, cached
locally, never bundled in this repository, and shown with the artist credit next to the art.
LifePi is not affiliated with Scryfall or with any other life-counter app.

## Roadmap / good first issues

Turn timer · Planechase/Archenemy decks (Scryfall `t:plane` / `t:scheme`) · art for a partner commander · monarch/initiative markers · turn tracker · round timer · commander tax per cast · a big "my seat" view on phones.

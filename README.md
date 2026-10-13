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

**Contents:** [Features](#features) · [Install on a Pi](#install-on-a-pi) ·
[Updates](#updates) · [Music (Spotify)](#music-spotify) · [Sounds](#sounds) ·
[TV on the Pi's HDMI](#tv-on-the-pis-hdmi) · [Phones & TV over Wi-Fi](#phones--tv-over-wi-fi) ·
[Phone app](#on-a-phone-or-tablet-no-pi-needed) · [Docker](#run-it-in-a-browser-docker) ·
[Controls](#controls) · [Troubleshooting](#troubleshooting) · [Releases](#releases) · [Tests](#tests)

## Features

**The game**
- 1–8 players, four seating styles (menu > Seating, or pick one in New game):
  - **Table `===`** – players along both long sides, the far row flipped 180° so everyone reads their own panel
  - **Ends `-=-`** – one player at each short end of the table (panels turned 90° to face them), the rest along the sides
  - **Round** – a slice per player from the centre, each turned to face its player, for a round table
  - **Upright** – everything upright, for a monitor on a stand
- Tap **−/+** for 1, **hold** for 10 - anywhere on the left / right half of a panel, the life number included; running delta ("−7") while you tap; true multi-touch, so two players can tap at once
- **Formats:** Commander (40), 1 v 1 (20, no commander damage) and Two-Headed Giant (30, 15 poison) - picked in New game
- Poison, commander tax, energy, experience; **commander damage per commander, your own included, partners tracked separately** - each source shows on your panel as a mini tile of that player's art (or colour) with the amount and their initials (optionally also subtracts life); warnings in amber before it's too late (life 5 or less, poison 3 from lethal, 15+ commander damage from one commander); automatic KO at 0 life / 10 poison / 21 commander damage, said in words and not just with colour; life can go below 0 and back (Platinum Angel, Phyrexian Unlife...) - the KO clears by itself if a player climbs back above 0
- **Commander damage, Lifetap style:** tap the *Cmdr dmg* strip at the bottom of a panel and a tile for every commander pops up (their art, the damage they've dealt) plus a poison tile; left half of a tile −1, right half +1; "3 more is lethal" / "LETHAL" on the tile
- **Each opponent ±1** in a player's sheet, for drain effects
- **Undo + history:** the round ↶ button next to the menu takes back the last change (a run of quick taps counts as one step - even a reset or a new game); hold it, or menu > *History / undo*, for a list of everything that happened this game (saved with the game), including who went first and every turn passed
- **Random first player** on every new game (on by default; switch it in New game or on a phone): a highlight spins round the table and lands on who goes first, a "1st" badge stays by their name all game, and the history notes it
- **Turn tracker** (optional - menu > *Turn tracker*): a blue outline on whoever's turn it is and a *Turn 3 - Ana* pill by the menu button; tap the pill to pass the turn clockwise (knocked-out players are skipped), hold it to go back one. The turn number goes up each time play comes back round to whoever started; passes are in the history, can be undone, follow seat swaps and are saved
- Dice (d4–d20), coin flip, planar die, random player

**Made for a real table**
- **No accidents:** *Reset*, *Quit*, *Start* over a game in progress and turning *Partner* off need a second, deliberate tap ("Tap again to ..." - a quick double tap doesn't count); a finger that slides or a palm that brushes the screen doesn't change anyone's life; a quick extra tap as a sheet closes doesn't land on the seat underneath
- **The menu faces whoever opened it:** the menu button is shared, so the menu opens facing the player on the side the tap came from (reach across from the far side and it opens toward you); *Turn* in its header turns it to the other side, and everything opened from it faces the same way. Player sheets always open facing their player
- Player colours chosen to stay distinguishable with colour blindness; a short line under every life total shows which way is up (6 vs 9 from across the table)
- **Manual screen rotation: 0 / 90 / 180 / 270°**, from the menu, the `R` key, or `--rotate`. It is done inside the app (render + touch remap), so it behaves identically on X11, Wayland (labwc/wayfire) and bare KMS/DRM, and doesn't touch the rest of the OS

**Players & art**
- **Images:** search any card on Scryfall with the on-screen keyboard - card names are suggested as you type, commanders first ("Atra" offers Atraxa, Grand Unifier and Atraxa, Praetors' Voice) - and use its art as your background, or drop your own PNG/JPGs in `~/.local/share/lifepi/images`. Searches, suggestions and art are cached on disk, so repeat searches cost no API calls and work offline. Nothing found? *Search again* takes you back to the keyboard
- **Random card art:** players without art - at start-up, and new seats whenever a game grows - get a random commander's art from Scryfall (all different, credited). Art you pick is never replaced, a saved profile's art wins, and choosing **Colour** for a player keeps them on a colour. Offline it uses art already on the device, and tries again a minute later if there is none
- **Player profiles** – name + art/colour are remembered and reloadable
- **Hold a player's name and drag it** onto another seat to swap seats (their damage, counters and turn go with them)

**Around the table**
- **Music:** the Pi can be a Spotify Connect speaker called *LifePi*; menu > **Music** shows what's playing and has play / pause, skip and volume, plus an optional scrolling now-playing banner ([details](#music-spotify))
- **Damage sounds:** a grunt when someone loses life, a metal clang for commander damage, a bubble for poison, "You lose!" on a knockout (or a soft tock for all); menu > **Sounds** picks which, or off ([details](#sounds))
- **TV on the Pi's HDMI:** plug in a TV and it shows the touchscreen and plays the sound ([details](#tv-on-the-pis-hdmi))
- **Phones as controllers:** everyone on the same Wi-Fi scans a QR code and runs their own life, counters and commander damage from their phone - changes show up instantly on the Pi and on every other phone
- **TV view over Wi-Fi:** a live scoreboard for a big screen - cast it to a Chromecast / Google TV / Nvidia Shield from the menu (and **Stop casting** when you're done), open it in a smart-TV browser, or mirror a phone to a Roku

**Looks after itself**
- **Keeps itself up to date** (installed with `install.sh`): a new release puts a gold dot on the menu button and *Update to ...* in the menu; **Check for updates** asks right away. One tap updates and restarts, keeping your game ([details](#updates))
- **Survives restarts:** life, counters, commander damage, turn, seats, names, art, profiles and settings autosave to `~/.config/lifepi/state.json` within half a second, are flushed to disk (safe against pulling the Pi's plug), saved on shutdown / `docker stop` / Ctrl+C / crashes, and the previous save is kept as `state.json.bak` in case the latest is damaged
- **Light on power:** when nothing is happening it drops to 10 frames a second (about 0.2% of a Pi 4 core); the phone app wakes about 10 times a second instead of 60; the phone page lets the phone's screen sleep after 5 minutes without a tap
- Its own **menu / taskbar icon**, and buttons drawn smooth enough to stay crisp when a TV blows the 7" screen up

## Install on a Pi

Raspberry Pi OS Bookworm or newer (32- or 64-bit). Tested on a Pi 4 with the official 7" touchscreen on
Pi OS Trixie (labwc desktop), with an LG TV on HDMI.

```bash
git clone https://github.com/TheRealShadoh/LifePi.git && cd LifePi
./install.sh --autostart          # options below; they combine
```

| `install.sh` option | What it adds |
|---|---|
| *(none)* | the app (`~/.local/bin/lifepi`), its menu entry and icon, and the optional packages for QR codes and casting |
| `--autostart` | start LifePi when the desktop logs in (kiosk style) |
| `--spotify` | make the Pi a Spotify Connect speaker called *LifePi* for menu > Music ([details](#music-spotify)) |
| `--tv` | when a TV is plugged into HDMI: show the touchscreen on it and play the sound through it ([details](#tv-on-the-pis-hdmi)) |

Run it with `~/.local/bin/lifepi` or from the desktop menu (Games > LifePi). Or without installing:
`sudo apt install python3-pygame && python3 lifepi.py`.

**Uninstall:** `./uninstall.sh` removes the app, its menu entry, icon and autostart, the Spotify speaker and
the TV mirror, and keeps your saved game, profiles and cached art; `./uninstall.sh --purge` removes those too
(and the speaker's settings). Delete the clone yourself.

| Command-line option | Meaning |
|---|---|
| `--rotate 0\|90\|180\|270` | set (and remember) screen rotation |
| `--windowed 800x480` | run in a window for development |
| `--fps 30` | frame rate while something is happening (idle it drops to 10) |
| `--companion-port 8765` | port for the phone / TV pages |
| `--no-companion` | don't serve the phone / TV pages at all |
| `--no-random-art` | leave players without art on their colour instead of a random card's art |
| `--no-update-check` | don't check GitHub for new releases at start-up (menu > *Check for updates* still works) |
| `--write-icon` | install the menu icon and point the menu entry at it, then exit (`install.sh` does this) |

Keys (optional, a keyboard is never required): `Esc` menu/back · `R` rotate · `Ctrl+Q` quit.

**Starting it over SSH** (the Pi is running its normal desktop): the desktop owns the screen, so point
LifePi at it - `WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/$(id -u) ~/.local/bin/lifepi &`.
Without that SDL reports `kmsdrm not available` / `wayland not available`.

**Pi OS Lite / no desktop:** SDL2's KMSDRM backend works from a console:
`SDL_VIDEODRIVER=kmsdrm python3 lifepi.py`. If touch lands in the wrong place because the
*OS* is also rotating the display, remove the OS rotation and use LifePi's instead.

## Updates

LifePi checks GitHub for a new release when it starts (at most every 6 hours; skipped when offline).
If there is one, a short message says so and the menu button gets a gold dot (nothing covers the seats
mid-game); the menu then offers **Update to ...**. **Check for updates** in the menu asks GitHub right away
instead - no 6-hour wait - and if there's a newer release installs it on the spot, or says you're up to date.

An update downloads the release, checks it (it must compile and carry the release's version), swaps it in
(the old file stays as `lifepi.previous`), installs the menu icon if needed, and restarts - keeping your
game. Running from a git clone, it does `git pull` instead. Only published releases are offered.
(Versions before 1.5.0 can't update themselves: update those once with `git pull && ./install.sh`.)
The phone app and Docker update their own way.

## Music (Spotify)

`./install.sh --spotify` turns the Pi into a **Spotify Connect speaker called *LifePi***, using the
open-source [go-librespot](https://github.com/devgianlu/go-librespot) (installed to `~/.local/bin`, run as
the user service `lifepi-spotify`, settings in `~/.config/go-librespot/config.yml`).

- **To play:** in the Spotify app on any phone or computer on the same Wi-Fi, tap the speaker / devices
  icon and choose **LifePi**. Spotify Connect needs **Spotify Premium**.
- **Menu > Music** shows the cover, song, artists and album, with **Prev**, **Play / Pause**, **Next** and
  **Volume − / +**. With nothing playing it tells you how to start; without the speaker installed it says how
  to add it.
- **Now-playing banner** (off by default; **Banner** in the Music sheet): a thin, see-through strip along the
  bottom edge with the song and artists, scrolling when it's too long to fit. It has no buttons of its own -
  taps go straight through to the table underneath - and it hides while a sheet is open, while paused and
  when nothing is playing. With it off, LifePi only talks to the speaker while the Music sheet is open; with
  it on, it checks every few seconds.
- **No account is stored on the Pi** - whoever picks LifePi in their app plays to it, and control stays with
  them in Spotify as well.
- Sound goes to the Pi's default audio output: the headphone jack, a USB / Bluetooth speaker, or the TV
  with [`--tv`](#tv-on-the-pis-hdmi).
- Why not Spotify's Web API? Since February 2026 it needs a Premium developer app limited to a handful of
  users, which can't work for a public project. A Connect speaker needs no developer account at all.

## Sounds

When someone takes damage, LifePi plays a short sound - a different kind for each sort of damage:

| Damage | Sound |
|---|---|
| life lost | a "hurt" grunt (six of them) |
| commander damage | a heavy metal clang (three) |
| poison | a bubble (three) |
| knocked out | "You lose!" (two voices) |

Within each kind it picks at random and never plays the same clip twice in a row. **Menu > Sounds** switches
between **grunts** (the sounds above, the default), **tock** (one short synthesised knock for every kind of
damage, with a click that carries over music) and **off**; each choice plays a sample.

A run of quick taps or a held −10 makes one sound per hit, not one per point; commander damage that also
takes life plays just the clang; the hit that knocks a player out - life to 0 or below, 21 commander damage
or 10 poison - plays "You lose!" instead (a deeper, longer knock in tock style), and further damage to a
player who's already out is an ordinary hit; gaining life or taking damage back is silent. Sounds play on the Pi's
default audio output (the TV with [`--tv`](#tv-on-the-pis-hdmi)); they aren't played in the phone app or in
Docker. All clips are CC0 (public domain), trimmed and levelled, and built into `lifepi.py` so updates bring
them along:
["Hurt Sound Effects" by EZduzziteh](https://opengameart.org/content/hurt-sound-effects) (grunts),
["Impact Sounds" by Kenney](https://kenney.nl/assets/impact-sounds) (clang),
["Bubble Sound Effects" by Brian MacIntosh](https://opengameart.org/content/bubble-sound-effects) (bubbles),
["Voiceover Pack" and "Voiceover Pack: Fighter" by Kenney](https://kenney.nl/assets/voiceover-pack) ("You lose!").

## TV on the Pi's HDMI

`./install.sh --tv` (labwc desktop - Pi OS Bookworm / Trixie) sets the Pi up so that **plugging a TV into
HDMI** does the right thing, at every login and whenever the TV is plugged in or out:

- **The TV shows the touchscreen**, scaled to fit, using [wl-mirror](https://github.com/Ferdi265/wl-mirror).
  Players keep tapping the touchscreen; the TV is for watching. (Expect bars at the sides: the 7" screen is
  squarer than a TV.)
- **The sound goes to the TV** - music and LifePi's sounds. PipeWire only looks for HDMI audio when it
  starts, so a TV plugged in later would otherwise stay silent; the mirror makes it look again and makes the
  TV the default output. Unplug the TV and sound falls back to the Pi's own output.
- **LifePi stays on the touchscreen**, never on the TV (a labwc window rule, added to your existing
  `~/.config/labwc/rc.xml`).

It runs as `~/.local/bin/lifepi-mirror` (from [`tv-mirror.sh`](tv-mirror.sh)). The touchscreen is assumed to
be `DSI-1`; set `LIFEPI_MIRROR_SRC` in the autostart entry for a different one. For a *different* picture on the
TV - a scoreboard made for a big screen - use the TV view below instead.

## Phones & TV over Wi-Fi

Open the menu (round button) > **Phones & TV**. It shows a QR code, the address and a 6-digit PIN.

- **Phones / tablets:** scan the QR code (or type the address and PIN). The phone shows the whole
  table, laid out exactly like the Pi (same seats, seating style and player count), on one screen
  with no scrolling: tap a panel's left / right side for −1 / +1, hold for ±10 (the life number too), tap the
  *Cmdr dmg* strip at the bottom for commander damage, tap a name for that player's counters and settings (rename, colour, partner commanders). **↻** turns
  the page sideways (landscape) while the phone stays upright - handy with the rotation lock on. **Pick your seat** (asked the first time a phone joins) gives your panel
  a blue outline; **Undo** takes back the last change; **Game** starts a new game (format, 1–8 players, starting life, same or new players) and holds
  the seating style, the rules (random first player, commander damage reduces life) and the game's history. The screen stays on while people are using the page (it may sleep after 5 minutes without a tap),
  and a banner says so if the phone loses its connection to LifePi. Changes show up instantly on the Pi
  and every other phone. Phones must be on the same network as LifePi; nothing is installed.
- **TV view:** the **TV view** address shown there is a live, read-only scoreboard sized for a big screen.
  - *Chromecast, Google TV, Nvidia Shield:* menu > **Cast to TV**, pick the device (needs `python3-pychromecast`,
    which `install.sh` adds). The menu then offers **Stop casting**, which closes the TV view and the connection;
    quitting LifePi stops casting too. Nothing Cast-related runs until you open Cast to TV.
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
random first player, the turn tracker, commander damage and partners - except Phones & TV (a web page
can't run a server), casting, Music, sounds and local image folders. The game is saved in the browser; card
art is fetched again if the browser clears its cache. Installing LifePi on a Pi doesn't need this.

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
(amd64 + arm64): `:latest` follows `main`; each release is also tagged with its version, e.g. `:1.9.0`
and `:1.9`. To update: `docker compose pull && docker compose up -d` (or `--build` from a clone).

```bash
docker run -d --name lifepi -p 8080:8080 -p 8765:8765 -v lifepi-data:/data \
  -e LIFEPI_PUBLIC_URL=http://<this-machine's-LAN-IP>:8765 ghcr.io/therealshadoh/lifepi-web
# or, from a clone:  docker compose up -d --build   (LIFEPI_PORT=8090 to use another host port)
```

Open **http://localhost:8080** - the app fills the page (the full noVNC client is at `/vnc.html` if you need its toolbar). Clicks and touches act as taps (one pointer at a time -
VNC has no multi-touch). `/data` keeps the saved game, profiles, cached art and
`share/lifepi/images` for your own backgrounds. There's no sound in the container.

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
- **Turn pill** (turn tracker on): tap to pass the turn, hold to take it back
- **Partner commanders:** switch *Partner* on in a player's sheet and their second commander gets its own tile (gold "2" badge) - damage from each partner is tracked separately, and 21 from either one is a KO
- **Hold a player's name and drag it** onto another panel to swap seats (commander damage follows the players)
- **Name strip** of a panel: opens that player's sheet, turned toward them - rename, card art, local image, colour,
  profiles, partner on / off, counters, *Each opponent* ±1, and every commander's damage as a list
- **Round button:** the main menu, opened facing whoever tapped it (*Turn* in its header turns it) - new game, reset,
  history / undo, dice / coin, rotate, seating, commander damage rule, turn tracker, sounds (grunts / tock / off), Music,
  Phones & TV, Cast to TV / Stop casting, *Update to ...* (when there is one) or *Check for updates*, quit;
  in the phone app also *Auto-rotate*
- **New game:** format (Commander / 1 v 1 / Two-Headed Giant), 1–8 players, starting life (20 / 30 / 40 / custom), seating style,
  random first player on / off, and *Same players* (keep names and art) or *New players*
- With a keyboard on the card-art search: Tab picks the first suggestion, Enter searches

## Troubleshooting

| Problem | Fix |
|---|---|
| `./install.sh: Permission denied` | an old clone lost the file's executable bit - `git pull`, or run `bash install.sh` |
| "Unlock Keyring" / "Default keyring" password prompt | that's **Chromium** (the Pi logs in automatically, so its password keyring stays locked), not LifePi - usually from the phone app installed as a Chromium web app. Use the native *LifePi* menu entry; type your login password or Cancel if it appears |
| `kmsdrm not available` / `wayland not available` when started over SSH | see [Starting it over SSH](#install-on-a-pi) |
| TV shows the desktop, not LifePi | `./install.sh --tv` mirrors the touchscreen onto the TV ([details](#tv-on-the-pis-hdmi)) |
| No sound from the TV | with `--tv` the sound follows the TV when it's plugged in; without it, pick the HDMI output in the desktop's volume menu. PipeWire only finds HDMI audio when it starts - plugging the TV in before booting also works |
| Spotify app doesn't list *LifePi* | the phone must be on the same Wi-Fi as the Pi, the account needs Premium, and the speaker must be running: `systemctl --user status lifepi-spotify` |
| Menu > Music says Spotify isn't set up | run `./install.sh --spotify` |
| No damage sound | menu > Sounds must not be *off*, and the Pi needs an audio output (TV with `--tv`, headphone jack, USB or Bluetooth speaker) |
| Taps land in the wrong place | turn off any rotation set in the OS and use LifePi's own *Rotate screen*; with a TV connected, the touchscreen must stay mapped to `DSI-1` in `~/.config/labwc/rc.xml` (Pi OS does this) |
| Menu opens facing the wrong side | tap the half of the round button nearest you, or use *Turn* in the menu's header |
| A new release doesn't show up | it's checked at most every 6 hours - use menu > *Check for updates* |

## Releases

Cut a release by pushing a version tag (bump `VERSION` in `lifepi.py` first):

```bash
git tag v1.9.2 && git push origin v1.9.2
```

[`release.yml`](.github/workflows/release.yml) first runs **every** test - the whole CI suite below
plus the container tests - and publishes only if all of them pass:

- the container image `ghcr.io/therealshadoh/lifepi-web:<version>` and `:<major.minor>` (amd64 + arm64)
- a [GitHub Release](https://github.com/TheRealShadoh/LifePi/releases) with release notes and the
  tested phone app attached (`lifepi-web.zip`)
- the phone app at https://therealshadoh.github.io/LifePi/ (GitHub Pages; controlled by the
  repository variable `DEPLOY_PAGES`) - installed phone apps pick it up the next time they're opened
- and from then on, installed Pis offer it (gold dot on the menu button, *Update to ...* in the menu)

Use tags rather than "Draft a new release" on GitHub - a release made on the website skips the
tests. `:latest` is published from `main` only after CI has passed there. Running the Release
workflow by hand on a branch is a dry run (tests only).

## Tests

All headless; CI runs them on every push and before every release.

| Test | Covers |
|---|---|
| `SDL_VIDEODRIVER=dummy python3 tests/smoke_test.py` | the app itself: rotation and touch mapping, tap / hold, slides and palm brushes not counting as taps, taps after a sheet closes, every seating style for 1–8 players (each screen point belongs to one seat, clockwise order, taps on rotated seats), modals and rotated sheets, the menu facing whoever opened it, drag-to-swap, the commander damage pane (halves, hold, rotated, poison tile), partner commanders (separate tracking, KO rules, swaps, saves), undo + history (merged taps, resets, swaps, the button, saved), two-tap confirms (double taps don't count, time-out), formats, each opponent, the planar die, warnings, random first player (incl. fairness) and in the history, the turn tracker, random card art, search cache and suggestions incl. commanders first and *Search again* (mocked Scryfall), the damage sounds (grunt / clang / bubble per kind of damage, no clip twice in a row, grunts / tock / off), the knockout sound (life, commander, poison; once), Music against a stand-in speaker, the now-playing banner (no touch zones, see-through, scrolling without full redraws, hidden under sheets and when paused), Stop casting, the idle frame rate, the desktop icon, the update check (toast + dot, Check for updates, bad downloads rejected, update + restart), saving / restart / crash / corrupt-save recovery, the installers' executable bits |
| `SDL_VIDEODRIVER=dummy python3 tests/companion_test.py` | the Phones & TV server: PIN and lock-out, every phone action (undo, each opponent, formats included), bad input, live updates, seat positions matching the Pi, art serving |
| `python tests/phone_test.py` | the phone page (QR code) in headless Chrome on a busy table (another phone tapping every 150 ms): the seat picker on first join, the life number as -/+, starting games (two taps, formats) and changing seating from the phone, scrolling sheets, the commander damage sheet (halves, hold, partner and poison tiles), undo, history, the offline banner, force landscape (needs Chrome) |
| `python web/build.py && python tests/web_test.py` | the phone app end to end in headless Chrome at phone size: boot, real touch input, holds, Scryfall in the browser, reload, the layout staying put when the phone turns, starting offline (needs Chrome and internet) |
| `python3 tests/live_scryfall.py` | the real Scryfall API (non-blocking in CI) |

CI runs the first two on x86_64 and arm64 (the Pi's architecture) with Python 3.11 and 3.13, and
again with Pi OS's own `python3-pygame` package. New releases are also tried on a real Pi 4 with the 7"
touchscreen, using real touch events.

## Legal

Unofficial fan content. Not approved/endorsed by Wizards of the Coast; Magic: The Gathering
and card art are © Wizards of the Coast LLC. Card data and images are fetched from the
[Scryfall API](https://scryfall.com/docs/api) on the user's request, rate-limited, cached
locally, never bundled in this repository, and shown with the artist credit next to the art.
LifePi is not affiliated with Scryfall, Spotify or any other life-counter app; Spotify is a trademark of
Spotify AB, and the Music feature uses the independent open-source go-librespot. The damage sounds are
CC0 clips by EZduzziteh, Kenney and Brian MacIntosh (see [Sounds](#sounds)).

## Roadmap / good first issues

Turn timer · Planechase/Archenemy decks (Scryfall `t:plane` / `t:scheme`) · art for a partner commander ·
monarch/initiative markers · round timer · commander tax per cast · a big "my seat" view on phones ·
music controls on the phone page · TV mirroring for non-labwc desktops.

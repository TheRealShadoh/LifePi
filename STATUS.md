# LifePi status

The newest dated section doubles as the Google Play release notes (scripts/play_upload.py takes
it, up to 500 characters), so it's written for players.

## 2026-10-26 LifePi 1.17.0

- Custom seating: choose how many players sit on each side of the table and at each end
- Menu > Seating opens a sheet with a picture of the table, so you can see where everyone sits
- Hold a player's name and drag it onto another seat to swap them

### Done

- "custom" style: settings["sides"] = [far, right end, near, left end] (ends up to 3), fitted to the
  player count (fit_sides); sides_layout lays ends as columns (stacked, facing that end) and the long
  sides in the rest (a lone long side gets the full height); layout() key "custom:a,b,c,d"
- Seating sheet (menu > Seating, New game > Custom...): five styles, a scaled preview with names
  facing their way, - n + on each side (move_seat keeps the player count), drag hint
- Saved, restored, undo of a new game / reset restores it; snapshot carries "sides"; phone op "sides"
- Tests: every side combination up to 8 players (coverage, facing, clockwise), taps on 7 custom
  tables, the sheet live and from New game, save, phones; companion test uses seating_key

## 2026-10-17 LifePi 1.16.0

- Swipe typing: slide across the on-screen keyboard to type a word; tap a guess to swap it
- Pi: the TV copy of the screen comes back after an update
- Pi: no more crash when a TV is plugged in or after a reboot; if LifePi ever stops it starts again

### Done

- Swipe decoding (swipe_decode): word must start/end at the finger's ends and pass each letter in order,
  then nearest shape wins with a nudge for common words; vocabulary = player / profile names, words
  from card names seen, then a built-in list. Trail drawn under the finger; up to 3 other guesses
  shown in the text box, tap to swap. Works on rotated sheets
- fit_window full screen: picks the touchscreen again and passes display= (set_mode without it went
  to display 0 = the TV when listed first); only when the screens change, so no resize loop
- A frame that hits a pygame screen error re-fits the window instead of ending the app (30 in a row:
  give up and let the supervisor restart)
- main() runs the app in a child process (supervise): restarts after a crash (logged to
  ~/.config/lifepi/crash.log, gives up after 5 in 2 minutes) and after an update (exit code 75,
  replaces the in-process execv); Quit / SIGTERM / Ctrl+C end both. LIFEPI_NO_SUPERVISOR=1 turns it off
- After an update or crash restart, wl-mirror is restarted (the mirror script brings it back in 3 s)
  and the mirror script is started if it isn't running
- Tests: swipe decode + keyboard swipes at 4 sheet angles, TV plug/unplug display choice, frame
  screen errors, supervisor restart/give-up

## 2026-10-16 LifePi 1.15.0

- Commander damage now lays each player's tile where they sit at the table, on the phone too
- Poison, Rad, Monarch and Initiative buttons in the middle of the commander damage screen
- New: Rad counters, and the Monarch and the Initiative - one holder each, shown on their panel

### Done

- Damage pane: tiles mapped from the seat layout as seen by whoever the pane faces (stretched to fit,
  partners split the seat's tile, round tables get a tile per wedge, nothing overlaps the middle)
- Poison tile gone; middle buttons poison / rad (tap +1, hold -1), monarch / initiative (tap toggles)
- Phone page damage sheet: tiles where players sit, turned (quarter turns, from the seat's facing,
  now in the state as "angle") so the player being hit is at the bottom; text never rotated
- Monarch / initiative: saved, undoable, in history, swap with seats, cleared by a new game, sent to
  phones (op "designate"); phone page and TV view show the chips; phone damage sheet has the buttons
- Tests: smoke (tile positions for 5 layouts x 4 facings, no overlaps, buttons, one holder, save
  repair), phone page, companion

## 2026-10-15 LifePi 1.14.0

- Tap a life total to open that player's commander damage
- Commander damage and poison show a running +3 / -2 while you change them
- The menu and undo buttons always sit in the middle of the screen
- A TV plugged into the Pi at power-on no longer makes the table the TV's size

### Done

- Display: opens on the smallest screen (the touchscreen); $LIFEPI_DISPLAY overrides; redraws when the
  desktop resizes or moves the window
- Life number tap opens commander damage on the Pi, the phone app and the phone page
- Running +/- on the damage pane, the player sheet rows and the phone page's damage tiles
- Menu + undo centred as a pair; names step beside them, or under them on narrow seats
- Bigger menu labels on portrait screens
- Tests: smoke (display choice, resize, centred buttons, deltas, life-number tap), companion, phone page

## 2026-10-14 LifePi for Android

- LifePi on Android: the full life counter for the whole table, free
- Commander damage, partners, poison and counters, card art, undo and history
- Works offline and keeps the screen on; Back closes the open menu
- A small banner only on the menu, history and dice screens; Remove ads turns it off for good

### Done

- Android app (mobile/): Capacitor shell, menu-only AdMob banner, Remove ads purchase, offline runtime
- GitHub Actions: build and emulator test on every push (android.yml)
- Local: helper and person emulators on the PC with host GPU, mobile/local_test.ps1 runs the same test - both pass
- Play: privacy policy live, store listing text, graphics, upload key, scripts/play_upload.py and release.ps1
- Upload key in %USERPROFILE%\dev\keys\lifepi-upload.p12
- AdMob account, app "LifePi - Life Counter" and "Menu banner" ad unit; IDs in dev\keys\lifepi-admob.json
- Google Cloud project lifepi-play: Play Developer API, service account lifepi-play-release (no key),
  Workload Identity Federation for this repo's v* tags; Play Console: it may release LifePi to testing only
- Play Console: app created; all App content done - privacy policy, ads, app access, government,
  financial, health, advertising ID, target audience 18 and over, Data safety, content rating (IARC:
  Teen / 14+ because card art can show fantasy violence)
- Play Console: default store listing (text, icon, feature graphic, 2 screenshots), category
  Entertainment, website https://therealshadoh.github.io/LifePi/
- Menu: Privacy policy button in the Android app (Play wants the policy inside the app)

### Left

- AdMob: add payment (billing) info so Google reviews the account; add the Play store to the app once it's listed
- GitHub secrets ADMOB_APP_ID / ADMOB_BANNER_ID (only if CI should build the real-ads bundle)
- Play Console: public contact email (Store settings), first upload by hand, Remove ads product
- GitHub secrets for tag releases to Play: upload key (base64), its password, ADMOB_APP_ID, ADMOB_BANNER_ID
- Closed test: 12 testers opted in for 14 days in a row, then apply for production

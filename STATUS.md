# LifePi status

The newest dated section doubles as the Google Play release notes (scripts/play_upload.py takes
it, up to 500 characters), so it's written for players.

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

### Left

- Upload key file into %USERPROFILE%\dev\keys\lifepi-upload.p12
- AdMob account and banner ad unit, then %USERPROFILE%\dev\keys\lifepi-admob.json
- Play Console: create the app, first upload by hand, Remove ads product, App content sections
- Google Cloud: enable the Play Developer API, service account lifepi-play-release, key to dev\keys
- Play Console: invite the service account with the two testing permissions
- Closed test: 12 testers opted in for 14 days in a row, then apply for production

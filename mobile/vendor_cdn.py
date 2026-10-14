#!/usr/bin/env python3
"""Copy pygbag's Python runtime into the Android app, so LifePi starts with no internet.

    python web/build.py && python mobile/vendor_cdn.py

The web build loads CPython-in-WebAssembly and pygame from pygbag's CDN when it starts. This
opens the built app in headless Chrome (the same driver as tests/web_test.py), uses it - menu,
dice, history, a Scryfall search - then reads which CDN files the page loaded (the browser's
resource timings plus the service worker's cache) and downloads each into
mobile/android/app/src/main/assets/cdn/<host>/<path>. The app's CdnWebViewClient answers the
page's requests for those URLs from there; anything missing still comes from the network.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import web_test as wt                       # noqa: E402  (Browser, serve, wait_for, BOOTED)

HOSTS = {"pygame-web.github.io"}           # keep in step with CdnWebViewClient.HOSTS
# asked for on a phone but not in a desktop browser (seen offline in the emulator test)
EXTRA = ["https://pygame-web.github.io/cdn/0.9.3/browserfs.min.js",
         "https://pygame-web.github.io/cdn/0.9.3/empty.ogg"]
OUT = ROOT / "mobile" / "android" / "app" / "src" / "main" / "assets" / "cdn"

LIST_URLS = """(async () => {
  const urls = new Set(performance.getEntriesByType("resource").map((e) => e.name));
  try {
    for (const name of await caches.keys()) {
      const c = await caches.open(name);
      for (const r of await c.keys()) urls.add(r.url);
    }
  } catch (e) {}
  return [...urls];
})()"""


def key(b, code):
    """A key press into the game (Escape opens / closes the menu)."""
    for kind in ("keyDown", "keyUp"):
        b.call("Input.dispatchKeyEvent", type=kind, key=code, code=code,
               windowsVirtualKeyCode=27 if code == "Escape" else 0)
    time.sleep(0.8)


def main():
    assert (wt.WEB / "index.html").exists(), "build first: python web/build.py"
    url, httpd = wt.serve()
    b = wt.Browser()
    try:
        b.size(wt.W, wt.H)
        b.call("Page.navigate", url=url + "?selftest")
        wt.wait_for(b, wt.BOOTED, wt.BOOT_TIMEOUT, "the app to boot")
        wt.wait_for(b, "!!localStorage['lifepi-selftest']", 90, "the Scryfall self-test")
        key(b, "Escape")                                     # menu, then close it
        key(b, "Escape")
        b.touch(wt.W * 0.88, wt.H * 0.75)                    # a life change
        time.sleep(3)
        urls = b.js(LIST_URLS) or []
    finally:
        b.close()
        httpd.shutdown()

    def clean(u):
        parts = urllib.parse.urlsplit(u.split("#")[0].split("?")[0])
        return f"{parts.scheme}://{parts.hostname}{re.sub('/+', '/', parts.path)}"
    wanted = sorted({clean(u) for u in list(urls) + EXTRA if urllib.parse.urlsplit(u).hostname in HOSTS})
    assert wanted, "no CDN files seen - did the page load pygbag's runtime?"
    total = 0
    for u in wanted:
        parts = urllib.parse.urlsplit(u)
        dest = OUT / parts.hostname / parts.path.lstrip("/")
        if parts.path.endswith("/"):
            continue                                        # a directory listing, not a file
        req = urllib.request.Request(u, headers={"User-Agent": "LifePi-build"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:                                # the page probes for optional files
                print(f"  (not on the CDN, skipped)  {u}")
                continue
            raise
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        total += len(data)
        print(f"  {len(data) / 1024:8.0f} KB  {u}")
    (OUT / "manifest.json").write_text(json.dumps(wanted, indent=1))
    print(f"vendored {len(wanted)} files, {total / 1e6:.1f} MB -> {OUT}")


if __name__ == "__main__":
    main()

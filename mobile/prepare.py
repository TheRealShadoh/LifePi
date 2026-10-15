#!/usr/bin/env python3
"""Turn the web build (build/web) into the Android app's page (mobile/www). Used by build.sh
(GitHub Actions, Linux) and local_test.ps1 (Windows), after web/build.py and vendor_cdn.py.

  - drops the service worker (the app bundles everything itself)
  - adds the ads / purchase bridge (native.js) and the banner ID from $ADMOB_BANNER_ID
  - stores *.gz files as *.gz.lpz: Android's asset packaging mangles .gz files, and
    CdnWebViewClient serves them under their real names (the game itself is lifepi.tar.gz)
"""
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB, WWW = ROOT / "build" / "web", ROOT / "mobile" / "www"
CDN = ROOT / "mobile" / "android" / "app" / "src" / "main" / "assets" / "cdn"


def main():
    assert (WEB / "index.html").exists(), "build the web app first: python web/build.py"
    shutil.rmtree(WWW, ignore_errors=True)
    shutil.copytree(WEB, WWW)
    shutil.copy(ROOT / "mobile" / "native.js", WWW / "native.js")
    page = WWW / "index.html"
    s = page.read_text(encoding="utf-8")
    s, n = re.subn(r'\s*if \("serviceWorker" in navigator\)[^\n]*\n', "\n", s)
    assert n == 1, "service worker registration not found in index.html"
    cfg = {"bannerId": os.environ.get("ADMOB_BANNER_ID", "")}
    tag = f'<script>window.LIFEPI_ADS = {json.dumps(cfg)};</script>\n<script src="native.js"></script>\n'
    s, n = re.subn(r"(<head[^>]*>)", lambda m: m.group(1) + "\n" + tag, s, count=1, flags=re.I)
    assert n == 1, "no <head> in index.html"
    page.write_text(s, encoding="utf-8")
    (WWW / "sw.js").unlink(missing_ok=True)
    renamed = 0
    for folder in (WWW, CDN):
        for f in folder.rglob("*.gz"):
            f.rename(f.with_name(f.name + ".lpz"))
            renamed += 1
    print("page prepared; ads:", "real banner ID" if cfg["bannerId"] else "Google's sample (test) ads",
          f"- {renamed} .gz file(s) stored as .gz.lpz")


if __name__ == "__main__":
    main()

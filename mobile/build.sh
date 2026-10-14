#!/usr/bin/env bash
# Build LifePi for Android (Google Play). Run from the repo root on a machine with the Android
# SDK, JDK 21, Node, Python + pygbag and Chrome - GitHub Actions does this (.github/workflows/android.yml).
#
#   mobile/build.sh            # -> mobile/android/app/build/outputs/{bundle,apk}/...
#
# Environment (all optional; without them: Google's sample ad IDs and an unsigned release):
#   ADMOB_APP_ID          AdMob app ID       ca-app-pub-XXXXXXXXXXXXXXXX~NNNNNNNNNN
#   ADMOB_BANNER_ID       banner ad unit ID  ca-app-pub-XXXXXXXXXXXXXXXX/NNNNNNNNNN
#   LIFEPI_UPLOAD_KEYSTORE / LIFEPI_UPLOAD_STORE_PASSWORD / LIFEPI_UPLOAD_KEY_ALIAS / LIFEPI_UPLOAD_KEY_PASSWORD
#   LIFEPI_SKIP_WEB=1     reuse build/web and the bundled runtime from an earlier run
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c "import re;print(re.search(r'^VERSION = \"([^\"]+)\"', open('lifepi.py').read(), re.M).group(1))")
IFS=. read -r MA MI PA <<<"$VERSION"
export LIFEPI_VERSION="$VERSION"
export LIFEPI_VERSION_CODE=$(( MA * 1000000 + MI * 1000 + PA ))   # 1.12.1 -> 1012001
echo "LifePi $LIFEPI_VERSION (version code $LIFEPI_VERSION_CODE)"

if [[ "${LIFEPI_SKIP_WEB:-}" != 1 ]]; then
  SDL_VIDEODRIVER=dummy python3 web/build.py
  rm -rf mobile/android/app/src/main/assets/cdn
  python3 mobile/vendor_cdn.py
fi

# the page: the web build, without its service worker (the app bundles everything itself), plus
# the ads / purchase bridge
rm -rf mobile/www && cp -r build/web mobile/www
cp mobile/native.js mobile/www/native.js
python3 - <<'PY'
import json, os, re
p = "mobile/www/index.html"
s = open(p, encoding="utf-8").read()
s, n = re.subn(r'\s*if \("serviceWorker" in navigator\)[^\n]*\n', "\n", s)
assert n == 1, "service worker registration not found in index.html"
cfg = {"bannerId": os.environ.get("ADMOB_BANNER_ID", "")}
tag = f'<script>window.LIFEPI_ADS = {json.dumps(cfg)};</script>\n<script src="native.js"></script>\n'
s, n = re.subn(r"(<head[^>]*>)", lambda m: m.group(1) + "\n" + tag, s, count=1, flags=re.I)
assert n == 1, "no <head> in index.html"
open(p, "w", encoding="utf-8").write(s)
os.remove("mobile/www/sw.js") if os.path.exists("mobile/www/sw.js") else None
print("page prepared; ads:", "real banner ID" if cfg["bannerId"] else "Google's sample (test) ads")
PY

cd mobile
npm ci --no-audit --no-fund
npx cap sync android
cd android
chmod +x gradlew
./gradlew --no-daemon bundleRelease assembleRelease assembleDebug
ls -la app/build/outputs/bundle/release/ app/build/outputs/apk/*/

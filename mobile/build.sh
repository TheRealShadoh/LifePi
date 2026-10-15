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

# the page: the web build plus the ads / purchase bridge (mobile/prepare.py)
python3 mobile/prepare.py

cd mobile
npm ci --no-audit --no-fund
npx cap sync android
cd android
chmod +x gradlew
./gradlew --no-daemon bundleRelease assembleRelease assembleDebug
ls -la app/build/outputs/bundle/release/ app/build/outputs/apk/*/

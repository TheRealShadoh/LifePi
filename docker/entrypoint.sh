#!/bin/sh
# Starts: Xvfb (virtual screen) -> x11vnc -> websockify/noVNC (browser) -> LifePi
set -eu
RESOLUTION="${RESOLUTION:-1280x720}"
PORT="${PORT:-8080}"
NOVNC_DIR="${NOVNC_DIR:-/usr/share/novnc}"
export DISPLAY=:0 SDL_VIDEODRIVER=x11 SDL_AUDIODRIVER=dummy

mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}" "${XDG_CACHE_HOME:-$HOME/.cache}" \
         "${XDG_DATA_HOME:-$HOME/.local/share}/lifepi/images"

rm -f /tmp/.X0-lock
Xvfb :0 -screen 0 "${RESOLUTION}x24" -nolisten tcp >/dev/null 2>&1 &
i=0; until [ -e /tmp/.X11-unix/X0 ]; do i=$((i+1)); [ $i -gt 50 ] && { echo "Xvfb failed"; exit 1; }; sleep 0.1; done

if [ -n "${VNC_PASSWORD:-}" ]; then
  x11vnc -storepasswd "$VNC_PASSWORD" /tmp/vncpass >/dev/null 2>&1
  AUTH="-rfbauth /tmp/vncpass"
else
  AUTH="-nopw"
fi
# shellcheck disable=SC2086
x11vnc -display :0 -forever -shared -quiet -localhost -rfbport 5900 -noxdamage $AUTH >/dev/null 2>&1 &
websockify --web "$NOVNC_DIR" "$PORT" 127.0.0.1:5900 >/dev/null 2>&1 &

echo "LifePi is up: http://localhost:${PORT}  (screen ${RESOLUTION}${ROTATE:+, rotated $ROTATE})"
# Keep the app alive: "Quit" in the menu simply restarts it.
while :; do
  python3 /app/lifepi.py ${ROTATE:+--rotate "$ROTATE"} || true
  sleep 1
done

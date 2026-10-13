#!/bin/bash
# LifePi TV mirror - installed by ./install.sh --tv as ~/.local/bin/lifepi-mirror and started at login.
# When a TV (any HDMI output) is connected: show the touchscreen on it, scaled to fit (wl-mirror),
# and send the sound - LifePi's and the Spotify speaker's - to it. Follows plugging the TV in and out.
# Only one copy runs. LIFEPI_MIRROR_SRC picks the screen to copy (default DSI-1, the Pi touchscreen).
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/lifepi-mirror.lock"
flock -n 9 || exit 0
SRC="${LIFEPI_MIRROR_SRC:-DSI-1}"

hdmi_sink() {   # id of a PipeWire HDMI output, if there is one
  pw-dump 2>/dev/null | python3 -c '
import sys, json
for o in json.load(sys.stdin):
    p = o.get("info", {}).get("props", {})
    if p.get("media.class") == "Audio/Sink" and "hdmi" in p.get("node.name", ""):
        print(o["id"]); break'
}

audio_to_tv() {
  # PipeWire only probes HDMI audio when it starts, so a TV plugged in later has no sound
  # output until the session manager looks again.
  local s=""
  for try in 1 2 3 4 5; do
    s=$(hdmi_sink)
    [ -n "$s" ] && break
    [ "$try" = 2 ] && systemctl --user restart wireplumber
    sleep 3
  done
  [ -n "$s" ] && wpctl set-default "$s"
}

prev=""
while true; do
  OUT=$(wlr-randr 2>/dev/null | awk '/^[^ ]/{o = ($1 ~ /^HDMI/) ? $1 : ""} /Enabled: yes/ && o != "" {print o; exit}')
  if [ -n "$OUT" ] && ! pgrep -u "$(id -u)" -x wl-mirror >/dev/null; then
    wl-mirror --fullscreen-output "$OUT" --scaling fit "$SRC" >/dev/null 2>&1 9>&- &
  fi
  if [ -n "$OUT" ] && [ "$OUT" != "$prev" ]; then
    audio_to_tv 9>&- &
  fi
  prev="$OUT"
  sleep 3
done

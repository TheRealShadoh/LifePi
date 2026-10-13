#!/usr/bin/env bash
# LifePi installer for Raspberry Pi OS (Bookworm/Trixie, 32- or 64-bit).
#   ./install.sh              install
#   ./install.sh --autostart  install and launch at desktop login (kiosk style)
#   ./install.sh --spotify    also make this Pi a Spotify Connect speaker called "LifePi"
#                             (go-librespot; needs Spotify Premium) - menu > Music shows and
#                             controls what's playing
# Options combine: ./install.sh --autostart --spotify
set -euo pipefail
cd "$(dirname "$0")"
autostart=0 spotify=0
for arg in "$@"; do
  case "$arg" in
    --autostart) autostart=1 ;;
    --spotify) spotify=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
sudo apt-get update
sudo apt-get install -y python3-pygame
# optional: QR code for phones to join, casting to Chromecast / Google TV / Shield
sudo apt-get install -y python3-qrcode python3-pychromecast \
  || echo "(optional packages unavailable - phones can still join by typing the address)"
install -Dm755 lifepi.py "$HOME/.local/bin/lifepi"
mkdir -p "$HOME/.local/share/lifepi/images" "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/lifepi.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=LifePi
Comment=MTG life counter
Exec=$HOME/.local/bin/lifepi
Icon=lifepi
StartupWMClass=lifepi
Categories=Game;
DESK
python3 "$HOME/.local/bin/lifepi" --write-icon >/dev/null || echo "(icon not installed - the menu entry uses a generic one)"
if (( autostart )); then
  mkdir -p "$HOME/.config/autostart"
  cp "$HOME/.local/share/applications/lifepi.desktop" "$HOME/.config/autostart/"
  echo "Autostart enabled."
fi

if (( spotify )); then
  case "$(uname -m)" in
    aarch64) arch=arm64 ;;
    armv6l|armv7l) arch=armv6_rpi ;;
    x86_64) arch=x86_64 ;;
    *) echo "Spotify: no go-librespot build for $(uname -m)" >&2; exit 1 ;;
  esac
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "https://github.com/devgianlu/go-librespot/releases/latest/download/go-librespot_linux_${arch}.tar.gz" \
    | tar xz -C "$tmp"
  install -Dm755 "$tmp/go-librespot" "$HOME/.local/bin/go-librespot"
  conf="$HOME/.config/go-librespot"; mkdir -p "$conf"
  if [[ ! -f "$conf/config.yml" ]]; then
    cat > "$conf/config.yml" <<'YML'
# LifePi: this machine shows up as a Spotify Connect speaker called "LifePi".
device_name: LifePi
device_type: speaker
audio_backend: pulseaudio
zeroconf_enabled: true
credentials:
  type: zeroconf
  zeroconf:
    persist_credentials: true
server:
  enabled: true
  address: localhost
  port: 3678
volume_steps: 100
initial_volume: 60
YML
  fi
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/lifepi-spotify.service" <<'UNIT'
[Unit]
Description=LifePi Spotify Connect speaker (go-librespot)
After=pipewire-pulse.service network-online.target

[Service]
ExecStart=%h/.local/bin/go-librespot --config_dir %h/.config/go-librespot
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now lifepi-spotify.service
  echo "Spotify: in the Spotify app (Premium), tap the speaker icon and choose \"LifePi\"."
fi
echo "Done. Run: ~/.local/bin/lifepi   (drop your own backgrounds in ~/.local/share/lifepi/images)"

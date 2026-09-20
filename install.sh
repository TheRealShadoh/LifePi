#!/usr/bin/env bash
# LifePi installer for Raspberry Pi OS (Bookworm/Trixie, 32- or 64-bit).
#   ./install.sh              install
#   ./install.sh --autostart  install and launch at desktop login (kiosk style)
set -euo pipefail
cd "$(dirname "$0")"
sudo apt-get update
sudo apt-get install -y python3-pygame
install -Dm755 lifepi.py "$HOME/.local/bin/lifepi"
mkdir -p "$HOME/.local/share/lifepi/images" "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/lifepi.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=LifePi
Comment=MTG life counter
Exec=$HOME/.local/bin/lifepi
Icon=applications-games
Categories=Game;
DESK
if [[ "${1:-}" == "--autostart" ]]; then
  mkdir -p "$HOME/.config/autostart"
  cp "$HOME/.local/share/applications/lifepi.desktop" "$HOME/.config/autostart/"
  echo "Autostart enabled."
fi
echo "Done. Run: ~/.local/bin/lifepi   (drop your own backgrounds in ~/.local/share/lifepi/images)"

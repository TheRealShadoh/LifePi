#!/usr/bin/env bash
# LifePi uninstaller - undoes install.sh.
#   ./uninstall.sh          remove the app, menu entry and autostart; keep saved games, profiles, art
#   ./uninstall.sh --purge  also delete saved games, profiles, cached card art and your images
# The apt packages (python3-pygame etc.) are left alone - other programs may use them.
set -euo pipefail
purge=0
[[ "${1:-}" == "--purge" ]] && purge=1

pkill -TERM -f "[p]ython3 .*$HOME/.local/bin/lifepi" 2>/dev/null && sleep 1 || true   # it saves on SIGTERM

removed=()
# Spotify speaker (install.sh --spotify)
if [[ -f "$HOME/.config/systemd/user/lifepi-spotify.service" ]]; then
  systemctl --user disable --now lifepi-spotify.service 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/lifepi-spotify.service" && removed+=("lifepi-spotify.service")
  systemctl --user daemon-reload 2>/dev/null || true
fi
pw="$HOME/.config/pipewire/pipewire.conf.d/50-lifepi.conf"
[[ -e "$pw" ]] && rm -f "$pw" && removed+=("$pw")
# TV mirror (install.sh --tv); the labwc window rule is harmless and stays
pkill -f "[l]ifepi-mirror" 2>/dev/null || true
pkill -x wl-mirror 2>/dev/null || true
for f in "$HOME/.local/bin/lifepi-mirror" "$HOME/.config/autostart/lifepi-mirror.desktop"; do
  [[ -e "$f" ]] && rm -f "$f" && removed+=("$f")
done
[[ -e "$HOME/.local/bin/go-librespot" ]] && rm -f "$HOME/.local/bin/go-librespot" && removed+=("$HOME/.local/bin/go-librespot")
for f in "$HOME/.local/bin/lifepi" "$HOME/.local/bin/lifepi.previous" "$HOME/.local/bin/lifepi.new" \
         "$HOME/.local/share/applications/lifepi.desktop" "$HOME/.config/autostart/lifepi.desktop" \
         "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps/lifepi.png"; do
  [[ -e "$f" ]] && rm -f "$f" && removed+=("$f")
done
if (( purge )); then
  for d in "${XDG_CONFIG_HOME:-$HOME/.config}/lifepi" "${XDG_CACHE_HOME:-$HOME/.cache}/lifepi" \
           "${XDG_DATA_HOME:-$HOME/.local/share}/lifepi" "${XDG_CONFIG_HOME:-$HOME/.config}/go-librespot"; do
    [[ -e "$d" ]] && rm -rf "$d" && removed+=("$d")
  done
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

if (( ${#removed[@]} )); then printf 'removed %s\n' "${removed[@]}"; else echo "LifePi was not installed."; fi
if (( ! purge )); then
  echo "Kept saved games, profiles and art in ~/.config/lifepi ~/.cache/lifepi ~/.local/share/lifepi"
  echo "(./uninstall.sh --purge removes them too)."
fi
echo "This folder (the git clone) is yours to delete."

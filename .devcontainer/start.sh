#!/usr/bin/env bash
# Build and start the web container inside the Codespace (runs on every start).
set -e
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
RESOLUTION="${RESOLUTION:-800x480}" docker compose up -d --build
echo
echo "LifePi is running. Open the PORTS tab -> 'LifePi (8080)' -> globe icon."
echo "Phone: open that same URL while signed in to GitHub, or right-click the port ->"
echo "Port Visibility -> Public to share it without a login (set VNC_PASSWORD first)."
echo "After editing lifepi.py:  docker compose up -d --build"

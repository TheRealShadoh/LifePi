# LifePi in a browser: the real app on a virtual display, streamed with noVNC.
# Multi-arch (amd64 / arm64) - also runs on a Raspberry Pi.
#   docker build -t lifepi-web . && docker run --rm -p 8080:8080 -v lifepi-data:/data lifepi-web
#   then open http://localhost:8080
FROM debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-pygame xvfb x11vnc novnc websockify scrot ca-certificates tini procps \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 lifepi && mkdir -p /data /app /tmp/.X11-unix \
 && chown lifepi:lifepi /data && chmod 1777 /tmp/.X11-unix
COPY lifepi.py /app/lifepi.py
COPY docker/entrypoint.sh /app/entrypoint.sh
COPY docker/index.html /usr/share/novnc/index.html
RUN chmod +x /app/entrypoint.sh

USER lifepi
ENV RESOLUTION=1280x720 \
    ROTATE= \
    VNC_PASSWORD= \
    PORT=8080 \
    XDG_CONFIG_HOME=/data/config XDG_CACHE_HOME=/data/cache XDG_DATA_HOME=/data/share
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python3 -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/'%os.environ.get('PORT','8080'),timeout=3)" && pgrep -f lifepi.py >/dev/null
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]

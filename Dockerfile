# MiniPrint in a container:  docker run --rm -p 127.0.0.1:8765:8765 -v miniprint-data:/data ghcr.io/OWNER/miniprint
# then open http://127.0.0.1:8765/ .  See docs/docker.md.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="MiniPrint" \
      org.opencontainers.image.description="Print preparation for miniatures on FDM printers: orientation, tree supports, build plates, 3MF export" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MINIPRINT_HOST=0.0.0.0 \
    MINIPRINT_PORT=8765 \
    MINIPRINT_DATA=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY miniprint ./miniprint
COPY examples ./examples
COPY profiles ./profiles
COPY LICENSE README.md CHANGELOG.md ./

# settings profiles (presets.json) live in /data; run as an ordinary user
RUN useradd --create-home --uid 10001 miniprint && mkdir /data && chown miniprint /data
USER miniprint
VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['MINIPRINT_PORT'] + '/api/health', timeout=4)"

# the GUI by default;  `docker run ... miniprint python -m miniprint model.stl`  runs the command line instead
CMD ["python", "-m", "miniprint.gui", "--no-browser"]

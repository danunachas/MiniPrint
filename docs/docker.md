# MiniPrint in Docker

Useful on Linux and macOS, on a home server or NAS, or when you do not want to install Python. On Windows the
ready-made `MiniPrint.exe` download is simpler.

## Run

    docker run -d --name miniprint -p 127.0.0.1:8765:8765 -v miniprint-data:/data ghcr.io/danunachas/miniprint

then open <http://127.0.0.1:8765/>. Or with the `docker-compose.yml` from this repository:

    docker compose up -d

To build the image yourself: `docker build -t miniprint .` in the repository folder (or
`docker compose up -d --build`).

* **Your settings** (profiles) are kept in the volume `miniprint-data` (`/data` in the container).
* **Models** are not stored: you import STL files in the browser and download the 3MF files there, as usual.
  The project is in memory - it is gone when the container restarts.
* **Updates:** `docker pull ghcr.io/danunachas/miniprint && docker compose up -d` (or re-run `docker run`).

## Using it from other devices

MiniPrint has **no password**. The commands above publish it on `127.0.0.1` only, so only the computer that
runs Docker can open it. To use it from other devices on your home network, publish the port on all
addresses - `-p 8765:8765` - and open `http://<address of that computer>:8765/`. Do that only on a network
where you trust every device, and never forward the port to the internet.

MiniPrint answers requests addressed to an IP address, to a name without a dot (`http://nas:8765/`) and to
`.local` / `.lan` / `.home.arpa` / `.internal` names. If you reach it under another name (a reverse proxy,
your own domain), list it: `-e MINIPRINT_HOSTS=miniprint.example.org`.

## Settings

| variable | default in the image | meaning |
|---|---|---|
| `MINIPRINT_PORT` | `8765` | port inside the container |
| `MINIPRINT_HOST` | `0.0.0.0` | address to listen on inside the container |
| `MINIPRINT_DATA` | `/data` | folder for `presets.json` |
| `MINIPRINT_HOSTS` | *(empty)* | extra host names MiniPrint answers to, comma separated |

## Command line in the container

    docker run --rm -v "$PWD:/work" -w /work ghcr.io/danunachas/miniprint python -m miniprint model.stl -o out

Orientation search and supports are CPU work; give the container several cores and at least 4 GB of memory
for large sculpts.

# Conan Exiles Dedicated Server (Docker)

A clean, minimal Docker image for running a **Conan Exiles Dedicated Server** on Linux.

Built on **Debian Trixie** with the latest **Wine** from WineHQ and **SteamCMD**.

## Features

- Auto-downloads game files on first run (~4.5GB)
- Auto-updates on every restart
- All settings via `.env` file — no config file editing
- PvE / PvP / PvE-C modes
- Optional RCON remote console
- Persistent data via Docker volumes
- Pre-built image — no build needed

---

## Quick Start

**1. Create a new folder and add these two files:**

`docker-compose.yml`:
```yaml
services:
  conan:
    image: ghcr.io/balnaimi/conan-exiles-server:latest
    container_name: conan-exiles
    restart: unless-stopped
    environment:
      - SERVER_NAME=${SERVER_NAME:-Conan Exiles Server}
      - SERVER_PASSWORD=${SERVER_PASSWORD:-}
      - SERVER_TYPE=${SERVER_TYPE:-pve}
      - SERVER_PORT=${SERVER_PORT:-7777}
      - QUERY_PORT=${QUERY_PORT:-27015}
      - MAX_PLAYERS=${MAX_PLAYERS:-40}
      - ADMIN_PASSWORD=${ADMIN_PASSWORD:-}
      - RCON_ENABLED=${RCON_ENABLED:-False}
      - RCON_PASSWORD=${RCON_PASSWORD:-}
      - RCON_PORT=${RCON_PORT:-25575}
      - BATTLEYE_ENABLED=${BATTLEYE_ENABLED:-False}
      - SERVER_REGION=${SERVER_REGION:-1}
      - TZ=${TZ:-UTC}
    ports:
      - "${SERVER_PORT:-7777}:${SERVER_PORT:-7777}/udp"
      - "7778:7778/udp"
      - "${QUERY_PORT:-27015}:${QUERY_PORT:-27015}/udp"
      - "${RCON_PORT:-25575}:${RCON_PORT:-25575}/tcp"
    volumes:
      - game-data:/conanexiles
      - config-data:/conanexiles/ConanSandbox/Saved

volumes:
  game-data:
  config-data:
```

`.env`:
```env
SERVER_NAME=My Conan Server
SERVER_PASSWORD=
SERVER_TYPE=pve
MAX_PLAYERS=40
ADMIN_PASSWORD=changeme
RCON_ENABLED=False
RCON_PASSWORD=changeme
RCON_PORT=25575
SERVER_PORT=7777
QUERY_PORT=27015
BATTLEYE_ENABLED=False
TZ=UTC
```

**2. Edit `.env` with your settings, then run:**

```bash
docker compose up -d
```

**3. Watch the logs (first run takes 10-30 minutes):**

```bash
docker compose logs -f
```

Done. Connect via **Direct Connect** in-game using your server IP and port `7777`.

---

## Or Download the Files

```bash
mkdir conan-server && cd conan-server
curl -O https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/.env.example
nano .env
docker compose up -d
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVER_NAME` | My Conan Server | Server name shown in browser |
| `SERVER_PASSWORD` | *(empty)* | Password to join (empty = public) |
| `SERVER_TYPE` | `pve` | `pve`, `pvp`, or `pve-c` |
| `MAX_PLAYERS` | `40` | Maximum players |
| `ADMIN_PASSWORD` | `changeme` | In-game admin password |
| `RCON_ENABLED` | `False` | Enable remote console |
| `RCON_PASSWORD` | `changeme` | RCON password |
| `RCON_PORT` | `25575` | RCON port |
| `SERVER_PORT` | `7777` | Game port (UDP) |
| `QUERY_PORT` | `27015` | Steam query port (UDP) |
| `BATTLEYE_ENABLED` | `False` | Enable BattlEye anti-cheat |
| `TZ` | `UTC` | Timezone ([list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) |

## Ports

| Port | Protocol | Description |
|------|----------|-------------|
| 7777 | UDP | Game port |
| 7778 | UDP | Game port (raw) |
| 27015 | UDP | Steam query |
| 25575 | TCP | RCON (if enabled) |

---

## Server Management

```bash
# Start
docker compose up -d

# Stop
docker compose down

# Restart
docker compose restart

# View logs
docker compose logs -f

# View last 50 lines
docker compose logs --tail 50
```

---

## First Run

On the first startup the container will:

1. Download Conan Exiles Dedicated Server (~4.5GB) via SteamCMD
2. Initialize Wine prefix
3. Apply your `.env` configuration
4. Start the server

This takes **10-30 minutes** depending on your internet speed.
Subsequent restarts are fast — game files are persisted in Docker volumes.

---

## Connecting

Use **Direct Connect** in Conan Exiles:

- **IP:** Your server's IP address
- **Port:** 7777 (or whatever you set in `SERVER_PORT`)

> **Note:** Conan Exiles does not support hostnames in Direct Connect — use an IP address.

---

## Building from Source

If you want to modify the Dockerfile or entrypoint script:

```bash
git clone https://github.com/balnaimi/conan-exiles-server.git
cd conan-exiles-server
cp .env.example .env
nano .env
docker compose -f docker-compose.build.yml up -d
```

### Files

| File | Description |
|------|-------------|
| `Dockerfile` | Image definition (Debian Trixie + Wine + SteamCMD) |
| `entrypoint.sh` | Startup script (download, configure, run) |
| `docker-compose.yml` | Production compose (pre-built image) |
| `docker-compose.build.yml` | Development compose (builds locally) |
| `.env.example` | Configuration template |

---

## Tech Stack

- **Base:** Debian Trixie (slim)
- **Wine:** Latest stable from WineHQ
- **SteamCMD:** Valve's command-line Steam client
- **Xvfb:** Virtual framebuffer (required by Wine)

## License

MIT

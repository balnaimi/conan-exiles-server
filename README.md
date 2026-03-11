# Conan Exiles Dedicated Server (Docker)

A clean, minimal Docker image for running a **Conan Exiles Dedicated Server** on Linux.

Built on **Debian Trixie** with the latest **Wine** from WineHQ and **SteamCMD**.

## Features

- **Clean & minimal** — no bloat, no unnecessary services
- **Auto-download** — game files are downloaded automatically on first run
- **Auto-update** — checks for updates on every restart
- **Configurable** — all settings via `.env` file
- **PvE / PvP / PvE-C** — choose your server type
- **RCON support** — optional remote console
- **Persistent data** — game data stored in Docker volumes
- **Pre-built image** — pull and run, no build needed

## Quick Start

```bash
# Download the compose file and config
curl -O https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/.env.example

# Edit your settings
nano .env

# Run
docker compose up -d

# Watch logs (first run downloads ~4.5GB)
docker compose logs -f
```

That's it. No cloning, no building.

## Building from Source

If you want to modify the Dockerfile or entrypoint:

```bash
# Clone the repo
git clone https://github.com/balnaimi/conan-exiles-server.git
cd conan-exiles-server

# Configure
cp .env.example .env
nano .env

# Build and run
docker compose -f docker-compose.build.yml up -d
```

## Configuration

Copy `.env.example` to `.env` and edit:

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
| `TZ` | `UTC` | Timezone |

## Ports

| Port | Protocol | Description |
|------|----------|-------------|
| 7777 | UDP | Game port |
| 7778 | UDP | Game port (raw) |
| 27015 | UDP | Steam query |
| 25575 | TCP | RCON (if enabled) |

## Connecting

Use **Direct Connect** in Conan Exiles with your server's IP address and port 7777.

> **Note:** Conan Exiles does not support hostnames — you must use an IP address.

## Commands

```bash
# Start
docker compose up -d

# Stop
docker compose down

# Logs
docker compose logs -f

# Restart
docker compose restart
```

## First Run

The first startup will:

1. Download Conan Exiles Dedicated Server (~4.5GB) via SteamCMD
2. Initialize Wine prefix
3. Apply your configuration from `.env`
4. Start the server

This takes **10-30 minutes** depending on your internet connection. Subsequent starts are fast.

## Tech Stack

- **Base:** Debian Trixie (slim)
- **Wine:** Latest stable from WineHQ
- **SteamCMD:** Valve's command-line Steam client
- **Xvfb:** Virtual framebuffer (required by Wine)

## License

MIT

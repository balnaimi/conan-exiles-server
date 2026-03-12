# ⚔️ Conan Exiles Dedicated Server (Docker)

The most comprehensive Docker image for **Conan Exiles Dedicated Server** — with **236 configurable settings** via a simple `.env` file.

Built on **Debian Trixie** with the latest **Wine** from WineHQ and **SteamCMD**.

<p align="center">
  <a href="https://balnaimi.github.io/conan-exiles-server/"><img src="https://img.shields.io/badge/⚙️_Config_Generator-Open-c8a84e?style=for-the-badge" alt="Config Generator"></a>
  <a href="https://github.com/balnaimi/conan-exiles-server/pkgs/container/conan-exiles-server"><img src="https://img.shields.io/badge/📦_Docker_Image-ghcr.io-blue?style=for-the-badge" alt="Docker Image"></a>
</p>

---

## ✨ Features

- 🚀 Auto-downloads game files on first run (~4.5GB)
- 🔄 Auto-updates on every restart
- ⚙️ **236 settings** via `.env` file — no config file editing needed
- 🌐 **Web-based Config Generator** with sliders and toggles
- 🎮 PvE / PvP / PvE-C modes with per-day schedules
- ⚡ Full Purge, Pet Hunger, Day/Night, Chat, Durability controls
- 🖥️ Optional RCON remote console
- 💾 Persistent data via Docker volumes
- 📦 Pre-built image — no build needed

---

## 🌐 Config Generator

Don't want to edit `.env` files manually? Use our **web-based Config Generator**:

<p align="center">
  <a href="https://balnaimi.github.io/conan-exiles-server/">
    <strong>👉 https://balnaimi.github.io/conan-exiles-server/</strong>
  </a>
</p>

- 🎚️ **Visual sliders** for all multiplier settings
- 🔘 **Toggle switches** for on/off options
- 💡 **Human-readable hints** — see "15 days" instead of "1296000 seconds"
- ✨ **Changed settings highlighted** so you know what you've modified
- 📋 **Copy or download** your `.env` file with one click
- 🌙 Dark gaming theme — looks great on any device

Just configure, generate, and paste into your `.env` file!

---

## 🚀 Quick Start

**1. Create a folder and download the files:**

```bash
mkdir conan-server && cd conan-server
curl -O https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/.env.example
```

**2. Edit your settings:**

```bash
nano .env
```

Or use the [Config Generator](https://balnaimi.github.io/conan-exiles-server/) and paste the output.

**3. Start the server:**

```bash
docker compose up -d
```

**4. Watch the logs (first run takes 10-30 minutes):**

```bash
docker compose logs -f
```

Done! Connect via **Direct Connect** in-game using your server IP and port `7777`.

---

## ⚙️ Configuration

All settings are in the `.env` file. The `.env.example` includes **236 settings** with descriptions.

### Basic Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVER_NAME` | My Conan Server | Server name in browser |
| `SERVER_PASSWORD` | *(empty)* | Join password (empty = public) |
| `SERVER_TYPE` | `pve` | `pve`, `pvp`, or `pve-c` |
| `MAX_PLAYERS` | `40` | Max players |
| `ADMIN_PASSWORD` | `changeme` | Admin password |
| `SERVER_REGION` | `0` | Region (see below) |
| `BATTLEYE_ENABLED` | `False` | BattlEye anti-cheat |
| `TZ` | `UTC` | Timezone |

### 🌍 Server Regions

| Value | Region | Best for |
|-------|--------|----------|
| `0` | EU | Europe, Middle East, Africa |
| `1` | NA | North America |
| `2` | Asia | Asia, Oceania |
| `3` | Oceania | Australia, New Zealand |
| `4` | SA | South America |
| `5` | Japan | Japan |

### 📋 All Setting Categories (20 sections)

| Section | What you can change |
|---------|-------------------|
| 🖥️ **Server Info** | Name, password, type, region, MOTD |
| 🔑 **Admin & RCON** | Admin password, remote console |
| 🌐 **Network** | Ports, AFK kick, ping limit, login queue |
| ❤️ **Player Stats** | Health and stamina pool multipliers |
| 🥤 **Survival** | Hunger & thirst rates (active, idle, offline) |
| 🌅 **Day/Night Cycle** | Day, night, dawn, dusk speed |
| ⚔️ **Combat & Damage** | Player/NPC/structure damage multipliers |
| 💀 **Death & Looting** | Equipment/quickbar/backpack drop, corpse loot |
| 🔨 **Durability** | Tool, weapon, and shield durability |
| 📈 **XP & Progression** | XP multipliers for kill/harvest/craft/time |
| ⛏️ **Harvesting & Crafting** | Resource amounts, spoil rates, fuel, costs |
| 🔧 **Crafting (Extra)** | Thrall training, station speed, knockout time |
| 🏃 **Stamina & Movement** | Stamina drain, walk/sprint speed, health regen |
| 👥 **Thralls & Followers** | Population limits, rescue, corruption |
| 🐾 **Pet & Hunger** | Thrall/pet hunger, starvation, diet |
| 🏗️ **Building & Decay** | Land claim, decay timers, stability |
| 🐉 **NPC & World** | NPC health, respawn, aggro range |
| ⚡ **Purge** | Enable, difficulty, timing, trigger, NPC damage |
| 🗿 **Avatars / Gods** | Summoning, lifetime, protection dome |
| 💭 **Chat** | Message length, local radius, global chat |
| 🛡️ **Clans** | Max clan size |
| 💬 **UI & Social** | Voice chat, player list, events |
| 🌍 **Region Restrictions** | Block players by region |
| ⏰ **PvP Schedule** | Per-day PvP hours and building damage windows |
| 🔧 **Advanced** | Anti-cheat, mods, network rate |

> 💡 **Tip:** Most settings are commented out in `.env.example` with their defaults. Uncomment (remove `#`) to override.

---

## 🔌 Ports

| Port | Protocol | Description |
|------|----------|-------------|
| 7777 | UDP | Game port |
| 7778 | UDP | Game port (raw) |
| 27015 | UDP | Steam query |
| 25575 | TCP | RCON (if enabled) |

Make sure these ports are open in your firewall and forwarded on your router.

---

## 🛠️ Server Management

### Basic Commands

```bash
docker compose up -d          # Start server
docker compose down           # Stop server
docker compose restart        # Restart server
docker compose logs -f        # View logs (live)
docker compose logs --tail 50 # Last 50 log lines
```

### 🔄 Update the Docker Image

When a new version of the Docker image is released:

```bash
docker compose pull           # Download latest image
docker compose down           # Stop current server
docker compose up -d          # Start with new image
```

> 💡 Your game data and saves are stored in Docker volumes — they are **preserved** during image updates. The game server files are also auto-updated via SteamCMD on every restart.

### 🗑️ Full Reset (Start Fresh)

Want to wipe everything and start from scratch?

```bash
docker compose down -v        # Stop + delete ALL volumes
docker compose up -d          # Fresh start (re-downloads ~4.5GB)
```

> ⚠️ **Warning:** `-v` permanently deletes all game data, player saves, and buildings. There is no undo. Back up first!

### 💾 Backup Your Data

Before major changes, back up your server:

```bash
docker compose down
docker run --rm \
  -v conan-server_config-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/conan-saves-$(date +%F).tar.gz /data
docker compose up -d
```

---

## ⏱️ First Run

On the first startup the container will:

1. 📥 Download Conan Exiles Dedicated Server (~4.5GB) via SteamCMD
2. 🍷 Initialize Wine prefix
3. ⚙️ Apply your `.env` configuration
4. 🚀 Start the server

This takes **10-30 minutes** depending on your internet speed.
Subsequent restarts are fast — game files are persisted in Docker volumes.

---

## 🎮 Connecting

Use **Direct Connect** in Conan Exiles:

- **IP:** Your server's IP address
- **Port:** 7777 (or whatever you set in `SERVER_PORT`)

> ⚠️ **Note:** Conan Exiles does not support hostnames in Direct Connect — use an IP address.

---

## 🔧 Building from Source

```bash
git clone https://github.com/balnaimi/conan-exiles-server.git
cd conan-exiles-server
cp .env.example .env
nano .env
docker compose -f docker-compose.build.yml up -d
```

### 📁 Files

| File | Description |
|------|-------------|
| `Dockerfile` | Image definition (Debian Trixie + Wine + SteamCMD) |
| `entrypoint.sh` | Startup script (download, configure, run) |
| `docker-compose.yml` | Production compose (pre-built image) |
| `docker-compose.build.yml` | Development compose (builds locally) |
| `.env.example` | Full configuration template (236 settings) |
| `docs/index.html` | Web-based Config Generator |

---

## 🏗️ Tech Stack

| Component | Version |
|-----------|---------|
| Base | Debian Trixie (slim) |
| Wine | Latest stable (WineHQ) |
| SteamCMD | Latest |
| Xvfb | Virtual framebuffer |

---

## 📄 License

MIT

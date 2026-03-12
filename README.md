# ⚔️ Conan Exiles Dedicated Server (Docker)

A clean, minimal Docker image for running a **Conan Exiles Dedicated Server** on Linux.

Built on **Debian Trixie** with the latest **Wine** from WineHQ and **SteamCMD**.

<p align="center">
  <a href="https://balnaimi.github.io/conan-exiles-server/"><img src="https://img.shields.io/badge/⚙️_Config_Generator-Open-c8a84e?style=for-the-badge" alt="Config Generator"></a>
  <a href="https://github.com/balnaimi/conan-exiles-server/pkgs/container/conan-exiles-server"><img src="https://img.shields.io/badge/📦_Docker_Image-ghcr.io-blue?style=for-the-badge" alt="Docker Image"></a>
</p>

---

## ✨ Features

- 🚀 Auto-downloads game files on first run (~4.5GB)
- 🔄 Auto-updates on every restart
- ⚙️ **140+ settings** via `.env` file — no config file editing
- 🎮 PvE / PvP / PvE-C modes
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

All settings are in the `.env` file. The `.env.example` includes **140+ settings** with descriptions.

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

### 📋 All Setting Categories

| Section | What you can change |
|---------|-------------------|
| 🖥️ **Server Info** | Name, password, type, region, MOTD |
| 🔑 **Admin & RCON** | Admin password, remote console |
| 🌐 **Network** | Ports, AFK kick, ping limit, login queue |
| ⚔️ **Combat & Damage** | Player/NPC/structure damage multipliers |
| 💀 **Death & Looting** | Corpse looting, body decay, equipment drop |
| 📈 **XP & Progression** | XP multipliers for kill/harvest/craft/time |
| ⛏️ **Harvesting & Crafting** | Resource amounts, spoil rates, fuel, crafting costs |
| 🏃 **Stamina & Movement** | Stamina drain, walk/sprint speed, health regen |
| 👥 **Thralls & Followers** | Conversion time, population limits, rescue, corruption |
| 🏗️ **Building & Decay** | Land claim, decay timers, stability, pickup |
| 🐉 **NPC & World** | NPC health, respawn, aggro range, wildlife |
| 🗿 **Avatars / Gods** | Summoning, lifetime, protection dome |
| 💬 **UI & Social** | Voice chat, player list, clan markers, events |
| 🌍 **Region Restrictions** | Block players by region |

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
| `.env.example` | Full configuration template (140+ settings) |
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

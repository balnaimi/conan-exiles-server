# ⚔️ Conan Exiles Enhanced Dedicated Server (Docker)

A community Docker Compose setup for hosting a **Conan Exiles Enhanced dedicated server** with automatic game downloads, SteamCMD updates, environment-based configuration, Steam Workshop mod support, and a web-based `.env` generator.

> ✅ Compatible with the renamed **Conan Exiles Enhanced** dedicated server. Steam app IDs, image names, repository URLs, and internal `ConanSandbox` paths may still use the legacy Conan Exiles naming because those are upstream/internal identifiers.

Built on **Debian Bookworm** with **WineHQ Staging**, **MS Visual C++ 2022 Redistributable**, **SteamCMD**, and headless Vulkan/EGL/OpenGL runtime libraries for better Unreal Engine 5 compatibility on VPS and home servers.

## About

This project is designed for server owners who want a practical Docker workflow instead of manually installing and configuring the Windows dedicated server. You download `docker-compose.yml`, create a `.env` file, and let the container handle the heavy lifting.

What the container handles:

- Downloads and updates the Conan Exiles Enhanced dedicated server with SteamCMD.
- Generates server configuration from your `.env` file on startup.
- Optionally downloads Steam Workshop mods from `SERVER_MOD_LIST` and writes `ConanSandbox/Mods/modlist.txt`.
- Runs the Windows server through Wine with VC++ 2022 and headless runtime libraries.
- Stores game files, saves, and configuration in Docker volumes so updates do not wipe your world.

What you provide:

- Docker and Docker Compose.
- Open firewall/router ports for the server.
- A `.env` file, either edited manually or generated from the website.
- Optional Steam Workshop mod IDs if you want a modded server.

## 🙌 Contributors

Special thanks to [@Sniijz](https://github.com/Sniijz) for the first community pull request, upgrading the Docker/Wine environment for better Unreal Engine 5 compatibility.

---

<p align="center">
  <a href="https://balnaimi.github.io/conan-exiles-server/"><img src="https://img.shields.io/badge/⚙️_Config_Generator-Open-c8a84e?style=for-the-badge" alt="Config Generator"></a>
  <a href="https://github.com/balnaimi/conan-exiles-server/pkgs/container/conan-exiles-server"><img src="https://img.shields.io/badge/📦_Docker_Image-ghcr.io-blue?style=for-the-badge" alt="Docker Image"></a>
</p>

---

## ✨ Features

- 🚀 Auto-downloads dedicated server files on first run.
- 🔄 Auto-updates game files on every container start.
- ⚙️ **239 settings** through a simple `.env` file.
- 🌐 **Web-based Config Generator** with sliders, toggles, and download/copy actions.
- 🧩 Optional Steam Workshop mod downloads via `SERVER_MOD_LIST`.
- 🎮 PvE / PvP / PvE-C modes with per-day PvP and building damage schedules.
- 🖥️ Optional RCON remote console.
- 🍷 WineHQ Staging + VC++ 2022 runtime for the Windows dedicated server.
- 🧱 Vulkan/EGL/OpenGL/Mesa runtime libraries for VPS/headless hosts.
- 💾 Persistent Docker volumes for game files, saves, and config.
- 📦 Pre-built GHCR image — no local build required.

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

### Fast Start

Use this if you want a simple server with only the basic settings.

```bash
mkdir conan-server && cd conan-server
curl -O https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/.env.minimal
nano .env
docker compose up -d
```

### Full Configuration

Use this if you want the full documented template with every available setting.

```bash
mkdir conan-server && cd conan-server
curl -O https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/.env.example
cp .env.example .env
nano .env
docker compose up -d
```

Or use the [Config Generator](https://balnaimi.github.io/conan-exiles-server/) to create a custom `.env` file from the website.

Watch the logs. The first run takes 10-30 minutes:

```bash
docker compose logs -f
```

Done! Connect via **Direct Connect** in-game using your server IP and port `7777`.

---

## ⚙️ Configuration

All settings are in the `.env` file. The `.env.example` includes **239 settings** with descriptions.

> ⚠️ **Startup-time configuration:** The Docker `.env` values are used by `entrypoint.sh` to generate Conan `.ini` files when the container starts. Changing `.env` while the server is already running does not update live in-game settings; restart/recreate the container after edits. If you change values in the in-game Admin Panel, remove the matching `.env` keys if you do not want them rewritten on the next startup.

The container generates the main runtime files under `ConanSandbox/Saved/Config/WindowsServer/`:

- `Engine.ini` for server browser/name/password and network rate settings.
- `ServerSettings.ini` for gameplay/server rules.
- `Game.ini` for `[/Script/Engine.GameSession]` and `[RconPlugin]` RCON settings. Conan Exiles Enhanced reads RCON from `Game.ini`; keeping RCON only in `ServerSettings.ini` can leave RCON disabled even when the file shows `RconEnabled=True`.

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

### CG-NAT, Tunnels, and Multi-Interface Hosts

If your server is behind CG-NAT, a tunnel, a VPS port forward, or a host with multiple network interfaces, Conan/Steam may advertise or bind to the wrong address. Set `MULTIHOME` to the public or forwarded IP address that players should use:

```env
MULTIHOME=your.public.or.forwarded.ip
```

Leave `MULTIHOME` empty unless you need this behavior. When set, the container adds both `-MULTIHOME=<value>` and `-MULTIHOMEHTTP=<value>` to the Conan server startup command so game traffic and server-browser HTTP registration use the same IP.

If your setup needs a different HTTP source IP, override it explicitly:

```env
MULTIHOMEHTTP=your.http.source.ip
```

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

## 🧩 Mods

To run a modded server, you only need to add Steam Workshop mod IDs to `SERVER_MOD_LIST` in your `.env` file.

What you do:

1. Find the Steam Workshop IDs for the mods you want.
2. Put them in `SERVER_MOD_LIST` as a comma-separated list.
3. Keep the IDs in the same order required by your mod collection.

```env
SERVER_MOD_LIST=3719513784,3720904511,3361295718
```

What the container does automatically on startup:

- Download each mod from the Conan Exiles Steam Workshop.
- Copy the downloaded `.pak` file into `ConanSandbox/Mods`.
- Generate `ConanSandbox/Mods/modlist.txt` in the same order as your list.
- Stop startup with a clear error if a mod ID is invalid, download output is missing, or no `.pak` file is found.

Leave `SERVER_MOD_LIST` empty to run an unmodded server.

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

### 🩺 Startup Troubleshooting

On some VPS or headless hosts, Wine/Conan may log Vulkan or EGL runtime messages during startup, such as:

```text
Failed to load libvulkan.so.1
Failed to load libEGL.so.1
```

Use the latest image first, because it includes the Vulkan/EGL/Mesa runtime libraries needed by Wine and Conan Exiles Enhanced on headless hosts:

```bash
docker compose pull
docker compose down
docker compose up -d
```

If the log appears to stop while mounting pak files, wait 10-15 minutes on first startup, then check whether the container is still running:

```bash
docker compose ps
docker compose logs --tail 150
```

If the container exits or the VPS has low memory, also check the host for out-of-memory kills.

### 🗑️ Full Reset (Start Fresh)

Want to wipe everything and start from scratch?

```bash
docker compose down -v        # Stop + delete ALL volumes
docker compose up -d          # Fresh start (re-downloads ~4.5GB)
```

> ⚠️ **Warning:** `-v` permanently deletes all game data, player saves, and buildings. There is no undo. Back up first!

### 💾 Backup

Before major changes, back up your server saves:

```bash
docker compose down
docker run --rm \
  -v conan-server_config-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/conan-saves-$(date +%F).tar.gz /data
docker compose up -d
```

### ♻️ Restore from Backup

To restore from a previous backup (replace the date with your backup file):

```bash
docker compose down
docker run --rm \
  -v conan-server_config-data:/data \
  alpine sh -c "rm -rf /data/*"
docker run --rm \
  -v conan-server_config-data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/conan-saves-YYYY-MM-DD.tar.gz -C /
docker compose up -d
```

> ⚠️ **Warning:** Restore deletes your current data first and replaces it with the backup.

> 💡 **Tip:** Volume names depend on your folder name. Use `docker volume ls` to find yours.

> 💡 **Enhanced save DB:** Conan Exiles Enhanced uses `ConanSandbox/Saved/game_0.db` as the default world database. Older UE4-era scripts that target `game.db` should be updated or verified before relying on them.

### Enhanced Migration Notes

- The default SteamCMD app `443030` installs Conan Exiles Enhanced / UE5. The old UE4 build lives on a legacy beta branch such as `conan-exiles-legacy` / `Conan-Exiles-Legacy`.
- If upgraded servers reject clients after moving from UE4 to UE5, inspect `Engine.ini` and remove stale build override lines such as `bUseBuildIdOverride=True` and `BuildIdOverride=...`.
- If using `MULTIHOME`, this image also passes `MULTIHOMEHTTP` so server-browser HTTP registration uses the expected source IP.
- Do not rename mod `.pak` files; Enhanced server documentation warns renamed pak files can fail to load.

---

## 💻 System Requirements

Based on [Funcom's official recommendations](https://www.conanexiles.com/):

| Server Size | CPU | RAM | Disk |
|-------------|-----|-----|------|
| **Small** (up to 10 players) | 2 cores @ 3.0 GHz | 8 GB | 35 GB |
| **Medium** (up to 35 players) | 4 cores @ 3.1 GHz | 8 GB | 35 GB |
| **Large** (up to 70 players) | 4 cores @ 3.4 GHz | 16 GB | 35 GB |

**Storage breakdown:**
- Docker image: ~1.5 GB
- Game server files (download ~5 GB, unpacked ~10 GB)
- World saves & database: grows over time
- Updates + temp space: ~5-10 GB headroom
- **Total recommended: 35 GB minimum**

**Network:**
- Stable internet with at least **10 Mbps upload** recommended
- Lower upload speeds may cause rubber-banding and lag for players
- Each connected player uses roughly **30-60 KB/s** of bandwidth

**Important notes:**
- This image runs the **Windows** dedicated server through **Wine** on Linux — Wine adds some CPU/RAM overhead compared to running natively on Windows
- The server is **single-threaded** for game logic — a fast single-core speed matters more than many cores
- RAM usage grows over time as players build and explore — monitor with `docker stats`
- No GPU required — the dedicated server is headless (no graphics)

> 💡 For a small private server with friends (2-10 players), any modern PC or VPS with 2+ cores and 8 GB RAM will work fine. No GPU needed!

---

## ⏱️ First Run

On the first startup the container will:

1. 📥 Download Conan Exiles Enhanced Dedicated Server (~4.5GB) via SteamCMD
2. 🍷 Initialize Wine prefix
3. ⚙️ Apply your `.env` configuration
4. 🚀 Start the server

This takes **10-30 minutes** depending on your internet speed.
Subsequent restarts are fast — game files are persisted in Docker volumes.

---

## 🎮 Connecting

Use **Direct Connect** in Conan Exiles Enhanced:

- **IP:** Your server's IP address
- **Port:** 7777 (or whatever you set in `SERVER_PORT`)

> ⚠️ **Note:** Conan Exiles Enhanced does not support hostnames in Direct Connect — use an IP address.

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
| `Dockerfile` | Image definition (Debian Bookworm + WineHQ Staging + VC++ 2022 runtime + SteamCMD) |
| `entrypoint.sh` | Startup script (download, configure, run) |
| `docker-compose.yml` | Production compose (pre-built image) |
| `docker-compose.build.yml` | Development compose (builds locally) |
| `.env.minimal` | Small quick-start template with only basic settings |
| `.env.example` | Full configuration template (239 settings) |
| `docs/index.html` | Web-based Config Generator |

---

## 🏗️ Tech Stack

| Component | Version |
|-----------|---------|
| Base | Debian Bookworm (slim) |
| Wine | WineHQ Staging |
| Windows Runtime | Microsoft Visual C++ 2022 Redistributable |
| Headless Runtime | Xvfb, Vulkan, EGL, OpenGL, Mesa |
| SteamCMD | Latest |
| Container | Docker / Docker Compose |

---

## ⚠️ Enhanced Setting Status / Legacy Compatibility Notes

The official Conan Exiles Enhanced server settings reference is now the first source to check: <https://exiles-enhanced.inflexion.io/servers/settings/>. That page is explicitly non-exhaustive, so absence from the official list is not proof that a setting is broken; it means the key still needs runtime verification on Enhanced before this project describes it as reliable.

These are not Docker-specific issues. The Docker image writes `ServerSettings.ini` from `.env` on startup, but the game may also store or override some values through the in-game Admin Panel and world database. If you can confirm one of the uncertain settings works reliably in Enhanced, please open an issue or pull request with the game version, setting value, and how you tested it.

### Officially Documented in Enhanced

| Setting | Status |
|---------|--------|
| `CraftingCostMultiplier` | Official Enhanced docs list this key, default `1.0f`, Requires Restart: No. Runtime gameplay testing is still welcome, but this is no longer treated as a legacy-only warning. |
| `PlayerStaminaRegenSpeedScale` | Official Enhanced docs list this key, Requires Restart: No. Keep Admin Panel/database caveats in mind if a running world appears to ignore changes. |

### Prefer Enhanced Key / Legacy Alias Uncertain

| Setting | Status |
|---------|--------|
| `StaminaCostMultiplier` | Preferred/current key in the official Enhanced docs for stamina cost per action. |
| `PlayerStaminaCostMultiplier` | Legacy/hosting-doc key. Do not prefer it over `StaminaCostMultiplier` unless runtime testing confirms a specific need. |
| `PlayerStaminaCostSprintMultiplier` | Legacy/hosting-doc key. Not found in the official Enhanced settings reference; needs Enhanced runtime verification. |

### Not Found in Official Enhanced Docs / Runtime Test Needed

| Setting | Issue |
|---------|-------|
| `PlayerEncumbranceMultiplier` | Legacy reports say it had no effect. Not found in the official Enhanced settings reference; needs runtime testing on Enhanced. |
| `PlayerSprintSpeedScale` | Present in older references and hosting docs, but not found in the official Enhanced settings reference; needs runtime confirmation. |
| `PlayerMovementSpeedScale` | Historically inconsistent and not found in the official Enhanced settings reference. Enhanced docs list `PlayerMovementAccelerationMultiplier (BP)` instead. |
| `PlayerHealthRegenSpeedScale` | Present in older/hosting references, but not found in the official Enhanced settings reference; may require Admin Panel or database-aware testing. |

### 💡 Workaround

For unreliable settings, change them via the **in-game Admin Panel**:
1. Join your server
2. Press **ESC → Settings → Server Settings**
3. Click **Make Me Admin** (enter your admin password)
4. Change settings from within the game
5. **Remove those settings from your `.env`** to avoid conflicts

> **Note:** This image rewrites `ServerSettings.ini` on every restart from your `.env`. Settings changed via Admin Panel are stored in the **game database** and may take priority. If a legacy setting appears to do nothing, remove it from your `.env` to avoid confusion.

---

## 📝 Release Notes

### v2.5.4 — Enhanced Settings Status Clarification

- Reclassified legacy server-setting warnings using the official Conan Exiles Enhanced INI reference.
- Marked `CraftingCostMultiplier` and `PlayerStaminaRegenSpeedScale` as officially documented in Enhanced.
- Documented `StaminaCostMultiplier` as the preferred Enhanced stamina-cost key while keeping legacy stamina aliases as uncertain.
- Clarified which older movement, encumbrance, and health regen keys are absent from the official Enhanced reference and still need runtime testing.

### v2.5.3 — Enhanced INI/RCON Alignment

- Generates `Game.ini` with `[RconPlugin]` so RCON enables correctly on Conan Exiles Enhanced.
- Writes server name/password under `[OnlineSubsystem]` while keeping `[OnlineSubsystemSteam]` password compatibility.
- Uses runtime-confirmed `clanMaxSize` casing for clan size.
- Adds missing `.env.example` and Config Generator entries for `ITEM_REPAIR_LOSS_BY_TIER`, `CAP_CHARACTER_LAYOUT`, and `PATH_FOLLOWING_ANGULAR`.
- Documents startup-time `.env` behavior, Enhanced `game_0.db`, UE4 legacy branch, stale build override cleanup, `MULTIHOMEHTTP`, and mod `.pak` rename warnings.

### v2.5.1 — MULTIHOMEHTTP Server Browser Fix

- Added automatic `MULTIHOMEHTTP` support when `MULTIHOME` is set, improving server browser registration for multi-IP hosts.
- Added optional `MULTIHOMEHTTP` override for advanced network setups.
- Updated README, `.env.example`, and the website Config Generator with the new option.
- Reworded legacy server setting warnings to avoid claiming unresolved Enhanced behavior as confirmed bugs.

### v2.5.0 — MULTIHOME Network Option

- Added optional `MULTIHOME` support for CG-NAT, tunnel, port-forwarded VPS, and multi-interface hosting setups.
- Documented when to use `MULTIHOME` in README, `.env.example`, and the website Config Generator.
- Thanks to [@xxirss](https://github.com/xxirss) for suggesting this improvement in issue #4.

### v2.4.1 — VPS Headless Runtime Fix

- Added Vulkan, EGL, OpenGL, and Mesa runtime libraries for both amd64 and i386 to improve Wine/UE5 startup on VPS and headless hosts.
- Added startup troubleshooting notes for Vulkan/EGL messages and pak mounting delays.
- Thanks to [@SummertimeSadnesss](https://github.com/SummertimeSadnesss) for reporting the Debian 12 VPS startup issue in issue #3.

### v2.4.0 — Steam Workshop Mod Support

- Added automatic Steam Workshop mod downloads when `SERVER_MOD_LIST` is set.
- Copies each downloaded `.pak` file into `ConanSandbox/Mods` and generates `ConanSandbox/Mods/modlist.txt` in the configured order.
- Validates mod IDs and stops startup with a clear error if a download output or `.pak` file is missing.
- Added a GitHub Actions validation workflow for shell syntax and required project files.
- Updated `.env.example`, README, and the website Config Generator documentation for mod setup.
- Thanks to [@pvillaverde](https://github.com/pvillaverde) for reporting that configured mods were not being downloaded in issue #2.

### v2.3.1 — Documentation Quick Start Fix

- Updated the website Quick Start to download `.env.example`, copy it to `.env`, then run `docker compose up -d`.
- Removed the broken two-command website option that failed because `docker-compose.yml` requires `env_file: .env`.
- Aligned the website setup instructions with the working README quick start.
- Thanks to [@pvillaverde](https://github.com/pvillaverde) for reporting the broken website quick start in issue #2.

### v2.3.0 — UE5/Wine Compatibility Update

- Switched the base image from Debian Trixie to Debian Bookworm for Wine compatibility/stability.
- Changed Wine package from `winehq-stable` to `winehq-staging`.
- Added `winbind` and `cabextract` dependencies.
- Added Microsoft Visual C++ 2022 Redistributable installation during image build.
- Wrapped Wine prefix initialization and VC++ runtime installation with `xvfb-run` to avoid headless display crashes.
- Community contribution: thanks to [@Sniijz](https://github.com/Sniijz) for PR #1.

---

## ⚖️ Legal

### Disclaimer

This project is a **community-made Docker wrapper** for hosting a Conan Exiles Enhanced dedicated server. It is **not affiliated with, endorsed by, or connected to** Funcom or Valve Corporation.

- **Conan Exiles / Conan Exiles Enhanced** is a trademark of [Funcom](https://www.funcom.com/). Official game website: [conanexiles.com](https://www.conanexiles.com/)
- **Steam** and **SteamCMD** are trademarks of [Valve Corporation](https://www.valvesoftware.com/)
- All game assets, content, and server binaries are property of their respective owners
- This project only provides Docker infrastructure and configuration tooling — it does **not** distribute any game files

### License

This project is licensed under the **GNU General Public License v3.0** (GPL-3.0).

You are free to:
- ✅ Use, modify, and distribute this project
- ✅ Host servers using this image
- ✅ Contribute improvements back to the community

You must:
- 📝 Keep the source code open if you distribute modified versions
- 📝 License any derivative work under GPL-3.0
- 📝 Give credit to the original project

You may **not**:
- ❌ Use this project or derivatives for closed-source commercial products
- ❌ Remove or alter the license terms

See [LICENSE](LICENSE) for the full text.

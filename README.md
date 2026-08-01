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

Watch the logs. First-run and update duration depends on storage, memory, CPU, network speed, and cache state:

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

#### The Log Stops at `pakchunk0`

Conan Exiles Enhanced mounts several Unreal Engine IoStore containers during startup. A final line such as:

```text
Mounted Pak file '../../../ConanSandbox/Content/Paks/pakchunk0-WindowsServer.pak'
```

means that `pakchunk0` was mounted successfully. It does **not** mean the server is still downloading or unpacking that file. On a healthy test system, the server progressed from this mount to `InProgress` within a few seconds.

A successful startup normally includes:

```text
Success! App '443030' fully installed.
Match State Changed from WaitingToStart to InProgress
Engine is initialized. Leaving FEngineLoop::Init()
Started SourceServerQueries on port 27015
```

If no new messages appear after the successful mount, do not rely on container uptime or the final stdout line alone. Query UDP port `27015`, inspect the actual process tree, and check memory limits, Docker OOM events, CPU virtualization, and the host kernel.

#### Runtime Diagnostics

```bash
docker compose ps
docker stats --no-stream
docker inspect conan-exiles-enhanced \
  --format 'status={{.State.Status}} oom={{.State.OOMKilled}} restart={{.RestartCount}}'
docker events --since 30m \
  --filter container=conan-exiles-enhanced
docker top conan-exiles-enhanced \
  -eo pid,ppid,stat,pcpu,pmem,wchan:32,comm,args
```

Wine wrapper processes may normally wait in `pipe_read` or `anon_pipe_read`; that alone is not proof of a hang. Verify the actual `ConanSandboxServer-Win64-Shipping.exe` process and its `GameThread`.

Check host memory and commit limits:

```bash
free -h
grep -E 'CommitLimit|Committed_AS' /proc/meminfo
sysctl vm.overcommit_memory
```

If RAM was increased while the VM or container was already running, restart Docker and verify that the daemon sees the new amount before starting the game server again:

```bash
sudo systemctl restart docker
docker info --format '{{.MemTotal}}'
free -b
```

#### Exit Code 137

Exit code `137` means the process received `SIGKILL`, but it does not prove an out-of-memory event occurred:

```bash
docker inspect conan-exiles-enhanced \
  --format 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}}'
docker events --since 30m \
  --filter container=conan-exiles-enhanced
```

`OOMKilled=true` or an explicit Docker `oom` event confirms memory exhaustion. `OOMKilled=false` may instead mean Docker forcibly terminated Wine after the stop timeout expired.

### 🗑️ Full Reset (Start Fresh)

Want to wipe everything and start from scratch?

```bash
docker compose down -v        # Stop + delete ALL volumes
docker compose up -d          # Fresh start (re-downloads the full server)
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

> [!IMPORTANT]
> Conan Exiles **Enhanced** uses Unreal Engine 5 and requires substantially more memory and storage than the previous UE4 dedicated server. Older Conan Exiles hardware recommendations should not be treated as reliable sizing guidance for the current Enhanced server running through Wine.

### Practical Enhanced / Wine Sizing

| Server Profile | CPU | RAM | Disk |
|----------------|-----|-----|------|
| **Test / very small server** | 4 modern cores | **16 GB** | **70 GB minimum** |
| **Small private server** (up to 10 players) | 4+ fast cores | **16 GB recommended** | **100 GB recommended** |
| **Growing or modded server** | 6+ fast cores | **24 GB or more** | **100 GB or more** |

These are practical planning recommendations for the Enhanced Windows dedicated server running through Wine—not official hard limits. Actual usage depends on the map, database size, buildings, active players, mods, and game updates.

### Verified Runtime Observation

A clean, unmodded Enhanced server using Steam build `24383534` and Wine Staging `11.10` was tested with the following results:

- An **8 GB** host repeatedly reached its memory limit and entered an out-of-memory restart loop.
- After increasing the host to **16 GB**, the same server started normally, reached `InProgress`, responded to Source/A2S queries on port `27015`, and remained stable during the test.
- Idle memory usage settled at approximately **9.1 GB**, without players or mods.

For the current Enhanced server, **8 GB RAM should be considered insufficient**. Use at least **16 GB** unless you have verified your own workload under stricter limits.

### Storage Guidance

Keep enough free space for Docker image layers, SteamCMD download/update staging, extracted server files, world databases, backups, logs, mods, and temporary space used by major updates.

A server with only **35–40 GB total storage is not considered sufficient** for reliable updates. Allocate at least **70 GB**, with **100 GB recommended** for comfortable maintenance and future updates.

**Network:**
- Stable internet with at least **10 Mbps upload** recommended
- Lower upload speeds may cause rubber-banding and lag for players
- Each connected player uses roughly **30-60 KB/s** of bandwidth

**Additional notes:**
- This image runs the **Windows** dedicated server through **Wine** on Linux, adding CPU and RAM overhead compared with native Windows
- Game logic benefits heavily from fast single-core performance
- RAM usage grows as players build and explore; monitor it with `docker stats`
- Mods may substantially increase startup time, memory use, and storage
- No physical GPU is required; the dedicated server remains headless

---

## ⏱️ First Run and Game Updates

On first startup, SteamCMD downloads and verifies the Conan Exiles Enhanced dedicated server. Major updates may also download several gigabytes and temporarily require additional disk space.

The container then initializes Wine, applies the `.env` configuration, and starts the server. Startup time depends on storage speed, available memory, CPU performance, and whether the game files are already cached. Do not determine readiness from container uptime alone.

Game files are persisted in Docker volumes, so subsequent restarts normally require only verification and any pending updates.

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

### v2.6.1 — Duration and Clipboard Reliability Hotfix

- Normalized rounded duration hints so minute/hour/day boundaries never display impossible combinations such as `23 hours 60 min` or `14 days 24h`.
- Preserved fractional-hour precision and corrected singular/plural labels, including `1.5` hours as `1 hour 30 min`.
- Rejected negative duration values instead of storing a negative setting while displaying a misleading zero-duration hint.
- Ensured the legacy clipboard fallback always removes its temporary textarea whether copying succeeds, is rejected, or throws.
- Expanded automated unit and headless-Chromium regression coverage for duration boundaries and clipboard failure cleanup.

### v2.6.0 — Pages UX and Accessibility Upgrade

- Added deep-linked tabs with correct scroll behavior, browser Back/Forward support, and keyboard arrow navigation.
- Rebuilt Config Generator accordions and form labels for keyboard and screen-reader access; closed sections no longer trap focus.
- Added setting search, Changed Only filtering, Expand/Collapse All, and a direct View Output action.
- Added safe single-quoted dotenv output for text, password, and select values containing spaces, `#`, `$`, or apostrophes.
- Changed server, admin, and RCON fields to masked password inputs with show/hide controls and warnings for public defaults.
- Rejected empty, non-finite, and out-of-range numeric values before they can produce invalid `.env` output.
- Added reliable clipboard fallback/error feedback and Copy command buttons for documentation examples.
- Improved the mobile hero, tab strip, generator toolbar, tables, color contrast, metadata, Open Graph data, and favicon.
- Expanded the Mods guide and simplified the on-page release history to the latest releases plus the full GitHub changelog.
- Added automated static and headless-Chromium Pages regression checks to GitHub Actions.

### v2.5.5 — Enhanced Runtime Requirements and Diagnostics

- Replaced outdated 8 GB / 35 GB sizing guidance with practical Enhanced/Wine recommendations.
- Documented the verified 8 GB OOM loop and stable 16 GB runtime at approximately 9.1 GB idle memory.
- Clarified that a successful `pakchunk0` mount is not an active download or unpack operation.
- Added startup readiness markers and Source/A2S query guidance for port `27015`.
- Added process-tree diagnostics for Wine wrapper processes, `Shipping.exe`, and `GameThread`.
- Added Docker OOM, memory-limit, post-resize daemon, and exit-code 137 checks.

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

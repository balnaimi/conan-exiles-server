

# ⚔️ Conan Exiles Enhanced Dedicated Server (Docker)

> [!IMPORTANT]
> ## Project Archived — No Longer Maintained
> This project is no longer maintained and remains available as a reference. For continued development or newer Conan Exiles compatibility, please fork it and maintain your own version. Releases, images, and documentation are provided as-is.
>
> Thank you to everyone who used, tested, contributed to, or learned from this project. Your support helped.

A community Docker Compose setup for hosting **Conan Exiles Enhanced** with automatic SteamCMD updates, `.env` configuration, Workshop mods, persistent data, and a web-based Config Generator.

<p align="center">
  <a href="https://balnaimi.github.io/conan-exiles-server/config/"><img src="https://img.shields.io/badge/⚙️_Config_Generator-Open-c8a84e?style=for-the-badge" alt="Config Generator"></a>
  <a href="https://github.com/balnaimi/conan-exiles-server/pkgs/container/conan-exiles-server"><img src="https://img.shields.io/badge/📦_Docker_Images-GHCR-blue?style=for-the-badge" alt="Docker images"></a>
</p>

> [!IMPORTANT]
> ## 🐧 Native Linux — Recommended for New Servers
> **Starting a new server? Use Native Linux.** Wine remains available for existing deployments and backward compatibility. Updating the default Wine Compose deployment never switches it to Native.

| Runtime | Status | Rolling image | Versioned image | Compose file |
|---|---|---|---|---|
| **Native Linux** | **Recommended for new servers** | `ghcr.io/balnaimi/conan-exiles-server:native` | `ghcr.io/balnaimi/conan-exiles-server:2.9.1-native` | `docker-compose.native.yml` |
| **Wine** | **Existing deployments / compatibility** | `ghcr.io/balnaimi/conan-exiles-server:latest` | `ghcr.io/balnaimi/conan-exiles-server:2.9.1` | `docker-compose.yml` |

See [GitHub Releases](https://github.com/balnaimi/conan-exiles-server/releases) for changelogs.

## 🚀 Quick Start

### Native Linux — Recommended for New Servers

> [!WARNING]
> **Fresh Native deployment:** never attach Native to Wine's live volumes. Existing Wine servers must use the documented migration into new Native volumes and keep the Wine data unchanged for rollback.

```bash
mkdir conan-native && cd conan-native
curl -O https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/docker-compose.native.yml
curl -o .env https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/.env.minimal
nano .env
docker compose -f docker-compose.native.yml up -d
```

### Wine — Existing Deployments

Keep your current `docker-compose.yml` and Wine volumes. `latest` stays on Wine to prevent automatic switching. To move, use the [guarded migration](docs/guides/native-linux.md#wine-to-native-migration).

Watch Wine startup with `docker compose logs -f`.

Connect in-game with **Direct Connect** using the server IP and port `7777`. Conan Exiles Enhanced requires an IP address rather than a hostname.

## ⚙️ Configuration

Use the [Config Generator](https://balnaimi.github.io/conan-exiles-server/config/) or edit:

- [`.env.minimal`](.env.minimal) — basic setup.
- [`.env.example`](.env.example) — all documented settings.

Restart/recreate the container after changing `.env`. Do not commit `.env` or password files.

## 💻 Requirements

Practical Enhanced planning guidance:

| Profile | CPU | RAM | Disk |
|---|---|---|---|
| Small/test | 4 fast x86-64 cores | **12 GB practical start** | **20 GB practical start** |
| Small private | 4+ fast x86-64 cores | **16 GB recommended** | **25–35 GB recommended** |
| Growing/modded | 6+ fast x86-64 cores | **24 GB or more** | **70 GB comfortable; 100 GB heavy** |

RAM sizing uses total host/VPS allocation. 12 GB is a practical starting allocation for a small vanilla server. In measured no-player tests under a hard 10 GiB container cap with no extra swap budget, Wine peaked at 9.19 GiB and Native peaked at 8.69 GiB; both remained A2S-ready through 20-minute observation windows without cgroup pressure or OOM events, and Native completed multiple save cycles. 16 GB is recommended for typical use, not a hard minimum. The test host remained at 16 GiB, so the 10 GiB cap tested the game budget under pressure but did not reproduce whole-system pressure on a 12 GB VPS. The measured worlds were small and unmodded; players, larger worlds, and mods can require more memory.

Storage sizing assumes one runtime—Wine or Native, not both. 20 GB is a practical starting allocation for one runtime. 25–35 GB is recommended for updates, world growth, and a simple backup. 35–40 GB is a comfortable allocation, not a minimum. The measured clean Wine-to-Native coexistence used about 14 GB on the host; 25 GB is a practical migration floor for that scenario, while 35 GB is recommended for safer migration headroom. 70 GB is comfortable, not required, for mods, multiple backups, long-term growth, or repeated maintenance. 100 GB is a safer recommendation for heavily modded servers or long backup retention. These are project planning recommendations, not official Funcom requirements or hard limits.

CPU flags must be visible inside the host or VM guest. Native preflight enforces SSE4.2 and reports AVX/AVX2. AVX2 is an important compatibility check, but Funcom has not officially confirmed AVX2 as a hard requirement. See the [CPU compatibility check](docs/guides/operations.md#cpu-compatibility-check).

## 🔌 Ports

| Port | Protocol | Purpose |
|---:|---|---|
| 7777 | UDP | Game |
| 7778 | UDP | Raw game port |
| 27015 | UDP | Steam/A2S query |
| 25575 | TCP | RCON, only if explicitly published |

Native does not publish RCON by default.

## 📚 Documentation

- **[Documentation index](docs/README.md)**
- [Configuration and networking](docs/guides/configuration.md)
- [Steam Workshop mods](docs/guides/mods.md)
- [Native Linux, backups, restore, and migration](docs/guides/native-linux.md)
- [Operations and troubleshooting](docs/guides/operations.md)
- [Enhanced compatibility and legacy settings](docs/guides/compatibility.md)
- [Development and local builds](docs/guides/development.md)
- [GitHub Releases and changelogs](https://github.com/balnaimi/conan-exiles-server/releases)

## 🛠️ Basic Management

Native commands are shown below for new servers. Existing Wine deployments omit `-f docker-compose.native.yml`.

```bash
docker compose -f docker-compose.native.yml up -d
docker compose -f docker-compose.native.yml pull
docker compose -f docker-compose.native.yml logs -f
```

Docker volumes preserve game data during normal image updates. Back up and verify your world before migrations, major game updates, restores, or destructive commands.

For portable backup, restore, diagnostics, and recovery commands, see [Operations and Troubleshooting](docs/guides/operations.md#portable-backup-verification-and-restore). Back up and verify the world before applying a restore.

## 🙌 Project Status and Community Use

This repository is archived and no longer accepts support requests, issues, or pull requests. No further maintenance, compatibility updates, or technical support are planned.

You are welcome to fork the repository and continue development independently under the terms of the [GPL-3.0 license](LICENSE). Before running a fork against a newer Conan Exiles release, review and test its runtime, configuration, backup, and migration behavior for compatibility.

Special thanks to [@Sniijz](https://github.com/Sniijz) for the first community pull request and to everyone whose use, testing, feedback, and contributions improved the project.

## ⚖️ Legal

This is a community Docker wrapper and is not affiliated with or endorsed by Funcom or Valve. It distributes infrastructure and configuration tooling, not game files.

Conan Exiles is a trademark of [Funcom](https://www.funcom.com/). Steam and SteamCMD are trademarks of [Valve Corporation](https://www.valvesoftware.com/).

Licensed under [GPL-3.0](LICENSE).

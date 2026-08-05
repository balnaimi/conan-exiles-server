# Development

[← Documentation index](../README.md)

## Build locally

Wine compatibility image:

```bash
git clone https://github.com/balnaimi/conan-exiles-server.git
cd conan-exiles-server
cp .env.example .env
nano .env
docker compose -f docker-compose.build.yml up -d
```

Native Linux:

```bash
cp .env.example .env
nano .env
docker compose -f docker-compose.native.build.yml up -d
```

## Repository layout

| Path | Purpose |
|---|---|
| `Dockerfile` | Wine compatibility image for existing deployments |
| `Dockerfile.native` | Non-root Native Linux image recommended for new servers |
| `entrypoint.sh` | Wine startup |
| `scripts/runtime/` | Shared config, secret, and atomic Workshop helpers |
| `scripts/native/` | Native install, preflight, lifecycle, health, backup, and restore |
| `docker-compose.yml` | Published Wine deployment |
| `docker-compose.native.yml` | Published Native deployment |
| `docker-compose.build.yml` | Local Wine build |
| `docker-compose.native.build.yml` | Local Native build |
| `.env.minimal` | Small quick-start configuration |
| `.env.example` | Full configuration template |
| `docs/index.html` | Interactive documentation and Config Generator |
| `docs/guides/` | Focused operator documentation |

## Stack

- Debian Bookworm slim bases pinned separately for Wine and Native.
- WineHQ Staging, VC++ 2022, Xvfb, Vulkan/EGL/Mesa for existing Wine deployments.
- Upstream Linux Shipping binary under a non-root user for the recommended Native runtime.
- SteamCMD with persistent writable cache.
- Python standard-library A2S/RCON and SQLite-safe backup/restore tooling.
- Separate GHCR channels with OCI runtime/support labels.

## Validation

Before proposing a change, run the relevant project checks. The complete validation workflow covers:

- Runtime configuration rendering.
- Native security/lifecycle/backup/restore behavior.
- Image contracts.
- Compose and migration fixtures.
- Configuration consistency.
- Pages static and headless-browser behavior.
- Bash syntax, ShellCheck, Python compile, HTML validation, and `git diff --check`.

Changes to a runtime require real disposable-host acceptance in addition to mocks when they affect SteamCMD paths, CPU preflight, RCON framing, lifecycle, data, mods, backup, or restore.

## Contributing

Open an issue with the environment, image tag/digest, game build, logs with secrets removed, and clear reproduction steps. Pull requests should keep Native recommended for new servers, Wine backward-compatible for existing deployments, and both runtimes on separate volumes and tags.

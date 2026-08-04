# Operations and Troubleshooting

[← Documentation index](../README.md)

Commands below use Wine Stable Compose. Add `-f docker-compose.native.yml` and the Native service name where applicable.

## Daily commands

```bash
docker compose up -d
docker compose down
docker compose restart
docker compose logs -f
docker compose logs --tail 50
```

Update the image safely:

```bash
docker compose pull
docker compose down
docker compose up -d
```

Docker volumes preserve game data during image updates. SteamCMD checks game files at startup.

## Readiness

Do not judge readiness from container uptime or the last log line. A healthy startup normally progresses to markers such as:

```text
Success! App '443030' fully installed.
Match State Changed from WaitingToStart to InProgress
Engine is initialized. Leaving FEngineLoop::Init()
Started SourceServerQueries on port 27015
```

A final `pakchunk0` mount line means the package mounted successfully; it is not evidence that the server is still downloading or that it hung. Verify A2S on UDP 27015 and inspect the real Shipping process.

## Diagnostics

```bash
docker compose ps
docker stats --no-stream
docker inspect conan-exiles-enhanced \
  --format 'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restart={{.RestartCount}}'
docker events --since 30m --filter container=conan-exiles-enhanced
```

Before printing complete process arguments, remember that third-party images or older scripts may expose passwords. The maintained images avoid putting RCON passwords in argv.

Wine wrapper processes waiting in `pipe_read` or `anon_pipe_read` are not proof of a hang. Check the real `ConanSandboxServer-Win64-Shipping.exe`, game thread, A2S response, memory, and Docker events.

## Exit code 137 and memory

Exit 137 means SIGKILL; it does not prove an OOM. Confirm with `OOMKilled=true` or a Docker `oom` event.

```bash
docker inspect conan-exiles-enhanced \
  --format 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}}'
docker events --since 30m --filter container=conan-exiles-enhanced
```

Host checks:

```bash
free -h
grep -E 'CommitLimit|Committed_AS' /proc/meminfo
sysctl vm.overcommit_memory
```

If VM memory changed while Docker was running, restart Docker and confirm its detected memory before retesting:

```bash
sudo systemctl restart docker
docker info --format '{{.MemTotal}}'
free -b
```

## Headless Wine messages

The current Wine image includes Vulkan/EGL/OpenGL/Mesa runtime libraries. If old images log missing `libvulkan.so.1` or `libEGL.so.1`, update first:

```bash
docker compose pull
docker compose down
docker compose up -d
```

## Sizing guidance

These are project planning recommendations, not official hard limits:

| Profile | CPU | RAM | Disk |
|---|---|---|---|
| Test / very small | 4 fast x86-64 cores | 12 GB practical start | 20 GB practical start |
| Small private server | 4+ fast x86-64 cores | 16 GB recommended | 25–35 GB recommended |
| Growing or modded | 6+ fast x86-64 cores | 24 GB or more | 70 GB comfortable; 100 GB heavy |

RAM sizing uses total host/VPS allocation. 12 GB is a practical starting allocation for a small vanilla server. In measured no-player tests under a hard 10 GiB container cap with no extra swap budget, Wine peaked at 9.19 GiB and Native peaked at 8.69 GiB; both remained A2S-ready through 20-minute observation windows without cgroup pressure or OOM events, and Native completed multiple save cycles. 16 GB is recommended for typical use, not a hard minimum. The test host remained at 16 GiB, so the 10 GiB cap tested the game budget under pressure but did not reproduce whole-system pressure on a 12 GB VPS. The measured worlds were small and unmodded; players, larger worlds, and mods can require more memory.

Native preflight prefers a finite cgroup memory limit when one is exposed; otherwise it falls back to /proc/meminfo. This prevents a constrained Docker container from reporting the physical host's larger RAM total as its own usable budget.

Storage sizing assumes one runtime—Wine or Native, not both. 20 GB is a practical starting allocation for one runtime. 25–35 GB is recommended for updates, world growth, and a simple backup. 35–40 GB is a comfortable allocation, not a minimum. The measured clean Wine-to-Native coexistence used about 14 GB on the host; 25 GB is a practical migration floor for that scenario, while 35 GB is recommended for safer migration headroom. 70 GB is comfortable, not required, for mods, multiple backups, long-term growth, or repeated maintenance. 100 GB is a safer recommendation for heavily modded servers or long backup retention. These are project planning recommendations, not official Funcom requirements or hard limits.

The table describes total host allocation in decimal GB. Native preflight reports the currently free space in binary GiB after the OS, Docker images, and existing data have already consumed disk space.

## CPU compatibility check

[Epic's UE 5.2 release notes](https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-5.2-release-notes?application_version=5.2) document SSE4.2 as the general UE5 x64 minimum CPU specification. An individual game build may choose a higher minimum. AVX and AVX2 are therefore important Conan Exiles Enhanced compatibility diagnostics, but Funcom has not officially confirmed AVX2 as a hard requirement.

Check the flags visible to Linux:

```bash
for flag in sse4_2 avx avx2; do
  grep -qw "$flag" /proc/cpuinfo \
    && echo "$flag=yes" \
    || echo "$flag=no"
done
```

Also inspect the guest-visible model:

```bash
lscpu
```

On VPS/QEMU and other virtual machines, the guest-visible flags matter—not only the physical host CPU. Select a current CPU model or `host-passthrough` when the platform supports it. A host change that exposes several previously missing flags can prove a CPU compatibility problem, but it does not isolate AVX2 alone.

A clean Enhanced/Wine test on Steam build `24383534` repeatedly OOM-restarted on 8 GB and stabilized near 9.1 GB idle after increasing the host to 16 GB. A later constrained test established the 12 GB practical-start guidance above. Actual use varies with players, buildings, world size, and mods. Keep free space for Docker layers, SteamCMD staging, game files, databases, backups, and updates.

Additional planning notes:

- A physical GPU is not required; the dedicated server is headless.
- Fast single-core performance matters for game logic.
- Wine adds CPU/RAM overhead, and mods can increase startup time, memory, and storage substantially.
- A stable connection with at least 10 Mbps upload is a practical starting recommendation; older observations estimated roughly 30–60 KB/s per connected player, but real use varies.

## Ports and connection

| Port | Protocol | Purpose |
|---:|---|---|
| 7777 | UDP | Game |
| 7778 | UDP | Raw game port |
| 27015 | UDP | Steam/A2S query |
| 25575 | TCP | RCON, if explicitly published |

Conan Exiles Enhanced Direct Connect requires an IP address rather than a hostname.

## Portable backup, verification, and restore

Run the unified tool from a complete project checkout. It discovers the labelled Wine or Native service and the exact Compose named volume; it never deletes a volume.

Create a portable world backup:

```bash
./scripts/conan-backup.sh create
```

`create` requires exactly one running runtime when `--runtime auto` is used. It stops that service, proves the save volume has no running users, takes a consistent SQLite snapshot, then restores the service's prior running state. If neither runtime is running, select one explicitly before the command:

```bash
./scripts/conan-backup.sh --runtime wine create
./scripts/conan-backup.sh --runtime native create
```

Archives are written to `.conan-backups/` with directory mode `0700` and archive mode `0600`. They contain the world database, bounded metadata, and checksums. WAL/SHM sidecars are never mixed into the snapshot. This portable format intentionally does not copy `.env` or plaintext passwords; keep your `.env` separately in a secret manager or protected host backup.

List and verify backups without Docker:

```bash
./scripts/conan-backup.sh list --verify
./scripts/conan-backup.sh verify .conan-backups/conan-wine-...-world.tar.gz
```

Restore is a non-mutating dry-run by default:

```bash
./scripts/conan-backup.sh restore .conan-backups/conan-wine-...-world.tar.gz
```

Apply only after reviewing the plan:

```bash
./scripts/conan-backup.sh restore .conan-backups/conan-wine-...-world.tar.gz --apply
```

Apply verifies the archive, its outer SHA-256 identity, and runtime; streams and validates every normalized archive header under strict member/size bounds before extracting any payload; refuses missing, aliased, foreign/unlabelled, or unexpectedly used volumes; and uses the resolved Compose project/volume labels to prove target ownership. For a non-matching world it stops and proves the target quiescent, creates and re-verifies a pre-restore archive, checkpoints old WAL state, replaces `game_0.db` atomically while preserving UID/GID, and restores the prior running state. If readiness fails, it automatically restores the pre-restore archive. A host `flock` serializes create/apply operations. The helper image is pinned by digest and runs without networking, with a read-only root filesystem, a disposable work volume, and minimal capabilities.

`.conan-backups/restore-state.json` records durable operation state before a running service is stopped. An unresolved state blocks another create or apply so its rollback pointer cannot be overwritten. Host-side verification creates its private workspace on the archive filesystem and checks available expansion space before extracting. After a host reboot, forced termination, or `recovery-required` result, run:

```bash
./scripts/conan-backup.sh recover
```

For an interrupted backup/stopping phase this restores the prior running state. For a prepared or partially applied restore it verifies and applies the pre-restore archive, then restores the prior running state. Reapplying an already-matching archive is idempotent. Its digest-bound, WAL-aware equality decision happens only after quiescence so a concurrent world commit cannot race the restore decision; the prior running state is then restored.

Enhanced's default world is `ConanSandbox/Saved/game_0.db`; archives or scripts that target only `game.db` are obsolete.

Native's existing scheduled/full backup tooling remains available for Native-only configuration and mod-state archives. The unified portable command is the common Wine/Native world recovery path.

## Privacy-safe diagnostics

Generate a concise report without reading `.env`, Docker environment values, process arguments, logs, or world data:

```bash
./scripts/conan-doctor.sh
./scripts/conan-doctor.sh --format json
./scripts/conan-doctor.sh --output conan-doctor-$(date +%F).json
```

The output contains host CPU capability flags, memory/disk totals, whitelisted Docker/Compose versions, labelled runtime state, health/OOM/restarts, bounded resource stats, published port numbers, privacy-minimized A2S reachability/player counts, process IDs with executable basenames only, and backup count/age without archive names. A2S server names and map names are intentionally omitted. Output files use mode `0600`.

Use offline mode when Docker is unavailable or should not be contacted:

```bash
./scripts/conan-doctor.sh --offline --format json
```

## Full reset

> **Danger:** `down -v` permanently deletes the world, players, buildings, configuration, and all Compose volumes. Back up and verify first.

```bash
docker compose down -v
docker compose up -d
```

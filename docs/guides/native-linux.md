# Native Linux Experimental

[← Documentation index](../README.md)

Native Linux runs the upstream Linux Shipping server without Wine. It is **experimental and opt-in**; Wine remains the stable/default runtime.

## Images and Compose

| Channel | Image | Compose |
|---|---|---|
| Rolling | `ghcr.io/balnaimi/conan-exiles-server:native` | `docker-compose.native.yml` |
| Versioned | `ghcr.io/balnaimi/conan-exiles-server:2.8.0-native` | `docker-compose.native.yml` |

The image runs as non-root UID/GID `1000:1000`.

## Fresh start

```bash
mkdir conan-native && cd conan-native
curl -O https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/docker-compose.native.yml
curl -o .env https://raw.githubusercontent.com/balnaimi/conan-exiles-server/main/.env.minimal
nano .env
docker compose -f docker-compose.native.yml up -d
```

Use the supplied named volumes. If you replace them with bind mounts, create writable directories owned by UID/GID 1000 before startup. The non-root image intentionally does not recursively change ownership of arbitrary host data.

## Isolation rule

Never attach the same live data volume to Wine and Native. Existing Wine servers must use the migration helper to copy into new Native volumes while the original Wine data remains unchanged for rollback.

## CPU and resources

- Guest-visible SSE4.2 is required by the current UE5 x64 baseline and enforced by preflight.
- AVX and AVX2 are reported as diagnostics; Funcom has not officially confirmed AVX2 as a hard requirement.
- Use 12 GB as a practical starting allocation for a small vanilla server; 16 GB remains recommended for typical use and more headroom.
- Storage sizing assumes one runtime—Wine or Native, not both. 20 GB is a practical starting allocation for one runtime.
- 25–35 GB is recommended for updates, world growth, and a simple backup. 35–40 GB is a comfortable allocation, not a minimum.
- A safe Wine-to-Native trial keeps both runtimes and their isolated data temporarily. The measured clean Wine-to-Native coexistence used about 14 GB on the host; 25 GB is a practical migration floor for that scenario, while 35 GB is recommended for safer migration headroom.
- 70 GB is comfortable, not required, for mods, multiple backups, long-term growth, or repeated maintenance. 100 GB is a safer recommendation for heavily modded servers or long backup retention.
- These are project planning recommendations, not official Funcom requirements or hard limits.
- These allocations are total disk sizes in decimal GB; preflight reports currently free binary GiB after existing host and Docker usage.

RAM sizing uses total host/VPS allocation. 12 GB is a practical starting allocation for a small vanilla server. In measured no-player tests under a hard 10 GiB container cap with no extra swap budget, Wine peaked at 9.19 GiB and Native peaked at 8.69 GiB; both remained A2S-ready through 20-minute observation windows without cgroup pressure or OOM events, and Native completed multiple save cycles. 16 GB is recommended for typical use, not a hard minimum. The test host remained at 16 GiB, so the 10 GiB cap tested the game budget under pressure but did not reproduce whole-system pressure on a 12 GB VPS. The measured worlds were small and unmodded; players, larger worlds, and mods can require more memory.

Native preflight prefers a finite cgroup memory limit when one is exposed; otherwise it falls back to /proc/meminfo. This prevents a constrained Docker container from reporting the physical host's larger RAM total as its own usable budget.

Check exactly what Linux can see:

```bash
for flag in sse4_2 avx avx2; do
  grep -qw "$flag" /proc/cpuinfo \
    && echo "$flag=yes" \
    || echo "$flag=no"
done
lscpu
```

On VPS/QEMU hosts, select a current virtual CPU model or `host-passthrough` when available. The guest-visible flags are what the server can use. Funcom has not officially confirmed AVX2 as a hard requirement; see the detailed [CPU compatibility check](operations.md#cpu-compatibility-check).

A dated acceptance run on Steam build `24383534` used about 8.70 GiB idle on a 16 GiB host and mounted StayBloody and Better Thralls. Treat that as a test observation, not a fixed sizing promise.

## Updates and readiness

SteamCMD updates Native game binaries in place at startup; updates are not transactionally rolled back. If SteamCMD fails, startup fails closed. Correct disk/network problems and retry, optionally with:

```env
NATIVE_VALIDATE_SERVER=true
```

Docker health uses A2S readiness by default. RCON is internal-only and is an explicit diagnostic, not a required liveness signal. Optional RCON health probing can be enabled with `NATIVE_HEALTHCHECK_RCON`, but Conan's timing-sensitive RCON endpoint may create false-unhealthy results.

## Secrets

Prefer file-backed password variables. The entrypoint removes plaintext password environment variables before launching the Shipping process, and the RCON client does not place passwords in argv. RCON is not published to the host by the default Native Compose file.

## Backup and restore

Native backups use SQLite's backup API and do not mix the consistent database snapshot with unrelated live WAL/SHM sidecars. INI password values are replaced with `[REDACTED]`; current secrets are rendered from environment/file sources after restore. Archives use owner-only permissions.

Create a light backup:

```bash
docker compose -f docker-compose.native.yml exec conan-native \
  /scripts/native/backup.sh --reason manual
```

Set `NATIVE_BACKUP_MODE=full` to include active managed PAKs. Scheduled backups are disabled by default and use the `NATIVE_BACKUP_*` controls.

Verify an archive before applying it:

```bash
docker compose -f docker-compose.native.yml run --rm --no-deps \
  --entrypoint /scripts/native/restore.sh conan-native \
  /data/backups/ARCHIVE.tar.gz --verify-only
```

Apply only while Native is stopped:

```bash
docker compose -f docker-compose.native.yml stop conan-native
docker compose -f docker-compose.native.yml run --rm --no-deps \
  --entrypoint /scripts/native/restore.sh conan-native \
  /data/backups/ARCHIVE.tar.gz --target /data/server --apply
docker compose -f docker-compose.native.yml up -d conan-native
```

Restore validates paths, checksums, and SQLite integrity, refuses an active tracked server, and creates a pre-restore backup when replacing a world. The next startup must activate the exact Workshop dependency set recorded by the restored archive before opening the world.

## Wine-to-Native migration

For the repository's default Compose named volumes, use the Compose-aware wrapper from the existing Wine project directory. It discovers the runtime services and the exact nested save volumes from `docker compose config`; it does not guess volume names.

Review the read-only plan while Wine is still running:

```bash
./scripts/migrate-compose-wine-to-native.sh plan
```

Apply the reviewed cutover:

```bash
./scripts/migrate-compose-wine-to-native.sh apply
```

Do **not** stop Wine manually first. Apply owns the cutover: it stops Wine, proves no running container still uses the source volume, rechecks the database hash, creates separate Native volumes, takes a mode-0600 rollback archive under `.conan-migration/`, snapshots `game_0.db` through SQLite, starts Native, and waits for Docker health. If Native becomes unhealthy or times out, the wrapper stops Native and restarts Wine automatically.

Rollback remains explicit and non-destructive:

```bash
./scripts/migrate-compose-wine-to-native.sh rollback
```

Rollback restarts Wine against its unchanged source volume and preserves the Native volume and archive for diagnosis. The wrapper never removes volumes. Keep both runtimes' data until database, A2S, RCON, mods, player connections, and at least one save cycle pass. The archive may contain credentials from Wine INIs; `.conan-migration/` is ignored by Git and must remain protected.

If the original deployment used `docker compose --project-name NAME`, pass the same value to every wrapper command:

```bash
./scripts/migrate-compose-wine-to-native.sh plan --project-name NAME
```

### Advanced filesystem-path helper

Operators using bind-mounted whole game directories can use the low-level helper directly. It defaults to dry-run:

```bash
./scripts/migrate-wine-to-native.sh \
  --source /path/to/wine-data \
  --destination /path/to/new-native-data
```

Review the plan, stop the source, then repeat with `--source-stopped --apply`. The low-level helper never deletes the source and never activates Windows INIs as Linux configuration. Native renders reviewed `LinuxServer` values from `.env`.

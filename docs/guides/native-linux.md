# Native Linux Experimental

[← Documentation index](../README.md)

Native Linux runs the upstream Linux Shipping server without Wine. It is **experimental and opt-in**; Wine remains the stable/default runtime.

## Images and Compose

| Channel | Image | Compose |
|---|---|---|
| Rolling | `ghcr.io/balnaimi/conan-exiles-server:native` | `docker-compose.native.yml` |
| Versioned | `ghcr.io/balnaimi/conan-exiles-server:2.7.2-native` | `docker-compose.native.yml` |

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
- Use at least 16 GB RAM for a small current Enhanced server; modded or growing worlds may need more.
- Storage sizing assumes one runtime—Wine or Native, not both. 35–40 GB is a practical starting allocation for one runtime, not the game download size or an official hard minimum; keep free space for updates.
- 70 GB is recommended, not required, for comfortable update, backup, and maintenance headroom. 100 GB is a safer recommendation for modded servers, multiple backups, or long-term growth.

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

The helper defaults to dry-run, checks `game_0.db`, creates a mode-0600 Wine rollback archive, and writes a SQLite snapshot into a new destination:

```bash
./scripts/migrate-wine-to-native.sh \
  --source /path/to/wine-data \
  --destination /path/to/new-native-data
```

Review the plan, stop Wine, then repeat with `--source-stopped --apply`.

The helper never deletes the source and does not activate Windows INIs as Linux configuration. Native renders reviewed `LinuxServer` values from `.env`. Keep Wine data and the rollback archive until database, A2S, RCON, mods, and player connections pass. The rollback archive may contain credentials; protect it as secret-bearing data.

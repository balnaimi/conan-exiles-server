# Steam Workshop Mods

[← Documentation index](../README.md)

Add ordered Workshop IDs to `SERVER_MOD_LIST`:

```env
SERVER_MOD_LIST=3722881816,3720904511
```

Keep the IDs in the exact load order required by your collection. Leave the value empty for an unmodded server.

## What startup does

1. Validates the complete requested list.
2. Downloads or updates each Workshop item.
3. Finds and verifies the usable `.pak` package.
4. Builds a temporary ordered `modlist.txt`.
5. Atomically replaces the active list only after every item succeeds.
6. Preserves the last-known-good list and world when a download or package check fails.

## Runtime differences

### Native Linux — Recommended for New Servers

Native uses strict parsing and fails closed:

- IDs must be comma-separated digits with no whitespace or empty entries.
- Duplicate package names, zero-byte PAKs, ambiguous multi-PAK items, and malformed lists are rejected.
- Removed managed files can be pruned with `NATIVE_PRUNE_REMOVED_MODS=True`.

Enhanced extracts embedded Linux payloads (`-LinuxServer.pak`, `.utoc`, and `.ucas`) automatically after the top-level Workshop PAK is activated.

### Wine compatibility / existing deployments

Wine preserves compatibility with older project behavior where possible. Legacy whitespace/comma gaps, multi-PAK selection, duplicate names, and zero-byte activation may produce warnings rather than strict Native-style rejection.

## Verified Enhanced example

Runtime acceptance used:

| Mod | Workshop ID | Verified order |
|---|---:|---:|
| StayBloody | `3722881816` | 1000 |
| Better Thralls | `3720904511` | 1001 |

Both mounted in requested order on the tested Enhanced build. This is evidence for those versions, not a guarantee that future Workshop updates remain compatible.

## Updating or removing mods

Restart the container after changing `SERVER_MOD_LIST`. SteamCMD checks configured items during startup. Removing an ID removes it from the active list; Native can prune old managed files when pruning is enabled.

Do not rename downloaded `.pak` files. Enhanced server documentation warns that renamed PAKs may fail to load.

## Failure handling

If startup reports a Workshop failure:

1. Check the Workshop ID still exists and is public.
2. Confirm the mod supports Conan Exiles Enhanced.
3. Check disk space and network access.
4. Retry startup; SteamCMD can repair a corrupt cache item.
5. Do not manually replace `modlist.txt` while the container is running.

Native deliberately refuses to open the world when the restored backup requires a different Workshop set than the configured `SERVER_MOD_LIST`. See [Native Linux](native-linux.md#backup-and-restore).

# Configuration

[← Documentation index](../README.md)

Both runtimes read `.env` at container startup and generate Conan configuration files. Use [.env.minimal](../../.env.minimal) for a small setup, [.env.example](../../.env.example) for every documented option, or the [Config Generator](https://balnaimi.github.io/conan-exiles-server/).

## Important behavior

- Restart or recreate the container after changing `.env`; edits do not update a running game process.
- The container rewrites managed INI keys on startup.
- If you change a setting through the in-game Admin Panel, remove the matching `.env` key when you do not want the next startup to overwrite it.
- Do not commit `.env` or password files.

## Main settings

| Variable | Default | Purpose |
|---|---|---|
| `SERVER_NAME` | `My Conan Server` | Server browser name |
| `SERVER_PASSWORD` | empty | Join password; empty means public |
| `SERVER_TYPE` | `pve` | `pve`, `pvp`, or `pve-c` |
| `MAX_PLAYERS` | `40` | Player limit |
| `ADMIN_PASSWORD` | `changeme` | Admin password; change it before public use |
| `SERVER_REGION` | `0` | Region code |
| `BATTLEYE_ENABLED` | `False` | BattlEye anti-cheat |
| `TZ` | `UTC` | Container timezone |
| `SERVER_MOD_LIST` | empty | Ordered Workshop IDs |

The full template and generator currently expose 250 settings: shared gameplay/server settings plus Native operation controls.

## Generated files

Wine Stable writes under:

```text
ConanSandbox/Saved/Config/WindowsServer/
```

Native Linux writes under:

```text
ConanSandbox/Saved/Config/LinuxServer/
```

The main files are:

- `Engine.ini` — server identity, browser, password, and network values.
- `ServerSettings.ini` — gameplay and server rules.
- `Game.ini` — game-session and RCON configuration.

Conan Exiles Enhanced reads RCON from `[RconPlugin]` in `Game.ini`; setting it only in `ServerSettings.ini` may leave RCON disabled.

## Password files

Native supports file-backed secrets for server, admin, and RCON passwords. Prefer the `_FILE` variables and mount files with restrictive permissions instead of putting plaintext passwords directly in environment variables.

The Native entrypoint reads the files and removes plaintext password variables before launching the game. RCON passwords are not passed in process arguments.

## Networking and advertised address

For CG-NAT, tunnels, port-forwarded VPS hosts, or systems with multiple interfaces, set the public/forwarded address:

```env
MULTIHOME=your.public.or.forwarded.ip
```

The container also uses that value for `MULTIHOMEHTTP`, keeping game traffic and server-browser registration on the same address. Override it only if the HTTP registration path needs a different source IP:

```env
MULTIHOMEHTTP=your.http.source.ip
```

Leave both empty for normal single-address hosting.

## Regions

| Value | Region |
|---:|---|
| `0` | Europe, Middle East, Africa |
| `1` | North America |
| `2` | Asia / Oceania |
| `3` | Australia / New Zealand |
| `4` | South America |
| `5` | Japan |

## Setting groups

The full template covers server identity, admin/RCON, network, player stats, survival, day/night, combat, death/loot, durability, XP, harvesting, crafting, stamina/movement, thralls, pets, building/decay, NPCs, Purge, avatars, chat, clans, UI/social, regional restrictions, PvP schedules, mods, and advanced runtime options.

For current/legacy key caveats, see [Enhanced Compatibility](compatibility.md).

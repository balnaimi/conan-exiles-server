# Conan Exiles Enhanced Compatibility

[← Documentation index](../README.md)

The first reference for current settings is the [official Conan Exiles Enhanced server settings page](https://exiles-enhanced.inflexion.io/servers/settings/). It is explicitly non-exhaustive: absence is not proof that a key is broken, only that runtime confirmation is still needed.

The image writes INIs from `.env`, while Conan may also store or override values through the Admin Panel and world database.

## Current and legacy setting notes

### Officially documented

- `CraftingCostMultiplier` — current Enhanced docs list it; gameplay testing remains welcome.
- `PlayerStaminaRegenSpeedScale` — current Enhanced docs list it; Admin Panel/database precedence can still affect a running world.

### Prefer the Enhanced key

- `StaminaCostMultiplier` — preferred current stamina cost key.
- `PlayerStaminaCostMultiplier` — legacy/hosting-doc alias; do not prefer it without a tested need.
- `PlayerStaminaCostSprintMultiplier` — older key not found in the current official reference; needs runtime confirmation.

### Runtime confirmation still needed

These older keys are not in the current official reference or have historically inconsistent behavior:

- `PlayerEncumbranceMultiplier`
- `PlayerSprintSpeedScale`
- `PlayerMovementSpeedScale` — Enhanced instead documents `PlayerMovementAccelerationMultiplier (BP)`.
- `PlayerHealthRegenSpeedScale`

If an `.env` setting appears ineffective, test it through the in-game Admin Panel. Remove the matching `.env` key afterward if the database/Admin Panel value should win on future starts.

## Enhanced migration notes

- Steam app `443030` installs Conan Exiles Enhanced / UE5.
- The old UE4 server lives on a legacy beta branch such as `conan-exiles-legacy` / `Conan-Exiles-Legacy`.
- Enhanced uses `ConanSandbox/Saved/game_0.db` as the default world database.
- If upgraded servers reject clients, inspect `Engine.ini` and remove stale build overrides such as `bUseBuildIdOverride=True` and `BuildIdOverride=...`.
- If `MULTIHOME` is set, the maintained image also passes `MULTIHOMEHTTP` for server-browser registration.
- Do not rename Workshop PAK files.

When reporting an uncertain setting, include the game build, exact key/value, whether the world already existed, whether the Admin Panel was used, and how behavior was measured.

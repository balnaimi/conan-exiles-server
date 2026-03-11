#!/bin/bash
set -e

# ============================================
# Conan Exiles Dedicated Server - Entrypoint
# ============================================

GAME_DIR="/conanexiles"
CONFIG_DIR="${GAME_DIR}/ConanSandbox/Saved/Config/WindowsServer"
SERVER_EXE="${GAME_DIR}/ConanSandboxServer.exe"
STEAM_APP_ID=443030

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[CONAN]${NC} $1"; }
warn() { echo -e "${YELLOW}[CONAN]${NC} $1"; }
error() { echo -e "${RED}[CONAN]${NC} $1"; }

# ============================================
# 1. Virtual display handled by xvfb-run
# ============================================

# ============================================
# 2. Download / Update game
# ============================================
if [ ! -f "$SERVER_EXE" ]; then
    log "Game not found. Downloading Conan Exiles Dedicated Server (~4.5GB)..."
    log "This may take 10-30 minutes on first run."
else
    log "Game found. Checking for updates..."
fi

/steamcmd/steamcmd.sh \
    +@sSteamCmdForcePlatformType windows \
    +force_install_dir "$GAME_DIR" \
    +login anonymous \
    +app_update $STEAM_APP_ID validate \
    +quit

if [ ! -f "$SERVER_EXE" ]; then
    error "Download failed! Retrying in 10 seconds..."
    sleep 10
    /steamcmd/steamcmd.sh \
        +@sSteamCmdForcePlatformType windows \
        +force_install_dir "$GAME_DIR" \
        +login anonymous \
        +app_update $STEAM_APP_ID validate \
        +quit
fi

if [ ! -f "$SERVER_EXE" ]; then
    error "Download failed after retry. Exiting."
    exit 1
fi

log "Game files ready!"

# ============================================
# 3. Initialize Wine prefix
# ============================================
if [ ! -d "$WINEPREFIX/drive_c" ]; then
    log "Initializing Wine prefix..."
    wineboot --init 2>/dev/null || true
    sleep 5
fi

# ============================================
# 4. Configure server settings
# ============================================
log "Applying server configuration..."
mkdir -p "$CONFIG_DIR"

# --- Determine PVP mode ---
SERVER_TYPE="${SERVER_TYPE:-pve}"
case "$SERVER_TYPE" in
    pvp)  PVP_ENABLED="True" ;;
    pve-c) PVP_ENABLED="True" ;;
    *)    PVP_ENABLED="False" ;;
esac

# --- Engine.ini ---
cat > "$CONFIG_DIR/Engine.ini" << EOF
[URL]
Port=${SERVER_PORT:-7777}

[OnlineSubsystemSteam]
ServerName=${SERVER_NAME:-Conan Exiles Server}
ServerPassword=${SERVER_PASSWORD:-}

[/Script/OnlineSubsystemUtils.IpNetDriver]
MaxClientRate=${MAX_CLIENT_RATE:-15000}
MaxInternetClientRate=${MAX_CLIENT_RATE:-15000}
EOF

# --- ServerSettings.ini ---
cat > "$CONFIG_DIR/ServerSettings.ini" << EOF
[ServerSettings]
AdminPassword=${ADMIN_PASSWORD:-}
ServerPassword=${SERVER_PASSWORD:-}
ServerMessageOfTheDay=${SERVER_MOTD:-}
serverRegion=${SERVER_REGION:-0}
ServerCommunity=0
MaxNudity=${MAX_NUDITY:-2}
PVPEnabled=${PVP_ENABLED}
IsBattlEyeEnabled=${BATTLEYE_ENABLED:-False}
IsVACEnabled=False

# --- Combat & Damage ---
PlayerDamageMultiplier=${PLAYER_DAMAGE:-1.0}
PlayerDamageTakenMultiplier=${PLAYER_DAMAGE_TAKEN:-1.0}
NPCDamageMultiplier=${NPC_DAMAGE:-1.0}
NPCDamageTakenMultiplier=${NPC_DAMAGE_TAKEN:-1.0}
ThrallDamageToPlayersMultiplier=${THRALL_DAMAGE_TO_PLAYERS:-0.5}
ThrallDamageToNPCsMultiplier=${THRALL_DAMAGE_TO_NPCS:-0.5}
MinionDamageMultiplier=${MINION_DAMAGE:-1.0}
MinionDamageTakenMultiplier=${MINION_DAMAGE_TAKEN:-1.0}
FriendlyFireDamageMultiplier=${FRIENDLY_FIRE_DAMAGE:-0.25}
StructureDamageMultiplier=${STRUCTURE_DAMAGE:-1.0}
StructureHealthMultiplier=${STRUCTURE_HEALTH:-1.0}
BuildingDamageMultiplier=${BUILDING_DAMAGE:-1.0}
CanDamagePlayerOwnedStructures=${CAN_DAMAGE_OWN_STRUCTURES:-False}
PlayerKnockbackMultiplier=${PLAYER_KNOCKBACK:-1.0}
NPCKnockbackMultiplier=${NPC_KNOCKBACK:-1.0}
EnableTargetLock=${ENABLE_TARGET_LOCK:-True}
EnableFatalities=${ENABLE_FATALITIES:-True}

# --- Death & Looting ---
ContainersIgnoreOwnership=${EVERYBODY_LOOT_CORPSE:-True}
LogoutCharactersRemainInTheWorld=${LOGOUT_REMAIN:-True}
UnconsciousTimeSeconds=${UNCONSCIOUS_TIME:-1800.0}
CorpsesPerPlayer=${CORPSES_PER_PLAYER:-10}
PlayerCorpseLifeTime=${PLAYER_CORPSE_LIFETIME:-1800.0}
NPCCorpseLifeTime=${NPC_CORPSE_LIFETIME:-600.0}

# --- XP & Progression ---
PlayerXPRateMultiplier=${XP_RATE:-1.0}
PlayerXPKillMultiplier=${XP_KILL:-1.0}
PlayerXPHarvestMultiplier=${XP_HARVEST:-1.0}
PlayerXPCraftMultiplier=${XP_CRAFT:-1.0}
PlayerXPTimeMultiplier=${XP_TIME:-1.0}

# --- Harvesting & Crafting ---
HarvestAmountMultiplier=${HARVEST_AMOUNT:-1.0}
ResourceRespawnSpeedMultiplier=${RESOURCE_RESPAWN:-1.0}
ItemSpoilRateScale=${ITEM_SPOIL_RATE:-1.0}
CraftingCostMultiplier=${CRAFTING_COST:-1.0}
FuelBurnTimeMultiplier=${FUEL_BURN_TIME:-1.0}
ItemConvertionMultiplier=${ITEM_CONVERSION:-1.0}

# --- Stamina & Movement ---
PlayerStaminaCostMultiplier=${STAMINA_COST:-1.0}
PlayerStaminaCostSprintMultiplier=${STAMINA_SPRINT_COST:-1.0}
PlayerMovementSpeedScale=${PLAYER_MOVE_SPEED:-1.0}
PlayerSprintSpeedScale=${PLAYER_SPRINT_SPEED:-1.0}
PlayerHealthRegenSpeedScale=${HEALTH_REGEN_SPEED:-1.0}
PlayerEncumbranceMultiplier=${PLAYER_ENCUMBRANCE:-1.0}
PlayerEncumbrancePenaltyMultiplier=${PLAYER_ENCUMBRANCE_PENALTY:-1.0}

# --- Thralls & Followers ---
ThrallConversionMultiplier=${THRALL_CONVERSION:-1.0}
ThrallScoutingTimeMinutes=${THRALL_SCOUTING_TIME:-10.0}
UseMinionPopulationLimit=${USE_MINION_POP_LIMIT:-False}
MinionPopulationBaseValue=${MINION_POP_BASE:-50}
MinionPopulationPerPlayer=${MINION_POP_PER_PLAYER:-5}
EnableFollowerDbno=True
EnableFollowerRescueOnLandClaimOnly=${ENABLE_FOLLOWER_RESCUE:-True}
FollowerRescueCooldown=${FOLLOWER_RESCUE_COOLDOWN:-3600}
DamageCooldownBeforeRescue=${DAMAGE_COOLDOWN_RESCUE:-600}
ThrallCorruptionRemovalMultiplier=${THRALL_CORRUPTION_REMOVAL:-1.0}
PlayerCorruptionGainMultiplier=${PLAYER_CORRUPTION_GAIN:-1.0}
PlayerCorruptionGainFromSorceryMultiplier=${PLAYER_CORRUPTION_SORCERY:-1.0}
AnimalPenCraftingTimeMultiplier=${ANIMAL_PEN_CRAFT_TIME:-1.0}
FeedBoxRangeMultiplier=${FEED_BOX_RANGE:-1.0}

# --- Building & Decay ---
LandClaimRadiusMultiplier=${LAND_CLAIM_RADIUS:-1.0}
AllowBuildingAnywhere=${BUILD_ANYWHERE:-False}
BuildingPickupEnabled=${BUILDING_PICKUP:-True}
StabilityLossMultiplier=${STABILITY_LOSS:-1.0}
DisableBuildingAbandonment=${DISABLE_BUILDING_DECAY:-True}
MaxBuildingDecayTime=${MAX_BUILDING_DECAY_TIME:-1296000.0}
MaxDecayTimeToAutoDemolish=${MAX_DECAY_AUTO_DEMOLISH:-604800.0}
DisableThrallDecay=${DISABLE_THRALL_DECAY:-True}
ThrallDecayTime=${THRALL_DECAY_TIME:-1296000.0}
BuildingDecayTimeMultiplier=${BUILDING_DECAY_MULTIPLIER:-1.0}
PoiProtectionEnabled=${POI_PROTECTION:-False}
BuildingValidationEnabled=${BUILDING_VALIDATION:-False}

# --- NPC & World ---
NPCHealthMultiplier=${NPC_HEALTH:-1.0}
NPCRespawnMultiplier=${NPC_RESPAWN:-1.0}
NPCMaxSpawnCapMultiplier=${NPC_MAX_SPAWN_CAP:-1.0}
MaxAggroRange=${MAX_AGGRO_RANGE:-9000.0}
AmbientLifeEnabled=${AMBIENT_LIFE:-True}
NPCMindReadingMode=0
ClampBuildingDamageToResources=True

# --- Events & Features ---
AvatarsDisabled=${AVATARS_DISABLED:-False}
AvatarLifetime=${AVATAR_LIFETIME:-600.0}
AvatarSummonTime=${AVATAR_SUMMON_TIME:-60.0}
EventSystemEnabled=${EVENT_SYSTEM:-True}
serverVoiceChat=${VOICE_CHAT:-1}
ShowOnlinePlayers=${SHOW_ONLINE_PLAYERS:-0}
MaxDeathMapMarkers=${MAX_DEATH_MARKERS:-3}
EnableClanMarkers=True
bCanBeDamaged=True

# --- AFK & Network ---
KickAFKPercentage=80
KickAFKTime=${KICK_AFK_TIME:-2700}
MaxAllowedPing=${MAX_ALLOWED_PING:-0}
EnableLoginQueue=${ENABLE_LOGIN_QUEUE:-True}
DisconnectionGraceTime=180
AllowFamilySharedAccount=${ALLOW_FAMILY_SHARE:-True}

# --- Region Restrictions ---
RegionAllowAfrica=${REGION_ALLOW_AFRICA:-True}
RegionAllowAsia=${REGION_ALLOW_ASIA:-True}
RegionAllowCentralEurope=${REGION_ALLOW_CENTRAL_EUROPE:-True}
RegionAllowEasternEurope=${REGION_ALLOW_EASTERN_EUROPE:-True}
RegionAllowWesternEurope=${REGION_ALLOW_WESTERN_EUROPE:-True}
RegionAllowNorthAmerica=${REGION_ALLOW_NORTH_AMERICA:-True}
RegionAllowOceania=${REGION_ALLOW_OCEANIA:-True}
RegionAllowSouthAmerica=${REGION_ALLOW_SOUTH_AMERICA:-True}
RegionBlockList=

[RCON]
RconEnabled=${RCON_ENABLED:-False}
RconPassword=${RCON_PASSWORD:-}
RconPort=${RCON_PORT:-25575}
RconMaxKarma=60

[/Script/Engine.GameSession]
MaxPlayers=${MAX_PLAYERS:-40}
EOF

log "Configuration applied:"
log "  Server Name: ${SERVER_NAME:-Conan Exiles Server}"
log "  Server Type: ${SERVER_TYPE:-pve}"
log "  Max Players: ${MAX_PLAYERS:-40}"
log "  Region: ${SERVER_REGION:-0}"
log "  RCON: ${RCON_ENABLED:-False}"
[ -n "$SERVER_PASSWORD" ] && log "  Password: Protected" || log "  Password: None (public)"

# ============================================
# 5. Start server
# ============================================
log "Starting Conan Exiles Dedicated Server..."
log "============================================"

cd "$GAME_DIR"
xvfb-run --auto-servernum wine ConanSandboxServer.exe -log \
    -Port=${SERVER_PORT:-7777} \
    -QueryPort=${QUERY_PORT:-27015} \
    -MaxPlayers=${MAX_PLAYERS:-40}

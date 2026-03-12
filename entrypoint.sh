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
# 1. Download / Update game
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
# 2. Initialize Wine prefix
# ============================================
if [ ! -d "$WINEPREFIX/drive_c" ]; then
    log "Initializing Wine prefix..."
    wineboot --init 2>/dev/null || true
    sleep 5
fi

# ============================================
# 3. Configure server settings
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
ServerCommunity=${SERVER_COMMUNITY:-0}
MaxNudity=${MAX_NUDITY:-2}
PVPEnabled=${PVP_ENABLED}
IsBattlEyeEnabled=${BATTLEYE_ENABLED:-False}
IsVACEnabled=${VAC_ENABLED:-False}
CreativeModeServer=${CREATIVE_MODE:-0}
CombatModeModifier=${COMBAT_MODE_MODIFIER:-0}

# --- Combat & Damage ---
PlayerDamageMultiplier=${PLAYER_DAMAGE:-1.0}
PlayerDamageTakenMultiplier=${PLAYER_DAMAGE_TAKEN:-1.0}
NPCDamageMultiplier=${NPC_DAMAGE:-1.0}
NPCDamageTakenMultiplier=${NPC_DAMAGE_TAKEN:-1.0}
MinionDamageMultiplier=${MINION_DAMAGE:-1.0}
MinionDamageTakenMultiplier=${MINION_DAMAGE_TAKEN:-1.0}
ThrallDamageToPlayersMultiplier=${THRALL_DAMAGE_TO_PLAYERS:-0.5}
ThrallDamageToNPCsMultiplier=${THRALL_DAMAGE_TO_NPCS:-0.5}
FriendlyFireDamageMultiplier=${FRIENDLY_FIRE_DAMAGE:-0.25}
StructureDamageMultiplier=${STRUCTURE_DAMAGE:-1.0}
StructureHealthMultiplier=${STRUCTURE_HEALTH:-1.0}
BuildingDamageMultiplier=${BUILDING_DAMAGE:-1.0}
CanDamagePlayerOwnedStructures=${CAN_DAMAGE_OWN_STRUCTURES:-False}
DynamicBuildingDamage=${DYNAMIC_BUILDING_DAMAGE:-False}
DynamicBuildingDamagePeriod=${DYNAMIC_BUILDING_DAMAGE_PERIOD:-1800}
PlayerKnockbackMultiplier=${PLAYER_KNOCKBACK:-1.0}
NPCKnockbackMultiplier=${NPC_KNOCKBACK:-1.0}
ConciousnessDamageMultiplier=${CONSCIOUSNESS_DAMAGE:-1.0}
EnableTargetLock=${ENABLE_TARGET_LOCK:-True}
EnableFatalities=${ENABLE_FATALITIES:-True}
PvPMountEnduranceDamageMultiplier=${PVP_MOUNT_ENDURANCE_DAMAGE:-1.0}
ClampBuildingDamageToResources=${CLAMP_BUILDING_DAMAGE:-True}

# --- Death & Looting ---
EverybodyCanLootCorpse=${EVERYBODY_LOOT_CORPSE:-True}
ContainersIgnoreOwnership=${CONTAINERS_IGNORE_OWNERSHIP:-True}
LogoutCharactersRemainInTheWorld=${LOGOUT_REMAIN:-True}
UnconsciousTimeSeconds=${UNCONSCIOUS_TIME:-1800.0}
OfflinePlayersUnconsciousBodiesHours=${OFFLINE_BODY_HOURS:-168}
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
ItemRepairMinimumDurability=${ITEM_REPAIR_MIN_DURABILITY:-0.1}
ItemRepairDurabilityLossPenaltyChance=${ITEM_REPAIR_PENALTY_CHANCE:-1.0}
ItemRepairDurabilityLossByRepairkitTier=${ITEM_REPAIR_LOSS_BY_TIER:-(0.200000,0.200000,0.150000,0.100000,0.050000,0.025000)}

# --- Stamina & Movement ---
PlayerStaminaCostMultiplier=${STAMINA_COST:-1.0}
PlayerStaminaCostSprintMultiplier=${STAMINA_SPRINT_COST:-1.0}
PlayerMovementSpeedScale=${PLAYER_MOVE_SPEED:-1.0}
PlayerSprintSpeedScale=${PLAYER_SPRINT_SPEED:-1.0}
PlayerHealthRegenSpeedScale=${HEALTH_REGEN_SPEED:-1.0}
PlayerStaminaRegenSpeedScale=${STAMINA_REGEN_SPEED:-1.0}
PlayerEncumbranceMultiplier=${PLAYER_ENCUMBRANCE:-1.0}
PlayerEncumbrancePenaltyMultiplier=${PLAYER_ENCUMBRANCE_PENALTY:-1.0}
StaminaRegenerationTime=${STAMINA_REGEN_TIME:-3.75}
StaminaExhaustionTime=${STAMINA_EXHAUSTION_TIME:-3.75}
StaminaStaticRegenRateMultiplier=${STAMINA_STATIC_REGEN:-1.0}
StaminaMovingRegenRateMultiplier=${STAMINA_MOVING_REGEN:-1.0}
StaminaOnConsumeRegenPause=${STAMINA_CONSUME_PAUSE:-1.3}
StaminaOnExhaustionRegenPause=${STAMINA_EXHAUSTION_PAUSE:-2.75}

# --- Thralls & Followers ---
ThrallConversionMultiplier=${THRALL_CONVERSION:-1.0}
ThrallScoutingTimeMinutes=${THRALL_SCOUTING_TIME:-10.0}
ThrallExclusionRadius=${THRALL_EXCLUSION_RADIUS:-100.0}
ThrallMinDistanceAwayFromHome=${THRALL_MIN_DISTANCE:-5000.0}
ThrallTeleportingCooldown=${THRALL_TELEPORT_COOLDOWN:-10.0}
UseMinionPopulationLimit=${USE_MINION_POP_LIMIT:-False}
MinionPopulationBaseValue=${MINION_POP_BASE:-50}
MinionPopulationPerPlayer=${MINION_POP_PER_PLAYER:-5}
MinionOverpopulationCleanup=${MINION_OVERPOP_CLEANUP:-60}
MinionOverpopulationAllowed=${MINION_OVERPOP_ALLOWED:-10}
EnableFollowerDbno=${ENABLE_FOLLOWER_DBNO:-True}
EnableFollowerRescueOnLandClaimOnly=${ENABLE_FOLLOWER_RESCUE:-True}
EnableFollowerRescueInBuildExclusionZone=${FOLLOWER_RESCUE_EXCLUSION:-False}
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
BuildingDecayTimePerScore=${BUILDING_DECAY_PER_SCORE:-5400.0}
BuildingDecayTimeMultiplier=${BUILDING_DECAY_MULTIPLIER:-1.0}
DecayCleanupTimeMultiplier=${DECAY_CLEANUP_MULTIPLIER:-2.0}
DecayBonusTimeRate=${DECAY_BONUS_TIME_RATE:-600.0}
DecayShowBuildingScore=${DECAY_SHOW_SCORE:-False}
PoiProtectionEnabled=${POI_PROTECTION:-False}
BuildingValidationEnabled=${BUILDING_VALIDATION:-False}
BuildingPreloadRadius=${BUILDING_PRELOAD_RADIUS:-80.0}
DisableLandclaimNotifications=${DISABLE_LANDCLAIM_NOTIFICATIONS:-True}
CampsIgnoreLandclaim=${CAMPS_IGNORE_LANDCLAIM:-True}

# --- NPC & World ---
NPCHealthMultiplier=${NPC_HEALTH:-1.0}
NPCRespawnMultiplier=${NPC_RESPAWN:-1.0}
NPCMaxSpawnCapMultiplier=${NPC_MAX_SPAWN_CAP:-1.0}
MaxAggroRange=${MAX_AGGRO_RANGE:-9000.0}
AmbientLifeEnabled=${AMBIENT_LIFE:-True}
NPCMindReadingMode=${NPC_MIND_READING:-0}
DogsOfTheDesertSpawnWithDogs=${DOGS_SPAWN_WITH_DOGS:-False}
CrossDesertOnce=${CROSS_DESERT_ONCE:-True}

# --- PVP Schedule (for pve-c mode) ---
RestrictPVPTime=${RESTRICT_PVP_TIME:-False}
RestrictPVPBuildingDamageTime=${RESTRICT_PVP_BUILDING_DAMAGE_TIME:-False}
DisableBuildingDuringTimeRestrictedPVP=${DISABLE_BUILDING_DURING_PVP:-False}
PVPTimeMondayStart=${PVP_TIME_MON_START:-0}
PVPTimeMondayEnd=${PVP_TIME_MON_END:-0}
PVPTimeTuesdayStart=${PVP_TIME_TUE_START:-0}
PVPTimeTuesdayEnd=${PVP_TIME_TUE_END:-0}
PVPTimeWednesdayStart=${PVP_TIME_WED_START:-0}
PVPTimeWednesdayEnd=${PVP_TIME_WED_END:-0}
PVPTimeThursdayStart=${PVP_TIME_THU_START:-0}
PVPTimeThursdayEnd=${PVP_TIME_THU_END:-0}
PVPTimeFridayStart=${PVP_TIME_FRI_START:-0}
PVPTimeFridayEnd=${PVP_TIME_FRI_END:-0}
PVPTimeSaturdayStart=${PVP_TIME_SAT_START:-0}
PVPTimeSaturdayEnd=${PVP_TIME_SAT_END:-0}
PVPTimeSundayStart=${PVP_TIME_SUN_START:-0}
PVPTimeSundayEnd=${PVP_TIME_SUN_END:-0}
PVPBuildingDamageTimeMondayStart=${PVP_BUILD_MON_START:-0}
PVPBuildingDamageTimeMondayEnd=${PVP_BUILD_MON_END:-0}
PVPBuildingDamageTimeTuesdayStart=${PVP_BUILD_TUE_START:-0}
PVPBuildingDamageTimeTuesdayEnd=${PVP_BUILD_TUE_END:-0}
PVPBuildingDamageTimeWednesdayStart=${PVP_BUILD_WED_START:-0}
PVPBuildingDamageTimeWednesdayEnd=${PVP_BUILD_WED_END:-0}
PVPBuildingDamageTimeThursdayStart=${PVP_BUILD_THU_START:-0}
PVPBuildingDamageTimeThursdayEnd=${PVP_BUILD_THU_END:-0}
PVPBuildingDamageTimeFridayStart=${PVP_BUILD_FRI_START:-0}
PVPBuildingDamageTimeFridayEnd=${PVP_BUILD_FRI_END:-0}
PVPBuildingDamageTimeSaturdayStart=${PVP_BUILD_SAT_START:-0}
PVPBuildingDamageTimeSaturdayEnd=${PVP_BUILD_SAT_END:-0}
PVPBuildingDamageTimeSundayStart=${PVP_BUILD_SUN_START:-0}
PVPBuildingDamageTimeSundayEnd=${PVP_BUILD_SUN_END:-0}

# --- Avatars/Gods ---
AvatarsDisabled=${AVATARS_DISABLED:-False}
AvatarLifetime=${AVATAR_LIFETIME:-600.0}
AvatarSummonTime=${AVATAR_SUMMON_TIME:-60.0}
AvatarDomeDurationMultiplier=${AVATAR_DOME_DURATION:-1.0}
AvatarDomeDamageMultiplier=${AVATAR_DOME_DAMAGE:-1.0}
RestrictAvatarSummoningTime=${RESTRICT_AVATAR_TIME:-False}
AvatarSummoningTimeWeekdayStart=${AVATAR_WEEKDAY_START:-0}
AvatarSummoningTimeWeekdayEnd=${AVATAR_WEEKDAY_END:-0}
AvatarSummoningTimeWeekendStart=${AVATAR_WEEKEND_START:-0}
AvatarSummoningTimeWeekendEnd=${AVATAR_WEEKEND_END:-0}

# --- Events & Features ---
EventSystemEnabled=${EVENT_SYSTEM:-True}
serverVoiceChat=${VOICE_CHAT:-1}
ShowOnlinePlayers=${SHOW_ONLINE_PLAYERS:-0}
MaxDeathMapMarkers=${MAX_DEATH_MARKERS:-3}
EnableClanMarkers=${ENABLE_CLAN_MARKERS:-True}
HealthbarVisibilityDistance=${HEALTHBAR_DISTANCE:-15000.0}
DisableChatFormatting=${DISABLE_CHAT_FORMATTING:-False}
bCanBeDamaged=True

# --- AFK & Network ---
KickAFKPercentage=${KICK_AFK_PERCENTAGE:-80}
KickAFKTime=${KICK_AFK_TIME:-2700}
MaxAllowedPing=${MAX_ALLOWED_PING:-0}
EnableLoginQueue=${ENABLE_LOGIN_QUEUE:-True}
DisconnectionGraceTime=${DISCONNECTION_GRACE_TIME:-180}
AllowFamilySharedAccount=${ALLOW_FAMILY_SHARE:-True}

# --- Event Log Privacy ---
EventLogPvPCauserPrivacy=${EVENT_LOG_PVP_PRIVACY:-2}
EventLogPvECauserPrivacy=${EVENT_LOG_PVE_PRIVACY:-1}

# --- Validation & Anti-Exploit ---
ValidatePlayerStats=${VALIDATE_PLAYER_STATS:-False}
AllowedTimeUndermesh=${ALLOWED_TIME_UNDERMESH:--1.0}
AllowedDistanceUndermeshSquared=${ALLOWED_DISTANCE_UNDERMESH:-490000.0}
CapCharacterLayoutScalarParams=${CAP_CHARACTER_LAYOUT:-False}
PathFollowingSendsAngularVelocity=${PATH_FOLLOWING_ANGULAR:-False}

# --- Region Restrictions ---
RegionAllowAfrica=${REGION_ALLOW_AFRICA:-True}
RegionAllowAsia=${REGION_ALLOW_ASIA:-True}
RegionAllowCentralEurope=${REGION_ALLOW_CENTRAL_EUROPE:-True}
RegionAllowEasternEurope=${REGION_ALLOW_EASTERN_EUROPE:-True}
RegionAllowWesternEurope=${REGION_ALLOW_WESTERN_EUROPE:-True}
RegionAllowNorthAmerica=${REGION_ALLOW_NORTH_AMERICA:-True}
RegionAllowOceania=${REGION_ALLOW_OCEANIA:-True}
RegionAllowSouthAmerica=${REGION_ALLOW_SOUTH_AMERICA:-True}
RegionBlockList=${REGION_BLOCK_LIST:-}

# --- Mods ---
ServerModList=${SERVER_MOD_LIST:-}
BlueprintConfigVersion=25

[RCON]
RconEnabled=${RCON_ENABLED:-False}
RconPassword=${RCON_PASSWORD:-}
RconPort=${RCON_PORT:-25575}
RconMaxKarma=${RCON_MAX_KARMA:-60}

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
# 4. Start server
# ============================================
log "Starting Conan Exiles Dedicated Server..."
log "============================================"

cd "$GAME_DIR"
xvfb-run --auto-servernum wine ConanSandboxServer.exe -log \
    -Port=${SERVER_PORT:-7777} \
    -QueryPort=${QUERY_PORT:-27015} \
    -MaxPlayers=${MAX_PLAYERS:-40}

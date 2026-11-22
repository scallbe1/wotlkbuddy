# Horde Mage questing integration — v0.11 (planning foundation)

This is a **read-only foundation**, not a working autonomous Horde Mage quester.
No attack, target, gossip, trainer, bank, talent, inventory, or pathfinding
commands are sent by these new planner commands.

## Upstream sources

- [Likon69/CopilotBuddy](https://github.com/Likon69/CopilotBuddy): the C# quest
  behavior engine (QuestOrder, objective types, loot, targeting, NPC turn-ins).
- [Likon69/Questing-profiles](https://github.com/Likon69/Questing-profiles):
  `Auto-Loader.xml`, `Profile v3/Horde/` quest sequences, NPC world positions,
  vendors, Mage trainers, objective targets, and hotspots.

The runtime profiles are referenced **externally**; they are not redistributed
in WotLKBuddy. Their XML includes conditional C# expressions and custom
behaviors. WotLKBuddy never evaluates arbitrary C#/Lua from these files.

## On Windows: install real leveling profiles and audit

```powershell
cd C:\Users\seanc
# Clone if not already present; this does not edit your WoW installation.
git clone https://github.com/Likon69/Questing-profiles.git
cd C:\Users\seanc\wotlkbuddy-v05
py -3.12 -m wotlkbuddy horde-profile-audit --profiles-root "$HOME\Questing-profiles" --race Undead --level 8
```

When a Horde Mage is logged in, add optional resource telemetry:

```powershell
py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\world of warcraft 3.3.5a hd"
```

Restart WoW or `/reload`; the upper-left telemetry pixel block must remain
visible. Run the following from PowerShell and bring WoW to the front during
the countdown:

```powershell
py -3.12 -m wotlkbuddy horde-mage-plan --profiles-root "$HOME\Questing-profiles" --race Undead --countdown 5
```

The planner will report one next **proposed** action and the missing driver
required for execution. `--world-map-id` may be used only with a **verified**
world map ID (different from the addon map zone index); unknown conditions
remain blocked if it is omitted. `--profile` can be used instead of
`--profiles-root` for an explicitly selected Horde XML file.

## Decisions and priorities

1. Death recovery, followed by combat if engaged.
2. HP recovery below 40%, then mana recovery below 30%.
3. Full/near-full bags; worn equipment; unspent talent points.
4. Quest order: pick up, travel, kill/collect/use, turn in, and advance profile.

A QuestLog entry disappearing does **not** prove it was turned in. Unobserved
spells, item counts, quest history, and world map ID are **unknown**, not guessed.
`CONDITION_UNKNOWN` and `PROFILE_ACTION_UNSUPPORTED` represent explicit blockers.

The profile's `WORLD X/Y/Z` cannot be sent to `live-follow`: it accepts zone
map fractions between 0 and 1. This is a fundamental interface mismatch,
not a scaling constant. CopilotBuddy uses live world positions and Recast/
Detour navigation; our addon does not yet expose or reconstruct the needed
world position or 3D terrain-safe path.

## Capability gap before unattended questing

| Workstream | Existing | Required for execution |
| --- | --- | --- |
| Navigation | Recorded zone-map trail replay | Character world XYZ, Recast/Detour mmaps, path planning, recovery |
| Quest order | Horde XML + conditional audit + QuestLog | Full completed history, objective-ID progress, next-profile transitions |
| Quest interaction | NPC ID/location plans | Object enumeration, target selection, gossip, accept/reward confirmation |
| Combat | Health + target name/GUID; optional mana | Nearby hostile IDs/locations, combat state, spellbook/cooldowns, cast driver |
| Sustain | HP and optional mana/bag/free slots | Food/water item IDs, safe eat/drink casting, conjure, out-of-combat checks |
| Loot | Objective mob/item IDs in profile | Lootable corpse enumeration and item pickup verification |
| Gear, vendor, repair | Vendor IDs and optional durability/bags | Bag contents, protect-quest-item policy, prices, repair/sell actions |
| Training and talents | Profile Mage trainer IDs + unspent talent count | Known spells, cost, trainer dialog, selected Fire talent build, spending driver |

The `S` status record is an **optional extension to protocol v3** so old
versions of the addon still decode. A new addon install is required to see
mana, bag slots, durability, unspent talent points, class and race. It may
report `-1` when a local WoW API does not expose a metric.

**Safety:** Do not launch an unattended `live-follow` through arbitrary quest
world coordinates. No such mapping or quest execution is available in v0.11.

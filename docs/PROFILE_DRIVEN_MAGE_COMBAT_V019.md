# v0.19 — experimental profile-driven Mage kill objectives

**Status: not validated against the user's live WoW combat client.** The prior
v0.18.1 client had successfully accepted The Mindless Ones (#364); the next
profile action is a pair of kill objectives sourced from XML (NPC entries
1501 and 1502, five kills each). No NPC, quest name or kill count is encoded
into the new combat engine. This first driver supports only an **Undead Mage
using Fireball** and only **KillMob** objectives. This is attended automation,
not a finished unattended leveler.

## Install and bottom-left telemetry

```powershell
cd C:\Users\seanc\wotlkbuddy-v05
git fetch origin
git switch --track origin/feature/profile-mage-combat-v0.19
py -3.12 -m pip install -e ".[live,dev]"
$wowDir = Split-Path (Get-Process Wow | Select-Object -First 1 -ExpandProperty Path) -Parent
py -3.12 -m wotlkbuddy install-addon --wow-dir "$wowDir"
```

Type `/reload` **in WoW** after installing the addon. The telemetry RGB grid
will be at the **bottom-left** rather than top-center, away from bottom-right
mouse-over tooltips. The marker scanner discovers it from its RGB magic header;
no monitor-coordinate flags need changing.

## New combat observations

The read-only 3.3.5a addon now also emits these bounded records:

- `T`: target NPC template entry derived from its **creature GUID**, HP/max HP,
  dead/attackable flags, and Fireball range (unknown remains unknown).
- `B`: whether Arcane Intellect / Frost Armor are learned and active, and
  whether Fireball is learned. The class routine refreshes known missing buffs
  before a fight, waits to observe them and allows mana to regenerate.
- `L`: number of loot slots in an open LootFrame. After a kill the bot may
  navigate to the observed corpse and interact/loot if still safe.
- `C`: completion-history flags for **recently observed quests** and cached
  server-completed quests, not only fixed quest 363.

## Trial from Deathknell

Make sure quest 364 is in the journal, W/A/D/Q/E remain standard movement
bindings, F is **Interact with Target**, and the character knows **Fireball**.
Start near Deathknell in a safe spot; close unrelated WoW windows. Supervise
movement, using Escape to stop. All input is gated by **both** flags:

```powershell
py -3.12 -m wotlkbuddy horde-next `
  --profiles-root "$HOME\Questing-profiles" `
  --nav-dll "$HOME\CopilotNav\Navigation.dll" `
  --python32 "$HOME\Python312-32\python.exe" `
  --spawns-db "$HOME\CopilotBuddy\datadb\CreatureSpawns.db" `
  --interact-key f `
  --enable-input --enable-combat `
  --countdown 5 --max-seconds 900 --max-kills 18
```

`horde-next` without `--enable-combat` still only prints the intended kill
objective, with no fighting or movement. When combat is opted in, the bounded
hunt will read the journal's unfinished objectives in XML order; find the
matching *creature template IDs* in local spawns near profile hotspots;
acquire/check targets; path with Detour; maintain Arcane Intellect/Frost Armor
if known; cast Fireball only at verified eligible targets within range;
observe HP and objective progression; optionally approach/loot a verified
corpse; and repeat until the journal reports all objectives complete.

The driver **does not automatically turn in** quest 364 after killing. Invoke
`horde-next` again near the quest giver for the next profile action. The generic
quest-turn-in adapter remains separate.

## Recovering from an auto-cleared target after Fireball

WoW may clear the selected creature GUID as a lethal Fireball lands. After
a target change, the driver now waits for several fresh addon frames and checks
the **specific** active KillMob journal objective for an increased kill count.
Only an observed increase permits another target-search iteration. If the quest
counter does not advance, it stops without casting at another creature.

A corpse whose GUID is no longer selected is not approached for looting.
This does not solve every case of ranged target acquisition or lost aggro.

The server's live journal (for example, `0/8`) is authoritative when it differs
from the profile XML count (for example, `x5`). The quest's actual completion
flags drive movement to the next species.

## Stop conditions and known limitations

- No client version other than compatible WoW 3.3.5a build 12340 is verified.
- If a hostile unrelated to the *current* objective attacks, combat stops;
  generic self-defence is not yet implemented.
- If target coordinates, Fireball range, navmesh path or combat spell telemetry
  cannot be verified, no blind chase or attack is attempted.
- If Fireball does no observed HP damage across three casts, if health falls
  below 25%, if mana remains insufficient, or if the selected GUID changes
  unexpectedly, the driver stops rather than spamming spells.
- No resurrection, multi-target combat, pathing between zones, vendor selling,
  consumable use, class-specialization rotations, arbitrary quest objectives,
  aggressive pull steering or robust post-combat corpse recovery yet.
- The new code has synthetic/offline tests but **has not fought a real enemy**.
  Treat this as the first supervised integration run and share its full output
  for refining target acquisition and travel/loot around Deathknell.

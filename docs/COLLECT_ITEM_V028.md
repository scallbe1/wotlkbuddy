# v0.28 — Generic XML-backed CollectItem combat, looting and quest completion

## Starting point: The Damned

`horde-next` reports quest 376's first CollectItem objective: item 3265 x6.
The quest also requires a second collection objective. Unlike KillMob,
collecting loot requires **explicit creature-source mapping**, **exact
corpse identity**, and **inventory + journal progress confirmation**.
No quest-specific source or NPC IDs are hardcoded into execution.

## What source data is used

- Selected Horde profile QuestOrder: CollectItem ItemId and Count.
- Selected Horde XML <Quest>/<Objective Type='CollectItem'> entries:
  item ID/count, nested <Mob Id='...'> entries and <Hotspot X/Y/Z>.
- CreatureSpawns.db: mapped mob template IDs and world X/Y/Z spawns.
- Live telemetry: quest objective type, text, current/required count,
  target GUID/template, corpse dead state, loot window status, item inventory.

The new addon allows `/wbttrack <item_id>` (bounded integer) and emits
read-only `J` telemetry with the exact requested item ID, bag item count and (when cached) localized item name. The bot cross-matches the item name to the journal objective, so differing QuestOrder, XML definition and journal order cannot silently select the wrong item.
Before combat, Python requires that the reported item ID matches XML.
After each corpse loot, it verifies the item count and journal objective.
When one item goal completes, the bot switches to the next CollectItem
stage in the same quest. After the complete quest is observed, the existing
guarded return/turn-in driver returns to the profile NPC.

## Source data confirmed upstream for The Damned

The public Likon69 Horde starter profile defines both item sources and nearby hotspots: item 3265 from creature entries 1508/1509 (scavengers), item 3264 from entries 1512/1513 (duskbats). Its QuestOrder lists 3265 then 3264, whereas the Quest objective definitions list 3264 then 3265. The collector binds item IDs to observed journal objectives by the client-reported localized item name instead of relying on XML list position. The user's installed profile still needs to be checked for matching source data.

## Critical source limitation

If the selected XML lacks nested Mob source IDs (or a valid Quest
objective declaration), the bot **does not guess**. The next-step
read-only output now contains `collection_sources.mob_source_ids` and
`ready_to_farm`. `horde-progress` returns an explicit
`missing_item_mob_sources` status without issuing combat commands.
A plain CreatureSpawns.db contains positions, not item-drop relations.
Use a trusted loot-source database/profiles with explicit item->mob
mappings if the installed XML does not provide them.

## Install after merge

This update changes Python **and Lua addon telemetry**, so reinstall both:

    cd C:\Users\seanc\wotlkbuddy-v05
    git switch main
    git pull --ff-only
    py -3.12 -m pip install -e ".[live,dev]"
    py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\world of warcraft 3.3.5a hd"

In WoW: `/reload`; keep the visible RGB telemetry canvas enabled.

## First, inspect the mapping without moving:

    py -3.12 -m wotlkbuddy horde-next --profiles-root "$HOME\Questing-profiles" --countdown 5

If `collection_sources.ready_to_farm` is true, to execute the profile
step in one attended session:

    py -3.12 -m wotlkbuddy horde-next `
      --profiles-root "$HOME\Questing-profiles" `
      --nav-dll "$HOME\CopilotNav\Navigation.dll" `
      --python32 "$HOME\Python312-32\python.exe" `
      --spawns-db "$HOME\CopilotBuddy\datadb\CreatureSpawns.db" `
      --interact-key f --enable-input --enable-combat --countdown 5

Alternatively use the existing `horde-progress --only objectives` command.
Both paths remain bounded: default 40 max kills for CollectItem objectives
and 900 seconds max, configurable with `--collect-max-kills` and
`--max-seconds`. A missing source, unexpected mob or unverified loot fails
closed. This is not a perpetual unattended profile-loop; it completes
one supported active quest, turns it in and exits.

CI/unit tests cannot prove the AzerothCore world data matches item drop
tables or that `LootSlot` actually works on the player's live server;
the first live run still needs verification.

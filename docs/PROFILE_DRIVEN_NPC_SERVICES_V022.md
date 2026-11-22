# v0.22 — Profile-driven NPC and service discovery

The player has verified end-to-end combat, turn-in, and new quest acceptance.
The next step removes the hardcoded NPC list in `horde-progress`.

## Data sources

- `Questing-profiles/Auto-Loader.xml` selects the active local Horde XML.
- `QuestOrder/PickUp` provides each quest NPC's giver ID, optional name, quest
  ID, conditions and optional world XYZ. The bot deduplicates nearby unresolved
  quest givers, preserving profile order. When name is absent, only a uniquely
  consistent name from another step of the same profile may supply it.
- `Vendors/Vendor` provides service provider NPC IDs (`Entry` **or** `Id`),
  names, `Type`, `TrainClass`, and world coordinates.
- When the selected profile has no suitable nearby trainer/vendor, a bounded
  read-only search over the installed `Questing-profiles/Horde/**/*.xml`
  indexes alternate Vendor entries. No remote services or source-code eval.
- `CopilotBuddy/datadb/CreatureSpawns.db` corroborates exact creature entry,
  map ID and world positions. It has no service classification itself.
- Navigation uses Detour, and interaction still requires a matching live target
  GUID and actual menu.

For training, choose the closest grounded `Type="Train" TrainClass="Mage"`
entry within the configured local radius, not a fixed NPC name or location.
The existing trainer routine buys **only** live available affordable,
level-eligible spells and verifies the service disappears.

For quest pickup, a bounded list of nearby unresolved profile quest givers
replaces the fixed Sarvis/Elreth loop. The existing gossip and direct-detail
quest verification routines remain responsible for acceptance.

For bag management, the profile `MinFreeBagSlots` threshold now determines
whether `horde-progress --only all` visits the nearest data-defined merchant.
There is an explicit `--only vendor` test stage. **Visiting a merchant is
not selling inventory**: this version stops rather than claiming the bags were
emptied. Automatic selling needs read-only merchant/item data and protected
item policies in a subsequent change.

## Installation after merging

This is Python-only: no addon reinstall is needed.

In PowerShell:

    cd C:\Users\seanc\wotlkbuddy-v05
    git switch main
    git pull --ff-only
    py -3.12 -m pip install -e ".[live,dev]"

To test quest-giver discovery (the names are sourced from your active XML):

    py -3.12 -m wotlkbuddy horde-progress --only quests `
      --profiles-root "$HOME\Questing-profiles" `
      --nav-dll "$HOME\CopilotNav\Navigation.dll" `
      --python32 "$HOME\Python312-32\python.exe" `
      --spawns-db "$HOME\CopilotBuddy\datadb\CreatureSpawns.db" `
      --interact-key f --enable-input --countdown 5

For Mage training, change `--only quests` to `--only train`.
For merchant location testing, use `--only vendor`; no sale occurs.

The navigation limit for local quest giver discovery is 160 yards
(relative to the command's initial player position), with a cap of twelve
NPC visits. For service discovery the radius is 600 yards. NPCs outside the
source profile's geographic area or with unsupported/contradictory metadata
are skipped, never fabricated.

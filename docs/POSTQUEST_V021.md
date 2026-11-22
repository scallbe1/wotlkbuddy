# WotLKBuddy v0.21 — Post-quest progression

This update adds three separately testable, attended steps after Aarr's
successful hand-in of The Mindless Ones.

## Equipment

The addon reports a complete scan of candidate cloth armor in bags and
currently equipped armor slots. Python permits only confirmed Mage-equippable
cloth items with required level no greater than Aarr's level. It compares
Mage attribute scores, with item level as a tiebreak, equips a verified
upgrade, and confirms the new item ID in the correct armor slot.

Staff, wand, one-hand, offhand, shield, rings, trinkets, and dual-wield
decisions are excluded. This is a basic scoring heuristic, not a complete
simulation of spell power and all secondary stats.

## Quest pickup

The bot visits Shadow Priest Sarvis (entry 1569) and Novice Elreth (1661)
via Detour and exact creature GUID verification. It accepts only actual,
uniquely observed available-quest menu entries and confirms new journal
IDs and titles. Custom server quests must be offered by the live NPC;
there is no hardcoded quest acceptance or level bypass.

## Class training

The bot visits Isabella (2124) and checks the actual trainer UI. It buys
only available services that satisfy level and money requirements, then
verifies that the purchased service is no longer available. Trainer menus
requiring an unrecognized gossip step stop without buying anything.
No talent points are spent; talent choices will be implemented separately.

## Installation after merging PR #39

In PowerShell:

    cd C:\Users\seanc\wotlkbuddy-v05
    git switch main
    git pull --ff-only
    py -3.12 -m pip install -e ".[live,dev]"
    py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\world of warcraft 3.3.5a hd"

In WoW: type /reload and /wbt show. Verify with:

    py -3.12 -m wotlkbuddy live-status --countdown 5

First check and equip Flax Gloves (only when better):

    py -3.12 -m wotlkbuddy horde-progress --only gear --enable-input --countdown 5

Then collect new quests:

    py -3.12 -m wotlkbuddy horde-progress --only quests   --nav-dll "$HOME\CopilotNav\Navigation.dll"   --python32 "$HOME\Python312-32\python.exe"   --spawns-db "$HOME\CopilotBuddy\datadb\CreatureSpawns.db"   --interact-key f --enable-input --countdown 5

For the trainer, change --only quests to --only train. After validating all
three stages, omit --only to run gear, quest pickup and training together.

Keep WoW foregrounded. Escape stops input. Navigation, quest menus and
trainer purchases have not yet been live-tested with this new version.

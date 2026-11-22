# v0.29 — Vendor detour, safe bag cleanup and return to quest hand-in

## Observed problem

The first successful CollectItem run for The Damned collected all quest
items, reached Novice Elreth, and selected the Flax Bracers reward. The
player's bags were full and GetQuestReward(1) did not receive a verified
server completion acknowledgement. Journal disappearance by itself cannot
prove success. The log also contains false 'Loot skipped' warnings while
quest item counts increased.

## New vendor behavior

Before starting objective hunts and again before turning in rewards,
the bot checks the actual free bag slot count. With fewer than
`max(2, MinFreeBagSlots + 1)` available it finds a merchant in the
selected Horde XML (or local XML merchant catalog), cross-checks its
world spawn against CreatureSpawns.db, follows a validated Detour path,
verifies merchant NPC name/GUID, then opens the observed MerchantFrame.
If the NPC offers services through gossip it selects a unique vendor-typed
option only after two stable frames.

The Lua addon scans bag slots while MerchantFrame is open and emits
merchant candidate records M/D. Python will sell only after a stable
complete item list verifies the same exact bag, slot, item ID, count,
quality, price, non-quest flag and merchant window. It calls bounded
UseContainerItem(bag,slot) for that item only and verifies disappearance,
a real increase in free bag slots, and nondecreasing currency before
proceeding. The merchant window is then closed and verified.

**Sale policy:** automatically sell grey vendor junk and unequipped
white/green/low-rarity wearable gear. Never sell quest-flagged goods,
any item listed as a CollectItem by the loaded XML, rare/epic gear,
bags, consumables, reagents, or unrecognized/locked items. If the
remaining safe-to-sell items cannot create enough room, stop rather
than discard valuable items. This is intentionally narrower than
'sell literally everything not currently required by a quest'.

## Recovery after a failed reward

When run with `--only objectives` or the new `--only turnin`, the bot
first checks live journal and server history for completed, still-active
quests. If exactly matched to an XML TurnIn step it first frees bag space
and then uses the existing GUID-verified return-and-turn-in routine.
It does NOT resubmit a missing quest with unknown completion history,
so a delayed server acknowledgement cannot generate duplicate rewards.

**Important for the current run:** if The Damned is still in the journal
as complete, the new command should sell safe goods, return to Novice
Elreth and claim reward. If it's absent, inspect the server completion
flag before assuming the earlier GetQuestReward failed.

## Installation

This update changes both Python and WoW addon telemetry. After merging:

    cd C:\Users\seanc\wotlkbuddy-v05
    git switch main
    git pull --ff-only
    py -3.12 -m pip install -e ".[live,dev]"
    py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\world of warcraft 3.3.5a hd"

In WoW: /reload. Run the existing `horde-progress --only objectives`
command or replace the mode with `--only turnin` to test just the
vendor/ready-reward recovery. Keep the nav DLL, x86 Python, spawn DB
and profile root arguments. This is an attended one-quest workflow;
no new infinite loop is enabled.

## Faster Fireball and reduced movement jitter

The addon now observes actionbar keys and only reports a Fireball shortcut
when the corresponding bound action button actually contains the Fireball
spell. The keyboard driver taps that key instead of opening chat and typing
/cast Fireball each time. If no such safe binding exists, it falls back to
the previous allowlisted chat command. It also observes live Fireball cast
time so the bot can begin the next cast at cast completion instead of
always waiting 2.65 seconds for a projectile to land; it does not use
unverified blind spell spam or change WoW bindings.

The Detour path follower now reuses verified keyboard turn calibration
for the same character/map and keyboard driver during one run. Movement
and turn directions remain checked against live position/facing on every
correction; only redundant D-key calibration before each short corpse
approach is removed. Forward holds remain bounded at 0.45 seconds.

Fast client auto-loot is recognized by an increase in the exact tracked
item inventory **and** matching live quest journal progress, even if the
LootFrame closes before RGB telemetry can observe it. Item/journal
contradictions are not considered confirmed loot.

All sales, native actionbar casting, and live vendor UI interactions
require first-run verification on the actual AzerothCore client; CI tests
run only against controlled simulated inputs.

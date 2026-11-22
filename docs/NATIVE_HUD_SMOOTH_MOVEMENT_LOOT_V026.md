# v0.26 — Smooth navigation, corpse looting and native in-game Goal / Action HUD

## Confirmed previous live outcome

- Quest #3901 Rattling the Rattlecages: 12/12 skeletons defeated.
- Quest #3901: server-confirmed turn-in at Shadow Priest Sarvis.
- Reward: Flax Boots (item 3274), equipped and verified.
- The log did NOT report Aarr's final level. New reports include
  character_level_before, character_level_after and levels_gained_this_run.

## Smooth movement

Validated Detour routes still supply all waypoints, and travel still checks
Escape, WoW foreground, health, combat, position, map and character GUID.
Forward-key presses are now distance-adaptive up to 0.52s instead of the
fixed 0.20s pulse: longer straight segments use fewer stop/start cycles,
while approaches to waypoints use short bounded taps. Turn calibration
and terrain collision recovery remain guarded. This is not movement
prediction or an unsafe shortcut across the navmesh.

## Recover and loot dead enemies

WoW can clear the target as a killing Fireball lands. The previous combat
code explicitly skipped all loot after that event, even with quest credit.
The bot now remembers the exact pre-kill creature GUID, uses the fixed
/targetlasttarget command, verifies the SAME GUID, creature template and
dead state, then navigates to its previously observed world position.
After clicking Interact, the new addon telemetry verifies the loot window
has actually opened; loot slots are collected and checked for disappearance.
Wrong/dead/missing targets and unavailable corpse XYZ are skipped with
clear logging rather than looting an unrelated creature.
Report fields: loot_attempts, loot_confirmed, loot_skipped. Empty or
unopened loot windows are not falsely reported as successful looting.

## Native WoW HUD, not an external Windows window

The previous Tk overlay was not visible on the player's WoW client,
despite its diagnostics reporting that a desktop window was created.
The new HUD is rendered by WotLKBuddyTelemetry.lua *inside WoW*.
It works in fullscreen because it is a game UI frame. Goal and Action
are shown at the top-left and stay noninteractive (no mouse capture).
Only allowlisted numeric /wbtgoal <quest_id> and fixed /wbtphase <phase>
commands are sent once when the objective phase changes. The addon reads
the matching journal quest title directly and uses WoW's own casting,
combat, movement, looting and trainer state to populate Action.
The RGB telemetry grid and checksum protocol remain separate and unchanged
except that the L row now also reports loot-frame visibility.

Manual visibility controls inside WoW:

    /wbthud show
    /wbthud hide

## Install after merging

This update **does** change Lua addon code. After merging PR #47:

    cd C:\Users\seanc\wotlkbuddy-v05
    git switch main
    git pull --ff-only
    py -3.12 -m pip install -e ".[live,dev]"
    py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\world of warcraft 3.3.5a hd"

In WoW: /reload. Then /wbthud show to inspect the game-native panel.
The standard horde-progress --only objectives command remains the same.

Current scope: the bot handles one supported KillMob quest per attended
run, then turns in and checks equipment. Item collection/use-object
objectives remain unsupported; additional unattended quest progression
must use live item/source information and reliable loot first.

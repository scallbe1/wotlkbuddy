# v0.23 — One NPC, both quest turn-in and class trainer

The live run found a multi-role NPC: the Horde profile selected a Mage trainer from XML,
Detour reached and verified the NPC, but interacting opened quest dialogue instead of
the trainer window. The old training driver assumed Interact always opened training.

## What changes

1. After reaching any XML-selected class trainer, inspect the active profile's TurnIn
   steps for the same verified NPC entry and the live quest journal. Only ready quests
   are considered. Delivery quests with no objectives can be eligible for turn-in.
   The existing safe turn-in verifies quest, reward and server completion history.
2. Dismiss leftover quest UI after turn-in and check any newly received cloth armor.
3. Interact again to open training. On GossipFrame, use GetGossipOptions to identify
   the uniquely typed 'trainer' option (not a fixed index). Verify two fresh frames,
   then call SelectGossipOption(index) through the bounded input allowlist.
4. Require the class trainer window and verify all purchased spells. Close unfinished
   quest progress without calling CompleteQuest; stop on unresolved UI ambiguity.

## Installation after PR merge

This changes the Lua addon and Python; reinstall the addon and reload WoW:

    cd C:\Users\seanc\wotlkbuddy-v05
    git switch main
    git pull --ff-only
    py -3.12 -m pip install -e ".[live,dev]"
    py -3.12 -m wotlkbuddy install-addon --wow-dir "C:\Games\world of warcraft 3.3.5a hd"

In WoW use /reload and /wbt show. Then run the existing horde-progress --only train
command with the same profiles root, navmesh DLL, x86 Python and spawn DB arguments.

The output includes trainer_quest_turnins (verified quest IDs, if any), and
trained_spells (verified purchases). This design applies to any profile-described
trainer who is also a turn-in NPC, not just Isabella. Live verification pending.

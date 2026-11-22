# v0.25 — Visible HUD diagnostics and automatic active-quest objectives

## Confirmed live status

The last live run showed Mage trainer Isabella closed cleanly and Glyphic
Scroll quest #3098 has server_history_complete=true and still_in_journal=false.
That quest is handed in. Aarr's remaining journal quests need objectives.

## Why the Goal/Action overlay was missing

The separate Tk window uses native Win32 HWND calls. The old ctypes calls
were untyped and could truncate **64-bit window handles** on Win64.
The viewer's stderr was also discarded, hiding Tcl startup errors.
v0.25 declares proper pointer-width HWND signatures, preserves click-through
and NOACTIVATE before making the overlay visible, and lets startup errors
appear in the PowerShell console.

To verify the overlay **without moving or controlling WoW**, run:

    py -3.12 -m wotlkbuddy.live.status_hud --test

A test Goal/Action panel should appear near the upper-left for 15 seconds.
The console prints startup messages and any Tk or Win32 errors.
Use WoW in windowed or windowed-fullscreen mode; exclusive fullscreen can
hide desktop overlays even if the Windows overlay is correctly created.

## Next quest objectives

After merging the PR, run the new, attended objectives-only mode:

    py -3.12 -m wotlkbuddy horde-progress --only objectives `
      --profiles-root "$HOME\Questing-profiles" `
      --nav-dll "$HOME\CopilotNav\Navigation.dll" `
      --python32 "$HOME\Python312-32\python.exe" `
      --spawns-db "$HOME\CopilotBuddy\datadb\CreatureSpawns.db" `
      --interact-key f --enable-input --countdown 5

Quest selection is **not hardcoded** to Sarvis, Isabella, The Damned or
Rattling the Rattlecages. The bot cross-matches active, unfinished journal
quests, completion history and the locally loaded Horde QuestOrder and
quest-objective specifications. It picks the first safely verified KillMob
objective in profile order and performs bounded navigation, buffing, combat
and loot, then returns for a verified quest turn-in and equips Mage cloth
upgrades when appropriate.

This intentionally executes **one supported KillMob quest per invocation**.
Collection and UseObject objective steps are not implemented by this
command. They are reported as unsupported rather than directing Aarr to
fight unverified creatures or claiming all quests are complete.
Increase bounded combat limits only when a test demonstrates they are
required: --max-kills (default 18), --max-seconds (default 900).

Regular horde-progress --only all now also performs one supported active
kill-objective quest after gear, pickups, vendor checks and class training.
To test the active quest directly without replaying other NPC visits, use
--only objectives.

No addon reinstall is required. This update is Python-only.

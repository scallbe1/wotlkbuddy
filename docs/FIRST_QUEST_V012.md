# v0.12 — Aarr's first guarded quest pickup

This introduces the **first actual quest interaction** (rather than only a
read-only plan). It deliberately handles **Aarr**, an Undead Mage at level 1,
and only the first quest **Rude Awakening (ID 363)** from **Undertaker Mordo
(NPC entry 1568)**. It uses the real upstream Tirisfal profile, never guessed
quest ID or a name from the local client.

## Prepare WoW

1. Update and install the latest WotLKBuddy addon: `install-addon --wow-dir <your-client-dir>`.
   Reload WoW (`/reload`) and keep the pixel telemetry block visible (`/wbt show`).
2. In WoW's Key Bindings, bind **F** or **G** to **Interact with Target**. The
   driver does not change your keybindings. If your chosen key is bound to a
   different action, it will not open the quest window. For a fresh client, F
   may be bound to Assist: replace that binding if you choose F.
3. Put Aarr within targeting/interacting distance of Undertaker Mordo in
   Deathknell. This version **does not yet walk to world X/Y/Z coordinates**.
   Ensure chat is closed and there is no modal window over WoW.
4. Use an **English/US keyboard layout** for the first test. The driver types
   fixed ASCII chat commands using physical scan codes (rather than relying
   on clipboard or text APIs). No arbitrary upstream Lua/XML is executed.

## Run

```powershell
cd C:\Users\seanc\wotlkbuddy-v05
py -3.12 -m wotlkbuddy horde-quest-pickup --profiles-root "$HOME\Questing-profiles" --character Aarr --interact-key f --enable-input --countdown 5
```

Switch to WoW during the countdown. **Escape** or switching away stops the
controller; the test sends only bounded keyboard input. It will:

1. Verify Aarr's identity, class, race, safety state, quest/dialog telemetry,
   and that the planner currently proposes quest 363 pickup.
2. Type `/targetexact Undertaker Mordo` via WoW's chat; verify target name.
3. Press F/G (must already be bound to Interact with Target).
4. Read the live quest dialogue from the addon and verify **DETAIL / quest 363**, or an exact **Rude Awakening** title if the client returns quest ID 0. Require two consecutive matching telemetry frames.
5. Only when verified, type the fixed `/run AcceptQuest()` command once.
6. Check the quest journal actually contains quest 363. The result reports
   `accepted`, `already_in_journal`, or an explicit stop status.

No automatic walking, click-to-move, item use, quest turn-in, spell casting,
combat, or looting is attempted in v0.12. The addon only **observes** quest UI
state; Python sends the explicitly enabled input.

### Fail-closed statuses

- `npc_not_targeted`: NPC was not within targeting range, or chat command did
  not reach WoW.
- `dialog_not_open`: usually the F/G interaction key is not correctly bound,
  or the NPC is out of interaction range.
- `dialog_unverified`: an unexpected quest, greeting, or other UI was shown;
  no acceptance was issued. CopilotBuddy quest-greeting selection is future work.
- `journal_not_confirmed`: acceptance was sent only once, but the quest did
  not appear in the journal within the confirmation interval.
- Missing `I` telemetry: reinstall the **v0.12** addon and `/reload`.

The goal of this incremental test is to validate **actual NPC targeting,
quest dialogue reading, and acceptance verification**, before connecting world
XYZ pathfinding or extending this to all Horde quests.

Upstream quest data: https://github.com/Likon69/Questing-profiles.
WoW 3.3.5.12340 client UI: QuestFrameDetailPanel + GetQuestID + AcceptQuest.

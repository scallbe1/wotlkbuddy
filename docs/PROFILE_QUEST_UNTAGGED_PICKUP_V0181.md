# v0.18.1 — generic quest pickup on WotLK panels missing ID and title

## Live case

After quest 363 completed, `horde-next` correctly planned quest 364 from the
Undead starter profile and clicked Sarvis's matching offered quest. The WoW
client showed the quest detail with **Accept**, but `GetQuestID()` and the
visible-title telemetry did not identify that panel; `horde-next` stopped with
`quest_selection_unverified` before calling `AcceptQuest()`.

## Generic behavior

For any profile-derived pickup (not only quest 364), the client must:

1. Verify the active character, NPC target, planner decision and exact quest
   title in the available NPC quest menu.
2. Observe the **same unique** available quest in a separate fresh telemetry
   frame, including the correct visible button/quest index, before clicking it.
3. Observe a fresh **detail** window belonging to the same NPC. A nonzero ID or
   nonempty title must agree with the profile; neither can contradict it.
4. When both ID and title are absent, accept their absence **only after the
   verified quest selection within the same invocation** and a second fresh
   detail frame. A blank panel at command start is never trusted.
5. Issue one fixed `AcceptQuest()` and require quest presence in the journal.

If retrying while an already-open unidentified detail window is present, the
controller closes it without accepting, reopens the NPC quest list, and
re-establishes the identity using the same checks. It never infers a quest from
an unrelated blank panel, guesses a menu button, or treats an absent journal
record as an accepted quest.

## Windows retry, without navigation

The character should remain within interaction range of the NPC:

```powershell
cd C:\Users\seanc\wotlkbuddy-v05
git fetch origin
git switch --track origin/feature/untagged-profile-pickup-v0.18.1
py -3.12 -m pip install -e ".[live,dev]"

py -3.12 -m wotlkbuddy horde-next `
  --profiles-root "$HOME\Questing-profiles" `
  --interact-key f --enable-input --countdown 5
```

No telemetry addon update or navigation DLL is required for this pickup-only
fix. The existing addon must remain visible, and the NPC must be in range.

A successful pickup prints `status: accepted` and `journal_confirmed: true`.
The following `horde-next` invocation will report the first kill objective,
which still has no automatic combat implementation.

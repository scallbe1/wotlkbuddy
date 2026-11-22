# v0.17.2 — Sarvis reward panel with missing quest ID and title

## Live regression

The active Undead Mage reached Sarvis using the Detour navmesh. The v0.17.1
`/click GossipTitleButton1` command **worked**: the next observed UI was
`reward id=0 title=''`. WotLKBuddy incorrectly reported
`quest_selection_unverified` because its old identity predicate required the
open reward panel to provide quest ID 363 or the exact title. The target,
completed quest list, and chosen button had already been independently verified.

## Fix

- A completed **unique** `Rude Awakening` entry must be observed in Sarvis's
  **active** NPC quest list and confirmed in a **second fresh telemetry frame**.
- The controller clicks only the mapped, verified active quest button.
- The following fresh QuestFrame `progress` or `reward` panel may have
  `quest_id=0` **and** an empty title. For this single invocation, the
  verified menu-selection transition now supplies the quest identity.
  A later `reward` panel can also inherit the evidence of a prior verified
  progress panel followed by this controller's `CompleteQuest()` request.
- Any different nonzero quest ID or any different nonempty quest title still
  overrides the inferred identity and is rejected.
- Two fresh reward frames must agree, while player GUID, target, combat status,
  reward choices, money requirements and journal state remain guarded.
- If a previous failed attempt left a **nameless reward panel open**, the bot
  cannot assume the earlier selection still applies. It closes that panel
  using the fixed `/click QuestFrameCloseButton`, waits until it is gone,
  interacts with Sarvis again, and re-verifies the unique completed quest
  through the NPC's active quest list before attempting any reward.
- The bot still requires server completion-history confirmation and quest-log
  removal to report `turned_in`; otherwise it reports `completion_unverified`.

**No addon reinstall is needed for v0.17.2** if the v0.17.1 addon (which already
exports quest-list button numbers and reward counts) is installed and working.

This has only been tested against simulated WoW telemetry. It has not been
verified on the live client yet.

## Retry from Sarvis (no navigation)

```powershell
cd C:\Users\seanc\wotlkbuddy-v05
git fetch origin
git switch --track origin/feature/sarvis-untagged-reward-v0.17.2
py -3.12 -m pip install -e ".[live,dev]"
py -3.12 -m pytest -q

py -3.12 -m wotlkbuddy horde-quest-363 `
  --profiles-root "$HOME\Questing-profiles" `
  --interact-key f --enable-input --countdown 5
```

Expected in this case:

```
Open quest panel has no ID/title; closing it to reverify the exact quest ...
Choosing verified active quest 'Rude Awakening': gossip index 1, action /click GossipTitleButton1
Verified reward by fresh transition ... (client omitted ID/title)
Reward panel verified: zero choices and zero required money; submitting reward
status: turned_in
history_confirmed: true
```

The initial close message only appears if the reward window remains open from
the last attempt. If the reward contains unknown/choice-based items or a
positive money requirement, the driver still **refuses automatic completion**.

This is solely the quest-interaction fix. The navigation DLL and combat driver
are unchanged. Combat still aborts attended navigation.

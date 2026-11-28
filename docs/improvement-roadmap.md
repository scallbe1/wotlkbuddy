# Data-driven questing and navigation roadmap

Carried forward from the November 27 discussion. Changes must work from source
data and verified observations, without character-specific quest blacklists.
The Tauren/Druid example illustrates portability; it is not a requested class
implementation. Each implementation PR requires passing checks before merge.

Already merged: generic profile catch-up (#274), then 9,464 bundled upstream
quest eligibility/dependency records with local overrides (#275).

| Priority | Work | Acceptance criteria / dependencies | Status |
|---|---|---|---|
| P0 | NPC and GameObject quest relations | Reproducible pinned starter/ender catalog, reverse lookup, local replacement, profile coverage checks. Automatic discovery additionally needs names, verified spawns, live offers and executable objectives. | Catalog and offline lookup merged in #276; profile comparison in #277. Runtime fallback now resolves missing profile giver XYZ using a matching creature-starter relation and unambiguous local spawn. Out-of-profile discovery remains pending. |
| P0 | Quest objectives | Preserve creature/object credit IDs, item requirements, counts and slots. Identify unsupported scripted/PvP/exploration requirements instead of inventing a kill objective. Compare with profiles and live journal. | 9,464 records, deterministic importer and profile comparison merged in #277. Active-quest differences appear in live-status --quest-audit. Runtime objective synthesis and scripted semantics pending. |
| P0 | Creature/GameObject loot sources | Resolve template loot IDs and reference-loot chains; preserve quest-only/group/condition semantics; never treat item IDs as creature IDs or promise a drop. | Planned. |
| P1 | Travel-aware quest discovery | Replace fixed discovery cutoffs with ranked reachable candidates and bounded travel legs; keep map, threat, identity, cooldown and session guards. | Planned after catalog integration. |
| P1 | SmartAI and scripted quest behavior | Identify gossip, faction changes, escort/event/credit requirements; mark unsupported behavior explicitly. Do not execute arbitrary upstream scripts. | Planned. |
| P1 | Persistent terrain knowledge | Cache observed failures and successful traversal by map, geometry version and server; expire stale observations and record confidence. | Planned. |
| P1 | Geometry-informed jump recovery | Require useful landing/clearance evidence, bounded attempts and measured progress; retain stop/aggro/fall guards. | Planned; live movement validation needed. |
| P2 | Native VMap collision/height queries | Versioned native interface, matching extracted assets, floor/LOS fixtures and Windows verification. SQL alone does not supply collision geometry. | Planned; runtime assets needed for validation. |
| P2 | Reduce typed chat commands | Inventory command paths; replace supported actions with verified addon/key interactions and acknowledgements. Preserve focus, target and result verification. | Planned. |

## Delivery boundaries

Upstream reference data is not a snapshot of a customized live server. Local
exports replace bundled tables; missing history stays unknown. A catalog hit
does not authorize accepting a quest, moving, attacking or claiming completion.
Implement small PRs, report their actual integration scope, and keep remaining
work visible here. Passing offline/CI checks is not live WoW validation.

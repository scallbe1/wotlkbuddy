"""Quest objective tracking and inventory/journal evidence for a hunt."""
from __future__ import annotations

from dataclasses import dataclass, field

from wotlkbuddy.live.control import LiveControlError


@dataclass
class HuntObjectiveState:
    active_tracked_item: int = 0
    collected_results: dict[int, tuple[int, int]] = field(default_factory=dict)
    known_item_names: dict[int, str] = field(default_factory=dict)
    logged_live_kill_totals: set[int] = field(default_factory=set)
    no_credit: int = 0
    last_description: str = ''

    def current(self, profile, s, quest_id, *, collect_items):
        from wotlkbuddy.horde.combat import next_collect_objective, next_kill_objective
        return (next_collect_objective(profile, s, quest_id, item_names=self.known_item_names)
                if collect_items else next_kill_objective(profile, s, quest_id))

    def finalize(self, s, *, collect_items):
        """Verify final item inventory before reporting completion."""
        if collect_items and self.active_tracked_item:
            result = self.collected_results.get(self.active_tracked_item)
            if (result is None or
                    s.tracked_item_id != self.active_tracked_item or
                    s.tracked_item_count is None or
                    s.tracked_item_count < result[1]):
                raise LiveControlError(
                    'Journal completed quest but final tracked '
                    'quest-item inventory count cannot be confirmed')
            self.collected_results[self.active_tracked_item] = (
                s.tracked_item_count, result[1])
        return tuple((item_id, counts[0], counts[1])
                     for item_id, counts in self.collected_results.items())

    def prepare(self, s, current_objective, *, collect_items, profile, quest_id,
                driver, fresh, log):
        """Authorize sources and bind collection IDs to observed journal entries."""
        from wotlkbuddy.horde.combat import next_collect_objective, objective_progress
        spec, observed = current_objective
        allowed_sources = (
            tuple(dict.fromkeys(
                source.entry for source in spec.sources
                if source.kind == 'mob' and 0 < source.entry < 2**24))
            if collect_items else (spec.entry,))
        driver.active_quest_entries = allowed_sources
        if not allowed_sources:
            raise LiveControlError(
                f'CollectItem {spec.entry} has no Mob source IDs in local '
                'Horde XML; no creature is authorized for item farming')
        if collect_items and self.active_tracked_item != spec.entry:
            driver.chat(f'/wbttrack {spec.entry}')
            observed_tracker = fresh(s)
            if (observed_tracker.tracked_item_id != spec.entry or
                    observed_tracker.tracked_item_count is None):
                raise LiveControlError(
                    f'Cannot verify inventory count for item {spec.entry}; '
                    'reinstall updated WotLKBuddyTelemetry addon and /reload')
            for _ in range(5):
                if observed_tracker.tracked_item_name:
                    break
                observed_tracker = fresh(observed_tracker)
            if not observed_tracker.tracked_item_name:
                raise LiveControlError(
                    f'Item {spec.entry} name not cached by 3.3.5a client; '
                    'cannot bind item ID to quest journal safely')
            self.known_item_names[spec.entry] = observed_tracker.tracked_item_name
            self.active_tracked_item = spec.entry
            s = observed_tracker
            # A previously collected item can be complete before this run;
            # the next profile item then becomes the legitimate objective.
            verified_objective = next_collect_objective(
                profile, s, quest_id, item_names=self.known_item_names)
            if verified_objective is None:
                return None
            verified_spec, verified_journal = verified_objective
            if verified_spec.entry != spec.entry:
                log(f'Item {spec.entry} already completed; '
                    'switching to next source item')
                return None
            observed = verified_journal
            log(f'Collecting item {spec.entry} '
                f'({s.tracked_item_name}) from profile Mob IDs '
                f'{allowed_sources}; inventory={s.tracked_item_count}, '
                f'journal={observed.description}')
        if collect_items and (
                s.tracked_item_id != spec.entry or
                s.tracked_item_count is None):
            raise LiveControlError(
                'Tracked item identity missing or changed during collection')
        if collect_items:
            parsed = objective_progress(observed.description)
            if parsed is None or s.tracked_item_count < parsed[0]:
                raise LiveControlError(
                    'Inventory count conflicts with live CollectItem journal')
            self.collected_results[spec.entry] = (s.tracked_item_count, spec.count)
        self.last_description = observed.description
        if not collect_items:
            # Server quest variants can demand a different number of kills
            # than the imported Honorbuddy XML. XML supplies the creature
            # entry and search hotspots; the live journal supplies the
            # required total and the done flag. Never stop at XML KillCount.
            live_credit = objective_progress(observed.description)
            if (live_credit is None or live_credit[1] <= 0 or
                    not 0 <= live_credit[0] < live_credit[1]):
                raise LiveControlError(
                    'Active kill objective has no valid unfinished live '
                    f'journal count: {observed.description!r}')
            if spec.entry not in self.logged_live_kill_totals:
                self.logged_live_kill_totals.add(spec.entry)
                if spec.count != live_credit[1]:
                    log(f'KillMob entry {spec.entry}: live journal requires '
                        f'{live_credit[1]} kills, while XML requests '
                        f'{spec.count}; honoring live quest journal')
                else:
                    log(f'KillMob entry {spec.entry}: live journal progress '
                        f'{live_credit[0]}/{live_credit[1]}')
        return s, spec, observed, allowed_sources

    def after_loot(self, s, *, qlog, collect_items, spec, observed, before_text, log):
        """Verify credit and apply the existing no-progress budget."""
        from wotlkbuddy.horde.combat import objective_progress
        latest = qlog(s)
        if collect_items:
            if (s.tracked_item_id != spec.entry or
                    s.tracked_item_count is None):
                raise LiveControlError(
                    'Item tracker lost during corpse looting')
            updated = next(
                (obj for obj in latest.objectives
                 if obj.index == observed.index), None)
            count = (objective_progress(updated.description)
                     if updated is not None else None)
            if count is None or count[0] > s.tracked_item_count:
                raise LiveControlError(
                    'Collected inventory item count does not '
                    'confirm live quest journal progress')
            self.collected_results[spec.entry] = (
                s.tracked_item_count, spec.count)
            log(f'CollectItem {spec.entry} inventory='
                f'{s.tracked_item_count}, journal={count[0]}/{count[1]}')
        after_text = tuple(o.description for o in latest.objectives)
        if latest.complete or after_text != before_text:
            self.no_credit = 0
            log('Quest journal objective progress changed')
        else:
            self.no_credit += 1
            if collect_items:
                log('No item-collection credit from this corpse; '
                    'checking another authorized source (bounded kill limit)')
            elif self.no_credit >= 2:
                raise LiveControlError('Two verified kills without quest progress; stop')

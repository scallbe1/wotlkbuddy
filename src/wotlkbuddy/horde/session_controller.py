"""Application controller for bounded continuous quest progression."""
from __future__ import annotations

import argparse
import json
import sys
import time
from wotlkbuddy.live.console_log import log_console
from dataclasses import dataclass, field
from typing import Callable

from wotlkbuddy.live.control import LiveControlError, WindowsKeyboard
from wotlkbuddy.live.capture import capture_monitor
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.horde.aggro_recovery import NpcInteractionInterrupted
from wotlkbuddy.horde.session_activity import Activity, ActivityGate


@dataclass
class QuestSession:
    args: argparse.Namespace
    progress: Callable
    recover: Callable
    start: float
    steps: list[dict] = field(default_factory=list)
    quest_ids: list[int] = field(default_factory=list)
    accepted_ids: list[int] = field(default_factory=list)
    reason: str = 'cycle_limit'
    train_level: int = -1
    recoveries: int = 0
    aggro_recoveries: int = 0
    activity: ActivityGate = field(default_factory=ActivityGate)
    hunt_memory: dict = field(default_factory=dict)
    local_offer_memory: dict = field(default_factory=dict)
    training_route_memory: dict = field(default_factory=dict)
    repair_route_memory: dict = field(default_factory=dict)

    def time_expired(self) -> bool:
        return time.monotonic() - self.start >= self.args.auto_minutes * 60

    def phase(self, mode: str) -> dict:
        self.args._hunt_memory = self.hunt_memory
        child = argparse.Namespace(**vars(self.args))
        child.only = mode
        child._continuous_training_check = mode == 'objectives'
        child._last_training_level = self.train_level
        child._hunt_memory = self.hunt_memory
        child._local_offer_memory = self.local_offer_memory
        child._training_route_memory = self.training_route_memory
        child._repair_route_memory = self.repair_route_memory
        owner = {'objectives': Activity.OBJECTIVES, 'quests': Activity.OFFERS,
                 'train': Activity.TRAINING}[mode]
        with self.activity.claim(owner):
            return self.progress(child, _skip_countdown=True, _emit_report=False)

    def retry_blocked_route(self, exc, cycle: int) -> None:
        log_console(f'Route temporarily unavailable: {exc}; refreshing after patrol wait')
        keyboard = WindowsKeyboard()
        initial = capture_monitor(self.args.monitor)
        if initial.truncated or not initial.player.guid:
            raise LiveControlError('Player telemetry lost during route retry') from exc
        for _ in range(10):
            if keyboard.emergency_stop_requested() or not keyboard.wow_is_foreground():
                raise LiveControlError('Escape pressed or WoW lost focus during route retry') from exc
            if self.time_expired():
                break
            state = capture_monitor(self.args.monitor)
            if state.truncated or state.player.guid != initial.player.guid:
                raise LiveControlError('Player identity changed during route retry') from exc
            if state.player.dead or state.player.in_combat:
                break
            time.sleep(1.)
        self.steps.append({'cycle': cycle, 'action': 'retry_blocked_route', 'reason': str(exc)})

    def recover_phase(self, exc: LiveControlError, cycle: int) -> None:
        if self.recoveries >= 3:
            raise exc
        if self.aggro_recoveries >= 5:
            from wotlkbuddy.horde.aggro_recovery import recoverable_aggro_error
            if recoverable_aggro_error(exc):
                raise exc
        with self.activity.claim(Activity.RECOVERY):
            recovery = self.recover_with_redeath(exc,cycle)
        if recovery.get('status') == 'ghost_wait':
            action = 'ghost_wait'
        elif recovery.get('status') == 'combat_cleared':
            self.aggro_recoveries += 1
            action = 'aggro_recovery'
        else:
            self.recoveries += 1
            self.aggro_recoveries = 0
            action = 'death_recovery'
        self.steps.append({'cycle': cycle, 'action': action, 'recovery': recovery})

    def recover_with_redeath(self, exc, cycle):
        """A second verified death is a new recovery, not a fatal nested exception."""
        owner=None
        while True:
            if self.time_expired() or self.recoveries>=3:
                raise exc
            before=capture_monitor(self.args.monitor)
            if before.truncated or not before.player.guid or (owner and before.player.guid!=owner):
                raise exc
            owner=before.player.guid
            try:
                return self.recover(self.args,original_error=exc)
            except LiveControlError as error:
                if 'no safe reclaim site found' in str(error) or 'Reclaim window changed' in str(error) or 'Final reclaim check failed' in str(error):
                    keyboard=WindowsKeyboard()
                    state=capture_monitor(self.args.monitor)
                    if (not keyboard.emergency_stop_requested() and keyboard.wow_is_foreground() and
                            not state.truncated and state.player.guid==owner and state.player.dead and
                            state.death_state and state.death_state.ghost):
                        log_console('No safe resurrection yet; remaining ghost and retrying next cycle')
                        return {'status':'ghost_wait','character_guid':owner}
                    raise
                if not any(marker in str(error) for marker in (
                        'character died again','Post-resurrection recovery did not restore',
                        'Defense lost player identity')):
                    raise
                keyboard=WindowsKeyboard()
                if keyboard.emergency_stop_requested() or not keyboard.wow_is_foreground():
                    raise
                state=capture_monitor(self.args.monitor)
                if state.truncated or not state.player.dead or state.player.guid!=owner:
                    raise
                self.recoveries+=1
                self.steps.append({'cycle':cycle,'action':'redeath','reason':str(error)})
                log_console('Verified repeat death; restarting corpse recovery with fresh live coordinates')
                exc=error

    def guarded_phase(self, mode: str, cycle: int) -> dict | None:
        try:
            return self.phase(mode)
        except NpcInteractionInterrupted as exc:
            self.steps.append({'cycle':cycle,'action':'interaction_combat_cleared','reason':str(exc)})
        except EncounterRouteUnavailable as exc:
            with self.activity.claim(Activity.ROUTE_WAIT):
                self.retry_blocked_route(exc, cycle)
        except LiveControlError as exc:
            self.recover_phase(exc, cycle)
        return None

    def train(self, cycle: int, level: int, *, resumed: bool = False) -> None:
        self.train_level = level
        message = ('checking trainer after resumed follow-up' if resumed
                   else 'checking verified Mage trainer')
        log_console(f'Level {level}: {message}')
        try:
            training = self.phase('train')
            self.steps.append({'cycle': cycle, 'action': 'train',
                               'trained_spells': training.get('trained_spells', ())})
        except LiveControlError as exc:
            log_console(f'Mage training unavailable this cycle: {exc}')

    def record_local_offers(self,report,cycle):
        ids=sorted({qid for ids in report.get('local_accepted_quests',{}).values()
                    for qid in ids if isinstance(qid,int) and qid>0 and qid not in self.accepted_ids})
        if ids:
            self.accepted_ids.extend(ids)
            self.steps.append({'cycle':cycle,'action':'local_pickup','quest_ids':ids})

    def record_item_offers(self,report,cycle):
        ids=[qid for qid in report.get('item_accepted_quests',())
             if isinstance(qid,int) and qid>0 and qid not in self.accepted_ids]
        if ids:
            self.accepted_ids.extend(ids)
            self.steps.append({'cycle':cycle,'action':'item_pickup','quest_ids':ids})

    def handle_objectives(self, report: dict, cycle: int) -> bool:
        """Return whether quest offers should be checked in this cycle."""
        self.aggro_recoveries = 0
        self.record_local_offers(report,cycle)
        self.record_item_offers(report,cycle)
        checked = report.get('training_checked_level')
        if isinstance(checked, int):
            self.train_level = checked
            self.steps.append({'cycle': cycle, 'action': 'training_check', 'level': checked,
                               'trained_spells': report.get('trained_spells', ())})
        completion = report.get('quest_turnin', {})
        chained = tuple(report.get('chained_quests', ())) + tuple(completion.get('followup_quest_ids', ()))
        if chained:
            new_ids = [qid for qid in chained if qid not in self.accepted_ids]
            self.accepted_ids.extend(new_ids)
            self.steps.append({'cycle': cycle, 'action': 'chained_pickup', 'quest_ids': new_ids})
        level = report.get('character_level_after', 0)
        if report.get('resumed_open_offer') and chained and not completion:
            if level >= 4 and level % 2 == 0 and level != self.train_level:
                self.train(cycle, level, resumed=True)
            return False
        if completion.get('status') in ('turned_in', 'already_turned_in'):
            quest_id = completion.get('quest_id')
            if not isinstance(quest_id, int) or quest_id <= 0:
                raise LiveControlError('Turn-in result missing verified quest ID')
            if quest_id in self.quest_ids:
                raise LiveControlError('Continuous loop repeated the same quest turn-in')
            self.quest_ids.append(quest_id)
            self.steps.append({'cycle': cycle, 'action': 'turnin', 'quest_id': quest_id})
            if report.get('levels_gained_this_run', 0) and level != self.train_level:
                self.train(cycle, level)
            return False
        if report.get('quest_vendor_purchase'):
            self.steps.append({'cycle':cycle,'action':'quest_vendor_purchase',
                               **report['quest_vendor_purchase']})
            return False
        milestone=report.get('level_milestone')
        if milestone:
            self.steps.append({'cycle':cycle,'action':'level_milestone',**milestone})
            # On verified XP progress, refresh training/readiness next cycle.
            # If deferred, seek another available quest rather than skipping the gate.
            return milestone.get('status')!='progress'
        objective = report.get('active_objective', {})
        status = objective.get('status')
        if status not in ('no_supported_objective', None):
            self.reason = ('unresolved_active_objectives'
                           if status == 'unresolved_active_objectives'
                           else 'unsupported_objective:' + str(status))
            self.steps.append({
                'cycle': cycle, 'action': 'stop', 'status': status,
                'unresolved_profile_objectives': objective.get('unresolved_profile_objectives', [])})
            return False
        return True

    def handle_offers(self, offers: dict, cycle: int) -> None:
        self.record_item_offers(offers,cycle)
        if isinstance(offers.get('training_checked_level'), int):
            self.train_level = offers['training_checked_level']
        newly_accepted = sorted({
            qid for ids in offers.get('accepted_quests', {}).values()
            for qid in ids if isinstance(qid, int) and qid > 0
        })
        if not newly_accepted:
            if offers.get('item_accepted_quests'):
                return
            milestone=offers.get('level_milestone')
            if milestone:
                self.steps.append({'cycle':cycle,'action':'level_milestone',**milestone})
                if milestone.get('status')=='progress':
                    return
                self.reason='level_milestone_deferred'
                return
            self.reason = 'no_supported_objectives_or_reachable_quest_offers'
            return
        if any(qid in self.accepted_ids for qid in newly_accepted):
            raise LiveControlError('Quest pickup repeated an already accepted quest')
        self.accepted_ids.extend(newly_accepted)
        self.steps.append({'cycle': cycle, 'action': 'pickup', 'quest_ids': newly_accepted})

    def run(self) -> dict:
        try:
            return self._run_cycles()
        finally:
            self.activity.finish()

    def _run_cycles(self) -> dict:
        for cycle in range(1, self.args.max_cycles + 1):
            if self.time_expired():
                self.reason = 'time_limit'
                break
            log_console(f'Continuous quest cycle {cycle}/{self.args.max_cycles}: '
                  'refreshing journal and objectives')
            report = self.guarded_phase('objectives', cycle)
            if report is not None and self.handle_objectives(report, cycle):
                offers = self.guarded_phase('quests', cycle)
                if offers is not None:
                    self.handle_offers(offers, cycle)
            if self.reason != 'cycle_limit':
                break
        return {
            'status': self.reason, 'cycles': len(self.steps),
            'quest_ids_turned_in': self.quest_ids,
            'quest_ids_accepted': self.accepted_ids,
            'steps': self.steps,
        }


def run_continuous(args: argparse.Namespace, *, progress: Callable, recover: Callable) -> dict:
    """Run existing guarded one-shot stages with explicit per-session state."""
    if not args.enable_input or not 2 <= args.countdown <= 20:
        raise ValueError('Continuous questing needs armed input and 2..20s countdown')
    if not 1 <= args.max_cycles <= 250 or not 5 <= args.auto_minutes <= 720:
        raise ValueError('Invalid continuous progression budgets')
    log_console(f'Switch to WoW now; continuous quest progression starts in '
          f'{args.countdown:g}s.')
    time.sleep(args.countdown)
    session = QuestSession(args, progress, recover, time.monotonic())
    result = {'continuous': session.run()}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result

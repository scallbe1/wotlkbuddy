"""Train observed, affordable Mage spells from a verified class trainer."""
from __future__ import annotations
from time import monotonic, sleep as real_sleep
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.combat import creature_entry_from_guid
from wotlkbuddy.horde.dialog_cleanup import dismiss_quest_dialog


def train_available_mage_spells(
    *, npc_name: str, npc_id: int, capture: Callable[[], ClientTelemetry],
    driver, max_spells: int = 8,
    sleep: Callable[[float], None] = real_sleep,
    now: Callable[[], float] = monotonic,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[str, ...]:
    if not 1 <= max_spells <= 20:
        raise ValueError('Invalid trainer purchase limit')
    first = capture()
    guid = first.player.guid
    if not guid:
        raise LiveControlError('Player GUID missing before trainer interaction')

    def guard(s):
        if (s.truncated or s.player.guid != guid or s.player.dead or
                s.player.in_combat or s.resources is None or
                s.resources.class_token.upper() != 'MAGE' or
                s.player.target_name != npc_name or
                creature_entry_from_guid(s.player.target_guid) != npc_id or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Trainer NPC/player identity or input state changed')

    def fresh(last, timeout=6):
        deadline = now()+timeout
        while now() < deadline:
            sleep(.35)
            s=capture()
            guard(s)
            if s.sequence != last.sequence:
                return s
        raise LiveControlError('Trainer telemetry not refreshing')

    guard(first)
    s = first
    # A trainer may also have quests or gossip options. Never assume that the
    # first Interact opens training; inspect the actual client UI instead.
    for attempt in range(3):
        if s.trainer_open:
            break
        kind = s.dialog.kind if s.dialog else 'none'
        if kind in ('detail', 'progress', 'reward', 'greeting'):
            log(f'{npc_name}: observed {kind} quest panel, not training; '
                'closing without issuing any quest-completion command')
            s = dismiss_quest_dialog(
                capture=capture, driver=driver, expected_guid=guid,
                sleep=sleep, now=now, log=log)
            guard(s)
            continue
        if kind == 'none':
            log(f'Opening class trainer {npc_name}')
            driver.interact()
            deadline = now() + 8
            last = s
            while now() < deadline:
                s = fresh(last)
                last = s
                if s.trainer_open or (s.dialog and s.dialog.kind != 'none'):
                    break
            if s.trainer_open:
                break
            if s.dialog is None or s.dialog.kind == 'none':
                raise LiveControlError(
                    f'{npc_name}: interaction did not open a visible trainer, '
                    'gossip or quest panel')
            # The next iteration processes whatever live UI actually opened.
            continue
        if kind != 'gossip':
            raise LiveControlError(
                f'{npc_name}: unsupported NPC UI state {kind!r} before training')
        if not s.gossip_services_complete:
            raise LiveControlError(
                f'{npc_name}: complete gossip service telemetry unavailable; '
                'reinstall updated WotLKBuddyTelemetry addon and /reload')
        matches = [option for option in s.gossip_services
                   if option.kind.lower() == 'trainer']
        if len(matches) != 1:
            raise LiveControlError(
                f'{npc_name}: expected one trainer gossip service; '
                f'observed {[(o.index, o.kind, o.text) for o in s.gossip_services]!r}')
        selected = matches[0]
        confirmed = fresh(s)
        if (confirmed.dialog is None or confirmed.dialog.kind != 'gossip'
                or not confirmed.gossip_services_complete or
                confirmed.gossip_services != s.gossip_services):
            raise LiveControlError(
                f'{npc_name}: trainer gossip service list changed before selection')
        guard(confirmed)
        log(f'{npc_name}: selecting verified training gossip option '
            f'{selected.index} {selected.text!r}')
        driver.chat(f'/run SelectGossipOption({selected.index})')
        deadline = now() + 8
        last = confirmed
        while now() < deadline:
            s = fresh(last)
            last = s
            if s.trainer_open:
                break
            if s.dialog and s.dialog.kind in ('detail','progress','reward'):
                raise LiveControlError(
                    f'{npc_name}: selecting training opened a quest panel '
                    f'({s.dialog.kind}) instead of class training')
        if not s.trainer_open:
            raise LiveControlError(
                f'{npc_name}: verified training gossip selection did not '
                'open the class trainer window')
        break
    if not s.trainer_open:
        raise LiveControlError(
            f'{npc_name}: quest/gossip UI did not yield class trainer '
            'within bounded attempts')
    bought=[]
    for _ in range(max_spells):
        guard(s)
        if not s.trainer_open:
            raise LiveControlError('Trainer window closed before training completed')
        available = [v for v in s.trainer_services if
                     v.cost <= s.resources.money_copper and
                     v.required_level <= s.player.level]
        if not available:
            log(f'{npc_name}: no affordable, level-eligible spells available')
            break
        available.sort(key=lambda v: (v.cost, v.index))
        choice = available[0]
        next_frame = fresh(s)
        if (not next_frame.trainer_open or
                choice not in next_frame.trainer_services or
                next_frame.resources.money_copper < choice.cost):
            raise LiveControlError('Trainer service list changed before purchase')
        log(f'Training {choice.name} {choice.rank} (service {choice.index}, '
            f'{choice.cost} copper)')
        driver.chat(f'/run BuyTrainerService({choice.index})')
        deadline = now()+8
        last=next_frame
        confirmed=None
        while now() < deadline:
            after=fresh(last)
            if after.trainer_open and not any(
                    v.name == choice.name and v.rank == choice.rank
                    for v in after.trainer_services):
                confirmed=after
                break
            last=after
        if confirmed is None:
            raise LiveControlError(
                f'Trainer purchase for {choice.name} was not confirmed')
        bought.append((choice.name+' '+choice.rank).strip())
        log(f'Trainer purchase confirmed: {bought[-1]}')
        s=confirmed
    return tuple(bought)

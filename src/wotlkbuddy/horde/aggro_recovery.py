"""Bounded live combat settlement before a continuous run abandons a phase."""
from time import monotonic, sleep as real_sleep
from wotlkbuddy.live.control import LiveControlError


def recoverable_aggro_error(exc):
    text=str(exc)
    return any(marker in text for marker in (
        'Aggro persists but no exact living attacker could be verified',
        'Aggro unresolved before starting quest phase',
        'Verified attacker is outside Fireball range',
        'Insufficient mana for defensive Fireball',
        'Defensive fight cast limit reached',
        'Two Fireballs failed to damage verified attacker',
    ))


def settle_aggro(*, initial, capture, driver, defend,
                 now=monotonic,sleep=real_sleep,log=lambda _:None,seconds=90):
    """Reacquire live aggressors or wait for combat/death to settle.

    Never turn a missing target into authorization to attack another creature.
    Death is an expected transition, not a telemetry failure. Other control
    errors, identity changes and lost focus are not recoverable.
    """
    guid=initial.player.guid
    def guard(s):
        if (not guid or s.truncated or s.player.guid!=guid or
                s.resources is None or s.resources.class_token.upper()!='MAGE' or
                s.resources.race_token.lower() not in ('scourge','undead') or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Aggro recovery interrupted by identity, telemetry, Escape or focus')
    state=initial
    deadline=now()+seconds
    for attempt in range(16):
        guard(state)
        if state.player.dead or not state.player.in_combat:
            return state
        if now()>=deadline:
            break
        log(f'Unresolved aggro recovery {attempt+1}/16: HP {state.player.health}/{state.player.max_health}, '
            f'mana {state.resources.mana}/{state.resources.max_mana}; reacquiring verified attacker')
        try:
            state=defend(state)
        except LiveControlError as exc:
            # The defender's living-state guard can fire on a fatal hit.
            # Reobserve before deciding whether to retry or propagate.
            state=capture()
            guard(state)
            if state.player.dead or not state.player.in_combat:
                return state
            if not recoverable_aggro_error(exc):
                raise
            log(f'Attacker not yet resolved: {exc}')
        guard(state)
        if state.player.dead or not state.player.in_combat:
            return state
        for _ in range(4):
            if now()>=deadline:
                break
            sleep(.5)
            state=capture()
            guard(state)
            if state.player.dead or not state.player.in_combat:
                return state
    raise LiveControlError('Aggro recovery budget exhausted; no safe combat resolution or verified death')

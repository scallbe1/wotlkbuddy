"""Class-aware, fail-closed character development.

Inspired by CopilotBuddy's separation of Talented and combat routines;
written from scratch for Aarr's read-only addon + verified keyboard inputs.
A policy lists ranks by talent NAME, not fixed tree/index identifiers.
"""
from __future__ import annotations

from time import sleep as real_sleep
from typing import Callable

from wotlkbuddy.live.codec import ClientTelemetry
from wotlkbuddy.live.control import LiveControlError


# Starter DPS leveling builds; only ranks confirmed learnable by the live
# server are ever spent. A pre-existing mismatched build stops allocation.
# The sequence can be extended without modifying the talent actuator.
DPS_TALENT_ORDERS: dict[str, tuple[tuple[str,int],...]] = {
    'MAGE': (
        ('Improved Frostbolt',5),
        ('Ice Shards',3),
        ('Precision',3),
        ('Piercing Ice',3),
        ('Icy Veins',1),
        ('Arctic Reach',2),
        ('Frost Channeling',3),
        ('Shatter',3),
    ),
    'PRIEST': (
        ('Spirit Tap',5),
        ('Improved Spirit Tap',2),
        ('Darkness',5),
        ('Shadow Focus',3),
    ),
}


def progress_dps_talents(
    *, capture: Callable[[], ClientTelemetry], driver,
    sleep: Callable[[float], None] = real_sleep,
    log: Callable[[str], None] = lambda _: None,
    max_points: int = 3,
) -> tuple[str,...]:
    """Allocate verified unspent points, one rank at a time, out of combat.

    The addon supplies the exact tree/index/name/current rank/max rank and
    learnability. Never guess a server-specific talent layout; verify each
    point by a changed live rank AND the unspent point count decreasing by 1.
    """
    if not 0 <= max_points <= 10:
        raise ValueError('Invalid talent allocation budget')
    first=capture()
    if (first.truncated or not first.player.guid or first.player.dead or
            first.player.in_combat or first.resources is None or
            driver.emergency_stop_requested() or not driver.wow_is_foreground()):
        raise LiveControlError('Living out-of-combat character required for talents')
    class_token=first.resources.class_token.upper()
    order=DPS_TALENT_ORDERS.get(class_token,())
    if (not order or first.resources.unspent_talent_points <= 0 or
            not callable(getattr(driver,'learn_verified_talent',None))):
        return ()
    guid=first.player.guid
    learned=[]
    for name, target_rank in order:
        if len(learned)>=max_points:
            break
        state=capture()
        if (state.truncated or state.player.guid!=guid or state.player.dead or
                state.player.in_combat or state.resources is None or
                state.resources.class_token.upper()!=class_token or
                driver.emergency_stop_requested() or not driver.wow_is_foreground()):
            raise LiveControlError('Character state changed during talent progression')
        if state.resources.unspent_talent_points <= 0:
            break
        driver.chat('/wbttalent '+name)
        sleep(.65)
        queried=capture()
        probe=queried.talent_probe
        if (queried.truncated or queried.player.guid!=guid or
                queried.player.dead or queried.player.in_combat):
            raise LiveControlError('Character changed during talent observation')
        if probe is None or probe.name!=name:
            log(f'Talent {name} not found in live tree; refusing subsequent points')
            break
        if target_rank>probe.max_rank:
            raise LiveControlError('Configured talent rank exceeds observed maximum')
        if probe.rank>=target_rank:
            continue
        if probe.learnable is not True or probe.rank>=probe.max_rank:
            log(f'Talent {name} rank {probe.rank}/{target_rank} not learnable; '
                'not skipping prerequisite')
            break
        if queried.resources is None or queried.resources.unspent_talent_points<1:
            raise LiveControlError('Unspent talent points not verified')
        # Confirm this identity on a fresh frame before sending protected input.
        sleep(.6)
        confirm=capture()
        if (confirm.sequence==queried.sequence or
                confirm.player.guid!=guid or
                confirm.talent_probe!=probe or
                confirm.resources is None or
                confirm.resources.unspent_talent_points !=
                    queried.resources.unspent_talent_points or
                confirm.player.dead or confirm.player.in_combat):
            raise LiveControlError('Talent identity or point count changed before spending')
        driver.learn_verified_talent(probe.tree,probe.index)
        verified=False
        for _ in range(10):
            sleep(.55)
            after=capture()
            if (after.truncated or after.player.guid!=guid or
                    after.player.dead or after.player.in_combat or
                    after.resources is None):
                raise LiveControlError('Character changed after talent input')
            nxt=after.talent_probe
            if (nxt is not None and (nxt.tree,nxt.index,nxt.name)==(
                    probe.tree,probe.index,probe.name) and
                    nxt.rank == probe.rank+1 and
                    after.resources.unspent_talent_points ==
                       queried.resources.unspent_talent_points-1):
                verified=True
                break
        if not verified:
            raise LiveControlError(
                f'Talent point for {name} not confirmed; no additional points spent')
        learned.append(name)
        log(f'Learned {name} rank {probe.rank+1}/{probe.max_rank} '
            f'for observed {class_token} build')
        # A talent can receive multiple ranks; re-query it on the next cycle.
        break
    return tuple(learned)

"""Validated route observations and exclusive encounter interruptions."""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError


@dataclass
class TravelObservationState:
    last_threat_scan_xyz: Vec3
    last_threat_scan_time: float
    aggro_fights: int = 0


def _distance_xy(a: Vec3, b: Vec3) -> float:
    return hypot(a.x-b.x, a.y-b.y)


def observe_travel(state, *, sample, guid, expected_map_id, valid_life_state,
                   on_aggro, max_aggro_fights, guard, notify, travel_input,
                   on_threat, threat_check_yards, now, continuous_forward,
                   upcoming_legs, expected_quest_id, force_scan=False,
                   suppress_scan=False, observation=None):
    """Observe live travel, resolving interruptions before allowing movement."""
    # Accept a position already captured immediately after a key release.
    # Threat scans and reactive combat can move the character; they must
    # not inflate the distance attributed to a single forward W hold.
    snap, pos = observation if observation is not None else sample()
    if (snap.truncated or snap.player.guid != guid or
            pos.player_guid.lower() != guid.lower() or
            pos.map_id != expected_map_id or not valid_life_state(snap)):
        raise LiveControlError('Character, map, telemetry, or alive state changed')
    if snap.player.in_combat:
        if on_aggro is None or state.aggro_fights >= max_aggro_fights:
            raise LiveControlError(
                'Aggro/combat detected; defensive handler unavailable '
                'or route fight budget exhausted')
        guard()
        origin = pos
        state.aggro_fights += 1
        notify(f'Aggro during waypoint travel; stopping input for '
               f'verified attacker defense {state.aggro_fights}/{max_aggro_fights}')
        after_defense = travel_input.interrupt('defense', on_aggro, snap)
        guard()
        if after_defense.player.guid != guid:
            raise LiveControlError('Character changed during defensive combat')
        snap, pos = sample()
        if (snap.truncated or snap.player.guid != guid or
                pos.player_guid.lower() != guid.lower() or
                pos.map_id != expected_map_id or not valid_life_state(snap) or
                snap.player.in_combat):
            raise LiveControlError(
                'Defense failed to restore safe same-character navigation')
        if (_distance_xy(pos.position, origin.position) > 6 or
                abs(pos.z-origin.z) > 3):
            raise LiveControlError(
                'Defensive combat displaced Mage away from original '
                'navmesh path; fresh Detour route required')
        notify('Verified combat ended; resuming existing Detour waypoints')
    if not suppress_scan and on_threat is not None and not snap.player.in_combat and (
            force_scan or
            _distance_xy(pos.position,state.last_threat_scan_xyz) >= threat_check_yards or
            now()-state.last_threat_scan_time >= (8. if continuous_forward else 5.)):
        # Movement keys are already released before checked() runs.
        # Scan only the next Detour legs, not arbitrary creatures off-path.
        guard()
        origin=pos
        upcoming=upcoming_legs()
        notify('Scanning upcoming path legs for nearby creature threats before advancing')
        travel_input.interrupt('threat_scan', on_threat, snap, pos, upcoming)
        snap,pos=sample()
        if (snap.truncated or snap.player.guid != guid or
                pos.player_guid.lower()!=guid.lower() or
                pos.map_id!=expected_map_id or not valid_life_state(snap)):
            raise LiveControlError('Proactive threat scan failed to restore '
                                   'safe live player/map state')
        if snap.player.in_combat:
            notify('Aggro began during threat scan; switching to reactive defense')
            snap,pos=observe_travel(state, sample=sample, guid=guid, expected_map_id=expected_map_id,
                                    valid_life_state=valid_life_state, on_aggro=on_aggro,
                                    max_aggro_fights=max_aggro_fights, guard=guard, notify=notify,
                                    travel_input=travel_input, on_threat=on_threat,
                                    threat_check_yards=threat_check_yards, now=now,
                                    continuous_forward=continuous_forward, upcoming_legs=upcoming_legs,
                                    expected_quest_id=expected_quest_id, suppress_scan=True,
                                    observation=(snap,pos))
        if (_distance_xy(pos.position,origin.position)>2.0 or
                abs(pos.z-origin.z)>2.):
            raise LiveControlError('Threat scan changed player position; '
                                   'fresh Detour replan is required')
        state.last_threat_scan_xyz=pos.position
        state.last_threat_scan_time=now()
    if expected_quest_id is not None and not any(
            q.quest_id == expected_quest_id for q in snap.quests):
        raise LiveControlError('Quest journal changed while walking; stopping')
    return snap, pos

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.missed_quests import RecoveryOffer, recovery_offers
from wotlkbuddy.horde.npc_catalog import NpcDestination
from wotlkbuddy.horde.quest_travel import (
    choose_recovery_route, visit_remote_quest_giver, plan_recovery_offer,
)
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable
from wotlkbuddy.live.control import LiveControlError


def offer(x, entry=1):
    return RecoveryOffer(NpcDestination('Giver', entry, Vec3(x, 0, 0), x, 'test', 'quest'),
                         frozenset({entry}), 5)


def environment():
    state = NS(truncated=False, player=NS(guid='p', dead=False, in_combat=False, level=12), quests=())
    world = NS(map_id=0, position=Vec3(0, 0, 0))
    driver = NS(emergency_stop_requested=lambda: False, wow_is_foreground=lambda: True)
    return state, world, driver


def test_selects_by_route_cost_and_skips_unreachable_giver():
    _, world, _ = environment()
    def path(map_id, origin, destination):
        if destination.x == 10:
            return [origin, Vec3(0, 100, 0), destination]
        if destination.x == 30:
            return [origin, Vec3(5, 0, 0)]  # partial
        return [origin, destination]
    chosen = choose_recovery_route((offer(10), offer(20, 2), offer(30, 3)),
        nav=NS(find_path=path), world=world, guard=lambda: None, now=lambda: 0)
    assert chosen.giver.entry == 2


def test_guards_are_not_swallowed_and_hazards_reject_route():
    state, world, driver = environment()
    nav = NS(find_path=lambda m, a, b: [a, b])
    assert choose_recovery_route((offer(100),), nav=nav, world=world,
        guard=lambda: None, hazards=(Vec3(50, 0, 0),), now=lambda: 0) is None
    driver.wow_is_foreground = lambda: False
    with pytest.raises(LiveControlError, match='Unsafe state'):
        plan_recovery_offer((offer(100),), nav=nav, world=world, state=state,
                            location=lambda: (state, world), driver=driver)


def test_remote_giver_uses_guarded_waypoints_then_one_final_target_visit():
    state, world, driver = environment()
    visits = []
    def visit(**kw):
        visits.append(kw)
        world.position = kw.get('_waypoint', kw.get('destination_hint'))
        return state
    result = visit_remote_quest_giver(offer(1400).giver, location=lambda: (state, world),
        driver=driver, nav=NS(find_path=lambda m, a, b: [a, b]), visit=visit, now=lambda: 0)
    assert result is state and world.position == Vec3(1400, 0, 0)
    assert len(visits) > 1 and all('_waypoint' in v for v in visits[:-1])
    assert 'destination_hint' in visits[-1] and '_waypoint' not in visits[-1]
    points = [Vec3(0, 0, 0)] + [v['_waypoint'] for v in visits[:-1]]
    assert all(a.distance_to(b) <= 450 for a, b in zip(points, points[1:]))


def test_no_interaction_after_partial_route_map_change_or_unconfirmed_leg():
    for failure in ('partial', 'map', 'arrival'):
        state, world, driver = environment()
        visits = []
        def visit(**kw):
            visits.append(kw)
            if failure == 'map':
                world.map_id = 1
        nav = NS(find_path=lambda m, a, b: [a, Vec3(5, 0, 0)] if failure == 'partial' else [a, b])
        with pytest.raises(LiveControlError):
            visit_remote_quest_giver(offer(1400).giver, location=lambda: (state, world),
                driver=driver, nav=nav, visit=visit, now=lambda: 0)
        assert all('destination_hint' not in v for v in visits)


def test_planning_time_counts_against_travel_budget():
    state, world, driver = environment()
    clock = [0]
    def path(m, a, b):
        clock[0] = 440
        return [a, b]
    visit = Mock()
    with pytest.raises(EncounterRouteUnavailable, match='planning exhausted'):
        visit_remote_quest_giver(offer(1400).giver, location=lambda: (state, world),
            driver=driver, nav=NS(find_path=path), visit=visit, now=lambda: clock[0])
    visit.assert_not_called()


def test_candidates_beyond_old_radius_are_available_with_explicit_travel_budget():
    from test_missed_tirisfal_quests import fixture, ids
    profile, state, current, db = fixture()
    assert 705 not in ids(recovery_offers(profile, state, current, db, {}, now=1000, metadata={}))
    assert 705 in ids(recovery_offers(profile, state, current, db, {}, now=1000,
                                     metadata={}, radius=4000))


def test_offer_acceptance_waits_for_remote_visit(monkeypatch):
    from wotlkbuddy.horde import local_offers, missed_quests
    state, world, driver = environment()
    state.merchant_open, state.dialog = False, None
    chosen = offer(900)
    monkeypatch.setattr(local_offers, 'nearby_profile_quest_givers', lambda **k: ())
    monkeypatch.setattr(missed_quests, 'recovery_offers', lambda *a, **k: (chosen,))
    steps = []
    def visit(**kw):
        steps.append('walk' if '_waypoint' in kw else 'target')
        world.position = kw.get('_waypoint', kw.get('destination_hint'))
        return state
    monkeypatch.setattr(local_offers, 'visit_npc', visit)
    def accept(**kw):
        assert steps[-1] == 'target'
        assert kw['allowed_quest_ids'] == chosen.quest_ids
        return (1,)
    monkeypatch.setattr(local_offers, 'accept_npc_offers', accept)
    result = local_offers.collect_profile_offers(profile=None, location=lambda: (state, world),
        cap=lambda: state, driver=driver, nav=NS(find_path=lambda m, a, b: [a, b]), db=None,
        pid=1, hud=Mock(), set_native_status=Mock(), log=Mock(), memory={}, now=lambda: 0)
    assert result == {'Giver': (1,)} and steps[0] == 'walk' and steps[-1] == 'target'

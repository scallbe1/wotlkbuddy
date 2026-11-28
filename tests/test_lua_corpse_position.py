from dataclasses import replace
from unittest.mock import Mock

import pytest

from test_postquest_v021 import snap
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.codec import DeathState, parse_payload
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.lua_corpse_position import resolve_lua_corpse, ZONE_SPANS
from wotlkbuddy.live.world_position import WorldPosition


def setup_marker():
    base = snap()
    player = replace(base.player, dead=True, health=0, map_x=.4, map_y=.5)
    state = replace(base, player=player,
                    death_state=DeathState(True, False, False, 'Tirisfal', .41, .52))
    pos = WorldPosition(player.guid, 0, 1800, 1400, 90, 0, 1)
    _, width, height = ZONE_SPANS['Tirisfal']
    target = Vec3(pos.x-.02*height, pos.y-.01*width, 95)
    return state, pos, target


def test_lua_marker_world_axes_and_navmesh_height():
    state, pos, target = setup_marker()
    nav = Mock()
    nav.nearest_point.return_value = target
    nav.find_path.return_value = [pos.position, target]
    assert resolve_lua_corpse(state, pos, nav=nav) == (
        0, target.x, target.y, target.z)
    query = nav.nearest_point.call_args_list[0].args[1]
    assert query.x == pytest.approx(target.x)
    assert query.y == pytest.approx(target.y)
    assert query.z == pos.z


def test_no_marker_or_no_mesh_does_not_guess():
    state, pos, _ = setup_marker()
    nav = Mock()
    nav.nearest_point.return_value = None
    with pytest.raises(LiveControlError, match='No reachable'):
        resolve_lua_corpse(state, pos, nav=nav)
    with pytest.raises(LiveControlError, match='marker unavailable'):
        resolve_lua_corpse(replace(state, death_state=DeathState(True, False, False)),
                           pos, nav=nav)


def test_multiple_reachable_floors_rejected():
    state, pos, target = setup_marker()
    nav = Mock()
    upper = Vec3(target.x, target.y, target.z+20)
    nav.nearest_point.side_effect = lambda _map, q: upper if q.z > 100 else target
    nav.find_path.side_effect = lambda _map, start, end: [start, end]
    with pytest.raises(LiveControlError, match='multiple reachable'):
        resolve_lua_corpse(state, pos, nav=nav)


def test_lua_extended_death_row():
    prefix = (b'P\tAarr\t0x000000000000025F\t5\t0\t60\t0\t1'
              b'\t.2\t.3\t2\t12\tDeathknell\t\t\n')
    state = parse_payload(prefix+b'DS\t1\t0\t0\tTirisfal\t.22\t.31\n', 2)
    assert state.death_state == DeathState(True, False, False, 'Tirisfal', .22, .31)
    assert parse_payload(prefix+b'DS\t1\t0\t0\n', 3).death_state.corpse_map_name is None


def test_deathknell_parent_selected_before_player_and_corpse_samples():
    from pathlib import Path
    addon = (Path(__file__).resolve().parents[1] / 'addons' /
             'WotLKBuddyTelemetry' / 'WotLKBuddyTelemetry.lua')
    text = addon.read_text(encoding='utf-8')
    helper = text[text.index('local function selectGhostMap()'):
                  text.index('local function tickData()')]
    assert "GetMapInfo() ~= 'DeathknellStart'" in helper
    assert "GetMapInfo() == 'Tirisfal'" in helper
    assert 'SetMapZoom(2, index)' in helper
    tick = text[text.index('local function tickData()'):]
    assert (tick.index('if ghostMap then selectGhostMap() end') <
            tick.index("local px, py = GetPlayerMapPosition('player')") <
            tick.index('local cx, cy = GetCorpseMapPosition()'))
    # Never alias DeathknellStart coordinates to Tirisfal's scale.
    assert 'DeathknellStart' not in ZONE_SPANS


def test_unreachable_body_has_reachable_reclaim_ring():
    state,pos,target=setup_marker()
    nav=Mock()
    def nearest(_map,q):
        if Vec3(q.x,q.y,target.z).distance_to(target)<3:
            return target
        if abs(q.z-target.z)<=8:
            return Vec3(q.x,q.y,target.z)
        return None
    nav.nearest_point.side_effect=nearest
    nav.find_path.side_effect=lambda m,a,b: [a,a] if b==target else [a,b]
    assert resolve_lua_corpse(state,pos,nav=nav)==pytest.approx((0,target.x,target.y,target.z))


def test_off_mesh_marker_keeps_original_xy_and_uses_reachable_ring_elevation():
    state,pos,target=setup_marker()
    nav=Mock()
    def nearest(_map,q):
        xy=Vec3(q.x,q.y,target.z).distance_to(target)
        if 25<=xy<=36 and abs(q.z-target.z)<=8:
            return Vec3(q.x,q.y,target.z)
        return None
    nav.nearest_point.side_effect=nearest
    nav.find_path.side_effect=lambda m,a,b:[a,b]
    logs=[]
    assert resolve_lua_corpse(state,pos,nav=nav,log=logs.append)==pytest.approx((0,target.x,target.y,target.z))
    assert any('live reclaim readiness required' in msg for msg in logs)


def test_partial_ring_routes_are_rejected():
    state,pos,target=setup_marker()
    nav=Mock()
    nav.nearest_point.side_effect=lambda m,q:Vec3(q.x,q.y,target.z) if abs(q.z-target.z)<=8 else None
    nav.find_path.side_effect=lambda m,a,b:[a,Vec3(a.x+1,a.y,a.z)]
    with pytest.raises(LiveControlError,match='No reachable'):
        resolve_lua_corpse(state,pos,nav=nav)

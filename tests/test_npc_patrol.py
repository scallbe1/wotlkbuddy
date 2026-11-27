from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.npc_patrol import load_patrol, patrol_order, visit_patrolling_npc
from wotlkbuddy.horde.npc_visit import NpcTargetUnavailable
from wotlkbuddy.live.control import LiveControlError


def test_gordo_server_route():
    source = Path(__file__).resolve().parents[1] / 'datadb' / 'CreaturePatrols.tsv'
    route = load_patrol(source, 10666, 0, Vec3(2235.52,607.519,33.4119))
    assert len(route) == 30
    assert route[15] == Vec3(2203.34,1060.23,28.8169)
    assert patrol_order(route, route[15]) == route[15:] + route[:15]
    assert load_patrol(source, 10666, 1, route[0]) == ()
    assert load_patrol(source, 10666, 0, Vec3(0,0,0)) == ()


def test_ambiguous_patrols_not_used(tmp_path):
    file = tmp_path / 'CreaturePatrols.tsv'
    file.write_text('1\t0\t1\t10\t1\t0\t0\t0\n1\t0\t1\t10\t2\t1\t0\t0\n'
                    '1\t0\t2\t20\t1\t0\t0\t0\n1\t0\t2\t20\t2\t1\t0\t0\n')
    assert load_patrol(file, 1, 0, Vec3(0,0,0)) == ()


def test_patrol_search_reaches_live_npc(monkeypatch):
    import wotlkbuddy.horde.npc_patrol as patrol
    import wotlkbuddy.horde.npc_visit as visit
    points = tuple(Vec3(x,0,0) for x in (0,40,80,120))
    player = NS(guid='owner', dead=False, in_combat=False, target_guid='')
    state = NS(player=player, truncated=False)
    position = [points[0]]
    walked = []
    mob = NS(entry=10666, guid='gordo', health=10, position=Vec3(123,0,0))
    driver = NS(emergency_stop_requested=lambda:False, wow_is_foreground=lambda:True,
                awareness=NS(read=lambda s,p:[mob] if p.distance_to(mob.position)<=50 else []))
    monkeypatch.setattr(patrol,'creature_entry_from_guid',lambda guid:10666)
    def select(*args):
        player.target_guid='gordo'
        return state
    monkeypatch.setattr(visit,'target_npc',select)
    def walk(point,budget):
        walked.append(point)
        position[0]=point
    result = visit_patrolling_npc(points=points,npc_name='Gordo',npc_id=10666,pid=1,
        owner='owner',map_id=0,capture=lambda:state,driver=driver,
        world_position=lambda s:(1,NS(position=position[0],map_id=0)),
        sleep=lambda _:None,now=lambda:0,log=lambda _:None,walk=walk)
    assert result is state
    assert walked == [points[1],points[2],mob.position]


def test_missing_patrol_is_bounded():
    points = tuple(Vec3(x,0,0) for x in (0,40,80))
    state = NS(player=NS(guid='owner',dead=False,in_combat=False),truncated=False)
    position = [points[0]]
    walked = []
    driver = NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True,
                awareness=NS(read=lambda s,p:[]))
    def walk(point,budget):
        walked.append(point)
        position[0]=point
    with pytest.raises(NpcTargetUnavailable):
        visit_patrolling_npc(points=points,npc_name='Gordo',npc_id=10666,pid=1,
            owner='owner',map_id=0,capture=lambda:state,driver=driver,
            world_position=lambda s:(1,NS(position=position[0],map_id=0)),
            sleep=lambda _:None,now=lambda:0,log=lambda _:None,walk=walk)
    assert walked == list(points[1:])

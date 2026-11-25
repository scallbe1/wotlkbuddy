from dataclasses import replace
from unittest.mock import Mock,patch
import pytest
from test_postquest_v021 import snap
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.death_recovery import recover_corpse
from wotlkbuddy.horde.resurrection_site import alternate_approach_path
from wotlkbuddy.live.codec import DeathState
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_position import WorldPosition

def test_partial_fence_replan_changes_reclaim_approach():
    base=snap()
    guid=base.player.guid
    start=WorldPosition(guid,0,100,0,0,0,1)
    fence=WorldPosition(guid,0,90,6,0,0,1)
    old_site=Vec3(33,0,0)
    site=WorldPosition(guid,0,0,33,0,0,1)
    where=start
    alive=False
    driver=Mock()
    driver.wow_is_foreground.return_value=True
    driver.emergency_stop_requested.return_value=False
    def sample():
        return replace(base,player=replace(base.player,dead=not alive,health=50 if alive else 0),
            death_state=DeathState(not alive,False,not alive and where==site)),where
    def chat(command):
        nonlocal alive
        assert command=='/run RetrieveCorpse()'
        assert where==site
        alive=True
    driver.chat.side_effect=chat
    def travel(path,**kwargs):
        nonlocal where
        if where==start:
            where=fence
            return Mock(stopped_reason='Backtracked but fresh Detour route unavailable: Navmesh route does not reach the quest destination (partial path)')
        assert path[0]==fence.position and path[-1]==site.position
        where=site
        return Mock(stopped_reason='complete')
    nav=Mock()
    with patch('wotlkbuddy.horde.death_recovery.follow_world_path',side_effect=travel), \
        patch('wotlkbuddy.horde.death_recovery.approach_path',return_value=(start.position,old_site)), \
        patch('wotlkbuddy.horde.death_recovery.alternate_approach_path',return_value=(fence.position,site.position)) as alternate:
        result=recover_corpse(sample=sample,driver=driver,nav=nav,known_corpse=(0,0,0,0),
            read_hazards=lambda *_:(),sleep=lambda _:None)
    assert alternate.call_args.args[2]==fence.position
    assert result['status']=='resurrected'

def test_alternative_reclaim_site_uses_complete_route():
    origin=Vec3(100,0,0)
    corpse=Vec3(0,0,0)
    nav=Mock()
    nav.nearest_point.side_effect=lambda _,p:p
    nav.find_path.side_effect=lambda _,a,b:[a,b] if b.y>20 else [a,Vec3(85,0,0)]
    path=alternate_approach_path(nav,0,origin,corpse)
    assert path[0]==origin
    assert path[-1].y>20
    assert 18<=path[-1].distance_to(corpse)<=37

def test_partial_routes_never_authorize_ghost_movement():
    origin=Vec3(100,0,0)
    corpse=Vec3(0,0,0)
    nav=Mock()
    nav.nearest_point.side_effect=lambda _,p:p
    nav.find_path.side_effect=lambda _,a,b:[a,a]
    with pytest.raises(LiveControlError,match='No complete alternative'):
        alternate_approach_path(nav,0,origin,corpse)


def test_previous_reclaim_endpoint_excluded_on_repeat():
    origin=Vec3(100,0,0)
    corpse=Vec3(0,0,0)
    nav=Mock()
    nav.nearest_point.side_effect=lambda _,p:p
    nav.find_path.side_effect=lambda _,a,b:[a,b]
    previous=Vec3(33,0,0)
    path=alternate_approach_path(nav,0,origin,corpse,excluded_ends=(previous,))
    assert path[-1].distance_to(previous)>=8

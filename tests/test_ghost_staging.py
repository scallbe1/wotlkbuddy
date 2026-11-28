from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from test_lua_corpse_position import setup_marker
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.lua_corpse_position import CorpseSurfaceUnavailable
from wotlkbuddy.horde import ghost_staging


def test_distant_corpse_can_be_resolved_after_local_ghost_transit(monkeypatch):
    state,pos,_=setup_marker()
    origin=replace(pos,x=2348.67,y=492.03,z=33.37)
    marker=CorpseSurfaceUnavailable(0,2822.81,1039.50)
    current=[origin]
    def resolve(s,p,**kwargs):
        if ghost_staging.remaining(p.position,marker)>600:
            raise marker
        return (0,marker.x,marker.y,111.63)
    monkeypatch.setattr(ghost_staging,'resolve_lua_corpse',resolve)
    nav=NS(nearest_point=lambda m,p:p,find_path=lambda m,a,b:[a,b])
    driver=Mock()
    def walk(path,**kwargs):
        assert kwargs['ghost_only'] and kwargs['expected_map_id']==0
        end=path[-1]
        current[0]=replace(origin,x=end.x,y=end.y,z=end.z)
        return NS(stopped_reason='complete')
    assert ghost_staging.resolve_with_ghost_staging(state,origin,nav=nav,
        sample=lambda:(state,current[0]),driver=driver,log=lambda _:None,
        supervise=lambda:None,travel=walk)==(0,marker.x,marker.y,111.63)
    assert current[0]!=origin


def test_incomplete_local_paths_never_authorize_movement():
    marker=CorpseSurfaceUnavailable(0,700,0)
    nav=NS(nearest_point=lambda m,p:p,find_path=lambda m,a,b:[a,Vec3(a.x+1,a.y,a.z)])
    assert ghost_staging.staged_ghost_path(nav,Vec3(0,0,0),marker,supervise=lambda:None) is None


def test_ambiguous_corpse_floor_does_not_trigger_staged_movement(monkeypatch):
    from wotlkbuddy.live.control import LiveControlError
    state,pos,_=setup_marker()
    def resolve(*a,**k):
        raise LiveControlError('corpse floor is ambiguous')
    monkeypatch.setattr(ghost_staging,'resolve_lua_corpse',resolve)
    travel=Mock()
    with pytest.raises(LiveControlError,match='ambiguous'):
        ghost_staging.resolve_with_ghost_staging(state,pos,nav=Mock(),sample=lambda:(state,pos),
            driver=Mock(),log=lambda _:None,supervise=lambda:None,travel=travel)
    travel.assert_not_called()

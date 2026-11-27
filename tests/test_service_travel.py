"""Regression coverage for town service trips beyond the old 600y bound."""
from types import SimpleNamespace as NS
import pytest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.profile import HordeProfile, VendorSpec
from wotlkbuddy.horde.service_travel import service_candidates, visit_service_npc
from wotlkbuddy.horde.hunt_routes import EncounterRouteUnavailable


ORIGIN=Vec3(2121.21,-550.36,73.02)
TOWN=Vec3(2259.73,275.571,34.8637)


def test_catalog_expands_to_verified_distant_repair_vendor():
    profile=HordeProfile('test','',(),(VendorSpec('Test repair',2118,'repair',TOWN),),
                         (),.2,2,())
    db=NS(find=lambda entry,map_id,limit:[NS(position=TOWN)])
    rows=service_candidates(profile=profile,spawns=db,origin=ORIGIN,map_id=0,
                            role='repair',profiles_root=None)
    assert len(rows)==1
    assert 800<rows[0].distance<850
    assert rows[0].entry==2118 and rows[0].position==TOWN


def service_fixture():
    state=NS(truncated=False,player=NS(guid='p',dead=False,in_combat=False),
             resources=NS(class_token='MAGE',race_token='Undead'))
    where=NS(position=ORIGIN,map_id=0)
    driver=NS(wow_is_foreground=lambda:True,emergency_stop_requested=lambda:False)
    db=NS(find=lambda entry,map_id,limit:[NS(position=TOWN)])
    kwargs=dict(npc_name='Abigail Shiel',npc_id=2118,pid=42,capture=lambda:state,
                driver=driver,nav=None,spawns=db,destination_hint=TOWN,
                log=lambda _:None,world_position=lambda s:(42,where))
    return state,where,kwargs


def test_remote_vendor_route_stages_then_uses_local_identity_checks():
    state,where,kwargs=service_fixture()
    calls=[]
    def transit(s,dest,**opts):
        calls.append('transit')
        assert opts['camp_transit'] and opts['quest_id'] is None
        assert dest==TOWN
        where.position=Vec3(TOWN.x,TOWN.y-100,TOWN.z)
        return state
    def visit(**opts):
        calls.append('visit')
        assert opts['radius']==600
        assert opts['destination_hint']==TOWN
        return state
    assert visit_service_npc(**kwargs,visit=visit,transit=transit) is state
    assert calls==['transit','visit']


def test_no_progress_is_recoverable_not_an_infinite_movement_loop():
    state,where,kwargs=service_fixture()
    with pytest.raises(EncounterRouteUnavailable,match='did not make measured progress'):
        visit_service_npc(**kwargs,transit=lambda *a,**k:state,
                          visit=lambda **k:pytest.fail('must not visit'))


def test_absent_spawn_does_not_authorize_guessed_travel():
    state,where,kwargs=service_fixture()
    kwargs['spawns']=NS(find=lambda *a:[])
    with pytest.raises(EncounterRouteUnavailable,match='No verified service spawn'):
        visit_service_npc(**kwargs,transit=lambda *a,**k:pytest.fail('must not move'),
                          visit=lambda **k:pytest.fail('must not visit'))

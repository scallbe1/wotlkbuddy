from types import SimpleNamespace as NS
from wotlkbuddy.combat.crowd_control import CONTROL_RULES, control_action
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.resurrection_site import acceptable_site, watch_reclaim_window


def test_learned_polymorph_without_bound_button_requires_live_range_and_identity():
    enemy=NS(guid='escort',entry=1537)
    state=NS(player=NS(target_guid='escort',level=9),
        combat_target=NS(entry_id=1537,attackable=True,dead=False,health=156,
            reaction=2,classification='normal',level=9,creature_type='Humanoid'),
        target_auras=(),action_spells=(),resources=NS(mana=300))
    rule=CONTROL_RULES['MAGE'][0]
    driver=NS(verified_polymorph_id=118,control_range=lambda _:20)
    assert control_action(state,enemy,rule,driver).named_fallback
    driver.control_range=lambda _:26
    assert control_action(state,enemy,rule,driver) is None
    assert control_action(state,enemy,rule,NS(control_range=lambda _:20)) is None
    state.player.target_guid='different'
    driver.control_range=lambda _:20
    assert control_action(state,enemy,rule,driver) is None


def test_reclaim_rejects_even_one_close_attacker():
    corpse=Vec3(0,0,0)
    site=Vec3(30,0,0)
    assert not acceptable_site(site,corpse,[Vec3(38.4,0,0)])
    assert acceptable_site(site,corpse,[Vec3(60,0,0)])


def test_reclaim_watch_never_accepts_one_attacker():
    corpse=Vec3(0,0,0)
    site=Vec3(30,0,0)
    state=NS(player=NS(dead=True),death_state=NS(ghost=True,retrieve_ready=True))
    where=NS(position=site,map_id=0)
    assert watch_reclaim_window(sample=lambda:(state,where),
        read_hazards=lambda *_:[Vec3(38.4,0,0)],corpse=corpse,
        nav=NS(),map_id=0,sleep=lambda _:None,now=lambda:0,
        patrol_seconds=2,reclaim_seconds=4) is None


def test_fallback_allows_one_distant_attacker_only_with_escape():
    from wotlkbuddy.horde.resurrection_site import reclaim_escape_path, reclaim_window_ok
    site=Vec3(30,0,0)
    distant=Vec3(50,0,0)
    nav=NS(find_path=lambda _,a,b:(a,b),nearest_point=lambda _,p:p)
    assert not reclaim_window_ok(nav,0,site,(distant,))
    assert reclaim_window_ok(nav,0,site,(distant,),fallback=True)
    path=reclaim_escape_path(nav,0,site,(distant,),route_hints=(Vec3(10,0,0),))
    assert path and path[-1].distance_to(distant)>=26
    assert reclaim_escape_path(nav,0,site,(Vec3(38.4,0,0),)) is None
    assert reclaim_escape_path(nav,0,site,(distant,Vec3(30,20,0))) is None


def test_fallback_without_reachable_exit_is_refused():
    from wotlkbuddy.horde.resurrection_site import reclaim_escape_path
    nav=NS(find_path=lambda *_:(),nearest_point=lambda *_:None)
    assert reclaim_escape_path(nav,0,Vec3(30,0,0),(Vec3(50,0,0),)) is None


def test_fallback_watch_requires_eight_stable_observations():
    from wotlkbuddy.horde.resurrection_site import watch_reclaim_window
    corpse=Vec3(0,0,0)
    site=Vec3(30,0,0)
    state=NS(player=NS(dead=True),death_state=NS(ghost=True,retrieve_ready=True))
    where=NS(position=site,map_id=0)
    nav=NS(find_path=lambda _,a,b:(a,b))
    t=[0.]
    def sleep(seconds):
        t[0]+=seconds
    assert watch_reclaim_window(sample=lambda:(state,where),
        read_hazards=lambda *_:(Vec3(50,0,0),),corpse=corpse,
        nav=nav,map_id=0,sleep=sleep,now=lambda:t[0],fallback=True)==site
    assert t[0]>=3.5

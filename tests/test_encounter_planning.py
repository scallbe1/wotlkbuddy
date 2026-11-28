from types import SimpleNamespace
from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.encounter import Enemy,assess_encounter

def mob(g,x,level=5,kind='normal',entry=1506):
    return Enemy(g,entry,g,Vec3(x,0,0),level,kind,75)

def test_linked_named_nonobjective_enemy_changes_pull_decision():
    p=SimpleNamespace(level=6,health=100,max_health=100)
    r=SimpleNamespace(mana=100,max_mana=100)
    a,b,c=mob('a',20),mob('b',29),mob('named',39,kind='rare',entry=1667)
    plan=assess_encounter(p,r,Vec3(0,0,0),a,(a,b,c),(1506,))
    assert not plan.allowed and len(plan.enemies)==3

def test_three_substantially_weaker_quest_enemies_allowed_at_full_resources():
    p=SimpleNamespace(level=9,health=150,max_health=150)
    r=SimpleNamespace(mana=100,max_mana=100)
    a,b,c=mob('a',20),mob('b',29),mob('c',39)
    assert assess_encounter(p,r,Vec3(0,0,0),a,(a,b,c),(1506,)).allowed
    p.health=50
    assert not assess_encounter(p,r,Vec3(0,0,0),a,(a,b,c),(1506,)).allowed

def test_single_quest_enemy_separate_from_other_pack():
    p=SimpleNamespace(level=6,health=100,max_health=100)
    r=SimpleNamespace(mana=100,max_mana=100)
    a,b,c=mob('a',20),mob('b',40),mob('c',49)
    assert assess_encounter(p,r,Vec3(0,0,0),a,(a,b,c),(1506,)).allowed


def test_level_eleven_approves_observed_five_seven_seven_pack():
    from wotlkbuddy.horde.encounter import assess_linked_pull, weaker_pack_budget, Enemy
    from types import SimpleNamespace as NS
    p=NS(level=11,health=192,max_health=192,dead=False,in_combat=False,target_guid='a')
    r=NS(mana=520,max_mana=520)
    a=Enemy('a',1935,'a',Vec3(20,0,0),5,'normal',102)
    b=Enemy('b',1934,'b',Vec3(24,0,0),7,'unknown',137)
    c=Enemy('c',1934,'c',Vec3(27,0,0),7,'unknown',137)
    state=NS(player=p,resources=r,combat_target=NS(entry_id=a.entry,attackable=True,
        dead=False,health=102,reaction=2))
    plan=assess_encounter(p,r,Vec3(0,0,0),a,(a,b,c))
    assert plan.allowed and weaker_pack_budget(p,r,(a,b,c))
    result,approved=assess_linked_pull(state,plan,a,NS(assessing_travel_corridor=True),
        None,(),lambda _:None)
    assert result is state and approved is plan


def test_weak_corridor_budget_rejects_elites_unknown_levels_and_large_groups():
    from wotlkbuddy.horde.encounter import weaker_pack_budget
    from dataclasses import replace
    p=SimpleNamespace(level=11,health=192,max_health=192)
    r=SimpleNamespace(mana=520,max_mana=520)
    a,b,c=mob('a',20),mob('b',22),mob('c',25)
    assert weaker_pack_budget(p,r,(a,b,c))
    assert not weaker_pack_budget(p,r,(a,b,replace(c,classification='elite')))
    assert not weaker_pack_budget(p,r,(a,b,replace(c,level=None)))
    assert not weaker_pack_budget(p,r,(a,b,c,mob('d',27)))
    r.mana=300
    assert not weaker_pack_budget(p,r,(a,b,c))


def test_weak_corridor_acquisition_failure_does_not_force_detour(monkeypatch):
    from wotlkbuddy.horde import threat_scan
    from wotlkbuddy.live.control import LiveControlError
    state=object()
    def fail(*a,**k):
        raise LiveControlError('Unsafe encounter: could not acquire exact discovered hostile GUID')
    monkeypatch.setattr(threat_scan,'acquire_corridor_target',fail)
    args=dict(awareness=object(),driver=object(),fresh=lambda s:s,max_targets=7,
              pack_positions=[],log=lambda _:None)
    assert threat_scan.acquire_travel_target(state,[(20,'a',1935)],weak=True,**args)==(state,None,None)
    import pytest
    with pytest.raises(LiveControlError):
        threat_scan.acquire_travel_target(state,[(20,'a',1935)],weak=False,**args)


def test_weaker_pack_behind_geometry_can_advance_only_with_accounted_units():
    from wotlkbuddy.horde.threat_scan import weaker_corridor
    state=SimpleNamespace(player=SimpleNamespace(level=11,health=192,max_health=192),
                          resources=SimpleNamespace(mana=520,max_mana=520))
    enemies=(mob('a',12),mob('b',22))
    positions=[e.position for e in enemies]
    assert weaker_corridor(state,enemies,(),positions)
    assert not weaker_corridor(state,enemies,(object(),),positions)
    assert not weaker_corridor(state,enemies,(),positions[:1])


def test_neutral_duskbat_is_authorized_only_for_active_quest_source():
    from types import SimpleNamespace as NS
    from wotlkbuddy.horde.encounter import survey_pull
    guid='0xF130000611000601'
    p=NS(guid='player',target_guid=guid,target_name='Greater Duskbat',level=11,
         health=192,max_health=192,in_combat=False)
    r=NS(mana=520,max_mana=520)
    target=NS(entry_id=1553,attackable=True,dead=False,health=75,reaction=4,
              level=6,classification='normal')
    s=NS(player=p,resources=r,combat_target=target)
    where=NS(position=Vec3(0,0,0),z=0)
    awareness=NS(survey=lambda *a,**k:(s,(),()),acquire=lambda *a:s)
    args=dict(initial=s,capture=lambda:s,driver=NS(awareness=awareness),
        player_position=lambda _:where,target_position=lambda _:Vec3(28,0,0))
    _,plan=survey_pull(**args,quest_entries=(1553,1554))
    assert plan.allowed and plan.enemies[0].guid==guid
    _,plan=survey_pull(**args,quest_entries=())
    assert not plan.allowed
    target.reaction=5
    _,plan=survey_pull(**args,quest_entries=(1553,1554))
    assert not plan.allowed


def test_neutral_objective_still_assesses_nearby_hostile_pack():
    from types import SimpleNamespace as NS
    from wotlkbuddy.horde.encounter import survey_pull
    guid='0xF130000611000601'
    p=NS(guid='player',target_guid=guid,target_name='Greater Duskbat',level=11,
         health=192,max_health=192,in_combat=False)
    s=NS(player=p,resources=NS(mana=520,max_mana=520),
         combat_target=NS(entry_id=1553,attackable=True,dead=False,health=75,
                          reaction=4,level=6,classification='normal'))
    where=NS(position=Vec3(0,0,0),z=0)
    awareness=NS(survey=lambda *a,**k:(s,(mob('elite',29,kind='elite'),),()),
                 acquire=lambda *a:s)
    _,plan=survey_pull(initial=s,capture=lambda:s,driver=NS(awareness=awareness),
        player_position=lambda _:where,target_position=lambda _:Vec3(28,0,0),
        quest_entries=(1553,1554))
    assert not plan.allowed and 'elite' in plan.reason


def test_neutral_quest_rotation_requires_exact_source_permission():
    from dataclasses import replace
    from wotlkbuddy.combat.runtime import selected_action
    from test_horde_combat_v019 import snap
    from types import SimpleNamespace as NS
    s=snap()
    s=replace(s,player=replace(s.player,target_guid='0xF130000611000601'),
        combat_target=replace(s.combat_target,entry_id=1553,reaction=4,
            health=75,max_health=75,attackable=True,dead=False),
        action_spells=(NS(spell_id=143,name='Fireball',key='2',cooldown_ms=0,usable=True,in_range=True),),
        current_cast_remaining_ms=0)
    assert selected_action(s) is None
    assert selected_action(s,quest_entry=1554) is None
    assert selected_action(s,quest_entry=1553).name=='Fireball'


def test_manageable_equal_level_pair_approved_but_risky_pair_rejected():
    from dataclasses import replace
    from wotlkbuddy.horde.encounter import manageable_pair
    p=SimpleNamespace(level=11,health=192,max_health=192)
    r=SimpleNamespace(mana=450,max_mana=520)
    a=replace(mob('a',20,level=11),health=140)
    b=replace(mob('b',29,level=10),health=160)
    assert manageable_pair(p,r,(a,b))
    assert assess_encounter(p,r,Vec3(0,0,0),a,(a,b)).allowed
    assert not manageable_pair(p,r,(a,replace(b,classification='elite')))
    assert not manageable_pair(p,r,(a,replace(b,level=12)))
    p.health=100
    assert not manageable_pair(p,r,(a,b))


def test_single_potential_hostile_can_be_approached_with_defense_armed():
    from dataclasses import replace
    from wotlkbuddy.horde.threat_scan import weaker_corridor
    p=SimpleNamespace(level=11,health=192,max_health=192)
    r=SimpleNamespace(mana=490,max_mana=520)
    state=SimpleNamespace(player=p,resources=r)
    a=replace(mob('a',20,level=10,kind='unknown'),health=135)
    assert weaker_corridor(state,(a,),(),[a.position])
    assert not weaker_corridor(state,(a,),(object(),),[a.position])
    assert not weaker_corridor(state,(replace(a,health=500),),(),[a.position])


def test_manageable_target_acquisition_failure_does_not_remember_pack():
    from wotlkbuddy.horde.threat_scan import acquire_travel_target
    state=SimpleNamespace(player=SimpleNamespace(target_guid='',in_combat=False))
    driver=SimpleNamespace(dangerous_hunt_positions=[])
    awareness=SimpleNamespace(acquire=lambda *a:state)
    args=dict(awareness=awareness,driver=driver,fresh=lambda s:s,max_targets=7,
              pack_positions=[Vec3(20,0,0)],log=lambda _:None)
    assert acquire_travel_target(state,[(20,'a',1533)],weak=True,**args)==(state,None,None)
    assert not driver.dangerous_hunt_positions
    import pytest
    from wotlkbuddy.live.control import LiveControlError
    with pytest.raises(LiveControlError,match='could not acquire'):
        acquire_travel_target(state,[(20,'a',1533)],weak=False,**args)
    assert driver.dangerous_hunt_positions

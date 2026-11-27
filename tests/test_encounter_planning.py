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

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

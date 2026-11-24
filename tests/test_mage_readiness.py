from types import SimpleNamespace
from wotlkbuddy.combat.mage_hotbar import cleanup_plan
from wotlkbuddy.horde.encounter import Enemy,assess_encounter
from wotlkbuddy.game.types import Vec3

def test_duplicate_fireball_and_cannibalize_cleanup_preserves_one_damage_key():
    rows=((1,'spell',20577,'Cannibalize'),(2,'spell',133,'Fireball'),
          (6,'spell',133,'Fireball'),(7,'spell',133,'Fireball'),(8,'item',0,''))
    assert cleanup_plan(rows,{1,2,6,7,8})==[(1,20577,'Cannibalize'),(6,133,'Fireball'),(7,133,'Fireball')]

def test_two_weaker_enemies_including_escort_can_be_approved():
    p=SimpleNamespace(level=6,health=120,max_health=120)
    r=SimpleNamespace(mana=100,max_mana=100)
    a=Enemy('a',1667,'Meven Korgal',Vec3(24,0,0),5,'normal',102)
    b=Enemy('b',1507,'Scarlet Initiate',Vec3(19,0,0),3,'normal',81)
    assert assess_encounter(p,r,Vec3(0,0,0),a,(a,b),(1667,)).allowed
    p.health=60
    assert not assess_encounter(p,r,Vec3(0,0,0),a,(a,b),(1667,)).allowed

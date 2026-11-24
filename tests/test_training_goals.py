from wotlkbuddy.horde.training_goals import TrainingGoal,save_goal,load_goal
from wotlkbuddy.horde.encounter import Enemy,assess_encounter
from wotlkbuddy.game.types import Vec3
from types import SimpleNamespace

def test_training_goal_persists_and_retries_only_when_useful(tmp_path):
    g=TrainingGoal(6,50,8,(('Frostbolt','Rank 1',95),),'Isabella',2124,0,(1846,1634,97),1.)
    save_goal('guid','Aarr',g,tmp_path)
    restored=load_goal('guid','Aarr',tmp_path)
    assert restored==g
    assert not restored.due(6,50,8)
    assert restored.due(6,95,8)
    assert restored.due(6,50,6)
    assert restored.due(7,50,8)
    assert load_goal('guid2','Aarr',tmp_path) is None

def test_level6_vs_5_3_3_pack_uses_combined_strength():
    p=SimpleNamespace(level=6,health=110,max_health=110)
    r=SimpleNamespace(mana=100,max_mana=100)
    a=Enemy('a',1667,'Meven Korgal',Vec3(17,0,0),5,'normal',102)
    b=Enemy('b',1506,'Convert',Vec3(22,0,0),3,'normal',71)
    c=Enemy('c',1507,'Initiate',Vec3(11,0,0),3,'normal',81)
    assert assess_encounter(p,r,Vec3(0,0,0),a,(a,b,c),(1667,)).allowed
    p.health=40
    assert not assess_encounter(p,r,Vec3(0,0,0),a,(a,b,c),(1667,)).allowed


def test_empty_training_record_refreshes_after_significant_cash_gain():
    g=TrainingGoal(6,61,8,(),'Isabella',2124,0,(1,2,3),1)
    assert not g.due(6,61,8)
    assert not g.due(6,155,8)
    assert g.due(6,235,5)

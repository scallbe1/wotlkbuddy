from types import SimpleNamespace as NS
from wotlkbuddy.horde.session_training import training_priority


def test_odd_level_does_not_require_round_trip_after_completed_even_level(monkeypatch):
    state=NS(player=NS(guid='player',name='Aarr',level=9),
             resources=NS(money_copper=1000,free_bag_slots=5))
    goal=NS(level=8,pending=(),describe=lambda _: 'checked',due=lambda *a,**k:True)
    monkeypatch.setattr('wotlkbuddy.horde.session_training.load_goal',lambda *_:goal)
    monkeypatch.setattr('wotlkbuddy.horde.session_training.estimated_vendor_copper',lambda *_:0)
    args=NS(_continuous_training_check=True)
    def priority():
        return training_priority(args,cap=lambda:state,quest_profile=None,log=lambda _:None)[2]
    assert not priority()
    state.player.level=10
    assert priority()
    state.player.level=9
    goal.pending=(('Fireball','Rank 2',100),)
    assert priority()

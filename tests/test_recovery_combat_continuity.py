from types import SimpleNamespace as NS
from wotlkbuddy.horde.hunt_terrain import attach_hunt_memory
from wotlkbuddy.horde.combat_awareness import bind_combat_awareness
from wotlkbuddy.horde.aggro_recovery import recoverable_aggro_error
from wotlkbuddy.combat.mage_hotbar import reconcile_mage_hotbar
from wotlkbuddy.live.control import LiveControlError
import pytest


def test_recovery_awareness_restores_only_same_character_learned_control():
    memory={}
    original=NS()
    attach_hunt_memory(original,'A',memory)
    original.learned_control['Polymorph']=118
    recovery=NS()
    attach_hunt_memory(recovery,'A',memory)
    bind_combat_awareness(recovery,pid=1,cap=lambda:None,log=lambda _:None)
    assert recovery.verified_polymorph_id==118
    assert callable(recovery.control_range)
    assert recovery.awareness.driver is recovery
    other=NS()
    attach_hunt_memory(other,'B',memory)
    bind_combat_awareness(other,pid=1,cap=lambda:None,log=lambda _:None)
    assert not hasattr(other,'verified_polymorph_id')


@pytest.mark.parametrize('combat,focus,recoverable',[(True,True,True),(True,False,False)])
def test_hotbar_guard_distinguishes_combat_from_focus_loss(combat,focus,recoverable):
    state=NS(truncated=False,player=NS(guid='A',dead=False,in_combat=combat))
    driver=NS(emergency_stop_requested=lambda:False,wow_is_foreground=lambda:focus)
    with pytest.raises(LiveControlError) as exc:
        reconcile_mage_hotbar(capture=lambda:state,driver=driver)
    assert recoverable_aggro_error(exc.value) is recoverable

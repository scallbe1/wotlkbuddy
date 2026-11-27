"""An NPC can turn hostile during a verified quest interaction."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wotlkbuddy.horde.aggro_recovery import (
    NpcInteractionInterrupted, guard_interaction_aggro, recoverable_aggro_error)
from wotlkbuddy.horde.quest_offers import accept_npc_offers
from wotlkbuddy.live.control import LiveControlError


def frame(combat=False, target='Calvin Montague', owner='player', dead=False):
    return NS(truncated=False, sequence=1,
        player=NS(guid=owner,dead=dead,in_combat=combat,target_name=target,
                  target_guid='changed' if combat else 'npc'))


def driver():
    return NS(wow_is_foreground=lambda:True,emergency_stop_requested=lambda:False,
              on_aggro=Mock(),chat=Mock(),interact=Mock())


def test_new_attacker_takes_precedence_over_changed_quest_target():
    d=driver()
    hostile=frame(True,target='Hostile Calvin')
    after=frame(target='',owner='player')
    with pytest.raises(NpcInteractionInterrupted):
        accept_npc_offers(npc_name='Calvin Montague',npc_id=6784,
            capture=Mock(side_effect=[hostile,after]),driver=d)
    d.on_aggro.assert_called_once_with(hostile)
    d.chat.assert_not_called()
    d.interact.assert_not_called()


@pytest.mark.parametrize('changed',[frame(True,owner='other'),frame(True,dead=True)])
def test_never_defend_on_invalid_character_or_dead_state(changed):
    d=driver()
    with pytest.raises(LiveControlError,match='Unsafe player'):
        guard_interaction_aggro(changed,owner='player',driver=d,capture=Mock(),log=Mock())
    d.on_aggro.assert_not_called()


def test_unresolved_interaction_aggro_uses_existing_recovery():
    d=driver()
    hostile=frame(True)
    with pytest.raises(LiveControlError) as error:
        guard_interaction_aggro(hostile,owner='player',driver=d,
            capture=lambda:hostile,log=Mock())
    assert recoverable_aggro_error(error.value)

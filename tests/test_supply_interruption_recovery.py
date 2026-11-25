from dataclasses import replace
import pytest
from test_mage_sustain_threat_v041 import FakeSupplies
from wotlkbuddy.horde.sustain import (maintain_mage_supplies,recover_after_encounter,
    SupplyCombatInterrupted)
from wotlkbuddy.live.control import LiveControlError

class Ambush(FakeSupplies):
    def __init__(self):
        super().__init__()
        self.water=3
        self.food=4
        self.combat=False
        self.hp=60
        self.mana=55
        self.defenses=0
        self.interrupted=False
        self.focus=True
    def wow_is_foreground(self):
        return self.focus
    def capture(self):
        s=super().capture()
        return replace(s,player=replace(s.player,in_combat=self.combat))
    def chat(self,command):
        super().chat(command)
        if command in ('/cast Conjure Water','/use 0 1') and not self.interrupted:
            self.interrupted=True
            self.combat=True
            self.mana=45
    def defend(self,state):
        assert state.player.in_combat
        self.defenses+=1
        self.combat=False
        return self.capture()

def test_supply_conjure_interrupt_identified_before_more_input():
    g=Ambush()
    with pytest.raises(SupplyCombatInterrupted):
        maintain_mage_supplies(capture=g.capture,driver=g,sleep=g.sleep)
    assert g.commands==['/cast Conjure Water']

def test_ambush_during_drinking_defends_then_resumes_rest():
    g=Ambush()
    s=recover_after_encounter(initial=g.capture(),capture=g.capture,driver=g,
        defend=g.defend,sleep=g.sleep)
    assert g.defenses==1
    assert not s.player.in_combat
    assert s.resources.mana>=85
    assert g.commands==['/use 0 1','/use 0 1']

def test_focus_loss_is_not_an_aggro_retry():
    g=Ambush()
    g.focus=False
    with pytest.raises(LiveControlError,match='focus'):
        recover_after_encounter(initial=g.capture(),capture=g.capture,driver=g,
            defend=g.defend,sleep=g.sleep)
    assert not g.commands and g.defenses==0

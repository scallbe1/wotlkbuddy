from dataclasses import replace
from types import SimpleNamespace
import pytest
from test_mage_sustain_threat_v041 import FakeSupplies
from wotlkbuddy.horde.sustain import maintain_mage_supplies
from wotlkbuddy.horde.recovery_rest import recover_and_buff
from wotlkbuddy.live.control import LiveControlError

def test_new_water_consumed_before_food_conjuration():
    g=FakeSupplies()
    state=maintain_mage_supplies(capture=g.capture,driver=g,sleep=g.sleep,
        mana_goal=.85,health_goal=.90,restock=False)
    assert g.commands[:2]==['/cast Conjure Water','/use 0 1']
    assert g.commands.index('/use 0 1')<g.commands.index('/cast Conjure Food')
    assert state.resources.mana>=80

def test_existing_water_consumed_before_any_optional_restock():
    g=FakeSupplies()
    g.water=3
    maintain_mage_supplies(capture=g.capture,driver=g,sleep=g.sleep,mana_goal=.85,restock=True)
    assert g.commands[0]=='/use 0 1'

def test_full_resources_dont_require_food_or_water_reserve_after_resurrection():
    g=FakeSupplies()
    g.hp=60
    g.mana=100
    s=g.capture()
    s=replace(s,mage_buffs=None)
    where=SimpleNamespace(position=object(),map_id=0)
    result=recover_and_buff(initial=s,sample=lambda:(s,where),driver=g,nav=None,
        read_hazards=lambda *_:(),sleep=g.sleep)
    assert result==s and not g.commands

def test_post_resurrection_reports_exact_focus_loss():
    g=FakeSupplies()
    s=g.capture()
    g.wow_is_foreground=lambda:False
    with pytest.raises(LiveControlError,match='lost foreground focus'):
        recover_and_buff(initial=s,sample=lambda:(s,None),driver=g,nav=None,
            read_hazards=lambda *_:(),sleep=g.sleep)
    assert not g.commands

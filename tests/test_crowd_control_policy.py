from types import SimpleNamespace as NS

from wotlkbuddy.combat.crowd_control import (
    EncounterControl, control_action, retain_fleeing_target)
from wotlkbuddy.combat.mage_hotbar import cleanup_plan


def snapshot(guid='enemy',hp=80,attacks=True,auras=(),cls='MAGE'):
    return NS(truncated=False,player=NS(target_guid=guid,dead=False,
        in_combat=True,health=100,max_health=100),
        resources=NS(class_token=cls), current_cast_remaining_ms=0,
        combat_target=NS(entry_id=1544,attackable=True,dead=False,
            health=hp,max_health=100,attacks_player=attacks,reaction=2),
        target_auras=auras,action_spells=(NS(name='Polymorph',spell_id=118,
            key='6',usable=True,in_range=True,cooldown_ms=0),))


def test_fleeing_target_must_be_original_exact_guid_and_low_health():
    assert retain_fleeing_target(snapshot(hp=15,attacks=False),'enemy',1544)
    assert not retain_fleeing_target(snapshot(hp=15,attacks=False),'other',1544)
    assert not retain_fleeing_target(snapshot(hp=80,attacks=False),'enemy',1544)


def test_polymorph_requires_second_observed_attacker_and_confirmed_aura():
    c=EncounterControl()
    c.observe(snapshot('first'))
    assert not c.may_control(snapshot('first'))
    c.observe(snapshot('second'))
    assert c.may_control(snapshot('second'))
    rule,action=control_action(snapshot('second'))
    assert action.spell_id==118
    assert not c.confirm(snapshot('second'),'second',rule)
    controlled=snapshot('second',auras=('Polymorph',))
    assert c.confirm(controlled,'second',rule)
    assert c.protected(controlled)
    assert not c.protected(snapshot('first'))
    assert not c.may_control(snapshot('first'))


def test_no_polymorph_on_near_dead_or_other_class():
    assert control_action(snapshot(hp=15)) is None
    assert control_action(snapshot(cls='PRIEST')) is None


def test_crowd_control_spells_preserved_on_hotbar():
    assert cleanup_plan([(6,'spell',118,'Polymorph'),
                         (7,'spell',122,'Frost Nova')],{6,7})==[]

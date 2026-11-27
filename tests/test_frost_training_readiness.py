from argparse import Namespace
from types import SimpleNamespace as NS

from wotlkbuddy.combat.mage_hotbar import MAGE_ACTIONS, FROST_REQUIRED_LEVELS
from wotlkbuddy.horde.session_training import missing_frost_requires_check
from wotlkbuddy.horde.training import trainer_purchase_priority


def test_missing_frost_overrides_stale_empty_budget_only_in_automatic_phase():
    args=Namespace(_continuous_training_check=True)
    driver=NS(missing_frost_spells={'Frostbolt','Frost Nova'})
    assert missing_frost_requires_check(args,driver,NS(pending=()))
    assert not missing_frost_requires_check(args,driver,NS(pending=(('Frostbolt','Rank 1',100),)))
    assert not missing_frost_requires_check(args,NS(missing_frost_spells=set()),NS(pending=()))
    assert not missing_frost_requires_check(Namespace(),driver,NS(pending=()))


def test_frost_spells_prioritized_for_training_and_hotbar():
    options=[NS(name='Arcane Missiles',cost=10,index=1),
             NS(name='Frost Nova',cost=100,index=2),
             NS(name='Frostbolt',cost=100,index=3)]
    assert [v.name for v in sorted(options,key=trainer_purchase_priority)]==[
        'Frostbolt','Frost Nova','Arcane Missiles']
    assert MAGE_ACTIONS.index('Frostbolt')<MAGE_ACTIONS.index('Conjure Water')
    assert MAGE_ACTIONS.index('Frost Nova')<MAGE_ACTIONS.index('Conjure Food')
    assert FROST_REQUIRED_LEVELS=={'Frostbolt':4,'Frost Nova':10}

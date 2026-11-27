"""Frost opener, sustained casting, finisher and verified range fallback."""
from types import SimpleNamespace as NS

from wotlkbuddy.combat.runtime import selected_action


def state(*, hp=100, frost_range=True, frost_usable=True, frost_cd=0,
          casting=False):
    def spell(name, ident, key, in_range=True, usable=True, cd=0):
        return NS(name=name,spell_id=ident,key=key,in_range=in_range,
                  usable=usable,cooldown_ms=cd)
    return NS(truncated=False,player=NS(dead=False,health=100,max_health=100),
              resources=NS(class_token='MAGE',mana=100,max_mana=100),
              combat_target=NS(dead=False,attackable=True,reaction=2,
                               health=hp,max_health=100),
              action_spells=(spell('Frostbolt',205,'7',frost_range,frost_usable,frost_cd),
                             spell('Fireball',143,'2'),spell('Fire Blast',2136,'3')),
              current_cast_remaining_ms=100 if casting else 0,
              player_auras=(),target_auras=())


def test_frostbolt_preferred_for_opening_and_sustained_damage():
    assert selected_action(state(),opening=True).name=='Frostbolt'
    assert selected_action(state()).name=='Frostbolt'


def test_fire_blast_finishes_low_health_target():
    assert selected_action(state(hp=20)).name=='Fire Blast'


def test_opening_fallback_preserves_range_and_readiness():
    for s in (state(frost_range=False),state(frost_range=None),
              state(frost_usable=False),state(frost_cd=1500)):
        assert selected_action(s,opening=True).name=='Fireball'
        assert selected_action(s).name=='Fire Blast'
    assert selected_action(state(casting=True),opening=True) is None

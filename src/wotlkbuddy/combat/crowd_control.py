"""Class-independent crowd-control policy, separate from damage rotation.

Only verified live attackers can be controlled. A control effect is not
assumed successful until its aura is observed on the exact selected GUID.
"""
from dataclasses import dataclass, field

from wotlkbuddy.combat.rotation import Ability


@dataclass(frozen=True)
class ControlRule:
    spell: str
    aura: str
    breaks_on_damage: bool
    min_target_health: float = .35


CONTROL_RULES = {
    'MAGE': (ControlRule('Polymorph', 'Polymorph', True),),
}


def control_action(state):
    """Choose an eligible targeted control action, not a damage spell."""
    target=state.combat_target
    if (state.truncated or state.player.dead or state.resources is None or
            not state.player.in_combat or not target or not target.attackable or
            target.dead or target.attacks_player is not True or
            target.reaction is None or not 1<=target.reaction<=3 or
            target.health/max(1,target.max_health)<.35 or
            state.current_cast_remaining_ms):
        return None
    for rule in CONTROL_RULES.get(state.resources.class_token.upper(),()):
        if rule.aura in state.target_auras:
            continue
        for a in state.action_spells:
            if a.name==rule.spell and a.usable and a.in_range is True and a.cooldown_ms==0:
                return rule, Ability(a.spell_id,a.name,a.key,in_range=True)
    return None


@dataclass
class EncounterControl:
    """Exact identities of attackers and confirmed controlled units."""
    attackers: set[str] = field(default_factory=set)
    controlled: dict[str, ControlRule] = field(default_factory=dict)
    attempted: set[str] = field(default_factory=set)

    def observe(self,state):
        guid=state.player.target_guid
        t=state.combat_target
        if not guid or not t:
            return
        if t.dead or t.health<=0:
            self.attackers.discard(guid)
            self.controlled.pop(guid,None)
        elif t.attackable and t.attacks_player is True:
            self.attackers.add(guid)
        if guid in self.controlled and self.controlled[guid].aura not in state.target_auras:
            self.controlled.pop(guid,None)

    def protected(self,state):
        guid=state.player.target_guid
        rule=self.controlled.get(guid)
        return bool(rule and rule.breaks_on_damage and rule.aura in state.target_auras)

    def may_control(self,state):
        guid=state.player.target_guid
        return (guid not in self.attempted and not self.controlled and
                len(self.attackers-{guid})>=1 and state.player.health/max(1,state.player.max_health)>=.45)

    def confirm(self,state,guid,rule):
        if state.player.target_guid==guid and rule.aura in state.target_auras:
            self.controlled[guid]=rule
            return True
        return False


def retain_fleeing_target(state, guid, entry):
    """An already engaged exact hostile remains valid when fleeing at low HP."""
    t=state.combat_target
    return bool(guid and state.player.target_guid==guid and t and
                t.entry_id==entry and t.attackable and not t.dead and
                t.reaction is not None and 1<=t.reaction<=3 and
                0<t.health<=.25*max(1,t.max_health))

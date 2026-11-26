"""Shared combat awareness for normal and recovery phases."""
from wotlkbuddy.horde.awareness import ObjectAwareness
from wotlkbuddy.live.world_position import (
    verified_live_position, verified_target_position, verified_nearby_creature_details)


def bind_combat_awareness(driver, *, pid, cap, log):
    driver.combat_position=lambda s: verified_live_position(s,pid=pid)[1]
    driver.control_range=lambda s: driver.combat_position(s).position.distance_to(
        verified_target_position(s,pid=pid))
    driver.awareness=ObjectAwareness(
        read=lambda state,center:verified_nearby_creature_details(
            state,pid=pid,center=center,radius=50),
        capture=cap,driver=driver,log=log)
    # This ID was observed from this character's spellbook in this session.
    # Never manufacture a learned spell or transfer another character's ID.
    learned=getattr(driver,'learned_control',None)
    if isinstance(learned,dict) and type(learned.get('Polymorph')) is int:
        driver.verified_polymorph_id=learned['Polymorph']

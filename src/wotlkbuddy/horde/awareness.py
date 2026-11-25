"""Read-only spatial discovery, followed by ordinary verified target acquisition.

A faction template is not a UnitReaction. Unknown creatures are retained as
potential hazards; only client-confirmed red, living, attackable units can be
offensive candidates. No target changes through process memory.
"""
from dataclasses import replace
from time import monotonic, sleep as real_sleep

from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.combat import creature_entry_from_guid


class ObjectAwareness:
    def __init__(self, *, read, capture, driver, log=lambda _:None,
                 sleep=real_sleep, now=monotonic):
        self.read=read
        self.capture=capture
        self.driver=driver
        self.log=log
        self.sleep=sleep
        self.now=now
        self.names={}
        self.classified={}
        self.owner=None

    def _guard(self,state,owner):
        if (state.truncated or state.player.dead or state.player.guid!=owner or
                self.driver.emergency_stop_requested() or not self.driver.wow_is_foreground()):
            raise LiveControlError('Creature awareness interrupted by player identity or input focus')

    def _fresh(self,state):
        owner=state.player.guid
        for _ in range(12):
            self.sleep(.10)
            newer=self.capture()
            self._guard(newer,owner)
            if newer.sequence!=state.sequence:
                return newer
        raise LiveControlError('Creature awareness telemetry stalled')

    def _observe(self,state,rows):
        from wotlkbuddy.horde.encounter import Enemy
        guid=state.player.target_guid
        mob=rows.get(guid)
        t=state.combat_target
        if not mob or not t or t.entry_id!=mob.entry or creature_entry_from_guid(guid)!=mob.entry:
            return
        if state.player.target_name:
            self.names[mob.entry]=state.player.target_name
        if t.reaction is None:
            return
        enemy=None
        if t.attackable and not t.dead and t.health>0 and 1<=t.reaction<=3:
            enemy=Enemy(guid,mob.entry,state.player.target_name,mob.position,
                        t.level,t.classification,t.health)
        self.classified[guid]=(self.now(),mob.entry,mob.faction,enemy)

    def acquire(self,state,guid,entry):
        """Acquire a known object; Tab is bounded fallback, never discovery."""
        self._guard(state,state.player.guid)
        if state.player.in_combat or state.player.target_guid==guid:
            return state
        name=self.names.get(entry)
        select=getattr(self.driver,'target_objective_name',None)
        if name and callable(select) and select(name):
            state=self._fresh(state)
            if state.player.in_combat or state.player.target_guid==guid:
                return state
        for _ in range(16):
            self.driver.chat('/targetenemy')
            state=self._fresh(state)
            if state.player.in_combat or state.player.target_guid==guid:
                return state
        return state

    def survey(self,state,position,relevant=lambda _:True):
        """Return spatially discovered rows, confirmed red enemies and unknowns."""
        self._guard(state,state.player.guid)
        owner=(state.player.guid,position.map_id)
        if owner!=self.owner:
            self.owner=owner
            self.classified.clear()
        stamp=self.now()
        self.classified={g:v for g,v in self.classified.items() if stamp-v[0]<=12}
        rows={m.guid:m for m in self.read(state,position.position)
              if m.entry>0 and relevant(m)}
        self._observe(state,rows)
        def pending():
            return {g for g,m in rows.items()
                    if g not in self.classified or self.classified[g][1:3]!=(m.entry,m.faction)}
        # Use profile and previously verified names first. A name is a hint,
        # never proof that the selected same-name spawn is the intended GUID.
        select=getattr(self.driver,'target_objective_name',None)
        attempted=set()
        for guid in sorted(pending(),key=lambda g:rows[g].position.distance_to(position.position)):
            if state.player.in_combat or len(attempted)>=6:
                break
            name=self.names.get(rows[guid].entry)
            if not name or name in attempted or not callable(select):
                continue
            attempted.add(name)
            if select(name):
                state=self._fresh(state)
                self._observe(state,rows)
        # Classify unresolved objects with ordinary input. Do not terminate
        # discovery on a repeated Tab target; the spatial census is independent.
        for _ in range(16):
            if not pending() or state.player.in_combat:
                break
            self.driver.chat('/targetenemy')
            state=self._fresh(state)
            self._observe(state,rows)
        unknown=tuple(rows[g] for g in pending())
        enemies=[]
        for guid,mob in rows.items():
            cached=self.classified.get(guid)
            if cached and cached[1:3]==(mob.entry,mob.faction) and cached[3] is not None:
                enemy=cached[3]
                enemies.append(replace(enemy,position=mob.position,
                                       health=mob.health if mob.health is not None else enemy.health))
        if rows:
            self.log(f'Object awareness: {len(rows)} nearby objects, {len(enemies)} confirmed hostile, '
                     f'{len(unknown)} unclassified')
        return state,tuple(enemies),unknown

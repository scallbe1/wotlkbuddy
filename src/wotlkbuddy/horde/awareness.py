"""Spatial discovery without repeated target cycling.

Template observations are planning hints, not attack authorization. Every
actual pull still requires an exact GUID and current client hostility/range.
"""
from dataclasses import replace
from time import monotonic, sleep as real_sleep

from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.horde.combat import creature_entry_from_guid
from wotlkbuddy.live.world_position import passive_creature, HOSTILE_PLAYER_FACTIONS


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
        self.templates={}
        self.probed={}
        self.last_tab=float('-inf')
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
        stamp=self.now()
        self.classified[guid]=(stamp,mob.entry,mob.faction,enemy)
        # A matching entry AND known faction may indicate a potential add.
        # Never copy a friendly classification onto an unverified GUID.
        if mob.faction is not None:
            key=(mob.entry,mob.faction)
            if enemy is not None:
                self.templates[key]=(stamp,enemy)
            else:
                self.templates.pop(key,None)

    def acquire(self,state,guid,entry):
        """Try exact name, then at most one globally throttled Tab for this pull."""
        self._guard(state,state.player.guid)
        if state.player.in_combat or state.player.target_guid==guid:
            return state
        name=self.names.get(entry)
        select=getattr(self.driver,'target_objective_name',None)
        if name and callable(select) and select(name):
            state=self._fresh(state)
            if state.player.in_combat or state.player.target_guid==guid:
                return state
        stamp=self.now()
        if stamp-self.last_tab>=12:
            self.last_tab=stamp
            self.log(f'Target acquisition fallback: one Tab for {entry} {guid}')
            self.driver.chat('/targetenemy')
            state=self._fresh(state)
        return state

    def survey(self,state,position,relevant=lambda _:True,*,probe=True):
        """Map nearby creatures; no Tab input, even if classification is missing."""
        self._guard(state,state.player.guid)
        owner=(state.player.guid,position.map_id)
        if owner!=self.owner:
            self.owner=owner
            self.classified.clear()
            self.templates.clear()
            self.probed.clear()
        stamp=self.now()
        self.classified={g:v for g,v in self.classified.items() if stamp-v[0]<=60}
        self.templates={k:v for k,v in self.templates.items() if stamp-v[0]<=60}
        rows={m.guid:m for m in self.read(state,position.position)
              if m.entry>0 and relevant(m)}
        self._observe(state,rows)

        def exact(m):
            cached=self.classified.get(m.guid)
            return cached if cached and cached[1:3]==(m.entry,m.faction) else None

        def hint(m):
            return self.templates.get((m.entry,m.faction)) if m.faction is not None else None

        # One deliberate named probe at most, and never disturb a selected
        # hostile that is being assessed for a pull.
        select=getattr(self.driver,'target_objective_name',None)
        current=state.combat_target
        preserve=bool(state.player.target_guid and current and current.attackable and
                      not current.dead and current.reaction is not None and
                      1<=current.reaction<=3)
        if probe and not preserve and not state.player.in_combat and callable(select):
            for mob in sorted(rows.values(),key=lambda m:m.position.distance_to(position.position)):
                name=self.names.get(mob.entry)
                if (exact(mob) or hint(mob) or passive_creature(mob) or not name or
                        stamp-self.probed.get(mob.entry,float('-inf'))<20):
                    continue
                self.probed[mob.entry]=stamp
                if select(name):
                    state=self._fresh(state)
                    self._observe(state,rows)
                break

        enemies=[]
        unknown=[]
        inferred=0
        passive=0
        from wotlkbuddy.horde.encounter import Enemy
        for mob in rows.values():
            cached=exact(mob)
            if cached is not None:
                enemy=cached[3]
            elif passive_creature(mob):
                passive+=1
                continue
            else:
                template=hint(mob)
                if template is not None:
                    enemy=template[1]
                elif mob.faction in HOSTILE_PLAYER_FACTIONS and mob.health is not None and mob.health>0:
                    # Live descriptor faction gives a planning hint without
                    # targeting. Do not cast until Lua confirms exact hostility.
                    enemy=Enemy(mob.guid,mob.entry,self.names.get(mob.entry,''),
                                mob.position,mob.level,'unknown',mob.health)
                else:
                    unknown.append(mob)
                    continue
                inferred+=1
            if enemy is not None:
                enemies.append(replace(enemy,guid=mob.guid,position=mob.position,
                                       health=mob.health if mob.health is not None else enemy.health,
                                       level=mob.level if mob.level is not None else enemy.level))
        if rows:
            self.log(f'Object awareness: {len(rows)} nearby objects, {len(enemies)-inferred} confirmed hostile, '
                     f'{inferred} potential faction/template hostile, {passive} passive ignored, '
                     f'{len(unknown)} unclassified; 0 scan Tabs')
        return state,tuple(enemies),tuple(unknown)

"""Active ghost survey using ordinary turning/targeting and Lua reaction telemetry."""
from math import pi
from time import monotonic, sleep as real_sleep

from wotlkbuddy.live.control import LiveControlError, _MOVEMENT_SCANCODES
from wotlkbuddy.live.world_travel import _angle
from wotlkbuddy.live.world_position import parse_unit_guid
from wotlkbuddy.horde.combat import creature_entry_from_guid


class GhostScout:
    def __init__(self, *, sample, driver, log=lambda _:None,
                 sleep=real_sleep, now=monotonic):
        self.sample=sample
        self.driver=driver
        self.log=log
        self.sleep=sleep
        self.now=now
        self.reactions={}

    def ignored_guids(self):
        # Exact GUID only, never assume other creatures of the same name or
        # entry have the same reaction. Unclassified units remain hazards.
        return frozenset(g for g,(reaction,seen) in self.reactions.items()
                         if reaction>=4 and self.now()-seen<=90)

    def survey(self):
        state, origin=self.sample()
        guid=state.player.guid
        def check():
            state,pos=self.sample()
            if (state.truncated or not state.player.dead or not state.death_state
                    or not state.death_state.ghost or state.player.guid!=guid
                    or pos.map_id!=origin.map_id or pos.position.distance_to(origin.position)>1.8
                    or self.driver.emergency_stop_requested()
                    or not self.driver.wow_is_foreground()):
                raise LiveControlError('Ghost survey interrupted or displaced')
            return state,pos

        def pulse(duration):
            check()
            key=_MOVEMENT_SCANCODES['turn-right']
            self.driver.keyboard.key_down(key)
            try:
                self.sleep(duration)
            finally:
                self.driver.keyboard.key_up(key)
            return check()

        def observe_target(previous):
            for _ in range(10):
                self.sleep(.08)
                state,_=check()
                if state.sequence!=previous.sequence:
                    return state
            raise LiveControlError('Ghost survey target telemetry stalled')

        # Calibrate measured heading, then turn through six sectors.
        _,pos=pulse(.12)
        delta=abs(_angle(pos.facing_rad-origin.facing_rad))
        if not .06<=delta<=1.3:
            self.log('Ghost turn could not be verified; retaining conservative position map')
            return
        rate=delta/.12
        observed=set()
        for sector in range(6):
            state,_=check()
            self.driver.chat('/cleartarget')
            state=observe_target(state)
            seen=set()
            for _ in range(6):
                self.driver.chat('/targetenemy')
                state=observe_target(state)
                target=state.combat_target
                tg=state.player.target_guid
                if not tg or tg in seen:
                    break
                seen.add(tg)
                if (not target or target.entry_id<=0 or
                        creature_entry_from_guid(tg)!=target.entry_id or
                        target.reaction is None or not 1<=target.reaction<=8):
                    continue
                unit=parse_unit_guid(tg)
                observed.add(unit)
                # Dead units aren't classified as neutral: descriptor health
                # handles corpses and their future respawn independently.
                if not target.dead:
                    self.reactions[unit]=(target.reaction,self.now())
            if sector<5:
                pulse(min(.85,pi/3/rate))
        state,_=check()
        if state.player.target_guid:
            self.driver.chat('/cleartarget')
            observe_target(state)
        self.log(f'Active ghost survey: six directions, {len(observed)} unique classified units, '
                 f'{len(self.ignored_guids())} confirmed neutral/friendly excluded; unknown units retained')

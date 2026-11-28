"""Session-level bag, equipment, vendor and encounter-loot coordination."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from wotlkbuddy.horde.bags import equip_bag_upgrades
from wotlkbuddy.horde.gear import equip_mage_upgrades
from wotlkbuddy.horde.merchant import free_bag_space
from wotlkbuddy.horde.encounter_loot import loot_encounter


@dataclass
class SessionInventory:
    report: dict
    vendor_sales: list[dict] = field(default_factory=list)

    def record_sale(self, sale):
        if sale is None:
            return None
        result = asdict(sale)
        self.vendor_sales.append(result)
        self.report['vendor_sales'] = self.vendor_sales
        return result

    def reconcile_upgrades(self, mode, *, cap, driver, log):
        """Equip newly looted bags before evaluating inventory pressure."""
        bag_upgrades = equip_bag_upgrades(capture=cap, driver=driver, log=log)
        if bag_upgrades:
            self.report['equipped_bag_ids'] = bag_upgrades
        if mode in ('objectives', 'collect', 'objects', 'turnin', 'vendor', 'train', 'quests') and cap().gear_scan_complete:
            upgraded = equip_mage_upgrades(capture=cap, driver=driver, log=log)
            if upgraded:
                self.report['equipped_item_ids'] = upgraded

    def ensure_reward_space(self, *, quest_profile, profiles_root, pid, cap,
                            driver, nav, db, location, hud, set_native_status, log, force_sale=False):
        """Reserve bag space using the existing verified merchant policy."""
        state, _ = location()
        minimum = max(2, quest_profile.min_free_bag_slots + 1)
        if not force_sale and state.resources.free_bag_slots >= minimum:
            return None
        hud.set(goal='Free bag space for quest rewards',
                action='Finding safe non-quest items to sell')
        set_native_status('vendor')
        sold = free_bag_space(
            profile=quest_profile, profiles_root=profiles_root,
            pid=pid, capture=cap, driver=driver, nav=nav, spawns=db,
            min_free_slots=minimum, force_sale=force_sale, log=log)
        result=self.record_sale(sold)
        if sold is not None:
            self.discover_item_quests(cap=cap,driver=driver,profile=quest_profile,log=log)
        return result


    def discover_item_quests(self, *, cap, driver, profile, log):
        from wotlkbuddy.horde.item_quests import accept_item_quests
        accepted=accept_item_quests(capture=cap,driver=driver,profile=profile,log=log)
        if accepted:
            self.report['item_accepted_quests']=tuple(dict.fromkeys(
                (*self.report.get('item_accepted_quests',()),*accepted)))

    def prepare_services(self, args, *, quest_profile, profiles_root, pid, cap,
                         driver, nav, db, location, hud, set_native_status, log):
        """Handle bag pressure and repairs before quest or trainer travel."""
        self.discover_item_quests(cap=cap,driver=driver,profile=quest_profile,log=log)
        if args.only in ('all', 'vendor', 'objectives', 'collect', 'objects', 'turnin'):
            self.ensure_reward_space(
                quest_profile=quest_profile, profiles_root=profiles_root,
                pid=pid, cap=cap, driver=driver, nav=nav, db=db,
                location=location, hud=hud, set_native_status=set_native_status,
                log=log, force_sale=args.only=='vendor')
            if args.only=='vendor':
                self.report['vendor']={'status':'bag space verified',
                                       'free_slots':cap().resources.free_bag_slots}
        from wotlkbuddy.horde.repair import maintain_equipment
        result=maintain_equipment(
            profile=quest_profile, profiles_root=profiles_root, location=location,
            cap=cap, driver=driver, pid=pid, nav=nav, db=db, hud=hud,
            set_native_status=set_native_status, log=log,
            memory=getattr(args,'_repair_route_memory',None))
        if result is not None:
            self.report['equipment_repair']=result


def bind_encounter_loot(*, cap, location, driver, nav, pid, log):
    """Attach verified corpse looting and post-loot bag upgrades to this driver."""
    from wotlkbuddy.live.world_position import observe_target_position
    from wotlkbuddy.game.win32 import ProcessMemory

    def corpse_position(state, corpse_guid):
        with ProcessMemory(pid, writable=False) as memory:
            return observe_target_position(
                memory, player_guid=state.player.guid, target_guid=corpse_guid)

    def drain_and_equip(state):
        if getattr(getattr(driver,'hunt_terrain',None),'entrance',None) is not None and getattr(driver,'active_quest_entries',()) == (driver.hunt_terrain.entrance.entry,):
            return loot_nearby(state)
        state = loot_encounter(initial=state, sample=location, driver=driver, nav=nav,
                               corpse_position=corpse_position, log=log)
        if not state.player.in_combat and state.bag_candidates:
            equip_bag_upgrades(capture=cap, driver=driver, log=log)
            state = cap()
        return state

    def loot_nearby(state):
        return loot_encounter(initial=state, sample=location, driver=driver, nav=nav,
                              corpse_position=corpse_position, log=log, nearby_only=True)

    driver.loot_nearby_corpses = loot_nearby
    driver.drain_encounter_loot = drain_and_equip

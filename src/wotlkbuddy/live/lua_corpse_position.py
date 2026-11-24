"""Resolve Lua's normalized corpse marker to a reachable world destination.

Map spans (yards) are from the WotLK Gatherer Astrolabe map-size data:
https://github.com/locus313/WoW-3.3.5a-Addons/blob/main/Gatherer/Astrolabe/Astrolabe.lua

3.3.5 CGWorldMap::GetWorldPosition maps world Y to map X and world X
to map Y, both with negative scale:
https://github.com/Kaev/335decomp/blob/main/WoW/Source/Ui/WorldMap.cpp

The live player's existing verified world position anchors the transform.
Lua cannot provide corpse Z. Query reachable navmesh surfaces without
reading corpse memory, guessing an elevation, or accessing the server.
"""
from math import hypot, isfinite

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.world_travel import validate_nav_path


# map texture token: (world map ID, horizontal span, vertical span)
ZONE_SPANS = {
    'Ashenvale': (1, 5766.729, 3843.722),
    'Aszhara': (1, 5070.887, 3381.226),
    'AzuremystIsle': (530, 4070.877, 2714.564),
    'Barrens': (1, 10133.442, 6756.202),
    'BloodmystIsle': (530, 3262.536, 2174.984),
    'Darkshore': (1, 6550.071, 4366.635),
    'Darnassis': (1, 1058.344, 705.724),
    'Desolace': (1, 4495.883, 2997.895),
    'Durotar': (1, 5287.556, 3524.975),
    'Dustwallow': (1, 5250.057, 3499.975),
    'Felwood': (1, 5750.063, 3833.306),
    'Feralas': (1, 6950.075, 4633.300),
    'Moonglade': (1, 2308.360, 1539.572),
    'Mulgore': (1, 5137.556, 3424.976),
    'Ogrimmar': (1, 1402.619, 935.410),
    'Silithus': (1, 3483.372, 2322.901),
    'StonetalonMountains': (1, 4883.386, 3256.227),
    'Tanaris': (1, 6900.075, 4599.967),
    'Teldrassil': (1, 5091.720, 3393.726),
    'TheExodar': (530, 1056.783, 704.683),
    'ThousandNeedles': (1, 4400.047, 2933.312),
    'ThunderBluff': (1, 1043.761, 695.829),
    'UngoroCrater': (1, 3700.040, 2466.649),
    'Winterspring': (1, 7100.077, 4733.299),
    'Alterac': (0, 2799.999, 1866.674),
    'Arathi': (0, 3600.000, 2400.009),
    'Badlands': (0, 2487.501, 1658.340),
    'BlastedLands': (0, 3349.999, 2233.342),
    'BurningSteppes': (0, 2929.167, 1952.091),
    'DeadwindPass': (0, 2499.999, 1666.674),
    'DunMorogh': (0, 4925.001, 3283.346),
    'Duskwood': (0, 2699.999, 1800.007),
    'EasternPlaguelands': (0, 4031.249, 2687.510),
    'Elwynn': (0, 3470.833, 2314.592),
    'EversongWoods': (530, 4925.003, 3283.346),
    'Ghostlands': (530, 3300.002, 2200.009),
    'Hilsbrad': (0, 3199.999, 2133.342),
    'Hinterlands': (0, 3849.999, 2566.677),
    'Ironforge': (0, 790.625, 527.607),
    'LochModan': (0, 2758.333, 1839.589),
    'Redridge': (0, 2170.833, 1447.922),
    'SearingGorge': (0, 2231.250, 1487.505),
    'SilvermoonCity': (530, 1211.459, 806.774),
    'Silverpine': (0, 4199.999, 2800.011),
    'Stormwind': (0, 1737.501, 1158.338),
    'Stranglethorn': (0, 6381.248, 4254.183),
    'Sunwell': (530, 3327.081, 2218.758),
    'SwampOfSorrows': (0, 2293.751, 1529.174),
    'Tirisfal': (0, 4518.748, 3012.512),
    'Undercity': (0, 959.375, 640.107),
    'WesternPlaguelands': (0, 4300.000, 2866.678),
    'Westfall': (0, 3500.000, 2333.343),
    'Wetlands': (0, 4135.416, 2756.261),
    'BladesEdgeMountains': (530, 5424.971, 3616.554),
    'Hellfire': (530, 5164.556, 3443.642),
    'Nagrand': (530, 5524.971, 3683.218),
    'Netherstorm': (530, 5574.971, 3716.551),
    'ShadowmoonValley': (530, 5499.971, 3666.552),
    'ShattrathCity': (530, 1306.243, 870.806),
    'TerokkarForest': (530, 5399.972, 3599.888),
    'Zangarmarsh': (530, 5027.057, 3351.979),
    'BoreanTundra': (571, 5764.582, 3843.765),
    'CrystalsongForest': (571, 2722.917, 1814.590),
    'Dalaran': (571, 830.015, 553.342),
    'Dragonblight': (571, 5608.332, 3739.598),
    'GrizzlyHills': (571, 5249.999, 3500.014),
    'HrothgarsLanding': (571, 3677.083, 2452.094),
    'HowlingFjord': (571, 6045.832, 4031.265),
    'IcecrownGlacier': (571, 6270.833, 4181.267),
    'LakeWintergrasp': (571, 2974.999, 1983.341),
    'SholazarBasin': (571, 4356.250, 2904.178),
    'TheStormPeaks': (571, 7112.498, 4741.685),
    'ZulDrak': (571, 4993.749, 3329.180),
}


def resolve_lua_corpse(state, position, *, nav):
    death = state.death_state
    if (state.truncated or not state.player.dead or death is None or not death.ghost or
            death.corpse_map_name is None or death.corpse_map_x is None or
            death.corpse_map_y is None or
            position.player_guid.lower() != state.player.guid.lower()):
        raise LiveControlError('Lua corpse marker unavailable; close world map, '
                               'install updated addon and /reload')
    zone = ZONE_SPANS.get(death.corpse_map_name)
    if zone is None or zone[0] != position.map_id:
        raise LiveControlError('Unsupported corpse zone or mismatched world map: '
                               + death.corpse_map_name)
    map_id, width, height = zone
    px, py = state.player.map_x, state.player.map_y
    cx, cy = death.corpse_map_x, death.corpse_map_y
    if (not all(isfinite(v) and 0 <= v <= 1 for v in (px, py, cx, cy)) or
            (px == py == 0) or (cx == cy == 0)):
        raise LiveControlError('Invalid Lua player/corpse map marker')
    x = position.x - (cy - py) * height
    y = position.y - (cx - px) * width
    if hypot(x-position.x, y-position.y) > 800:
        raise LiveControlError('Corpse marker exceeds bounded recovery route')
    # Query at multiple heights around the current ghost's elevation;
    # collapse duplicate polygons and reject genuinely ambiguous floors.
    candidates = []
    for dz in (0, -10, 10, -20, 20, -30, 30, -40, 40, -50, 50, -60, 60, -80, 80):
        point = nav.nearest_point(map_id, Vec3(x, y, position.z + dz))
        if (point is None or not all(isfinite(v) for v in (point.x, point.y, point.z))
                or hypot(point.x-x, point.y-y) > 3
                or any(abs(point.z-p.z) < 4 for p in candidates)):
            continue
        if position.position.distance_to(point) > 2:
            path = nav.find_path(map_id, position.position, point)
            try:
                validate_nav_path(path, position.position, point, max_step=9)
            except LiveControlError:
                continue
        candidates.append(point)
    if not candidates:
        raise LiveControlError('No reachable navmesh surface at Lua corpse marker')
    if len(candidates) > 1:
        raise LiveControlError('Lua corpse marker has multiple reachable elevations; '
                               'corpse floor is ambiguous')
    point = candidates[0]
    return (map_id, point.x, point.y, point.z)

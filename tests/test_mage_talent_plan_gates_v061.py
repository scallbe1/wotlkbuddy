"""Executability of DPS_TALENT_ORDERS under 3.3.5a talent tier gates.

The actuator walks one order strictly in sequence and never skips an entry,
so a rank whose tree-point gate or per-row minimum level is unreachable is a
permanent deadlock, not a delay. A row requires 5 * (row - 1) points spent
in that tree and its first rank is learnable at character level 5 * row + 5
(WotLK ranks/rows cross-checked against the AzerothPrime WotLK calculator).
Each ordered pair is a cumulative target rank, not an additional point count.
"""
import unittest

from wotlkbuddy.combat.character import DPS_TALENT_ORDERS

# name: (tree 4=Arcane 6=Frost, row, max_rank).
# https://azerothprime.com/en/wotlk/talent-calculator/mage
FROST_ARCANE_TALENTS = {
    'Precision': (6, 2, 3),
    'Improved Frostbolt': (6, 1, 5),
    'Frostbite': (6, 1, 3),
    'Ice Shards': (6, 2, 3),
    'Piercing Ice': (6, 3, 3),
    'Icy Veins': (6, 3, 1),
    'Arctic Reach': (6, 4, 2),
    'Frost Channeling': (6, 4, 3),
    'Shatter': (6, 4, 3),
    'Cold Snap': (6, 5, 1),
    'Improved Cone of Cold': (6, 5, 3),
    'Cold as Ice': (6, 6, 2),
    "Winter's Chill": (6, 6, 3),
    'Ice Barrier': (6, 7, 1),
    'Shattered Barrier': (6, 7, 2),
    'Empowered Frostbolt': (6, 8, 2),
    'Fingers of Frost': (6, 8, 2),
    'Summon Water Elemental': (6, 9, 1),
    'Enduring Winter': (6, 9, 3),
    'Brain Freeze': (6, 9, 3),
    'Chilled to the Bone': (6, 10, 5),
    'Deep Freeze': (6, 11, 1),
    'Permafrost': (6, 2, 3),
    'Arctic Winds': (6, 7, 5),
    'Ice Floes': (6, 1, 3),
    'Frost Warding': (6, 2, 2),
    'Arcane Subtlety': (4, 1, 2),
    'Arcane Focus': (4, 1, 3),
    'Arcane Concentration': (4, 2, 5),
}


def plan_completion(order, fixtures):
    """Earliest level a sequential order fully spends, or the first stall.

    Returns (True, finished_level) when every rank is learnable in order,
    otherwise (False, first_blocked_talent_name).
    """
    spent = {}
    learned = {}
    level = 9
    for name, ranks in order:
        if name not in fixtures:
            raise AssertionError(f'{name} has no verified tier fixture')
        tree, row, max_rank = fixtures[name]
        if not learned.get(name, 0) < ranks <= max_rank:
            raise AssertionError(f'{name} exceeds verified max rank {max_rank}')
        if spent.get(tree, 0) < 5 * (row - 1):
            return False, name
        for _ in range(ranks - learned.get(name, 0)):
            level = max(level + 1, 5 * row + 5)
            spent[tree] = spent.get(tree, 0) + 1
        learned[name] = ranks
    return True, level


class MageTalentPlanGateTests(unittest.TestCase):
    def test_verified_fixtures_cover_the_mage_order(self):
        self.assertTrue(all(
            name in FROST_ARCANE_TALENTS
            for name, _ in DPS_TALENT_ORDERS['MAGE']))

    def test_current_mage_order_completes_while_leveling(self):
        done, finished_level = plan_completion(
            DPS_TALENT_ORDERS['MAGE'], FROST_ARCANE_TALENTS)
        self.assertTrue(done, f'mage order deadlocks at {finished_level!r}')
        # The full Frost path finishes inside 60s; the rest is Northrend.
        self.assertIsInstance(finished_level, int)
        self.assertEqual(finished_level, 80)

    def test_row_gates_hold_at_every_rank(self):
        spent = {}
        learned = {}
        level = 9
        for name, ranks in DPS_TALENT_ORDERS['MAGE']:
            tree, row, maximum = FROST_ARCANE_TALENTS[name]
            self.assertLessEqual(ranks, maximum)
            self.assertGreater(ranks, learned.get(name, 0))
            for _ in range(ranks - learned.get(name, 0)):
                self.assertGreaterEqual(
                    spent.get(tree, 0), 5 * (row - 1),
                    f'{name} rank spent below its row gate')
                self.assertGreaterEqual(
                    level + 1, 5 * row + 5,
                    f'{name} rank scheduled below its row min level')
                level += 1
                spent[tree] = spent.get(tree, 0) + 1
            learned[name] = ranks
        self.assertEqual(level, 80)
        self.assertEqual(spent, {4: 10, 6: 61})

    def test_arcane_concentration_requires_arcane_points(self):
        # Frost investment cannot unlock Arcane row 2.
        broken = (('Improved Frostbolt', 5), ('Arcane Concentration', 5))
        self.assertEqual(
            plan_completion(broken, FROST_ARCANE_TALENTS),
            (False, 'Arcane Concentration'))

    def test_precision_is_frost_and_accepts_cumulative_targets(self):
        order = (('Improved Frostbolt', 5), ('Ice Shards', 3),
                 ('Precision', 2), ('Icy Veins', 1), ('Precision', 3))
        self.assertEqual(plan_completion(order, FROST_ARCANE_TALENTS),
                         (True, 21))

    def test_rank_cap_rejects_invalid_ice_shards(self):
        with self.assertRaises(AssertionError):
            plan_completion((('Improved Frostbolt', 5), ('Ice Shards', 5)),
                            FROST_ARCANE_TALENTS)

    def test_arctic_reach_needs_frostbite_to_unlock_row_4(self):
        # This order has only 12 Frost points before row 4, which needs 15.
        without_filler = (
            ('Improved Frostbolt', 5), ('Ice Shards', 3), ('Frostbite', 2),
            ('Icy Veins', 1), ('Piercing Ice', 1), ('Arctic Reach', 2),
        )
        self.assertEqual(
            plan_completion(without_filler, FROST_ARCANE_TALENTS),
            (False, 'Arctic Reach'))


if __name__ == '__main__':
    unittest.main()

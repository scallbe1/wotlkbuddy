"""Executability of DPS_TALENT_ORDERS under 3.3.5a talent tier gates.

The actuator walks one order strictly in sequence and never skips an entry,
so a rank whose tree-point gate or per-row minimum level is unreachable is a
permanent deadlock, not a delay. A row requires 5 * (row - 1) points spent
in that tree and its first rank is learnable at character level 5 * row + 5
(TALETNT rows verified against wotlk.evowow.com ?spells=-2.8.6 and -2.8.4).
"""
import unittest

from wotlkbuddy.combat.character import DPS_TALENT_ORDERS

# name: (tree 4=Arcane 6=Frost, row, max_rank) — verified 2026-11-26.
FROST_ARCANE_TALENTS = {
    'Precision': (4, 2, 3),
    'Improved Frostbolt': (6, 1, 5),
    'Frostbite': (6, 1, 3),
    'Ice Shards': (6, 2, 5),
    'Piercing Ice': (6, 3, 3),
    'Icy Veins': (6, 3, 1),
    'Arctic Reach': (6, 4, 2),
    'Frost Channeling': (6, 4, 3),
    'Shatter': (6, 4, 3),
    'Cold Snap': (6, 5, 1),
    'Improved Cone of Cold': (6, 5, 3),
    'Cold as Ice': (6, 6, 2),
    "Winter's Chill": (6, 6, 5),
    'Ice Barrier': (6, 7, 1),
    'Shattered Barrier': (6, 7, 3),
    'Empowered Frostbolt': (6, 8, 2),
    'Fingers of Frost': (6, 8, 3),
    'Summon Water Elemental': (6, 9, 1),
    'Enduring Winter': (6, 9, 3),
    'Brain Freeze': (6, 9, 3),
    'Chilled to the Bone': (6, 10, 5),
    'Deep Freeze': (6, 11, 1),
}


def plan_completion(order, fixtures):
    """Earliest level a sequential order fully spends, or the first stall.

    Returns (True, finished_level) when every rank is learnable in order,
    otherwise (False, first_blocked_talent_name).
    """
    spent = {}
    level = 9
    for name, ranks in order:
        if name not in fixtures:
            raise AssertionError(f'{name} has no verified tier fixture')
        tree, row, max_rank = fixtures[name]
        if ranks > max_rank:
            raise AssertionError(f'{name} exceeds verified max rank {max_rank}')
        if spent.get(tree, 0) < 5 * (row - 1):
            return False, name
        for _ in range(ranks):
            level = max(level + 1, 5 * row + 5)
            spent[tree] = spent.get(tree, 0) + 1
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
        self.assertLessEqual(finished_level, 80)

    def test_row_gates_hold_at_every_rank(self):
        spent = {}
        level = 9
        for name, ranks in DPS_TALENT_ORDERS['MAGE']:
            tree, row, _ = FROST_ARCANE_TALENTS[name]
            for _ in range(ranks):
                self.assertGreaterEqual(
                    spent.get(tree, 0), 5 * (row - 1),
                    f'{name} rank spent below its row gate')
                self.assertGreaterEqual(
                    level + 1, 5 * row + 5,
                    f'{name} rank scheduled below its row min level')
                level += 1
                spent[tree] = spent.get(tree, 0) + 1

    def test_pre_fix_order_deadlocked_at_precision(self):
        # Precision is Arcane row 2: it needs 5 points in Arcane, which the
        # old plan never spent, so the strict actuator stalled there forever.
        broken = (
            ('Improved Frostbolt', 5), ('Ice Shards', 3), ('Precision', 3),
            ('Piercing Ice', 3), ('Icy Veins', 1), ('Arctic Reach', 2),
            ('Frost Channeling', 3), ('Shatter', 3),
        )
        self.assertEqual(
            plan_completion(broken, FROST_ARCANE_TALENTS), (False, 'Precision'))

    def test_arctic_reach_needs_frostbite_to_unlock_row_4(self):
        # Rows 1-3 useful ranks sum to 14 Frost points; row 4 needs 15 spent,
        # so an order without any row 1-3 filler deadlocks at Arctic Reach.
        without_filler = (
            ('Improved Frostbolt', 5), ('Ice Shards', 5),
            ('Piercing Ice', 3), ('Icy Veins', 1), ('Arctic Reach', 2),
        )
        self.assertEqual(
            plan_completion(without_filler, FROST_ARCANE_TALENTS),
            (False, 'Arctic Reach'))


if __name__ == '__main__':
    unittest.main()

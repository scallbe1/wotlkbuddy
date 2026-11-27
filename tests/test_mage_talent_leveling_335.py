"""Validate every level of the Mage build against WotLK 3.3.5a talent gates."""
from collections import Counter
from types import SimpleNamespace as NS

from wotlkbuddy.combat.character import DPS_TALENT_ORDERS, progress_dps_talents
from wotlkbuddy.live.codec import TalentProbe

# Independent tree metadata: tree, tier (zero-based), maximum rank, prerequisite.
# https://azerothprime.com/en/wotlk/talent-calculator/mage
# https://wowcalc.net/en/wotlk/talents/mage
FROST = {
    'Improved Frostbolt': (0, 5, None),
    'Frostbite': (0, 3, None),
    'Ice Floes': (0, 3, None),
    'Ice Shards': (1, 3, None),
    'Precision': (1, 3, None),
    'Permafrost': (1, 3, None),
    'Frost Warding': (1, 2, None),
    'Icy Veins': (2, 1, None),
    'Piercing Ice': (2, 3, None),
    'Arctic Reach': (3, 2, None),
    'Frost Channeling': (3, 3, None),
    'Shatter': (3, 3, None),
    'Cold Snap': (4, 1, None),
    'Cold as Ice': (5, 2, 'Cold Snap'),
    'Ice Barrier': (6, 1, 'Cold Snap'),
    'Arctic Winds': (6, 5, None),
    'Shattered Barrier': (6, 2, 'Ice Barrier'),
    'Empowered Frostbolt': (7, 2, None),
    'Fingers of Frost': (7, 2, None),
    'Summon Water Elemental': (8, 1, None),
    'Enduring Winter': (8, 3, 'Summon Water Elemental'),
    'Brain Freeze': (8, 3, None),
    'Chilled to the Bone': (9, 5, None),
    'Deep Freeze': (10, 1, None),
}
ARCANE = {
    'Arcane Subtlety': (0, 2, None),
    'Arcane Focus': (0, 3, None),
    'Arcane Concentration': (1, 5, None),
}
META = {name: (tree, tier, maximum, required)
        for tree, data in ((3, FROST), (1, ARCANE))
        for name, (tier, maximum, required) in data.items()}


class MageTalentSimulation:
    """Server-style rank, tier and prerequisite checks, not the planned order."""
    def __init__(self):
        self.ranks = Counter()
        self.request = ''
        self.points = 0
        self.sequence = 0
        self.level = 9
        self.indices = {name: index for index, name in enumerate(META, 1)}

    def wow_is_foreground(self):
        return True

    def emergency_stop_requested(self):
        return False

    def chat(self, command):
        assert command.startswith('/wbttalent ')
        self.request = command.removeprefix('/wbttalent ')
        assert self.request in META
        assert all(c.isascii() and (c.isalpha() or c in ' :') for c in self.request)

    def probe(self):
        if not self.request:
            return None
        tree, tier, maximum, required = META[self.request]
        earlier = sum(rank for name, rank in self.ranks.items()
                      if META[name][0] == tree and META[name][1] < tier)
        learnable = (self.points > 0 and self.ranks[self.request] < maximum
                     and earlier >= 5 * tier
                     and (required is None or self.ranks[required] == META[required][2]))
        return TalentProbe(tree, self.indices[self.request],
                           self.ranks[self.request], maximum, learnable, self.request)

    def capture(self):
        self.sequence += 1
        return NS(sequence=self.sequence, truncated=False,
                  player=NS(guid='mage', dead=False, in_combat=False, level=self.level),
                  resources=NS(class_token='MAGE', unspent_talent_points=self.points),
                  talent_probe=self.probe())

    def learn_verified_talent(self, tree, index):
        probe = self.probe()
        assert probe.learnable and (tree, index) == (probe.tree, probe.index)
        self.ranks[self.request] += 1
        self.points -= 1


def test_mage_can_spend_all_71_points_without_skipping_tiers_or_prerequisites():
    game = MageTalentSimulation()
    milestones = {20: 'Icy Veins', 30: 'Cold Snap', 40: 'Ice Barrier',
                  50: 'Summon Water Elemental', 60: 'Deep Freeze'}
    for level in range(10, 81):
        game.level = level
        game.points += 1
        result = progress_dps_talents(capture=game.capture, driver=game,
                                     sleep=lambda _: None, max_points=1)
        assert len(result) == 1, f'No talent learned at level {level}'
        assert game.points == 0
        if level in milestones:
            assert result == (milestones[level],)
    assert sum(game.ranks.values()) == 71
    assert sum(game.ranks[name] for name in FROST) == 61
    assert sum(game.ranks[name] for name in ARCANE) == 10
    assert game.ranks == dict(DPS_TALENT_ORDERS['MAGE'])

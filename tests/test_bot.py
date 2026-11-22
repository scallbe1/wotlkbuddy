import unittest

from wotlkbuddy.bots.grind import GrindBot
from wotlkbuddy.engine.behavior import Context, Status
from wotlkbuddy.game.simulated import SimulatedGame
from wotlkbuddy.game.types import Unit, Vec3


class GrindTests(unittest.TestCase):
    def test_grind_kills_loots_and_patrols(self):
        game = SimulatedGame()
        game.units[20] = Unit(20, Vec3(8, 0, 0), 30, hostile=True)
        bot = GrindBot(game, [Vec3(0, 0, 0), Vec3(25, 0, 0)])
        for _ in range(15):
            self.assertEqual(bot.tick(Context()), Status.RUNNING)
        self.assertIn("target:20", game.actions)
        self.assertIn("loot:20", game.actions)
        self.assertGreater(game.player.position.x, 8)

    def test_requires_hotspots(self):
        with self.assertRaises(ValueError):
            GrindBot(SimulatedGame(), [])

    def test_dead_player_resurrects(self):
        from dataclasses import replace
        game = SimulatedGame()
        game.player = replace(game.player, health=0, is_dead=True)
        GrindBot(game, [Vec3(0, 0, 0)]).tick(Context())
        self.assertEqual(game.player.health, 100)
        self.assertIn("resurrect", game.actions)


if __name__ == "__main__":
    unittest.main()

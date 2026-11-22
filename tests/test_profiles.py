import tempfile
import unittest
from pathlib import Path

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.profiles.honorbuddy import read_profile


class ProfileTests(unittest.TestCase):
    def test_parse_grind_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.xml"
            path.write_text('''<HBProfile Name="test" MinLevel="1" MaxLevel="60">
              <GrindArea><Hotspots><Hotspot X="1" Y="2" Z="3"/></Hotspots></GrindArea>
              <QuestOrder><PickUp QuestId="123" /></QuestOrder>
            </HBProfile>''', encoding="utf-8")
            p = read_profile(path)
            self.assertEqual(p.name, "test")
            self.assertEqual(p.maximum_level, 60)
            self.assertEqual(p.hotspots, (Vec3(1, 2, 3),))
            self.assertEqual(p.quest_actions, ("pickup",))

    def test_bad_hotspot_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.xml"
            path.write_text('<HBProfile><Hotspot X="a" Y="2" Z="3"/></HBProfile>')
            with self.assertRaises(ValueError):
                read_profile(path)

    def test_distance_and_step(self):
        self.assertEqual(Vec3(0, 0, 0).distance_to(Vec3(3, 4, 0)), 5)
        self.assertEqual(Vec3(0, 0, 0).step_towards(Vec3(10, 0, 0), 3), Vec3(3, 0, 0))


if __name__ == "__main__":
    unittest.main()

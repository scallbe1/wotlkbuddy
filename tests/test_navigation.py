import ctypes
import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.navigation.detour import XYZ


class NavTests(unittest.TestCase):
    def test_abi_xyz_has_expected_layout(self):
        self.assertEqual(ctypes.sizeof(XYZ), 12)
        self.assertEqual(XYZ.from_vec(Vec3(1, 2, 3)).as_vec(), Vec3(1, 2, 3))


if __name__ == "__main__":
    unittest.main()

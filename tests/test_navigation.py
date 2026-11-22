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


class NavmeshWaypointsTests(unittest.TestCase):
    def test_real_world_query_preloads_start_and_destination_tiles(self):
        from wotlkbuddy.navigation.detour import DetourNavigator
        class DLL:
            def __init__(self):
                self.calls=[]
                self.memory=(XYZ*3)(XYZ(100,200,20), XYZ(110,210,22), XYZ(125,230,25))
            def EnsureTiles_C(self,map_id,point,ring):
                self.calls.append(('tile',map_id,round(point.x,1),round(point.y,1),ring))
            def CalculatePath_C(self,map_id,start,end,smooth,length):
                ctypes.cast(length,ctypes.POINTER(ctypes.c_int))[0]=3
                return ctypes.cast(self.memory,ctypes.POINTER(XYZ))
            def FreePathArr_C(self,pointer):
                self.calls.append(('free',))
            def Nav_UnloadMaps(self):
                self.calls.append(('unload',))
        nav=object.__new__(DetourNavigator)
        nav._dll=DLL()
        nav._loaded=True
        path=nav.find_path(0,Vec3(100,200,20),Vec3(125,230,25))
        self.assertEqual(len(path),3)
        self.assertEqual(path[1],Vec3(110,210,22))
        self.assertEqual(nav._dll.calls[:2],[('tile',0,100,200,1),('tile',0,125,230,1)])
        self.assertEqual(nav._dll.calls[2],('free',))
        nav.close()
        nav.close()
        self.assertEqual(nav._dll.calls.count(('unload',)),1)

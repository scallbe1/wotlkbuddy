import unittest

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.navigation.worker import process_request


class FakeNav:
    def find_path(self, map_id, start, end, *, smooth=True):
        return [start, end]

    def nearest_point(self, map_id, point):
        return Vec3(point.x, point.y, 5)


class WorkerTests(unittest.TestCase):
    def test_path(self):
        response = process_request(FakeNav(), {
            "op": "path", "map_id": 0, "start": [1, 2, 3], "end": [4, 5, 6]
        })
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], [[1, 2, 3], [4, 5, 6]])

    def test_nearest(self):
        response = process_request(FakeNav(), {
            "op": "nearest", "map_id": 0, "point": [1, 2, 3]
        })
        self.assertEqual(response["result"], [1, 2, 5])

    def test_unknown_op(self):
        with self.assertRaises(ValueError):
            process_request(FakeNav(), {"op": "bad"})


if __name__ == "__main__":
    unittest.main()

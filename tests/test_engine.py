import unittest

from wotlkbuddy.engine.behavior import (
    Action, Condition, Context, Inverter, Selector, Sequence, Status,
)
from wotlkbuddy.engine.runner import BotRunner


class BehaviorTreeTests(unittest.TestCase):
    def test_sequence_remembers_running_child(self):
        calls = []
        def first(_):
            calls.append("first")
            return Status.SUCCESS

        runs = iter([Status.RUNNING, Status.SUCCESS])
        def second(_):
            calls.append("second")
            return next(runs)

        tree = Sequence([Action(first), Action(second)])
        self.assertEqual(tree.tick(Context()), Status.RUNNING)
        self.assertEqual(tree.tick(Context()), Status.SUCCESS)
        self.assertEqual(calls, ["first", "second", "second"])

    def test_selector_and_inverter(self):
        node = Selector([
            Condition(lambda _: False),
            Inverter(Condition(lambda _: False)),
        ])
        self.assertEqual(node.tick(Context()), Status.SUCCESS)

    def test_runner_requires_start(self):
        runner = BotRunner(Action(lambda _: Status.SUCCESS))
        with self.assertRaises(RuntimeError):
            runner.tick()
        runner.start()
        self.assertEqual(runner.tick(), Status.SUCCESS)
        runner.stop()
        self.assertEqual(runner.tick_count, 1)


if __name__ == "__main__":
    unittest.main()

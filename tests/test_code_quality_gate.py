"""Regression tests for the repository function-size ratchet."""
from pathlib import Path
import runpy

CHECKER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "tools" / "code_quality.py")
)
inventory = CHECKER["inventory"]
violations = CHECKER["violations"]
limit = CHECKER["LIMIT"]


def test_inventory_identifies_qualified_nested_and_async_functions():
    source = (
        "class Controller:\n"
        "    async def run(self):\n"
        "        def step():\n"
        "            return 1\n"
        "        return step()\n"
    )
    assert inventory("controller.py", source) == {
        "controller.py:Controller.run": 4,
        "controller.py:Controller.run.step": 2,
    }


def test_existing_debt_may_shrink_but_not_grow():
    assert not violations({"old": 200}, {"old": 199})
    assert not violations({"old": 200}, {"old": 200})
    assert violations({"old": 200}, {"old": 201})


def test_new_and_previously_small_functions_cannot_exceed_limit():
    assert not violations({}, {"new": limit})
    assert violations({}, {"new": limit + 1})
    assert violations({"old": 10}, {"old": limit + 1})


def test_duplicate_definition_cannot_hide_larger_span():
    source = "def f():\n    x = 1\n    return x\ndef f():\n    pass\n"
    assert inventory("module.py", source) == {"module.py:f": 3}

"""Report large production functions and prevent new or growing oversized functions."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import subprocess

LIMIT = 120
ROOT = Path(__file__).resolve().parents[1]


class FunctionInventory(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.scope: list[str] = []
        self.functions: dict[str, int] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        name = f"{self.filename}:{'.'.join(self.scope)}"
        # Duplicate definitions must not conceal the larger implementation.
        size = node.end_lineno - node.lineno + 1
        self.functions[name] = max(size, self.functions.get(name, 0))
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


def inventory(filename: str, source: str) -> dict[str, int]:
    visitor = FunctionInventory(filename)
    visitor.visit(ast.parse(source, filename=filename))
    return visitor.functions


def violations(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """Permit existing debt, but prohibit enlarging it or creating new debt."""
    errors = []
    for name, size in sorted(after.items()):
        previous = before.get(name, 0)
        if size > max(LIMIT, previous):
            errors.append(f"{name}: {previous} -> {size} lines (allowed {max(LIMIT, previous)})")
    return errors


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, encoding="utf-8"
    )


def revision_inventory(revision: str) -> dict[str, int]:
    # Resolve the revision first; never interpret a user-supplied option as a ref.
    sha = git("rev-parse", "--verify", "--end-of-options", revision + "^{commit}").strip()
    result: dict[str, int] = {}
    files = git("ls-tree", "-r", "--name-only", sha, "--", "src/wotlkbuddy")
    for filename in files.splitlines():
        if filename.endswith(".py"):
            result.update(inventory(filename, git("show", f"{sha}:{filename}")))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Git revision to compare against")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    before = revision_inventory(args.base)
    after = revision_inventory(args.head)
    print(f"Production functions: {len(after)}; over {LIMIT} lines: "
          f"{sum(size > LIMIT for size in after.values())}")
    print("Largest functions:")
    for name, size in sorted(after.items(), key=lambda pair: (-pair[1], pair[0]))[:15]:
        print(f"{size:5}  {name}")
    errors = violations(before, after)
    if errors:
        print("\nFunction-size regressions:")
        print("\n".join(errors))
        return 1
    print("\nFunction-size ratchet passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

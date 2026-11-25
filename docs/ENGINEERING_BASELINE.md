# Engineering baseline and refactoring rules

The post-regression-repair checkpoint is commit `efce36680d2d6b0822a76d30b2a9d8254c96a24f` (PR #126).
The Linux Python 3.11 and 3.13 jobs reported 705 passing tests and 150
passing subtests; Lua validation passed. Live gameplay remains a separate
acceptance step.

## Quality ratchet

The Code quality workflow compares the AST function spans in production
Python under `src/wotlkbuddy` with the PR base or previous main commit.
New functions must not exceed 120 source lines. Existing larger functions
may remain unchanged or shrink, but may not grow. Nested functions and
methods are included, and their lines count toward their containing
function. A rename or move does not grandfather an oversized function.

This is a deliberately narrow maintainability guard, not a quality score,
a complexity metric, a coverage guarantee, or an excuse to split code
mechanically. The full Python and Lua regression suite remains essential.
Run locally with `python tools/code_quality.py --base origin/main` after
committing changes and fetching the base branch.

## Refactoring order

1. Move orchestration out of CLI into independently testable application
   services without changing game behavior.
2. Establish explicit activity transitions and a single input owner.
3. Extract combat, loot and navigation recovery from their large executors.
4. Isolate class-specific policy and verify multiple classes independently.

Keep commits focused and preserve public commands and safety guards. Add
behavioral regression tests before changing state transitions. Run the
full matrix before merging; never skip a failing test merely to get green.

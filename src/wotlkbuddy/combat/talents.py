"""Class-independent talent build planning, without speculative talent spending.

This is a pure planner. Before executing any talent point the future input
adapter must observe live GetTalentInfo(tab, index) and the new unspent-point
count, then revalidate. Never derive tab/index from a translated name.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Talent:
    tree: int
    index: int
    name: str
    rank: int
    max_rank: int
    learnable: bool


@dataclass(frozen=True)
class TalentChoice:
    tree: int
    index: int
    name: str


@dataclass(frozen=True)
class TalentBuild:
    class_token: str
    name: str
    order: tuple[TalentChoice, ...]


def next_talent_choice(
    build: TalentBuild, *, class_token: str, points_available: int,
    observed: tuple[Talent, ...],
) -> TalentChoice | None:
    """Plan one class-verified next rank; never skip blocked prerequisites.

    A build order lists *each point* (repeated choices for multi-rank
    talents). A mismatch in earlier ranks or talent identity aborts
    selection; spent talents are not reset or reallocated.
    """
    if (build.class_token.upper() != class_token.upper() or
            points_available <= 0 or points_available > 100):
        return None
    if len({(t.tree,t.index) for t in observed}) != len(observed):
        return None
    by_index={(t.tree,t.index):t for t in observed}
    desired_ranks: dict[tuple[int,int],int]={}
    for planned in build.order:
        key=(planned.tree,planned.index)
        desired_ranks[key]=desired_ranks.get(key,0)+1
        current=by_index.get(key)
        if (current is None or current.name != planned.name or
                not 0 <= current.rank <= current.max_rank or
                desired_ranks[key] > current.max_rank):
            return None
        if current.rank < desired_ranks[key]:
            if current.rank != desired_ranks[key]-1 or not current.learnable:
                return None
            return planned
    return None

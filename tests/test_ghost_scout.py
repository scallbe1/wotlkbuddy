from wotlkbuddy.horde.ghost_scout import GhostScout


def test_only_recent_exact_neutral_or_friendly_guids_are_excluded():
    scout=GhostScout(sample=None,driver=None,now=lambda:100.)
    scout.reactions={
        101:(4,99.),102:(5,95.),103:(3,99.),104:(4,0.)}
    assert scout.ignored_guids()==frozenset({101,102})

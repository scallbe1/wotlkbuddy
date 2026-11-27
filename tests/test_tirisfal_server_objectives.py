"""Quest totals verified against the supplied AzerothCore world export."""
from wotlkbuddy.horde.profile import load_horde_profile, native_profiles_root

def test_starter_kill_totals_match_world_database():
    p = load_horde_profile(native_profiles_root() / 'Horde/Starter/Tirisfal-Glades-1-12.xml')
    expected = {364: {1501: 8, 1502: 8}, 3901: {1890: 12},
                380: {1504: 10, 1505: 8}}
    for qid, counts in expected.items():
        q = next(q for q in p.quests if q.quest_id == qid)
        assert {o.entry: o.count for o in q.objectives} == counts
        assert {s.entry: s.count for s in p.steps if s.quest_id == qid
                and s.kind == 'objective'} == counts

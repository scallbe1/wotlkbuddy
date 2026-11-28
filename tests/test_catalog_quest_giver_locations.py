from dataclasses import replace
from types import SimpleNamespace as NS

from wotlkbuddy.game.types import Vec3
from wotlkbuddy.horde.missed_quests import recovery_offers
from wotlkbuddy.horde.npc_catalog import nearby_profile_quest_givers, quest_giver_position
from wotlkbuddy.horde.profile import HordeProfile, OrderStep


def setup(tmp_path, relation='900001\tcreature\tstarter\t900002\n'):
    (tmp_path / 'QuestRelations.local.tsv').write_text(relation, encoding='utf8')
    (tmp_path / 'QuestMetadata.local.tsv').write_text(
        '900001\t1\t5\t0\t0\t0\t0\t0\t2\n', encoding='utf8')
    step = OrderStep('pickup', 900001, 'Synthetic', 900002, npc_name='Known profile giver')
    profile = HordeProfile('Synthetic', 'profile.xml', (), (), (step,), .2, 2, ())
    state = NS(truncated=False, player=NS(guid='player', level=10, dead=False, in_combat=False),
               resources=NS(race_token='Scourge', class_token='MAGE'), quests=(), quest_completions=())
    current = NS(map_id=1, position=Vec3(0, 0, 0))

    class DB:
        path = tmp_path / 'CreatureSpawns.db'
        points = (Vec3(50, 0, 0),)

        def find(self, entry, map_id, limit):
            assert entry == 900002 and map_id == 1 and limit == 100
            return [NS(position=p) for p in self.points][:limit]

    return step, profile, state, current, DB()


def test_missing_profile_xyz_uses_relation_and_unique_same_map_spawn(tmp_path):
    step, profile, state, current, db = setup(tmp_path)
    offers = recovery_offers(profile, state, current, db, {}, now=1000)
    assert len(offers) == 1 and offers[0].quest_ids == frozenset({900001})
    assert offers[0].giver.position == Vec3(50, 0, 0)
    assert 'quest relation' in offers[0].giver.source
    nearby = nearby_profile_quest_givers(
        profile=profile, telemetry=state, spawns=db, origin=current.position, map_id=1)
    assert nearby[0].position == offers[0].giver.position
    assert step.world_position is None  # Never rewrite the profile.


def test_missing_unknown_gameobject_and_receiver_relations_do_not_authorize_npc(tmp_path):
    for relation in ('', '900001\tgameobject\tstarter\t900002\n',
                     '900001\tcreature\tender\t900002\n',
                     '900003\tcreature\tstarter\t900002\n'):
        _, profile, state, current, db = setup(tmp_path, relation)
        assert not recovery_offers(profile, state, current, db, {}, now=1000)
        assert not nearby_profile_quest_givers(
            profile=profile, telemetry=state, spawns=db, origin=current.position, map_id=1)


def test_missing_distant_ambiguous_or_truncated_spawns_are_rejected(tmp_path):
    step, _, _, current, db = setup(tmp_path)
    for points in ((), (Vec3(601, 0, 0),), (Vec3(50, 0, 0), Vec3(70, 0, 0)),
                   (Vec3(50, 0, 0),) * 100):
        db.points = points
        assert quest_giver_position(db, step, map_id=1, origin=current.position, radius=600) is None


def test_existing_profile_coordinates_do_not_require_upstream_relation(tmp_path):
    step, _, _, current, db = setup(tmp_path, '')
    anchored = replace(step, world_position=Vec3(50, 0, 0))
    assert quest_giver_position(db, anchored, map_id=1, origin=current.position, radius=600) == db.points[0]


def test_eligibility_cooldown_and_completed_reward_guards_remain(tmp_path):
    _, profile, state, current, db = setup(tmp_path)
    assert not recovery_offers(profile, state, current, db,
                               {('player', 10, 900002): 999}, now=1000)
    state.quest_completions = ((900001, True),)
    assert not recovery_offers(profile, state, current, db, {}, now=1000)
    state.quest_completions = ()
    state.quests = (NS(quest_id=900003, complete=True),)
    assert not recovery_offers(profile, state, current, db, {}, now=1000)
    state.quests = ()
    state.player.level = 1
    assert not recovery_offers(profile, state, current, db, {}, now=1000)

from types import SimpleNamespace as NS

from wotlkbuddy.horde.quest_metadata import (
    QuestCatalog, QuestMetadata, metadata_for, quest_eligible,
)


def state(level=12):
    return NS(player=NS(level=level),resources=NS(race_token='Scourge',class_token='MAGE'))


def rule(qid,previous=0,next_quest=0,group=0):
    return QuestMetadata(qid,1,5,0,0,0,previous,group,2,next_quest)


def test_bundled_reference_covers_all_upstream_quests(tmp_path):
    db=NS(path=tmp_path/'CreatureSpawns.db')
    catalog=metadata_for(db)
    assert len(catalog)==9464
    calvin=catalog[590]
    assert (calvin.level,calvin.minimum,calvin.previous)==(5,1,8)
    assert not quest_eligible(catalog,calvin,state(),set(),set())
    assert quest_eligible(catalog,calvin,state(),set(),{8})
    assert metadata_for(db) is catalog


def test_custom_export_takes_precedence_and_legacy_format_still_works(tmp_path):
    db=NS(path=tmp_path/'CreatureSpawns.db')
    path=tmp_path/'QuestMetadata.local.tsv'
    path.write_text('590\t1\t9\t0\t0\t0\t0\t0\t2\n',encoding='utf8')
    catalog=metadata_for(db)
    assert len(catalog)==1
    assert catalog[590].level==9 and catalog[590].next_quest==0


def test_reverse_next_quest_is_an_alternative_prerequisite():
    rows=QuestCatalog({1:rule(1,next_quest=3),2:rule(2),3:rule(3,previous=2)})
    assert not rows.eligible(rows[3],state(),set(),set())
    assert rows.eligible(rows[3],state(),set(),{1})
    assert rows.eligible(rows[3],state(),set(),{2})


def test_negative_prerequisite_group_requires_all_rewarded():
    rows=QuestCatalog({1:rule(1,group=-5),2:rule(2,group=-5),3:rule(3,previous=1)})
    assert not rows.eligible(rows[3],state(),set(),{1})
    assert rows.eligible(rows[3],state(),set(),{1,2})


def test_positive_exclusive_group_blocks_alternatives():
    rows=QuestCatalog({1:rule(1,group=5),2:rule(2,group=5)})
    assert not rows.eligible(rows[2],state(),{1},set())
    assert not rows.eligible(rows[2],state(),set(),{1})
    assert rows.eligible(rows[2],state(),set(),set())

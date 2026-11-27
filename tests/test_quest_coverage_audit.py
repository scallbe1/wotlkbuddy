"""Quest coverage must not confuse an absent history flag with completion."""
from types import SimpleNamespace as NS
from wotlkbuddy.horde.profile import HordeProfile, OrderStep, QuestSpec, ObjectiveSpec
from wotlkbuddy.horde.quest_audit import audit_profile_quests


def test_reconcile_observed_and_unknown_quest_status():
    steps=tuple(OrderStep('pickup',qid,f'Quest {qid}',100,npc_name='Quest giver',
                         conditions=('Me.Class == WoWClass.Warrior',) if qid==7 else ())
                for qid in range(1,9))
    profile=HordeProfile('Native','profile.xml',(
        QuestSpec(2,'Active',(ObjectiveSpec('CollectItem',100,5,(),()),)),),
        (),steps,.2,2,())
    def q(qid,complete=False,objectives=()):
        return NS(quest_id=qid,title=f'Quest {qid}',complete=complete,objectives=objectives)
    state=NS(truncated=False,player=NS(guid='guid',name='Mage',level=11),
             resources=NS(race_token='Scourge',class_token='MAGE'),
             quests=(q(2,objectives=(NS(description='Pelt 3/5',done=False),
                                    NS(description='Thread 0/1',done=False))),
                     q(3,True),q(6),q(99)),
             quest_completions=((1,True),(4,False),(6,True)))
    audit=audit_profile_quests(profile,state)
    rows={r['quest_id']:r for r in audit['quests']}
    assert [rows[i]['status'] for i in range(1,9)]==[
        'completed','active','ready_to_turn_in','not_completed',
        'history_unknown','history_conflict','not_applicable','history_unknown']
    assert rows[2]['issues']==['objective count differs: 1 XML vs 2 live']
    assert audit['active_outside_profile'][0]['quest_id']==99
    assert rows[4]['pickups'][0]['entry']==100


def test_live_status_argument_registration_and_default_profile_root():
    import argparse
    from pathlib import Path
    from wotlkbuddy.cli import _register_live_status

    parser = argparse.ArgumentParser()
    _register_live_status(parser.add_subparsers(dest='command', required=True))
    args = parser.parse_args(['live-status', '--quest-audit', '--countdown', '0'])
    assert args.quest_audit
    assert Path(args.profiles_root) == Path.home() / 'Questing-profiles'
    assert args.countdown == 0
    assert not parser.parse_args(['live-status']).quest_audit
    assert parser.parse_args(['live-status', '--profiles-root', 'custom']).profiles_root == 'custom'

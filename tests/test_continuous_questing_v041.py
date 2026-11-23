"""Offline bounded continuous quest loop tests."""
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import unittest

from wotlkbuddy.cli import _horde_continuous
from wotlkbuddy.live.control import LiveControlError


def args():
    return Namespace(only='continuous', enable_input=True,
                     countdown=2., max_cycles=12, auto_minutes=20)


class ContinuousQuestTests(unittest.TestCase):
    def run_loop(self, responses):
        modes=[]
        items=iter(responses)
        def stage(phase_args, **kwargs):
            modes.append(phase_args.only)
            self.assertTrue(kwargs.get('_skip_countdown'))
            self.assertFalse(kwargs.get('_emit_report'))
            return next(items)
        with patch('wotlkbuddy.cli._horde_progress', side_effect=stage), \
             patch('wotlkbuddy.cli.time.sleep'), \
             redirect_stdout(StringIO()):
            report=_horde_continuous(args())
        return report['continuous'],modes

    def test_one_turnin_then_accept_then_turnin_then_stop(self):
        output,modes=self.run_loop([
            {'quest_turnin': {'quest_id':6395, 'status':'turned_in'}},
            {'active_objective': {'status':'no_supported_objective'}},
            {'accepted_quests': {'Sarvis': (364,)}},
            {'quest_turnin': {'quest_id':364,'status':'turned_in'}},
            {'active_objective': {'status':'no_supported_objective'}},
            {'accepted_quests': {}},
        ])
        self.assertEqual(modes,['objectives','objectives','quests','objectives',
                                 'objectives','quests'])
        self.assertEqual(output['quest_ids_turned_in'],[6395,364])
        self.assertEqual(output['quest_ids_accepted'],[364])
        self.assertEqual(output['status'],'no_supported_objectives_or_reachable_quest_offers')

    def test_unsupported_objective_stops_without_looping(self):
        output,modes=self.run_loop([
            {'active_objective': {'status':'missing_item_mob_sources'}}
        ])
        self.assertEqual(modes,['objectives'])
        self.assertTrue(output['status'].startswith('unsupported_objective:'))

    def test_duplicate_turnin_refuses_repeat(self):
        with patch('wotlkbuddy.cli._horde_progress',
                   side_effect=[
                       {'quest_turnin': {'quest_id':6395,'status':'turned_in'}},
                       {'quest_turnin': {'quest_id':6395,'status':'turned_in'}}
                   ]),patch('wotlkbuddy.cli.time.sleep'),redirect_stdout(StringIO()):
            with self.assertRaisesRegex(LiveControlError,'same quest'):
                _horde_continuous(args())

    def test_cycle_limit_bounds_runs(self):
        a=args()
        a.max_cycles=1
        with patch('wotlkbuddy.cli._horde_progress',
                   return_value={'quest_turnin':{'quest_id':6395,'status':'turned_in'}}) as run, \
             patch('wotlkbuddy.cli.time.sleep'), redirect_stdout(StringIO()):
            output=_horde_continuous(a)['continuous']
        self.assertEqual(run.call_count,1)
        self.assertEqual(output['status'],'cycle_limit')

    def test_invalid_budgets_rejected_without_input(self):
        a=args()
        a.max_cycles=251
        with patch('wotlkbuddy.cli._horde_progress') as phase:
            with self.assertRaisesRegex(ValueError,'budgets'):
                _horde_continuous(a)
        phase.assert_not_called()


if __name__=='__main__': unittest.main()

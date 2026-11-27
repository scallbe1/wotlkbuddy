"""Regression for refreshing live trainer services."""
from types import SimpleNamespace as NS
from wotlkbuddy.horde.training import refresh_trainer_services


def test_trainer_filter_refresh_reads_new_services():
    initial=NS(trainer_open=True,trainer_services=())
    frost=NS(name='Frostbolt',rank='Rank 1',cost=100,required_level=4)
    ready=NS(trainer_open=True,trainer_services=(frost,))
    calls=[]
    driver=NS(show_available_trainer_services=lambda s:calls.append('filter'))
    def fresh(s):
        calls.append('fresh')
        return ready
    assert refresh_trainer_services(initial,driver=driver,fresh=fresh,log=lambda _:None) is ready
    assert calls==['filter','fresh','fresh','fresh']

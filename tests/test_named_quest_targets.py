from types import SimpleNamespace as NS

from wotlkbuddy.horde.hunt_objective_state import seed_objective_names
from wotlkbuddy.horde.awareness import ObjectAwareness


def test_live_kill_journal_names_exact_quest_creature():
    driver=NS(awareness=NS(names={}))
    logs=[]
    seed_objective_names(driver,NS(sources=()),NS(description='Captain Perrine slain: 0/1'),
        (1662,),collect_items=False,log=logs.append)
    assert driver.awareness.names=={1662:'Captain Perrine'}
    assert 'Captain Perrine' in logs[0]


def test_collection_names_come_from_creature_sources_not_item_name():
    driver=NS(awareness=NS(names={}))
    spec=NS(sources=(NS(kind='mob',entry=1544,name='Vile Fin Minor Oracle'),))
    seed_objective_names(driver,spec,NS(description='Vile Fin Scale: 0/5'),
        (1544,),collect_items=True,log=lambda _:None)
    assert driver.awareness.names=={1544:'Vile Fin Minor Oracle'}


def test_named_target_selected_without_tab():
    commands=[]
    driver=NS(target_objective_name=lambda name:commands.append(name) or True,
        emergency_stop_requested=lambda:False,wow_is_foreground=lambda:True,
        chat=lambda command:commands.append(command))
    def frame(seq,target):
        return NS(sequence=seq,truncated=False,player=NS(guid='player',
            target_guid=target,in_combat=False,dead=False))
    target='0xF13000067E00085B'
    awareness=ObjectAwareness(read=lambda *_:(),capture=lambda:frame(2,target),
        driver=driver,sleep=lambda _:None)
    seed_objective_names(NS(awareness=awareness),NS(sources=()),
        NS(description='Captain Perrine slain: 0/1'),(1662,),
        collect_items=False,log=lambda _:None)
    result=awareness.acquire(frame(1,''),target,1662)
    assert result.player.target_guid==target
    assert commands==['Captain Perrine']

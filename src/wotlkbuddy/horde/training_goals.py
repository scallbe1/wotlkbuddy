"""Persistent character-scoped training budget; copper values are live observations."""
from pathlib import Path
import json
import os
import re
from dataclasses import asdict, dataclass
from time import time

@dataclass(frozen=True)
class TrainingGoal:
    level: int
    cash: int
    free_slots: int
    pending: tuple[tuple[str,str,int], ...]
    trainer: str
    trainer_entry: int
    map_id: int
    xyz: tuple[float,float,float]
    checked_at: float

    @property
    def next_cost(self):
        return min((p[2] for p in self.pending),default=None)

    @property
    def total_cost(self):
        return sum(p[2] for p in self.pending)

    def due(self,level,cash,free_slots,*,vendor_copper=None):
        if level!=self.level:
            return True
        if not self.pending:
            # A filtered trainer list can falsely appear exhausted after a
            # purchase. Recheck when funds have materially increased since
            # the last actual inspection (one low-level rank's worth).
            return cash-self.cash>=95
        # Retry when one known rank is affordable, or new loot has occupied
        # at least two more slots since the last vendor/trainer visit.
        if cash>=self.next_cost:
            return True
        if vendor_copper is not None:
            return vendor_copper>0 and (cash+vendor_copper>=self.next_cost or free_slots<=self.free_slots-2)
        return free_slots<=self.free_slots-2

    def describe(self,cash):
        if not self.pending:
            return f'Training checked at level {self.level}; no observed available ranks outstanding'
        return (f'Training priority: {len(self.pending)} outstanding rank(s), '
                f'cash {cash}c, next {self.next_cost}c, short {max(0,self.next_cost-cash)}c, '
                f'all {self.total_cost}c; trainer {self.trainer} '
                f'map {self.map_id} XYZ {self.xyz}')


def goal_path(guid,name,root=None):
    # GUID + name, no executable input or arbitrary path components.
    token=re.sub(r'[^A-Za-z0-9_-]','_',str(guid)+'_'+str(name))[:120]
    return (Path(root) if root is not None else Path.home()/'.wotlkbuddy'/'training')/(token+'.json')


def load_goal(guid,name,root=None):
    try:
        raw=json.loads(goal_path(guid,name,root).read_text(encoding='utf-8'))
        if raw['guid']!=guid or raw['name']!=name:
            return None
        d=raw['goal']
        d['pending']=tuple((str(n),str(r),int(c)) for n,r,c in d['pending'])
        d['xyz']=tuple(float(x) for x in d['xyz'])
        g=TrainingGoal(**d)
        if not 1<=g.level<=80 or g.cash<0 or any(c<0 for _,_,c in g.pending) or len(g.xyz)!=3:
            return None
        return g
    except (OSError,ValueError,KeyError,TypeError):
        return None


def save_goal(guid,name,goal,root=None):
    path=goal_path(guid,name,root)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps({'guid':guid,'name':name,'goal':asdict(goal)},indent=2),encoding='utf-8')
    os.replace(tmp,path)

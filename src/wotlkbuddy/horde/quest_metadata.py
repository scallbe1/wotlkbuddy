"""Optional server-exported quest eligibility metadata; no quest-ID rules."""
from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
import csv

# WotLK DBC numeric race/class IDs, not character-specific policy.
RACES=dict(zip(('Human','Orc','Dwarf','NightElf','Undead','Tauren','Gnome',
                'Troll','BloodElf','Draenei'),(1,2,3,4,5,6,7,8,10,11)))
CLASSES=dict(zip(('Warrior','Paladin','Hunter','Rogue','Priest','DeathKnight',
                  'Shaman','Mage','Warlock','Druid'),(1,2,3,4,5,6,7,8,9,11)))


def character_tokens(state):
    resources=getattr(state,'resources',None)
    race=getattr(resources,'race_token','')
    cls=getattr(resources,'class_token','')
    def normalized(token,choices):
        key=token.replace(' ','').replace('_','').casefold()
        return next((name for name in choices if name.casefold()==key),'')
    return ('Undead' if race.casefold()=='scourge' else normalized(race,RACES),
            normalized(cls,CLASSES))


@dataclass(frozen=True)
class QuestMetadata:
    quest_id: int
    minimum: int
    level: int
    maximum: int
    races: int
    classes: int
    previous: int
    exclusive_group: int
    quest_type: int
    next_quest: int = 0

    def permits(self,state,active,done):
        return self.character_eligible(state) and self.previous_ready(active,done)

    def character_eligible(self,state):
        level=state.player.level
        race,cls=character_tokens(state)
        def mask_allows(mask,token,ids):
            return not mask or (token in ids and bool(mask & (1<<(ids[token]-1))))
        return (self.quest_type!=1 and level>=self.minimum
                and (not self.maximum or level<=self.maximum)
                and (self.level<=0 or self.level<=level)
                and mask_allows(self.races,race,RACES)
                and mask_allows(self.classes,cls,CLASSES))

    def previous_ready(self,active,done):
        return (self.previous==0 or
                (self.previous>0 and self.previous in done) or
                (self.previous<0 and -self.previous in active))


class QuestCatalog(dict):
    """Index reverse NextQuestID links and exclusive groups once per snapshot."""
    def __init__(self,rows):
        super().__init__(rows)
        self.previous_links={}
        self.groups={}
        for qid,rule in self.items():
            if rule.next_quest:
                self.previous_links.setdefault(rule.next_quest,set()).add(qid)
            if rule.exclusive_group:
                self.groups.setdefault(rule.exclusive_group,set()).add(qid)

    def eligible(self,rule,state,active,done):
        if not rule.character_eligible(state):
            return False
        group=rule.exclusive_group
        if group>0 and (self.groups.get(group,set())-{rule.quest_id}) & (active|done):
            return False
        previous=set(self.previous_links.get(rule.quest_id,()))
        if rule.previous:
            previous.add(rule.previous)
        if not previous:
            return True
        return any(self.predecessor_ready(p,active,done) for p in previous)

    def predecessor_ready(self,p,active,done):
        if p<0:
            # Negative requirements depend on live quest status. Leave the
            # more complex grouped case to the server.
            return -p in active
        if p not in done:
            return False
        prior=self.get(p)
        if prior and prior.exclusive_group<0:
            return self.groups.get(prior.exclusive_group,set()) <= done
        return True


def quest_eligible(metadata,rule,state,active,done):
    catalog=metadata if isinstance(metadata,QuestCatalog) else QuestCatalog(metadata)
    return catalog.eligible(rule,state,active,done)


def read_quest_metadata(path):
    """Strict TSV reader: malformed local data cannot silently alter eligibility."""
    rows={}
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:
        for line,row in enumerate(csv.reader(stream,delimiter='\t'),1):
            if not row:
                continue
            try:
                data=QuestMetadata(*map(int,row))
            except (TypeError,ValueError) as exc:
                raise ValueError(f'Invalid quest metadata at line {line}') from exc
            if (data.quest_id<=0 or data.quest_id in rows or
                    not 0<=data.minimum<=255 or not -1<=data.level<=255 or
                    not 0<=data.maximum<=255 or data.races<0 or data.classes<0 or
                    data.quest_type not in (0,1,2) or data.next_quest<0):
                raise ValueError(f'Invalid or duplicate quest metadata at line {line}')
            rows[data.quest_id]=data
    if not rows:
        raise ValueError('Quest metadata export is empty')
    return QuestCatalog(rows)


@lru_cache(maxsize=4)
def cached_metadata(path,mtime_ns,size):
    return read_quest_metadata(path)


def load_metadata(path):
    stat=path.stat()
    return cached_metadata(path.resolve(),stat.st_mtime_ns,stat.st_size)


def metadata_for(db):
    path=getattr(db,'path',None)
    if not isinstance(path,(str,Path)):
        return {}
    base=Path(path)
    for name in ('QuestMetadata.local.tsv','QuestMetadata.tsv'):
        candidate=base.with_name(name)
        if candidate.is_file():
            return load_metadata(candidate)
    return load_metadata(Path(__file__).with_name('QuestMetadata.upstream.tsv'))

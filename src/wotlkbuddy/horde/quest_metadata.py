"""Optional server-exported quest eligibility metadata; no quest-ID rules."""
from dataclasses import dataclass
from pathlib import Path
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

    def permits(self,state,active,done):
        level=state.player.level
        race,cls=character_tokens(state)
        def mask_allows(mask,token,ids):
            return not mask or (token in ids and bool(mask & (1<<(ids[token]-1))))
        return (self.quest_type!=1 and level>=self.minimum
                and (not self.maximum or level<=self.maximum)
                and (self.level<=0 or self.level<=level)
                and mask_allows(self.races,race,RACES)
                and mask_allows(self.classes,cls,CLASSES)
                and (self.previous==0 or
                     (self.previous>0 and self.previous in done) or
                     (self.previous<0 and -self.previous in active)))


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
                    data.quest_type not in (0,1,2)):
                raise ValueError(f'Invalid or duplicate quest metadata at line {line}')
            rows[data.quest_id]=data
    if not rows:
        raise ValueError('Quest metadata export is empty')
    return rows


def metadata_for(db):
    path=getattr(db,'path',None)
    if not isinstance(path,(str,Path)):
        return {}
    base=Path(path)
    for name in ('QuestMetadata.local.tsv','QuestMetadata.tsv'):
        candidate=base.with_name(name)
        if candidate.is_file():
            return read_quest_metadata(candidate)
    return {}

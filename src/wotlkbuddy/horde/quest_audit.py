"""Read-only reconciliation of profile quests with live journal and observed history."""
from collections import Counter
import re
from wotlkbuddy.horde.profile import condition_value
from wotlkbuddy.live.control import LiveControlError


def audit_profile_quests(profile, state, *, map_id=None):
    """Missing history is unknown, not proof of an uncompleted quest."""
    if state.truncated or not state.player.guid or state.resources is None:
        raise LiveControlError('Complete character telemetry required for quest audit')
    journal={q.quest_id:q for q in state.quests}
    history=dict(state.quest_completions)
    completed={qid for qid,done in history.items() if done}
    definitions={q.quest_id:q for q in profile.quests}
    race=state.resources.race_token
    if race.casefold() in ('undead','scourge'):
        race='Undead'
    cls=state.resources.class_token.title()
    groups={}
    for index,step in enumerate(profile.steps,1):
        if step.quest_id>0:
            groups.setdefault(step.quest_id,[]).append((index,step))
    rows=[]
    for qid,steps in groups.items():
        pickups=[(i,s) for i,s in steps if s.kind=='pickup']
        # The pickup's class/race guards are more authoritative than an
        # unguarded turn-in left behind in imported profiles.
        relevant=pickups or steps
        checks=[(i,s,[condition_value(c,level=state.player.level,race=race,
                     class_name=cls,map_id=map_id,active_quests=set(journal),
                     completed_quests=completed if all(int(q) in history for q in
                         re.findall(r'IsQuestCompleted\s*\(\s*(\d+)\s*\)',c)) else None)
                     for c in s.conditions]) for i,s in relevant]
        eligible=any(all(v is True for v in vs) for _,_,vs in checks)
        conditional=any(False not in vs and None in vs for _,_,vs in checks)
        q=journal.get(qid)
        if q is not None and history.get(qid) is True:
            status='history_conflict'
        elif q is not None:
            status='ready_to_turn_in' if q.complete else 'active'
        elif history.get(qid) is True:
            status='completed'
        elif not eligible:
            status='condition_unknown' if conditional else 'not_applicable'
        elif history.get(qid) is False:
            status='not_completed'
        else:
            status='history_unknown'
        spec=definitions.get(qid)
        title=q.title if q is not None else next((s.name for _,s in steps if s.name),'')
        issues=[]
        if q is not None and not q.complete:
            if spec is not None and len(spec.objectives)!=len(q.objectives):
                issues.append(f'objective count differs: {len(spec.objectives)} XML vs {len(q.objectives)} live')
            if any(not s.supported for _,s in steps):
                issues.append('unsupported profile action')
        rows.append(dict(quest_id=qid,title=title,status=status,
                         first_step=steps[0][0],
                         pickups=[dict(step=i,npc=s.npc_name,entry=s.entry,
                                       eligible=all(v is True for v in vs))
                                  for i,s,vs in checks if s.kind=='pickup'],
                         issues=issues,
                         objectives=[dict(description=o.description,done=o.done)
                                     for o in q.objectives] if q is not None else []))
    outside=[dict(quest_id=q.quest_id,title=q.title,complete=q.complete)
             for q in journal.values() if q.quest_id not in groups]
    return dict(profile=profile.name,source=profile.source_path,character=state.player.name,
                level=state.player.level,history_flags=len(history),
                counts=dict(Counter(row['status'] for row in rows)),
                quests=rows,active_outside_profile=outside)


def print_profile_audit(audit):
    print(f"{audit['character']} level {audit['level']} | {audit['profile']}")
    print(f"Source: {audit['source']}")
    print('Observed history flags: '+str(audit['history_flags']))
    print('Coverage: '+', '.join(f'{k}={v}' for k,v in sorted(audit['counts'].items())))
    for row in audit['quests']:
        if row['status'] in ('completed','not_applicable'):
            continue
        print(f"  [{row['quest_id']}] {row['title']}: {row['status']}")
        for issue in row['issues']:
            print('    '+issue)
        for obj in row['objectives']:
            print('    '+obj['description'])
        for giver in row['pickups']:
            if giver['eligible'] and row['status'] in ('not_completed','history_unknown'):
                print(f"    profile pickup: {giver['npc']} ({giver['entry']}); availability unverified")
    for row in audit['active_outside_profile']:
        print(f"  [{row['quest_id']}] {row['title']}: active outside profile")
    print('History unknown does not prove a quest was skipped; pickup availability is not verified.')

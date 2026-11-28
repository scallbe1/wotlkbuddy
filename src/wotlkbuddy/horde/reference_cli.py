"""Offline reference inspection: no client attachment, input, or network."""
from dataclasses import asdict
import json

from wotlkbuddy.horde.quest_relations import load_quest_relations
from wotlkbuddy.horde.quest_requirements import load_quest_requirements


def positive_id(value):
    import argparse
    try:
        number = int(value)
        if 0 < number < 2**24:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('ID must be a positive integer below 2^24')


def register_reference_command(sub):
    parser = sub.add_parser('quest-reference', help='Inspect bundled quest reference (offline)')
    key = parser.add_mutually_exclusive_group(required=True)
    key.add_argument('--quest-id', type=positive_id)
    key.add_argument('--entry', type=positive_id)
    key.add_argument('--item-id', type=positive_id, help='Trace candidate quest-item loot sources')
    key.add_argument('--profile', help='Audit a profile XML against reference relations/objectives')
    parser.add_argument('--kind', choices=('creature', 'gameobject'), default='creature')
    parser.add_argument('--data-dir', help='Directory containing optional local reference TSVs')
    parser.set_defaults(func=print_reference)
    return sub


def print_reference(args):
    if args.item_id:
        from wotlkbuddy.horde.quest_loot import load_quest_loot
        loot = load_quest_loot(args.data_dir)
        print(json.dumps(dict(source=loot.source, reference_only=True,
                              execution_authorized=False, **loot.sources_for(args.item_id)), indent=2))
        return
    catalog = load_quest_relations(args.data_dir)
    requirements = load_quest_requirements(args.data_dir)
    if args.profile:
        from wotlkbuddy.horde.profile import load_horde_profile
        from wotlkbuddy.horde.reference_audit import audit_reference_profile
        print(json.dumps(audit_reference_profile(
            load_horde_profile(args.profile), catalog, requirements), indent=2))
        return
    rows = (catalog.for_quest(args.quest_id) if args.quest_id else
            catalog.for_entity(args.kind, args.entry))
    rule = requirements.get(args.quest_id) if args.quest_id else None
    print(json.dumps(dict(
        source=catalog.source, reference_only=True,
        availability='unverified',
        relations=[asdict(row) for row in rows],
        objectives_source=requirements.source,
        requirements_known=rule is not None if args.quest_id else None,
        objectives=[asdict(o) for o in rule.objectives] if rule else [],
        constraints=(dict(flags=rule.flags, time_allowed=rule.time_allowed,
                          player_kills=rule.player_kills,
                          required_money_copper=max(0, -rule.reward_money),
                          anomalies=rule.anomalies) if rule else None),
        execution_authorized=False,
    ), indent=2))

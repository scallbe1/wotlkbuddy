"""Offline reference inspection: no client attachment, input, or network."""
from dataclasses import asdict
import json

from wotlkbuddy.horde.quest_relations import load_quest_relations


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
    parser.add_argument('--kind', choices=('creature', 'gameobject'), default='creature')
    parser.add_argument('--data-dir', help='Directory containing optional local reference TSVs')
    parser.set_defaults(func=print_reference)
    return sub


def print_reference(args):
    catalog = load_quest_relations(args.data_dir)
    rows = (catalog.for_quest(args.quest_id) if args.quest_id else
            catalog.for_entity(args.kind, args.entry))
    print(json.dumps(dict(
        source=catalog.source, reference_only=True,
        availability='unverified',
        relations=[asdict(row) for row in rows],
    ), indent=2))

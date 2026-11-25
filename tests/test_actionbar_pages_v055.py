"""Read-only actionbar page diagnosis and legacy write-suspension and guarded maintenance regressions."""
import ast
import inspect
from dataclasses import replace
from textwrap import dedent

import pytest

from test_class_combat_runtime_v049 import state
from wotlkbuddy.horde.interaction import KeyboardQuestInput
from wotlkbuddy.live.codec import (
    ActionBarRawSlot, TelemetryError, parse_payload)
from wotlkbuddy.live.control import LiveControlError
from wotlkbuddy.live.observe import summarize_telemetry

PLAYER = (b'P\tAarr\t0x000000000000025F\t4\t87\t87\t0\t0\t'
          b'0.51\t0.52\t2\t12\tDeathknell\t\t\n')


def make_page(page=2):
    lines = [b'AH\t1', f'APG\t{page}'.encode()]
    for button in range(1,11):
        lines.append(f'AR\tbase\t{button}\t{button}\tempty\t0\t\t1'.encode())
        if page > 1:
            index=(page-1)*12+button
            if button in (2,6,7):
                lines.append(f'AR\tvisible\t{button}\t{index}\tspell\t133\tFireball\t1'.encode())
            else:
                lines.append(f'AR\tvisible\t{button}\t{index}\tempty\t0\t\t1'.encode())
    return PLAYER+b'\n'.join(lines)+b'\n'


def test_base_page_and_visible_page_are_different_without_guessing():
    snap = parse_payload(make_page(2),9)
    assert snap.actionbar_page==2
    assert len(snap.raw_action_slots)==20
    visual=[s for s in snap.raw_action_slots if
            s.view=='visible' and s.name=='Fireball']
    assert [(s.button,s.action_index) for s in visual]==[
        (2,14),(6,18),(7,19)]
    assert not snap.observed_bar_spells
    text=summarize_telemetry(replace(snap,resources=state().resources))
    assert 'visible button 2 -> action 14: spell raw=133 [Fireball]' in text
    assert 'visible button 7 -> action 19: spell raw=133 [Fireball]' in text
    assert 'Guarded Mage hotbar maintenance may edit verified page-1 slots' in text


def test_raw_page_index_mismatch_is_rejected():
    payload=make_page(2).replace(
        b'AR\tvisible\t7\t19\tspell\t133',
        b'AR\tvisible\t7\t7\tspell\t133')
    with pytest.raises(TelemetryError,match='Invalid or duplicate raw actionbar slot'):
        parse_payload(payload,10)


def test_incomplete_raw_page_does_not_misrepresent_missing_as_empty():
    payload=make_page(2).replace(
        b'AR\tvisible\t7\t19\tspell\t133\tFireball\t1\n',b'')
    with pytest.raises(TelemetryError,match='Incomplete visible actionbar slot inventory'):
        parse_payload(payload,11)


def test_no_valid_page_still_exports_raw_base_slot_data():
    raw=PLAYER+b'AH\t1\nAPG\t0\n'
    for button in range(1,11):
        raw += f'AR\tbase\t{button}\t{button}\tempty\t0\t\t0\n'.encode()
    snap=parse_payload(raw,12)
    assert snap.actionbar_page is None
    assert len(snap.raw_action_slots)==10


def test_keyboard_spell_placement_refuses_before_typing_or_touching_any_slot():
    driver=KeyboardQuestInput.__new__(KeyboardQuestInput)
    sent=[]
    driver.chat=sent.append
    with pytest.raises(LiveControlError, match='Automatic hotbar placement is suspended'):
        driver.place_book_spell(3,7)
    assert sent==[]


def test_raw_chat_placement_command_is_rejected_before_any_keypress():
    driver=KeyboardQuestInput.__new__(KeyboardQuestInput)
    events=[]
    driver._tap=events.append
    with pytest.raises(ValueError, match='Only verified quest-targeting'):
        driver.chat('/run PickupSpell(3,BOOKTYPE_SPELL);'
                    'PlaceAction(7);ClearCursor()')
    assert events==[]


def test_continuous_cli_uses_guarded_hotbar_reconciler_not_legacy_mutation():
    from wotlkbuddy import cli
    from wotlkbuddy.horde.session_startup import prepare_phase
    from wotlkbuddy.horde.session_training import execute_training_phase, visit_and_train

    def calls(function):
        tree = ast.parse(dedent(inspect.getsource(function)))
        return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    def calls_named(function, name):
        return [node for node in calls(function)
                if isinstance(node.func, ast.Name) and node.func.id == name]

    # Verify the actual delegation path, not a particular source location
    # or whitespace. Startup and post-training both use the guarded editor.
    assert calls_named(cli._horde_progress, 'prepare_phase')
    assert calls_named(cli._horde_progress, 'execute_training_phase')
    assert calls_named(execute_training_phase, 'visit_and_train')
    for function in (prepare_phase, visit_and_train):
        guarded = calls_named(function, 'reconcile_mage_hotbar')
        assert len(guarded) == 1
        assert {kw.arg: ast.unparse(kw.value) for kw in guarded[0].keywords} == {
            'capture': 'cap', 'driver': 'driver', 'log': 'log'}
    for function in (cli._horde_progress, prepare_phase,
                     execute_training_phase, visit_and_train):
        assert not calls_named(function, 'provision_rotation_hotbar')
        assert not any(isinstance(node.func, ast.Attribute)
                       and node.func.attr == 'place_book_spell'
                       for node in calls(function))

    # Legacy mutation must still fail before sending keyboard input.
    driver = KeyboardQuestInput.__new__(KeyboardQuestInput)
    sent = []
    driver.chat = sent.append
    with pytest.raises(LiveControlError, match='Automatic hotbar placement is suspended'):
        driver.place_book_spell(3, 7)
    assert sent == []

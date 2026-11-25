"""Existing CLI entry points delegate to independently importable services."""
from wotlkbuddy import cli
from wotlkbuddy.horde.session_delivery import execute_profile_return
from wotlkbuddy.horde.session_recovery import recover_horde_death


def test_cli_delegates_to_application_services():
    assert cli._execute_profile_return is execute_profile_return
    assert cli._recover_horde_death is recover_horde_death

import pytest

from dials.panels import HIDE, LAUNCH, RAISE, SHOW, decide, reapply_geometry


@pytest.mark.parametrize("found,hidden,is_active,expected", [
    (False, False, False, LAUNCH),   # nothing matched -> confirm-then-launch
    (True,  True,  False, SHOW),     # minimized -> show it
    (True,  False, False, RAISE),    # visible but buried -> surface it
    (True,  False, True,  HIDE),     # visible and active -> put it away
])
def test_full_action_table(found, hidden, is_active, expected):
    assert decide(found, hidden, is_active) == expected


def test_a_hidden_window_shows_even_if_x_still_claims_it_is_active():
    # HIDDEN wins: a minimized window must never be interpreted as "hide me".
    assert decide(found=True, hidden=True, is_active=True) == SHOW


def test_buried_dial_raises_rather_than_hiding():
    """The whole reason for three states instead of a strict toggle."""
    assert decide(True, False, False) == RAISE


def test_geometry_is_applied_on_show_always():
    assert reapply_geometry(SHOW, pin_geometry=False) is True
    assert reapply_geometry(SHOW, pin_geometry=True) is True


def test_geometry_is_not_reapplied_on_a_plain_raise_by_default():
    # Otherwise hand-nudging a window is undone on the next keypress.
    assert reapply_geometry(RAISE, pin_geometry=False) is False


def test_pin_geometry_opts_a_dial_into_strict_enforcement_on_raise():
    assert reapply_geometry(RAISE, pin_geometry=True) is True


def test_geometry_is_never_applied_on_hide_or_launch():
    for action in (HIDE, LAUNCH):
        assert reapply_geometry(action, pin_geometry=True) is False

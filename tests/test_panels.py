import pytest

from dials.panels import (
    HIDE, LAUNCH, RAISE, SHOW, decide, reapply_geometry, reapply_hints,
)
from dials.panels import (
    ACTIVATING, ACTIVATION_TIMEOUT, ACTIVE, HIDE, INACTIVE, FocusTracker,
)


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


def test_hints_are_applied_on_raise_not_only_on_show():
    """The regression this exists for.

    A window that was already visible when the daemon first saw it never goes
    through SHOW - every press is a RAISE. Gating hints on SHOW meant such a
    window never became sticky and never left the taskbar/switcher, which is
    what the Firefox Dial did: it kept appearing in alt-tab forever.
    """
    assert reapply_hints(RAISE) is True
    assert reapply_hints(SHOW) is True


def test_hints_do_not_depend_on_pin_geometry():
    """pin_geometry is about position; the hints are window properties.

    Taking a single argument is the guard: if `reapply_hints` ever grows a
    pin_geometry parameter, a Dial with pin off would silently stop being
    excluded from the switcher.
    """
    import inspect
    assert list(inspect.signature(reapply_hints).parameters) == ["action"]


def test_hints_are_never_applied_on_hide_or_launch():
    # HIDE is about to iconify the window and LAUNCH has no window yet.
    for action in (HIDE, LAUNCH):
        assert reapply_hints(action) is False


WIN = 0x600001
OTHER = 0x700001


def tracker(mode="hide", clock=None):
    from tests.conftest import FakeClock
    return FocusTracker(on_focus_loss=mode, clock=clock or FakeClock())


def test_starts_inactive():
    assert tracker().state == INACTIVE


def test_activating_then_observed_becomes_active():
    t = tracker()
    t.activating(WIN)
    assert t.state == ACTIVATING
    assert t.observe_active(WIN, {WIN}) is None
    assert t.state == ACTIVE


def test_does_not_hide_itself_during_its_own_show_sequence():
    """The race: focus moves transiently between geometry and activation."""
    t = tracker()
    t.activating(WIN)
    # Some other window is briefly active before ours lands.
    assert t.observe_active(OTHER, {WIN}) is None
    assert t.state == ACTIVATING          # guard has NOT armed


def test_hides_only_after_having_been_observed_active():
    t = tracker()
    t.activating(WIN)
    t.observe_active(WIN, {WIN})          # arms here, and only here
    assert t.observe_active(OTHER, {WIN}) == HIDE
    assert t.state == INACTIVE


def test_a_transient_of_the_dial_counts_as_still_active():
    # A Dial must not hide itself when its own dialog takes focus.
    t = tracker()
    t.activating(WIN)
    t.observe_active(WIN, {WIN})
    dialog = 0x800001
    assert t.observe_active(dialog, {WIN, dialog}) is None
    assert t.state == ACTIVE


def test_normal_mode_never_asks_to_hide():
    t = tracker("normal")
    t.activating(WIN)
    t.observe_active(WIN, {WIN})
    assert t.observe_active(OTHER, {WIN}) is None


def test_above_mode_never_asks_to_hide():
    t = tracker("above")
    t.activating(WIN)
    t.observe_active(WIN, {WIN})
    assert t.observe_active(OTHER, {WIN}) is None


def test_activation_timeout_returns_to_inactive_without_hiding():
    from tests.conftest import FakeClock
    c = FakeClock()
    t = tracker(clock=c)
    t.activating(WIN)
    c.advance(ACTIVATION_TIMEOUT + 0.1)
    assert t.check_timeout() is True       # refused or lost
    assert t.state == INACTIVE


def test_no_timeout_while_still_within_the_window():
    from tests.conftest import FakeClock
    c = FakeClock()
    t = tracker(clock=c)
    t.activating(WIN)
    c.advance(ACTIVATION_TIMEOUT / 2)
    assert t.check_timeout() is False
    assert t.state == ACTIVATING


def test_deadline_is_none_unless_activating():
    """This is what keeps the daemon's select() timeout infinite at idle."""
    from tests.conftest import FakeClock
    c = FakeClock()
    t = tracker(clock=c)
    assert t.deadline() is None
    t.activating(WIN)
    assert t.deadline() == c.t + ACTIVATION_TIMEOUT
    t.observe_active(WIN, {WIN})
    assert t.deadline() is None


def test_a_second_activation_supersedes_the_first():
    t = tracker()
    t.activating(WIN)
    t.activating(OTHER)                    # rapid second press
    assert t.observe_active(OTHER, {OTHER}) is None
    assert t.state == ACTIVE


def test_forget_resets_for_config_reload_mid_transition():
    t = tracker()
    t.activating(WIN)
    t.forget()
    assert t.state == INACTIVE
    # A late event for the abandoned window must not drive a transition.
    assert t.observe_active(OTHER, {WIN}) is None


def test_repeated_identical_active_events_are_idempotent():
    t = tracker()
    t.activating(WIN)
    t.observe_active(WIN, {WIN})
    for _ in range(3):
        assert t.observe_active(WIN, {WIN}) is None
    assert t.state == ACTIVE

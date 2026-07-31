from dials.windows import WindowInfo, choose, group_ids


def win(wid, cls="spotify", wtype="_NET_WM_WINDOW_TYPE_NORMAL",
        override=False, transient=None, appeared=0.0):
    return WindowInfo(wid=wid, wm_class=cls, wtype=wtype,
                      override_redirect=override, transient_for=transient,
                      appeared=appeared)


def test_no_match_returns_none():
    assert choose([win(1, cls="firefox")], "spotify") is None


def test_single_match_wins():
    assert choose([win(1)], "spotify").wid == 1


def test_class_match_is_exact_not_substring():
    assert choose([win(1, cls="spotify-tui")], "spotify") is None


def test_dialogs_and_utilities_are_never_the_dial():
    windows = [
        win(1, wtype="_NET_WM_WINDOW_TYPE_DIALOG"),
        win(2, wtype="_NET_WM_WINDOW_TYPE_UTILITY"),
        win(3, wtype="_NET_WM_WINDOW_TYPE_NORMAL"),
    ]
    assert choose(windows, "spotify").wid == 3


def test_all_excluded_types_are_filtered():
    for wtype in ("_NET_WM_WINDOW_TYPE_DIALOG", "_NET_WM_WINDOW_TYPE_UTILITY",
                  "_NET_WM_WINDOW_TYPE_SPLASH", "_NET_WM_WINDOW_TYPE_MENU",
                  "_NET_WM_WINDOW_TYPE_TOOLTIP"):
        assert choose([win(1, wtype=wtype)], "spotify") is None


def test_override_redirect_windows_are_filtered():
    assert choose([win(1, override=True)], "spotify") is None


def test_active_window_is_preferred():
    assert choose([win(1), win(2)], "spotify", active_id=2).wid == 2


def test_most_recently_active_is_preferred_when_none_is_active():
    assert choose([win(1), win(2), win(3)], "spotify", recent=(3, 1)).wid == 3


def test_lowest_id_is_the_stable_last_resort():
    # Stability matters: the same press must pick the same window every time.
    assert choose([win(7), win(3), win(9)], "spotify").wid == 3
    assert choose([win(9), win(3), win(7)], "spotify").wid == 3


def test_active_beats_recent():
    assert choose([win(1), win(2)], "spotify", active_id=1, recent=(2,)).wid == 1


def test_since_accepts_only_windows_that_appeared_after_a_launch():
    old, new = win(1, appeared=10.0), win(2, appeared=50.0)
    # Without `since`, the stable rule picks the pre-existing window.
    assert choose([old, new], "spotify").wid == 1
    # After launching at t=40, only the new window may be adopted.
    assert choose([old, new], "spotify", since=40.0).wid == 2


def test_since_returns_none_when_only_old_windows_exist():
    assert choose([win(1, appeared=10.0)], "spotify", since=40.0) is None


def test_group_ids_includes_the_window_itself():
    chosen = win(5)
    assert group_ids(chosen, [chosen]) == {5}


def test_group_ids_includes_transients_owned_by_the_choice():
    chosen = win(5)
    dialog = win(6, wtype="_NET_WM_WINDOW_TYPE_DIALOG", transient=5)
    unrelated = win(7, transient=99)
    assert group_ids(chosen, [chosen, dialog, unrelated]) == {5, 6}


def test_group_ids_ignores_transients_of_other_windows():
    chosen = win(5)
    other_dialog = win(6, wtype="_NET_WM_WINDOW_TYPE_DIALOG", transient=8)
    assert group_ids(chosen, [chosen, other_dialog]) == {5}


def test_an_excluded_window_named_in_recent_is_skipped():
    """`recent` must be filtered through the candidate set, not trusted blindly.

    The EXCLUDED window deliberately has the LOWER id: with the ids the other
    way round, an implementation that ignored recent/active_id/type-filtering
    entirely and just returned the naive minimum id would pass by coincidence.
    """
    dialog = win(1, wtype="_NET_WM_WINDOW_TYPE_DIALOG")
    normal = win(2, wtype="_NET_WM_WINDOW_TYPE_NORMAL")
    assert choose([dialog, normal], "spotify", recent=(1, 2)).wid == 2


def test_an_excluded_window_named_as_active_is_skipped():
    """A dialog holding focus must not become the Dial's target window.

    Excluded window at the LOWER id, for the same reason as above.
    """
    dialog = win(1, wtype="_NET_WM_WINDOW_TYPE_DIALOG")
    normal = win(2, wtype="_NET_WM_WINDOW_TYPE_NORMAL")
    assert choose([dialog, normal], "spotify", active_id=1).wid == 2

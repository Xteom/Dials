"""WindowOps is I/O, so these tests assert the *protocol* it emits, not X state.

A live check against the real server is a separate manual step.
"""
from dials.geometry import Rect
from dials.windows import PANEL_HINTS, WindowOps


class FakeAtoms(dict):
    def __missing__(self, name):
        self[name] = 1000 + len(self)
        return self[name]


class FakeWindow:
    def __init__(self, wid):
        self.id = wid
        self.configured = None

    def configure(self, **kw):
        self.configured = kw


class FakeDisplay:
    def __init__(self):
        self.atoms = FakeAtoms()
        self.sent = []
        self.windows = {}

    def intern_atom(self, name):
        return self.atoms[name]

    def get_atom_name(self, atom):
        for k, v in self.atoms.items():
            if v == atom:
                return k
        return "?"

    def create_resource_object(self, kind, wid):
        return self.windows.setdefault(wid, FakeWindow(wid))

    def flush(self):
        pass

    def sync(self):
        pass


class FakeRoot:
    def __init__(self, display):
        self.display = display
        self.id = 1

    def send_event(self, event, event_mask=0, onerror=None):
        self.display.sent.append((event, event_mask))


def ops():
    d = FakeDisplay()
    return WindowOps(d, FakeRoot(d)), d


class _Prop:
    """A get_full_property() result: just needs a `.value`."""
    def __init__(self, value):
        self.value = value


class _FlakyWindow:
    """A managed window whose property reads can be told to fail on demand -
    simulates a transient X error while the window stays in _NET_CLIENT_LIST
    throughout, for list_windows()'s first-seen bookkeeping tests."""

    def __init__(self, wid):
        self.id = wid
        self.fail = False

    def get_wm_class(self):
        if self.fail:
            raise RuntimeError("x server hiccup")
        return ("instance", "cls")

    def get_attributes(self):
        if self.fail:
            raise RuntimeError("x server hiccup")

        class Attrs:
            override_redirect = False
        return Attrs()

    def get_full_property(self, atom, prop_type):
        if self.fail:
            raise RuntimeError("x server hiccup")
        return None


def test_panel_hints_are_the_three_unconditional_ones():
    """ABOVE is deliberately NOT here - it is conditional on on_focus_loss."""
    assert PANEL_HINTS == (
        "_NET_WM_STATE_STICKY",
        "_NET_WM_STATE_SKIP_TASKBAR",
        "_NET_WM_STATE_SKIP_PAGER",
    )


def test_apply_geometry_configures_the_window():
    o, d = ops()
    o.apply_geometry(0x500001, Rect(1720, 0, 1720, 1440))
    w = d.windows[0x500001]
    assert w.configured == {"x": 1720, "y": 0, "width": 1720, "height": 1440}


def test_apply_hints_always_sets_sticky_skip_taskbar_and_skip_pager():
    o, d = ops()
    o.apply_hints(0x500001, above=False)
    props = {d.get_atom_name(ev.data[1][1]) for ev, _ in d.sent}
    assert {"_NET_WM_STATE_STICKY", "_NET_WM_STATE_SKIP_TASKBAR",
            "_NET_WM_STATE_SKIP_PAGER"} <= props


def test_apply_hints_adds_above_only_when_requested():
    o, d = ops()
    o.apply_hints(0x500001, above=True)
    props = {d.get_atom_name(ev.data[1][1]) for ev, _ in d.sent}
    assert "_NET_WM_STATE_ABOVE" in props

    o2, d2 = ops()
    o2.apply_hints(0x500001, above=False)
    props2 = {d2.get_atom_name(ev.data[1][1]) for ev, _ in d2.sent}
    assert "_NET_WM_STATE_ABOVE" not in props2


def test_activate_sends_the_supplied_timestamp_not_currenttime():
    o, d = ops()
    o.activate(0x500001, timestamp=987654)
    ev, _ = d.sent[-1]
    assert d.get_atom_name(ev.client_type) == "_NET_ACTIVE_WINDOW"
    assert ev.data[1][1] == 987654


def test_activate_uses_pager_source_indication():
    o, d = ops()
    o.activate(0x500001, timestamp=1)
    ev, _ = d.sent[-1]
    assert ev.data[1][0] == 2


def test_iconify_sends_wm_change_state_with_iconic_state():
    from Xlib import Xutil
    o, d = ops()
    o.iconify(0x500001)
    ev, _ = d.sent[-1]
    assert d.get_atom_name(ev.client_type) == "WM_CHANGE_STATE"
    assert ev.data[1][0] == Xutil.IconicState


def test_state_messages_use_substructure_masks():
    from Xlib import X
    o, d = ops()
    o.activate(0x500001, timestamp=1)
    _, mask = d.sent[-1]
    assert mask == X.SubstructureRedirectMask | X.SubstructureNotifyMask


def test_apply_hints_uses_add_action_and_pager_source():
    """_NET_WM_STATE messages carry a source indication too, not just
    _NET_ACTIVE_WINDOW - action must be "add" (1) and source must be
    pager (2) on every hint message."""
    o, d = ops()
    o.apply_hints(0x500001, above=True)
    assert d.sent
    for ev, _ in d.sent:
        assert ev.data[1][0] == 1
        assert ev.data[1][3] == 2


def test_a_failing_client_message_is_logged_and_swallowed(caplog):
    """Silent swallowing is what hid a real AttributeError; logging must fire."""
    o, d = ops()

    def boom(event, event_mask=0, onerror=None):
        raise RuntimeError("x server said no")

    d.windows  # ensure display built
    o.root.send_event = boom

    with caplog.at_level("WARNING"):
        o.activate(0x500001, timestamp=123)   # must NOT raise

    assert any("500001" in r.getMessage() or "client message" in r.getMessage()
               for r in caplog.records), "no diagnostic was logged"


def test_a_transient_read_failure_does_not_reset_a_windows_appeared_time():
    """choose(since=) relies on appeared times being stable identity, not
    liveness. A window that is still in _NET_CLIENT_LIST but whose property
    reads happen to fail on one scan must not look freshly-appeared on the
    next successful scan."""
    WID = 0x500002
    o, d = ops()

    root_win = _FlakyWindow(o.root.id)
    root_win.get_full_property = lambda atom, prop_type: _Prop([WID])
    d.windows[o.root.id] = root_win

    target = _FlakyWindow(WID)
    target.fail = True
    d.windows[WID] = target

    # scan 1: this window's property reads fail, but it IS in _NET_CLIENT_LIST
    o.list_windows()
    first = o._first_seen.get(WID)
    assert first is not None, "window must be registered even when its props fail"

    # scan 2: reads now succeed
    target.fail = False
    o.list_windows()
    assert o._first_seen[WID] == first, "appeared time was reset by a failed read"


def test_list_windows_prunes_first_seen_for_windows_that_left_the_client_list():
    """The other half of the same invariant: _first_seen must not grow
    without bound in a long-running daemon, so a window that genuinely
    leaves _NET_CLIENT_LIST still needs to be pruned."""
    WID = 0x500003
    o, d = ops()

    root_win = _FlakyWindow(o.root.id)
    root_win.get_full_property = lambda atom, prop_type: _Prop([WID])
    d.windows[o.root.id] = root_win
    d.windows[WID] = _FlakyWindow(WID)

    o.list_windows()
    assert WID in o._first_seen

    root_win.get_full_property = lambda atom, prop_type: _Prop([])
    o.list_windows()
    assert WID not in o._first_seen, \
        "a window that left _NET_CLIENT_LIST must be pruned"

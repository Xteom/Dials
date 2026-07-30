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

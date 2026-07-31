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


def test_apply_geometry_asks_for_the_rect_with_static_gravity_and_pager_source():
    """The WRITE has to mean the same rectangle as the READ.

    geometry() reports the CLIENT origin; mutter reads a NorthWest-gravity
    ConfigureRequest's x,y as the position of the visible FRAME. On a
    server-side-decorated window - Spotify, in the shipped reference config -
    those differ by the titlebar, so a read -> write round trip walked the
    window 37 px down the screen every time and `dials capture` compounded
    another 37 px per run. StaticGravity is what makes x,y the client's own
    position; measured against all four candidates in
    docs/probes/10-frame-extents-and-geometry-round-trip.py.
    """
    from Xlib import X

    o, d = ops()
    o.apply_geometry(0x500001, Rect(1720, 0, 1720, 1440))

    ev, mask = d.sent[-1]
    assert d.get_atom_name(ev.client_type) == "_NET_MOVERESIZE_WINDOW"
    flags, x, y, w, h = ev.data[1][:5]
    assert flags & 0xFF == 10, "gravity must be StaticGravity (10)"
    assert flags & 0xF00 == 0xF00, "x, y, width and height must all be flagged"
    assert (flags >> 12) & 0xF == 2, "source indication must be pager"
    assert (x, y, w, h) == (1720, 0, 1720, 1440)
    assert mask == X.SubstructureRedirectMask | X.SubstructureNotifyMask


def test_apply_geometry_never_sends_a_plain_configure_request():
    """The mutant that would silently restore the 37 px drift.

    A ConfigureRequest is the natural thing to reach for and reads correctly, so
    nothing but an explicit assertion stops it coming back.
    """
    o, d = ops()
    w = d.create_resource_object("window", 0x500001)   # a configure() would land here
    o.apply_geometry(0x500001, Rect(100, 200, 300, 400))
    assert w.configured is None, \
        "configure(x=, y=) sets the FRAME position, not the client's - that was C2"


def test_apply_hints_always_sets_sticky_skip_taskbar_and_skip_pager():
    o, d = ops()
    o.apply_hints(0x500001, above=False)
    props = {d.get_atom_name(ev.data[1][1]) for ev, _ in d.sent}
    assert {"_NET_WM_STATE_STICKY", "_NET_WM_STATE_SKIP_TASKBAR",
            "_NET_WM_STATE_SKIP_PAGER"} <= props


def _above_action(display):
    """The action byte of the one _NET_WM_STATE_ABOVE message sent, or None."""
    actions = [ev.data[1][0] for ev, _ in display.sent
               if display.get_atom_name(ev.data[1][1]) == "_NET_WM_STATE_ABOVE"]
    assert len(actions) <= 1, "ABOVE must be addressed exactly once per call"
    return actions[0] if actions else None


def test_apply_hints_adds_above_when_requested():
    o, d = ops()
    o.apply_hints(0x500001, above=True)
    assert _above_action(d) == 1                      # 1 = _NET_WM_STATE_ADD


def test_apply_hints_explicitly_removes_above_when_not_requested():
    """Not merely "does not add" - it must actively REMOVE.

    _NET_WM_STATE messages are add/remove, never a whole-state assignment, so an
    add-only implementation cannot undo itself. Changing a Dial from
    on_focus_loss="above" to "normal" left the window pinned on top until the app
    was restarted, with the config silently disagreeing with the screen. That is
    what this asserts, and the previous version of this test - "ABOVE is not
    among the atoms sent" - was satisfied by the broken behaviour.
    """
    o, d = ops()
    o.apply_hints(0x500001, above=False)
    assert _above_action(d) == 0                      # 0 = _NET_WM_STATE_REMOVE


def test_apply_hints_never_removes_the_three_unconditional_panel_hints():
    """Sticky/skip-taskbar/skip-pager are what make a Dial a panel at all."""
    for above in (True, False):
        o, d = ops()
        o.apply_hints(0x500001, above=above)
        for ev, _ in d.sent:
            name = d.get_atom_name(ev.data[1][1])
            if name != "_NET_WM_STATE_ABOVE":
                assert ev.data[1][0] == 1, f"{name} must always be added"


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


def test_wm_class_is_the_class_field_not_the_instance():
    """WM_CLASS is (instance, class) and Dials matches on the CLASS field.

    `firefox --class=Dial6` lands in the SECOND field, and on a real desktop
    the two differ for nearly every app - so reading cls[0] would break all 15
    Dials at once while every other test still passed.
    """
    WID = 0x500004
    o, d = ops()

    root_win = _FlakyWindow(o.root.id)
    root_win.get_full_property = lambda atom, prop_type: _Prop([WID])
    d.windows[o.root.id] = root_win
    d.windows[WID] = _FlakyWindow(WID)      # get_wm_class -> ("instance", "cls")

    listed = o.list_windows()
    assert [w.wm_class for w in listed] == ["cls"]
    assert listed[0].wm_class != "instance"


def test_an_unreadable_client_list_is_reported_as_none_not_as_no_windows():
    """`None` from the property read must not collapse into an empty list.

    Two things break if it does: the pruning loop deletes EVERY _first_seen
    entry (resetting every window's identity age, which choose(since=) relies
    on), and handle_slot cannot tell "no windows" from "unknown" - so a Dial
    for a RUNNING app decides to LAUNCH and a second instance appears.
    _NET_CLIENT_LIST is genuinely unset for a moment across a
    `gnome-shell --replace`, so this is not a hypothetical.

    Protecting only the pruning was not enough: the CALLERS need the unknown
    state to be expressible, which is why this returns None rather than [].
    """
    WID = 0x500005
    o, d = ops()

    root_win = _FlakyWindow(o.root.id)
    root_win.get_full_property = lambda atom, prop_type: _Prop([WID])
    d.windows[o.root.id] = root_win
    d.windows[WID] = _FlakyWindow(WID)

    o.list_windows()                        # scan 1: normal
    first = o._first_seen[WID]

    # scan 2: the ROOT window's read fails - the existing _FlakyWindow tests
    # only ever fail the TARGET window, which is why 341 tests missed this.
    def boom(atom, prop_type):
        raise RuntimeError("x server hiccup")

    root_win.get_full_property = boom
    unknown = o.list_windows()
    assert unknown is None, "unknown must be expressible, not collapsed to empty"
    assert unknown != [], "an empty list makes every Dial look unlaunched"
    assert o._first_seen.get(WID) == first, \
        "an unreadable client list wiped a live window's appeared time"

    # scan 3: the read works again and the window is still the same window.
    root_win.get_full_property = lambda atom, prop_type: _Prop([WID])
    listed = o.list_windows()
    assert [w.appeared for w in listed] == [first]
    assert o._first_seen[WID] == first


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

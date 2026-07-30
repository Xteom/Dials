from dials.config import Dial, loads
from dials.daemon import Daemon
from dials.geometry import Monitor, Rect
from dials.panels import HIDE, LAUNCH, RAISE, SHOW
from dials.windows import WindowInfo
from tests.conftest import FakeClock

HDMI = Monitor("HDMI-0", Rect(0, 0, 3440, 1440), primary=False, crtc=63)
ROOT = Rect(0, 0, 3440, 2880)

CONFIG = loads("""
[dials."9"]
label = "Spotify"
match_class = "spotify"
launch = "/snap/bin/spotify"
rect = [0.0, 0.0, 0.5, 1.0]
on_focus_loss = "hide"

[dials."6"]
label = "Firefox"
match_class = "Dial6"
rect = [0.5, 0.0, 0.5, 1.0]
on_focus_loss = "above"
""")


class FakeOps:
    def __init__(self, windows=(), active=None, hidden=()):
        self.windows = list(windows)
        self.active = active
        self.hidden = set(hidden)
        self.calls = []

    def list_windows(self):
        return list(self.windows)

    def active_window(self):
        return self.active

    def is_hidden(self, wid):
        return wid in self.hidden

    def geometry(self, wid):
        return Rect(0, 0, 800, 600)

    def window_name(self, wid):
        return "A Window"

    def apply_geometry(self, wid, rect):
        self.calls.append(("geometry", wid, rect))

    def apply_hints(self, wid, above):
        self.calls.append(("hints", wid, above))

    def activate(self, wid, timestamp):
        self.calls.append(("activate", wid, timestamp))
        self.active = wid
        self.hidden.discard(wid)

    def iconify(self, wid):
        self.calls.append(("iconify", wid))
        self.hidden.add(wid)
        self.active = None


class FakeMonitors:
    def monitors(self):
        return [HDMI]

    def root_rect(self):
        return ROOT

    def invalidate(self):
        pass


def win(wid, cls="spotify"):
    return WindowInfo(wid=wid, wm_class=cls,
                      wtype="_NET_WM_WINDOW_TYPE_NORMAL",
                      override_redirect=False, transient_for=None, appeared=0.0)


def daemon(ops, config=CONFIG, clock=None):
    return Daemon(config=config, ops=ops, monitors=FakeMonitors(),
                  clock=clock or FakeClock(), notifier=lambda *a, **k: True,
                  spawner=lambda cmd: None)


# ---- action dispatch ----------------------------------------------------

def test_missing_window_arms_a_launch_confirmation():
    ops = FakeOps(windows=[])
    d = daemon(ops)
    assert d.handle_slot("9", timestamp=111) == LAUNCH
    assert d.launcher.pending is not None
    assert ops.calls == []      # nothing spawned or touched


def test_hidden_window_is_shown_with_geometry_and_hints():
    ops = FakeOps(windows=[win(5)], hidden=[5])
    d = daemon(ops)
    assert d.handle_slot("9", timestamp=222) == SHOW
    kinds = [c[0] for c in ops.calls]
    assert kinds == ["geometry", "hints", "activate"]
    # Geometry and hints BEFORE activation, so focus lands last.
    assert ("activate", 5, 222) in ops.calls


def test_show_applies_the_configured_rect_for_the_slot():
    ops = FakeOps(windows=[win(5)], hidden=[5])
    daemon(ops).handle_slot("9", timestamp=1)
    geom = [c for c in ops.calls if c[0] == "geometry"][0]
    assert geom[2] == Rect(0, 0, 1720, 1440)      # left half of HDMI-0


def test_visible_but_not_active_window_is_raised_without_geometry():
    ops = FakeOps(windows=[win(5)], active=999)
    d = daemon(ops)
    assert d.handle_slot("9", timestamp=333) == RAISE
    kinds = [c[0] for c in ops.calls]
    assert "geometry" not in kinds
    assert ("activate", 5, 333) in ops.calls


def test_pin_geometry_reapplies_on_raise():
    cfg = loads("""
[dials."9"]
match_class = "spotify"
pin_geometry = true
""")
    ops = FakeOps(windows=[win(5)], active=999)
    daemon(ops, config=cfg).handle_slot("9", timestamp=1)
    assert any(c[0] == "geometry" for c in ops.calls)


def test_active_window_is_iconified():
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops)
    assert d.handle_slot("9", timestamp=444) == HIDE
    assert ("iconify", 5) in ops.calls


def test_above_hint_follows_on_focus_loss_setting():
    ops = FakeOps(windows=[win(7, cls="Dial6")], hidden=[7])
    daemon(ops).handle_slot("6", timestamp=1)      # on_focus_loss = "above"
    hints = [c for c in ops.calls if c[0] == "hints"][0]
    assert hints[2] is True

    ops2 = FakeOps(windows=[win(5)], hidden=[5])
    daemon(ops2).handle_slot("9", timestamp=1)     # on_focus_loss = "hide"
    assert [c for c in ops2.calls if c[0] == "hints"][0][2] is False


def test_an_unbound_slot_is_a_no_op():
    ops = FakeOps(windows=[win(5)])
    d = daemon(ops)
    assert d.handle_slot("1", timestamp=1) is None
    assert ops.calls == []


# ---- focus-loss hiding --------------------------------------------------

def test_hide_dial_hides_when_another_window_takes_focus():
    ops = FakeOps(windows=[win(5)], hidden=[5])
    d = daemon(ops)
    d.handle_slot("9", timestamp=1)              # SHOW -> activating
    d.on_active_window_changed()                 # observed active -> arms
    ops.active = 999                             # focus moves away
    d.on_active_window_changed()
    assert ("iconify", 5) in ops.calls


def test_hide_dial_does_not_hide_itself_during_its_own_show():
    ops = FakeOps(windows=[win(5)], hidden=[5])
    d = daemon(ops)
    d.handle_slot("9", timestamp=1)
    ops.active = 999                             # transient foreign focus
    d.on_active_window_changed()
    assert ("iconify", 5) not in ops.calls


def test_above_dial_is_never_auto_hidden():
    ops = FakeOps(windows=[win(7, cls="Dial6")], hidden=[7])
    d = daemon(ops)
    d.handle_slot("6", timestamp=1)
    d.on_active_window_changed()
    ops.active = 999
    d.on_active_window_changed()
    assert ("iconify", 7) not in ops.calls


def test_showing_a_second_dial_hides_a_hide_dial_like_any_focus_change():
    """Overlap is resolved by on_focus_loss, with no special-casing."""
    ops = FakeOps(windows=[win(5, "spotify"), win(7, "Dial6")], hidden=[5, 7])
    d = daemon(ops)
    d.handle_slot("9", timestamp=1)      # Spotify shown, active
    d.on_active_window_changed()
    d.handle_slot("6", timestamp=2)      # Firefox shown -> steals focus
    d.on_active_window_changed()
    assert ("iconify", 5) in ops.calls


# ---- select() timeout ---------------------------------------------------

def test_idle_select_timeout_is_none():
    """Zero wakeups at idle is a hard budget item."""
    assert daemon(FakeOps()).select_timeout() is None


def test_select_timeout_is_finite_while_a_launch_is_armed():
    d = daemon(FakeOps(windows=[]))
    d.handle_slot("9", timestamp=1)
    t = d.select_timeout()
    assert t is not None and 0 < t <= 5.0


def test_select_timeout_is_finite_while_activating():
    ops = FakeOps(windows=[win(5)], hidden=[5])
    d = daemon(ops)
    d.handle_slot("9", timestamp=1)
    assert d.select_timeout() is not None


def test_select_timeout_returns_to_none_after_activation_settles():
    ops = FakeOps(windows=[win(5)], hidden=[5])
    d = daemon(ops)
    d.handle_slot("9", timestamp=1)
    d.on_active_window_changed()
    assert d.select_timeout() is None


def test_select_timeout_picks_the_nearest_deadline():
    """Two armed deadlines at genuinely different times; the nearer one wins."""
    from dials.assign import Capture
    clk = FakeClock()
    d = daemon(FakeOps(windows=[]), clock=clk)

    d.assign.arm(Capture(1, "x", "x", "HDMI-0", (0, 0, 1, 1), clk.t))
    clk.advance(3.0)                     # assign now expires in 2.0s
    d.handle_slot("9", timestamp=1)      # confirm expires in 5.0s

    t = d.select_timeout()
    assert 1.9 <= t <= 2.1, f"expected the nearer (assign) deadline, got {t}"


def test_pausing_releases_a_pending_launch():
    """A paused daemon must not leave a confirmation Enter could still fire."""
    d = daemon(FakeOps(windows=[]))
    d.handle_slot("9", timestamp=1)
    assert d.launcher.pending is not None
    d.pause()
    d.launcher.cancel()                  # what main()'s pause path does
    assert d.launcher.pending is None
    assert d.select_timeout() is None


# ---- pause / reload ----------------------------------------------------

def test_pause_stops_dispatching_slots():
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops)
    d.pause()
    assert d.paused is True
    assert d.handle_slot("9", timestamp=1) is None
    assert ops.calls == []


def test_resume_restores_dispatching():
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops)
    d.pause()
    d.resume()
    assert d.handle_slot("9", timestamp=1) == HIDE


def test_reload_abandons_in_flight_transitions():
    ops = FakeOps(windows=[win(5)], hidden=[5])
    d = daemon(ops)
    d.handle_slot("9", timestamp=1)
    d.reload(CONFIG)
    ops.active = 999
    d.on_active_window_changed()
    assert ("iconify", 5) not in ops.calls

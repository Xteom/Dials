import pytest

from dials import keys
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


class Notes(list):
    """Recording notifier. Matches `notify(summary, body="", **kw)`."""

    def __call__(self, summary, body="", **kw):
        self.append((summary, body))
        return True

    def mentions(self, needle):
        return any(needle.lower() in (s + " " + b).lower() for s, b in self)


def win(wid, cls="spotify", appeared=0.0):
    return WindowInfo(wid=wid, wm_class=cls,
                      wtype="_NET_WM_WINDOW_TYPE_NORMAL",
                      override_redirect=False, transient_for=None,
                      appeared=appeared)


def daemon(ops, config=CONFIG, clock=None, notifier=None, spawner=None,
           confirm_runner=None):
    # `is None` rather than `or`: an empty Notes() is a falsy list, so `notifier
    # or default` would silently throw the recorder away and every notification
    # assertion would pass vacuously.
    if notifier is None:
        notifier = lambda *a, **k: True          # noqa: E731
    if spawner is None:
        spawner = lambda cmd: None               # noqa: E731
    return Daemon(config=config, ops=ops, monitors=FakeMonitors(),
                  clock=clock if clock is not None else FakeClock(),
                  notifier=notifier, spawner=spawner,
                  confirm_runner=confirm_runner)


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


def test_a_paused_daemon_cannot_arm_a_new_confirmation():
    """Replaces a test that only re-asserted LaunchCoordinator.cancel().

    Pause is the safety valve for a game or remote-desktop session, so the
    invariant that matters is that a paused daemon arms NOTHING - no pending
    confirmation, and therefore no finite select() timeout keeping the process
    awake.
    """
    d = daemon(FakeOps(windows=[]))
    d.pause()
    assert d.dispatch_key(keys.keycode_for("9"), timestamp=1) is None
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


def test_reload_rebuilds_assign_mode_with_the_new_defaults():
    """AssignMode snapshots `defaults` at construction, so reload must rebuild it.

    Otherwise a Dial assigned after SIGHUP silently inherits the PRE-reload
    on_focus_loss / pin_geometry.
    """
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops)
    assert CONFIG.defaults.on_focus_loss == "hide"      # the stale value

    d.reload(loads("""
[defaults]
on_focus_loss = "above"
pin_geometry = true
"""))
    d.arm_assign()
    bound = d.assign.resolve("7", None)
    assert bound.on_focus_loss == "above"
    assert bound.pin_geometry is True


def test_reload_cancels_a_pending_launch_so_the_return_grab_is_dropped():
    """A leaked global `Return` grab takes Enter away from EVERY application.

    main() holds the temporary Enter grabs for exactly as long as
    grabs_needed() is True, so reload() dropping the pending launch is what
    releases them. Removing launcher.cancel() from reload() survived the whole
    suite before this test existed.
    """
    d = daemon(FakeOps(windows=[]))
    d.handle_slot("9", timestamp=1)
    assert d.launcher.grabs_needed(False) is True

    d.reload(CONFIG)

    assert d.launcher.pending is None
    assert d.launcher.grabs_needed(False) is False, \
        "the temporary global Return grab would be held forever"
    assert d.select_timeout() is None


def test_a_malformed_config_on_reload_keeps_the_last_good_one_and_notifies():
    """SIGHUP with a broken hand-edited config must not kill the daemon.

    If the ConfigError escaped, the process would exit non-zero, systemd would
    restart it, the startup load() would raise the same error, and the default
    5-starts-in-10s limit would leave the unit permanently `failed` - i.e. one
    typo would take the numpad layer down until a human noticed.
    """
    from dials.config import ConfigError

    notes = Notes()
    d = daemon(FakeOps(windows=[win(5)]), notifier=notes)
    before = d.config

    def broken():
        raise ConfigError("dials.'9': rect w and h must be greater than 0")

    assert d.reload_from(broken) is False       # must NOT propagate
    assert d.config is before, "the last-good config was thrown away"
    assert notes.mentions("not reloaded")
    assert notes.mentions("keeping the previous config")


def test_a_good_config_on_reload_is_adopted():
    """The other half: reload_from is still a real reload when the file parses."""
    d = daemon(FakeOps(windows=[win(5)]))
    new = loads("""
[dials."9"]
match_class = "somethingelse"
""")
    assert d.reload_from(lambda: new) is True
    assert d.config is new


def test_reload_does_not_leave_a_stale_capture_armed():
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops)
    d.arm_assign()
    assert d.assign.armed is True
    d.reload(CONFIG)
    assert d.assign.armed is False


# ---- assign mode --------------------------------------------------------

def test_arm_assign_snapshots_the_active_window():
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops)
    d.arm_assign()
    assert d.assign.armed is True

    bound = d.assign.resolve("7", None)
    assert bound.slot == "7"
    assert bound.match_class == "spotify"       # from ops.list_windows()
    assert bound.label == "A Window"            # from ops.window_name()
    assert bound.monitor == "HDMI-0"            # from FakeMonitors()
    # ops.geometry() is 800x600 at 0,0 on a 3440x1440 monitor.
    assert bound.rect == (0.0, 0.0, 0.2326, 0.4167)


def test_arm_assign_without_an_active_window_notifies_and_does_not_arm():
    notes = Notes()
    d = daemon(FakeOps(windows=[], active=None), notifier=notes)
    d.arm_assign()
    assert d.assign.armed is False
    assert notes.mentions("no active window")


def test_arm_assign_ignores_an_unmanageable_active_window():
    """_NET_ACTIVE_WINDOW can name a window absent from _NET_CLIENT_LIST."""
    notes = Notes()
    d = daemon(FakeOps(windows=[win(5)], active=4242), notifier=notes)
    d.arm_assign()
    assert d.assign.armed is False
    assert notes.mentions("not manageable")


def test_pressing_the_assign_key_again_cancels():
    notes = Notes()
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops, notifier=notes)
    d.arm_assign()
    d.arm_assign()
    assert d.assign.armed is False
    assert notes.mentions("cancelled")


def test_handle_assign_key_persists_an_empty_slot(monkeypatch):
    """Exercises the LAZY import of the TOML writer inside _persist."""
    import dials.configwrite as cw
    saved = []
    monkeypatch.setattr(cw, "upsert_dial",
                        lambda path, dial: saved.append(dial) or CONFIG)

    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops)
    d.arm_assign()
    d.handle_assign_key("7")                    # slot 7 is unbound in CONFIG
    assert [dl.slot for dl in saved] == ["7"]
    assert saved[0].match_class == "spotify"


def test_handle_assign_key_on_an_occupied_slot_confirms_and_saves_nothing(
        monkeypatch):
    import dials.configwrite as cw
    monkeypatch.setattr(cw, "upsert_dial", lambda path, dial: pytest.fail(
        "an occupied slot must not be overwritten without confirmation"))

    asked = []
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops, confirm_runner=asked.append)
    d.arm_assign()
    d.handle_assign_key("9")                    # slot 9 is Spotify in CONFIG
    assert len(asked) == 1
    assert asked[0].existing.label == "Spotify"
    assert asked[0].incoming.slot == "9"


def test_handle_assign_key_without_a_capture_notifies_and_does_not_raise():
    notes = Notes()
    d = daemon(FakeOps(windows=[win(5)], active=5), notifier=notes)
    d.handle_assign_key("7")                    # never armed
    assert notes.mentions("assign failed")


def test_handle_assign_key_rejects_the_reserved_assign_slot():
    notes = Notes()
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops, notifier=notes)
    d.arm_assign()
    d.handle_assign_key(".")
    assert notes.mentions("reserved")


def test_a_failing_toml_writer_notifies_instead_of_raising(monkeypatch):
    """`_persist` is a keypress path: nothing there may kill the daemon."""
    import dials.configwrite as cw

    def boom(path, dial):
        raise OSError("disk on fire")

    monkeypatch.setattr(cw, "upsert_dial", boom)
    notes = Notes()
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops, notifier=notes)
    d.arm_assign()
    d.handle_assign_key("7")
    assert notes.mentions("could not save")


# ---- key dispatch ordering ----------------------------------------------

def test_the_confirm_key_confirms_instead_of_firing_its_own_dial():
    """KP_Enter is BOTH a confirm key and slot "enter".

    `is_confirm` therefore has to be checked before `slot_for`, or Enter would
    dispatch its own (here unbound) Dial and the launch could never be
    confirmed.
    """
    spawned = []
    d = daemon(FakeOps(windows=[]), spawner=spawned.append)
    assert d.dispatch_key(keys.keycode_for("9"), timestamp=10) == LAUNCH

    assert d.dispatch_key(keys.KP_ENTER_KEYCODE, timestamp=20) == "confirm"
    assert spawned == ["/snap/bin/spotify"]
    assert d.launcher.pending.launched_at is not None


def test_main_return_also_confirms_a_pending_launch():
    spawned = []
    d = daemon(FakeOps(windows=[]), spawner=spawned.append)
    d.dispatch_key(keys.keycode_for("9"), timestamp=10)
    assert d.dispatch_key(keys.RETURN_KEYCODE, timestamp=20) == "confirm"
    assert spawned == ["/snap/bin/spotify"]


def test_autorepeat_is_swallowed_at_dispatch():
    """Holding a Dial key must not toggle it repeatedly."""
    ops = FakeOps(windows=[win(5)], hidden=[5])
    clk = FakeClock()
    d = daemon(ops, clock=clk)
    kc = keys.keycode_for("9")

    assert d.dispatch_key(kc, timestamp=1) == SHOW
    assert d.dispatch_key(kc, timestamp=2) is None      # autorepeat, swallowed
    clk.advance(0.5)
    assert d.dispatch_key(kc, timestamp=3) == HIDE      # a real second press


def test_an_unknown_keycode_is_ignored():
    d = daemon(FakeOps(windows=[win(5)]))
    assert d.dispatch_key(9999, timestamp=1) is None


def test_the_assign_key_arms_assign_mode_through_dispatch():
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops)
    assert d.dispatch_key(keys.keycode_for("."), timestamp=1) == "assign"
    assert d.assign.armed is True


def test_a_dial_key_binds_rather_than_toggles_while_assign_is_armed():
    asked = []
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops, confirm_runner=asked.append)
    d.dispatch_key(keys.keycode_for("."), timestamp=1)
    assert d.dispatch_key(keys.keycode_for("9"), timestamp=2) == "bind"
    assert len(asked) == 1                  # went to assign, not to the Dial
    assert ops.calls == []                  # Dial 9 was NOT toggled


# ---- post-launch activation --------------------------------------------

def test_post_launch_activation_uses_the_confirming_keypress_timestamp():
    """CurrentTime (0) is ruled out: EWMH wants a real user-activity time."""
    clk = FakeClock()
    ops = FakeOps(windows=[])
    d = daemon(ops, clock=clk)
    d.dispatch_key(keys.keycode_for("9"), timestamp=10)
    d.dispatch_key(keys.RETURN_KEYCODE, timestamp=4242)

    # The launched window appears after launched_at.
    ops.windows = [win(11, appeared=clk.t + 1.0)]
    d.on_active_window_changed()

    assert ("activate", 11, 4242) in ops.calls
    assert ("activate", 11, 0) not in ops.calls
    assert d.launcher.pending is None       # stopped waiting


def test_a_launched_window_denied_focus_is_still_adopted():
    """The case focus-stealing prevention makes NORMAL, not exceptional.

    The launched window maps but never becomes _NET_ACTIVE_WINDOW, so
    on_active_window_changed() is never called for it. Without the
    _NET_CLIENT_LIST route nothing adopts it: no geometry, no hints, no
    activation, and 10s later a notification claims it never appeared.
    """
    clk = FakeClock()
    ops = FakeOps(windows=[])
    d = daemon(ops, clock=clk)
    d.dispatch_key(keys.keycode_for("9"), timestamp=10)
    d.dispatch_key(keys.RETURN_KEYCODE, timestamp=4242)

    ops.windows = [win(11, appeared=clk.t + 1.0)]
    ops.active = 777                    # a FOREIGN window kept the focus
    d.on_client_list_changed()

    kinds = [c[0] for c in ops.calls]
    assert kinds == ["geometry", "hints", "activate"]
    assert ("activate", 11, 4242) in ops.calls
    assert d.launcher.pending is None, "the waiter must stop waiting"


def test_a_failed_post_launch_activation_leaves_the_launch_armed(caplog):
    """window_found() must fire only on success.

    Clearing `pending` before activating orphans the window a second way: the
    Dial is never activated and the waiter has already forgotten it, so no
    later event can retry inside the 10s deadline.
    """
    clk = FakeClock()
    ops = FakeOps(windows=[])
    d = daemon(ops, clock=clk)
    d.dispatch_key(keys.keycode_for("9"), timestamp=10)
    d.dispatch_key(keys.RETURN_KEYCODE, timestamp=20)

    def boom(wid, timestamp):
        raise RuntimeError("activation refused")

    ops.activate = boom
    ops.windows = [win(11, appeared=clk.t + 1.0)]

    with caplog.at_level("WARNING"):
        d.on_client_list_changed()      # must not raise

    assert d.launcher.pending is not None, "a retry must still be possible"
    assert any("adoption failed" in r.getMessage() for r in caplog.records)

    # ...and the retry succeeds once activation works again.
    ops.activate = lambda wid, timestamp: ops.calls.append(
        ("activate", wid, timestamp))
    d.on_client_list_changed()
    assert ("activate", 11, 20) in ops.calls
    assert d.launcher.pending is None


def test_a_client_list_read_failure_is_swallowed():
    """on_client_list_changed runs on every window map: it may never raise."""
    ops = FakeOps(windows=[])

    def boom():
        raise RuntimeError("x server hiccup")

    ops.list_windows = boom
    daemon(ops).on_client_list_changed()


def test_a_pre_existing_window_is_not_mistaken_for_the_launched_one():
    clk = FakeClock()
    ops = FakeOps(windows=[])
    d = daemon(ops, clock=clk)
    d.dispatch_key(keys.keycode_for("9"), timestamp=10)
    d.dispatch_key(keys.RETURN_KEYCODE, timestamp=20)

    ops.windows = [win(11, appeared=clk.t - 5.0)]      # older than the launch
    d.on_active_window_changed()
    assert ops.calls == []
    assert d.launcher.pending is not None              # still waiting


# ---- the temporary Enter grabs ------------------------------------------

class FakeGrabs:
    """Stands in for GrabManager. `fail` lists keycodes whose grabs all fail."""

    def __init__(self, fail=(), masks=(0, 2)):
        self.fail = set(fail)
        self.masks = list(masks)
        self.installed: list[tuple[int, int]] = []
        self.removed = 0

    def install(self, keycodes):
        failures: dict[int, list[int]] = {}
        for kc in keycodes:
            for mask in self.masks:
                if kc in self.fail:
                    failures.setdefault(kc, []).append(mask)
                else:
                    self.installed.append((kc, mask))
        return failures

    @property
    def active(self):
        return bool(self.installed)

    def remove_all(self):
        self.removed += 1
        self.installed.clear()


def test_a_failed_return_grab_cancels_the_launch_and_notifies():
    """Both Enter keys ungrabbable: never leave a confirmation nobody can reach.

    This branch decides whether a temporary GLOBAL `Return` grab is abandoned
    or held, which is the most intrusive thing in the design - and it was
    untested.
    """
    from dials.daemon import _install_confirm_grabs

    notes = Notes()
    d = daemon(FakeOps(windows=[]))
    d.handle_slot("9", timestamp=1)
    assert d.launcher.pending is not None

    grabs = FakeGrabs(fail=(keys.RETURN_KEYCODE, keys.KP_ENTER_KEYCODE))
    held = _install_confirm_grabs(d.launcher, lambda: grabs, notifier=notes)

    assert held is None, "nothing was grabbed, so nothing may be held"
    assert grabs.removed == 1
    assert d.launcher.pending is None, "the launch must be cancelled"
    assert notes.mentions("return/enter")
    assert notes.mentions("cancelled")


def test_a_failed_kp_enter_grab_still_keeps_the_launch_and_reports_it(caplog):
    """KP_Enter ALWAYS fails: it is already grabbed as Dial slot "enter".

    So the guard must ask "did anything at all get grabbed" rather than count
    keycodes, or every single confirmation would be spuriously abandoned. The
    failure is still reported, since otherwise an operator cannot tell why
    Enter did not work.
    """
    from dials.daemon import _install_confirm_grabs

    notes = Notes()
    d = daemon(FakeOps(windows=[]))
    d.handle_slot("9", timestamp=1)

    grabs = FakeGrabs(fail=(keys.KP_ENTER_KEYCODE,))
    with caplog.at_level("WARNING"):
        held = _install_confirm_grabs(d.launcher, lambda: grabs, notifier=notes)

    assert held is grabs
    assert d.launcher.pending is not None, "Return could still confirm this"
    assert notes == [], "nothing to tell the user: Return was grabbed"
    assert any(str(keys.KP_ENTER_KEYCODE) in r.getMessage()
               for r in caplog.records), "the failure was not reported"


def test_handle_slot_logs_when_it_swallows(caplog):
    """The most-executed swallow in the project; the spec says "logged"."""
    ops = FakeOps(windows=[win(5)])

    def boom():
        raise RuntimeError("x server hiccup")

    ops.list_windows = boom
    with caplog.at_level("ERROR"):
        assert daemon(ops).handle_slot("9", timestamp=1) is None
    assert any("handle_slot failed" in r.getMessage() for r in caplog.records)


# ---- tick ---------------------------------------------------------------

def test_tick_expires_an_armed_confirmation():
    clk = FakeClock()
    d = daemon(FakeOps(windows=[]), clock=clk)
    d.handle_slot("9", timestamp=1)
    assert d.launcher.pending is not None

    clk.advance(5.0)
    d.tick()
    assert d.launcher.pending is None
    assert d.select_timeout() is None       # back to an infinite block


def test_tick_expires_an_unanswered_activation():
    ops = FakeOps(windows=[win(5)], hidden=[5])
    clk = FakeClock()
    d = daemon(ops, clock=clk)
    d.handle_slot("9", timestamp=1)
    assert d.select_timeout() is not None

    clk.advance(1.5)
    d.tick()
    assert d.select_timeout() is None


def test_tick_expires_an_armed_assign_capture():
    from dials.assign import Capture
    clk = FakeClock()
    d = daemon(FakeOps(windows=[]), clock=clk)
    d.assign.arm(Capture(1, "x", "x", "HDMI-0", (0, 0, 1, 1), clk.t))
    clk.advance(5.0)
    d.tick()
    assert d.assign.armed is False
    assert d.select_timeout() is None

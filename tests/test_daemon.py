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
    """Stands in for GrabManager.

    `fail` lists keycodes whose grabs fail on every mask; `fail_pairs` lists
    individual (keycode, mask) pairs that fail.

    Records what it was ASKED to grab and what it later UNGRABBED, because that
    is exactly what the KP_Enter defect was about: a keycode the temporary
    manager should never have been handed ended up in its installed set, and
    `remove_all()` then ungrabbed the daemon's permanent grab on it. The real
    GrabManager.remove_all() ungrabs precisely its `_installed` pairs, so
    mirroring that here is what gives these assertions teeth.
    """

    def __init__(self, fail=(), fail_pairs=(), masks=(0, 2)):
        self.fail = set(fail)
        self.fail_pairs = set(fail_pairs)
        self.masks = list(masks)
        self.asked: list[int] = []
        self.installed: list[tuple[int, int]] = []
        self.ungrabbed: list[tuple[int, int]] = []
        self.removed = 0

    def install(self, keycodes):
        failures: dict[int, list[int]] = {}
        for kc in keycodes:
            self.asked.append(kc)
            for mask in self.masks:
                if kc in self.fail or (kc, mask) in self.fail_pairs:
                    failures.setdefault(kc, []).append(mask)
                else:
                    self.installed.append((kc, mask))
        return failures

    @property
    def active(self):
        return bool(self.installed)

    def remove_all(self):
        self.removed += 1
        self.ungrabbed.extend(self.installed)
        self.installed.clear()


def test_a_failed_return_grab_cancels_the_launch_and_notifies():
    """`Return` ungrabbable: say so rather than arm a confirmation silently.

    This branch decides whether a temporary GLOBAL `Return` grab is abandoned
    or held, which is the most intrusive thing in the design - and it was
    untested.
    """
    from dials.daemon import _install_confirm_grabs

    notes = Notes()
    d = daemon(FakeOps(windows=[]))
    d.handle_slot("9", timestamp=1)
    assert d.launcher.pending is not None

    grabs = FakeGrabs(fail=(keys.RETURN_KEYCODE,))
    held = _install_confirm_grabs(d.launcher, lambda: grabs, notifier=notes)

    assert held is None, "nothing was grabbed, so nothing may be held"
    assert grabs.removed == 1
    assert d.launcher.pending is None, "the launch must be cancelled"
    assert notes.mentions("return/enter")
    assert notes.mentions("cancelled")


def test_only_return_is_temporarily_grabbed_never_kp_enter():
    """The temporary manager must be handed `Return` and nothing else.

    Replaces a test that asserted the opposite. The old code grabbed
    confirm_keycodes() - both Enter keys - on the belief that keycode 104 would
    always fail with BadAccess because the daemon already held it permanently.
    docs/probes/09-duplicate-grab-same-client.py measures the truth: a duplicate
    grab from the SAME client succeeds silently (BadAccess is cross-client only),
    so 104 really did land in the temporary manager.
    """
    from dials.daemon import _install_confirm_grabs

    notes = Notes()
    d = daemon(FakeOps(windows=[]))
    d.handle_slot("9", timestamp=1)

    grabs = FakeGrabs()
    held = _install_confirm_grabs(d.launcher, lambda: grabs, notifier=notes)

    assert held is grabs
    assert grabs.asked == [keys.RETURN_KEYCODE]
    assert keys.KP_ENTER_KEYCODE not in grabs.asked, \
        "KP_Enter must never be handed to the temporary manager"
    assert {kc for kc, _ in grabs.installed} == {keys.RETURN_KEYCODE}
    assert notes == []


def test_releasing_the_confirm_grabs_cannot_ungrab_the_enter_dial():
    """The actual C1 failure: release_confirm_grabs() killed keycode 104.

    Every confirmation resolves - confirmed, timed out or cancelled - and each
    resolution calls remove_all(). If keycode 104 is in the temporary manager's
    installed set, that ungrabs the daemon's PERMANENT grab, the `enter` Dial
    goes dead and KP_Enter falls through to whatever is focused, silently and
    until the next pause/resume or restart.
    """
    from dials.daemon import _install_confirm_grabs

    d = daemon(FakeOps(windows=[]))
    d.handle_slot("9", timestamp=1)

    grabs = FakeGrabs()
    held = _install_confirm_grabs(d.launcher, lambda: grabs, notifier=Notes())
    held.remove_all()                       # what release_confirm_grabs() does

    assert grabs.ungrabbed, "the temporary grab must actually be released"
    assert all(kc != keys.KP_ENTER_KEYCODE for kc, _ in grabs.ungrabbed), \
        "the permanent KP_Enter grab was ungrabbed by the confirm-grab cycle"


def test_the_temporary_grab_set_never_overlaps_the_permanent_one():
    """The guard that fires if KP_Enter is ever put back into the grab set.

    Stated as the general invariant rather than as "not 104": ANY permanently
    grabbed Dial keycode appearing in the temporary set would be ungrabbed out
    from under the daemon by the next remove_all().
    """
    from dials.launcher import LaunchCoordinator

    temporary = set(LaunchCoordinator(notifier=lambda *a, **k: True)
                    .grab_keycodes())
    permanent = set(keys.SLOT_KEYCODES.values())

    assert temporary == {keys.RETURN_KEYCODE}
    assert temporary & permanent == set(), \
        f"temporarily grabbing {sorted(temporary & permanent)} would delete " \
        "the daemon's permanent grab on it"


def test_kp_enter_still_confirms_although_it_is_never_temporarily_grabbed():
    """The other half: narrowing the GRAB set must not narrow what CONFIRMS.

    KP_Enter needs no temporary grab precisely because the permanent Dial grab
    already delivers it - so it must still reach confirm_launch().
    """
    spawned = []
    d = daemon(FakeOps(windows=[]), spawner=spawned.append)
    d.dispatch_key(keys.keycode_for("9"), timestamp=10)

    assert keys.KP_ENTER_KEYCODE not in d.launcher.grab_keycodes()
    assert d.launcher.is_confirm(keys.KP_ENTER_KEYCODE) is True
    assert d.dispatch_key(keys.KP_ENTER_KEYCODE, timestamp=20) == "confirm"
    assert spawned == ["/snap/bin/spotify"]


def test_a_partly_grabbed_return_keeps_the_launch_and_reports_the_gap(caplog):
    """Return grabbed for mask 0 but not LockMask still confirms with Caps off.

    So the abandon guard must ask "did anything at all get grabbed" rather than
    "did every mask succeed", or a confirmation Return could still deliver would
    be spuriously thrown away. The gap is still reported, since otherwise an
    operator cannot tell why Enter did not work with CapsLock on.
    """
    from dials.daemon import _install_confirm_grabs

    notes = Notes()
    d = daemon(FakeOps(windows=[]))
    d.handle_slot("9", timestamp=1)

    grabs = FakeGrabs(fail_pairs=((keys.RETURN_KEYCODE, 2),))
    with caplog.at_level("WARNING"):
        held = _install_confirm_grabs(d.launcher, lambda: grabs, notifier=notes)

    assert held is grabs
    assert grabs.installed == [(keys.RETURN_KEYCODE, 0)]
    assert d.launcher.pending is not None, "Return on mask 0 could still confirm"
    assert notes == [], "nothing to tell the user: Return was grabbed"
    assert any("confirm-grab failures" in r.getMessage() for r in caplog.records), \
        "the partial failure was not reported"


def test_handle_slot_logs_when_it_swallows(caplog):
    """The most-executed swallow in the project; the spec says "logged"."""
    ops = FakeOps(windows=[win(5)])

    def boom():
        raise RuntimeError("x server hiccup")

    ops.list_windows = boom
    with caplog.at_level("ERROR"):
        assert daemon(ops).handle_slot("9", timestamp=1) is None
    assert any("handle_slot failed" in r.getMessage() for r in caplog.records)


# ---- the event drain ----------------------------------------------------

class FakeXQueue:
    """A python-xlib Display stand-in for `_drain_events`.

    `pending_events()` reports the queue length; the real one drains the socket
    first and then reports the resulting EVENT count, which is exactly why
    re-asking on every iteration is safe rather than wasteful.

    `next_event()` on an EMPTY queue raises loudly instead of returning something
    plausible, because the real one BLOCKS INDEFINITELY there - that is what made
    the earlier `range(max(1, pending_events))` idea unusable, and a fake that
    quietly returned None would hide a reintroduction of it.
    """

    def __init__(self, events=()):
        self.queue = list(events)
        self.polls = 0
        self.reads = 0

    def pending_events(self):
        self.polls += 1
        return len(self.queue)

    def next_event(self):
        self.reads += 1
        if not self.queue:
            raise AssertionError(
                "next_event() on an empty queue: the real one blocks forever")
        return self.queue.pop(0)


def test_the_drain_processes_events_that_arrive_during_a_handler():
    """The starvation this replaced a snapshot count to fix.

    Handlers make dozens of synchronous round trips (list_windows() is ~5 per
    window) and python-xlib queues whatever events arrive while it reads those
    replies. With a snapshot count those events are not processed, the socket is
    already drained so select() does not fire again, and at idle select() blocks
    with a None timeout - so an on_focus_loss="hide" panel stays up indefinitely.
    """
    from dials.daemon import _drain_events

    q = FakeXQueue(["focus-change"])
    seen = []

    def handle(event):
        seen.append(event)
        if event == "focus-change":
            # What the handler's own round trips do: queue more events.
            q.queue.append("client-list-change")

    drained, closed = _drain_events(q, handle)

    assert seen == ["focus-change", "client-list-change"], \
        "an event queued during a handler was starved"
    assert (drained, closed) == (2, False)


def test_the_drain_never_reads_from_an_empty_queue():
    """next_event() on an empty queue blocks forever; the loop must not go there.

    This is what separates draining-while-non-empty from the rejected
    range(max(1, pending_events)) change.
    """
    from dials.daemon import _drain_events

    q = FakeXQueue([])
    drained, closed = _drain_events(q, lambda event: pytest.fail(
        "there was nothing to handle"))

    assert (drained, closed) == (0, False)
    assert q.reads == 0, "next_event() was called with an empty queue"


def test_the_drain_re_asks_the_queue_rather_than_trusting_one_count():
    """One poll per read, plus the final poll that finds the queue empty."""
    from dials.daemon import _drain_events

    q = FakeXQueue(["a", "b", "c"])
    _drain_events(q, lambda event: None)
    assert q.polls == 4
    assert q.reads == 3


def test_the_drain_count_is_what_keeps_the_dead_connection_guard_honest():
    """main() presumes the connection dead after MAX_DEAD_READY eventless
    wakeups, and now counts what the drain returns. So an eventless wakeup must
    still report 0 - otherwise a peer-closed socket spins at 100% CPU forever -
    and any event at all must report non-zero, or a busy session would eventually
    exit for no reason."""
    from dials.daemon import _drain_events

    assert _drain_events(FakeXQueue([]), lambda event: None) == (0, False)
    assert _drain_events(FakeXQueue(["a", "b"]), lambda event: None) == (2, False)


def test_a_closed_connection_is_reported_rather_than_raised():
    """main() turns the flag into `exit 1` so systemd restarts with the session."""
    from Xlib.error import ConnectionClosedError

    from dials.daemon import _drain_events

    class Closed:
        def pending_events(self):
            raise ConnectionClosedError("server")

        def next_event(self):
            raise AssertionError("must not be reached")

    assert _drain_events(Closed(), lambda event: None) == (0, True)


def test_a_connection_closed_mid_drain_keeps_the_events_already_processed():
    from Xlib.error import ConnectionClosedError

    from dials.daemon import _drain_events

    class ClosesAfterOne(FakeXQueue):
        def next_event(self):
            if self.reads == 1:
                raise ConnectionClosedError("server")
            return super().next_event()

    seen = []
    assert _drain_events(ClosesAfterOne(["a", "b"]), seen.append) == (1, True)
    assert seen == ["a"]


def test_an_ordinary_read_failure_returns_to_select_and_is_logged(caplog):
    """A non-fatal read error must not spin: return and let select() decide."""
    from dials.daemon import _drain_events

    class Flaky:
        def pending_events(self):
            return 1

        def next_event(self):
            raise RuntimeError("x server hiccup")

    with caplog.at_level("WARNING"):
        assert _drain_events(Flaky(), lambda event: None) == (0, False)
    assert any("event read failed" in r.getMessage() for r in caplog.records)


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

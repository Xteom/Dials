import io

import pytest

from dials.cli import Deps, main
from dials.config import Config, Defaults, Dial
from dials.geometry import Monitor, Rect
from dials.windows import WindowInfo

DEFAULT_MONITOR = Monitor(name="HDMI-0", rect=Rect(0, 0, 1920, 1080),
                          primary=True, crtc=1)


def dial(slot="9", cls="spotify"):
    return Dial(slot=slot, label="Spotify", match_class=cls, launch="spotify",
                icon="", monitor="HDMI-0", rect=(0.0, 0.0, 0.5, 1.0),
                on_focus_loss="hide", pin_geometry=False)


def deps(dials=None, paused=False, windows=(), active=None,
         geometry=Rect(100, 100, 800, 600), monitor=DEFAULT_MONITOR):
    state = {
        "config": Config(defaults=Defaults(), dials=dials or {}),
        "paused": paused,
        "signals": [],
        "exports": 0,
        "upserts": 0,
    }
    out = io.StringIO()

    def _load(path=None):
        return state["config"]

    def _upsert(path, d):
        state["upserts"] += 1
        state["config"] = Config(state["config"].defaults,
                                 {**state["config"].dials, d.slot: d})
        return state["config"]

    def _remove(path, slot):
        state["config"] = Config(
            state["config"].defaults,
            {s: v for s, v in state["config"].dials.items() if s != slot},
        )
        return state["config"]

    def _export(live=None, ref=None):
        state["exports"] += 1
        return "/repo/config/config.reference.toml"

    d = Deps(
        load=_load, upsert=_upsert, remove=_remove,
        export=_export, differs=lambda live=None, ref=None: True,
        pause_get=lambda: state["paused"],
        pause_set=lambda v: state.__setitem__("paused", v),
        signal_daemon=lambda: state["signals"].append("HUP") or True,
        list_windows=lambda: list(windows),
        active_window=lambda: active,
        window_geometry=lambda wid: geometry,
        monitor_for=lambda rect: monitor,
        out=out,
    )
    return d, state, out


def test_list_renders_all_fifteen_bindable_slots():
    from dials import keys
    d, _, out = deps({})
    main(["list"], deps=d)
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1 + len(keys.BINDABLE_SLOTS)      # header + one row per slot
    for slot in keys.BINDABLE_SLOTS:
        assert any(ln.split()[0] == slot for ln in lines[1:]), f"slot {slot} missing"


def test_list_marks_every_unbound_slot():
    """Header contains a hyphen, so never assert on '-' - it can never fail."""
    d, _, out = deps({"9": dial()})
    main(["list"], deps=d)
    text = out.getvalue()
    assert text.lower().count("(unbound)") == 14   # 15 bindable slots minus one bound
    assert "Spotify" in text


def test_unbind_removes_the_slot_and_signals_the_daemon():
    d, state, _ = deps({"9": dial()})
    assert main(["unbind", "9"], deps=d) == 0
    assert "9" not in state["config"].dials
    assert state["signals"] == ["HUP"]


def test_unbind_rejects_the_reserved_slot():
    d, _, out = deps({})
    assert main(["unbind", "."], deps=d) != 0
    assert "reserved" in out.getvalue().lower()


def test_unbind_rejects_an_unknown_slot():
    d, _, _ = deps({})
    assert main(["unbind", "99"], deps=d) != 0


def test_capture_updates_the_dial_from_the_matched_window():
    win = WindowInfo(wid=42, wm_class="spotify",
                     wtype="_NET_WM_WINDOW_TYPE_NORMAL",
                     override_redirect=False, transient_for=None, appeared=0.0)
    rect = Rect(960, 0, 960, 1080)
    d, state, _ = deps({"9": dial()}, windows=(win,), active=42,
                       geometry=rect, monitor=DEFAULT_MONITOR)
    assert main(["capture", "9"], deps=d) == 0
    from dials.assign import derive_rect
    updated = state["config"].dials["9"]
    assert updated.monitor == DEFAULT_MONITOR.name
    assert updated.rect == derive_rect(rect, DEFAULT_MONITOR)
    assert state["signals"] == ["HUP"]
    assert state["upserts"] == 1


def test_capture_reports_no_matching_window():
    d, state, out = deps({"9": dial()}, windows=(), active=None)
    assert main(["capture", "9"], deps=d) == 1
    assert state["upserts"] == 0
    assert "no window" in out.getvalue().lower()


def test_capture_distinguishes_an_unreadable_window_list_from_no_match():
    """`None` means "could not read", and capture WRITES the config, so guessing
    "no window matching class" both lies and risks a persistent wrong rect."""
    d, state, out = deps({"9": dial()}, windows=(), active=None)
    d.list_windows = lambda: None
    assert main(["capture", "9"], deps=d) == 1
    assert state["upserts"] == 0
    text = out.getvalue().lower()
    assert "could not read" in text
    assert "no window matching" not in text


def test_capture_rejects_an_unbound_slot():
    d, state, _ = deps({})
    assert main(["capture", "9"], deps=d) == 2
    assert state["upserts"] == 0


def test_capture_rejects_the_reserved_slot():
    d, state, out = deps({})
    assert main(["capture", "."], deps=d) == 2
    assert "reserved" in out.getvalue().lower()
    assert state["upserts"] == 0


def test_capture_falls_back_to_a_default_rect_when_geometry_is_unavailable():
    win = WindowInfo(wid=42, wm_class="spotify",
                     wtype="_NET_WM_WINDOW_TYPE_NORMAL",
                     override_redirect=False, transient_for=None, appeared=0.0)
    d, state, _ = deps({"9": dial()}, windows=(win,), active=42,
                       geometry=None, monitor=DEFAULT_MONITOR)
    assert main(["capture", "9"], deps=d) == 0
    from dials.assign import derive_rect
    updated = state["config"].dials["9"]
    assert updated.rect == derive_rect(Rect(0, 0, 800, 600), DEFAULT_MONITOR)
    assert state["upserts"] == 1


def test_pause_sets_the_flag_and_signals():
    d, state, _ = deps({})
    assert main(["pause"], deps=d) == 0
    assert state["paused"] is True
    assert state["signals"] == ["HUP"]


def test_resume_clears_the_flag_and_signals():
    d, state, _ = deps({}, paused=True)
    assert main(["resume"], deps=d) == 0
    assert state["paused"] is False
    assert state["signals"] == ["HUP"]


def test_pause_reports_failure_when_the_daemon_could_not_be_signalled():
    """Pause is the safety valve for a game or remote-desktop session.

    If the signal never lands, a running daemon still holds all 32 grabs - so
    printing "the numpad now behaves normally" and exiting 0 would be a lie
    about the one thing this command exists to guarantee.
    """
    d, state, out = deps({})
    d.signal_daemon = lambda: False
    assert main(["pause"], deps=d) == 1
    assert state["paused"] is True              # the flag still persists
    text = out.getvalue().lower()
    assert "could not signal" in text
    assert "behaves normally" not in text


def test_resume_reports_failure_when_the_daemon_could_not_be_signalled():
    d, state, out = deps({}, paused=True)
    d.signal_daemon = lambda: False
    assert main(["resume"], deps=d) == 1
    assert state["paused"] is False
    assert "could not signal" in out.getvalue().lower()
    assert "still paused" in out.getvalue().lower()


def test_status_reports_paused_state():
    d, _, out = deps({"9": dial()}, paused=True)
    assert main(["status"], deps=d) == 0
    assert "paused" in out.getvalue().lower()


def test_status_counts_bound_dials():
    d, _, out = deps({"9": dial(), "6": dial("6", "Dial6")})
    main(["status"], deps=d)
    assert "2" in out.getvalue()


def test_config_prints_both_paths_and_flags_drift():
    d, _, out = deps({})
    assert main(["config"], deps=d) == 0
    text = out.getvalue()
    assert "config.toml" in text
    assert "reference" in text.lower()
    assert "differ" in text.lower()


def test_config_export_refreshes_the_snapshot():
    d, state, out = deps({"9": dial()})
    assert main(["config", "export"], deps=d) == 0
    assert state["exports"] == 1


def test_reload_signals_the_daemon():
    d, state, _ = deps({})
    assert main(["reload"], deps=d) == 0
    assert state["signals"] == ["HUP"]


def test_no_arguments_requests_the_tui():
    d, _, _ = deps({})
    called = []
    assert main([], deps=d, tui=lambda cfg: called.append(True) or 0) == 0
    assert called == [True]


def test_unknown_command_exits_nonzero():
    d, _, _ = deps({})
    with pytest.raises(SystemExit):
        main(["frobnicate"], deps=d)

import pytest

from dials.config import Dial
from dials.launcher import (
    CONFIRM_TIMEOUT, LAUNCH_WAIT_TIMEOUT, LaunchCoordinator,
)
from dials import keys
from tests.conftest import FakeClock


def dial(slot="9", launch="/snap/bin/spotify"):
    return Dial(slot=slot, label="Spotify", match_class="spotify", launch=launch,
                icon="", monitor="HDMI-0", rect=(0.0, 0.0, 0.5, 1.0),
                on_focus_loss="hide", pin_geometry=False)


class FakeSpawner:
    def __init__(self, raises=None):
        self.raises = raises
        self.commands = []

    def __call__(self, command):
        if self.raises:
            raise self.raises
        self.commands.append(command)


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def __call__(self, summary, body="", **kw):
        self.messages.append((summary, body))
        return True


def coord(clock=None, spawner=None, notifier=None):
    return LaunchCoordinator(
        clock=clock or FakeClock(),
        spawner=spawner or FakeSpawner(),
        notifier=notifier or FakeNotifier(),
    )


def test_nothing_pending_initially():
    c = coord()
    assert c.pending is None
    assert c.deadline() is None


def test_arm_does_not_launch_anything():
    """A stray keypress must never spawn a process."""
    spawner = FakeSpawner()
    c = coord(spawner=spawner)
    c.arm(dial())
    assert spawner.commands == []
    assert c.pending is not None


def test_arm_notifies_with_the_dial_label():
    notifier = FakeNotifier()
    coord(notifier=notifier).arm(dial())
    summary, body = notifier.messages[0]
    assert "Spotify" in summary + body
    assert "Enter" in body


def test_confirm_keycodes_are_return_and_kp_enter():
    assert coord().confirm_keycodes() == (keys.RETURN_KEYCODE, keys.KP_ENTER_KEYCODE)


def test_grab_keycodes_are_return_only():
    """What must be temporarily GRABBED is narrower than what CONFIRMS.

    KP_Enter is permanently grabbed as Dial slot "enter", so the daemon already
    receives it; grabbing it again in the temporary manager is what let
    release_confirm_grabs() delete the permanent grab (probe 09).
    """
    c = coord()
    assert c.grab_keycodes() == (keys.RETURN_KEYCODE,)
    assert keys.KP_ENTER_KEYCODE not in c.grab_keycodes()


def test_grab_keycodes_is_a_strict_subset_of_confirm_keycodes():
    """The two accessors may diverge, but never contradict: anything grabbed
    for a confirmation had better be able to confirm."""
    c = coord()
    assert set(c.grab_keycodes()) < set(c.confirm_keycodes())


def test_both_enter_keys_confirm():
    for kc in (keys.RETURN_KEYCODE, keys.KP_ENTER_KEYCODE):
        c = coord()
        c.arm(dial())
        assert c.is_confirm(kc) is True


def test_pressing_the_originating_dial_key_also_confirms():
    """The 'press it twice' instinct should work too."""
    c = coord()
    c.arm(dial(slot="9"))
    assert c.is_confirm(keys.keycode_for("9")) is True


def test_an_unrelated_key_does_not_confirm():
    c = coord()
    c.arm(dial(slot="9"))
    assert c.is_confirm(keys.keycode_for("4")) is False


def test_nothing_confirms_when_nothing_is_armed():
    assert coord().is_confirm(keys.RETURN_KEYCODE) is False


def test_confirm_spawns_the_launch_command():
    spawner = FakeSpawner()
    c = coord(spawner=spawner)
    c.arm(dial())
    assert c.confirm() is True
    assert spawner.commands == ["/snap/bin/spotify"]


def test_confirm_starts_the_wait_window():
    clk = FakeClock()
    c = coord(clock=clk)
    c.arm(dial())
    c.confirm()
    assert c.waiting_since() == clk.t
    assert c.deadline() == clk.t + LAUNCH_WAIT_TIMEOUT


def test_confirm_with_no_launch_command_notifies_and_clears():
    notifier = FakeNotifier()
    c = coord(notifier=notifier)
    c.arm(dial(launch=None))
    assert c.confirm() is False
    assert c.pending is None
    assert any("no launch" in b.lower() or "no launch" in s.lower()
               for s, b in notifier.messages)


def test_a_failing_spawn_notifies_and_clears():
    notifier = FakeNotifier()
    c = coord(spawner=FakeSpawner(raises=OSError("nope")), notifier=notifier)
    c.arm(dial())
    assert c.confirm() is False
    assert c.pending is None


def test_confirmation_times_out_after_five_seconds():
    clk = FakeClock()
    c = coord(clock=clk)
    c.arm(dial())
    assert c.check_timeout() is None
    clk.advance(CONFIRM_TIMEOUT + 0.1)
    assert c.check_timeout() == "confirm"
    assert c.pending is None


def test_deadline_while_armed_is_the_confirm_deadline():
    clk = FakeClock()
    c = coord(clock=clk)
    c.arm(dial())
    assert c.deadline() == clk.t + CONFIRM_TIMEOUT


def test_arming_a_second_dial_replaces_the_first():
    """Only one pending confirmation, so Enter is never ambiguous."""
    c = coord()
    c.arm(dial(slot="9"))
    c.arm(dial(slot="6", launch="firefox -P dial6"))
    assert c.pending.slot == "6"
    assert c.is_confirm(keys.keycode_for("9")) is False


def test_wait_times_out_after_ten_seconds_and_does_not_kill_the_process():
    clk = FakeClock()
    spawner = FakeSpawner()
    c = coord(clock=clk, spawner=spawner)
    c.arm(dial())
    c.confirm()
    clk.advance(LAUNCH_WAIT_TIMEOUT + 0.1)
    assert c.check_timeout() == "wait"
    assert c.pending is None
    # Nothing was killed: a slow app showing a window at 11s beats a dead one.
    assert spawner.commands == ["/snap/bin/spotify"]


def test_window_found_clears_the_wait():
    clk = FakeClock()
    c = coord(clock=clk)
    c.arm(dial())
    c.confirm()
    c.window_found()
    assert c.pending is None
    assert c.deadline() is None


def test_cancel_clears_everything():
    c = coord()
    c.arm(dial())
    c.cancel()
    assert c.pending is None
    assert c.deadline() is None


def test_grabs_needed_is_true_only_while_awaiting_confirmation():
    clk = FakeClock()
    c = coord(clock=clk)
    assert c.grabs_needed(installed=False) is False
    c.arm(dial())
    assert c.grabs_needed(installed=False) is True
    c.confirm()                      # now waiting for a window, not for Enter
    assert c.grabs_needed(installed=True) is False


# ---- path expansion in launch commands ----------------------------------

def test_expand_resolves_a_leading_tilde():
    """No shell runs launch commands, so nothing else would expand this."""
    from dials.launcher import expand
    assert expand("~/.mozilla/firefox/x.dial6").startswith(str(__import__("pathlib").Path.home()))
    assert "~" not in expand("~/.mozilla/firefox/x.dial6")


def test_expand_resolves_environment_variables(monkeypatch):
    from dials.launcher import expand
    monkeypatch.setenv("DIALS_TEST_DIR", "/opt/thing")
    assert expand("$DIALS_TEST_DIR/bin") == "/opt/thing/bin"


def test_expand_resolves_after_an_equals_sign():
    # `--profile=~/x` is natural to write, but expanduser only handles a
    # LEADING tilde, so this would otherwise pass through unexpanded.
    from dials.launcher import expand
    result = expand("--profile=~/.mozilla/firefox/x.dial6")
    assert result.startswith("--profile=/")
    assert "~" not in result


def test_expand_leaves_ordinary_tokens_untouched():
    from dials.launcher import expand
    for token in ("firefox", "-P", "dial6", "--class=Dial6", "--no-remote",
                  "/snap/bin/spotify", "--force-device-scale-factor=0.7"):
        assert expand(token) == token


def test_expand_leaves_an_unset_variable_alone():
    from dials.launcher import expand
    assert expand("$DIALS_NOT_SET_ANYWHERE") == "$DIALS_NOT_SET_ANYWHERE"


def test_the_spawned_argv_is_expanded(monkeypatch, tmp_path):
    """End-to-end: a tilde in `launch` must reach Popen already resolved."""
    import dials.launcher as mod
    seen = {}
    monkeypatch.setattr(mod.subprocess, "Popen",
                        lambda argv, **kw: seen.setdefault("argv", argv))
    mod._spawn("firefox --profile ~/.mozilla/firefox/x.dial6 --class=Dial6")
    assert "~" not in " ".join(seen["argv"])
    assert seen["argv"][0] == "firefox"
    assert seen["argv"][-1] == "--class=Dial6"

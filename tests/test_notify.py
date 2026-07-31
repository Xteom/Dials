from dials.notify import APP_NAME, notify


class FakeRunner:
    def __init__(self, returncode=0, raises=None):
        self.returncode = returncode
        self.raises = raises
        self.calls = []

    def __call__(self, cmd, **kw):
        if self.raises:
            raise self.raises
        self.calls.append((cmd, kw))
        class R:
            pass
        r = R()
        r.returncode = self.returncode
        return r


def test_builds_a_gdbus_notify_call():
    runner = FakeRunner()
    assert notify("Assign mode", "press a numpad key", runner=runner) is True
    cmd = runner.calls[0][0]
    assert cmd[0] == "gdbus"
    assert "org.freedesktop.Notifications.Notify" in cmd
    assert APP_NAME in cmd
    assert "Assign mode" in cmd
    assert "press a numpad key" in cmd


def test_timeout_is_passed_as_the_last_argument():
    runner = FakeRunner()
    notify("x", timeout_ms=1234, runner=runner)
    assert runner.calls[0][0][-1] == "1234"


def test_a_nonzero_exit_reports_failure_without_raising():
    assert notify("x", runner=FakeRunner(returncode=1)) is False


def test_a_missing_gdbus_never_raises():
    """Notifications are cosmetic; failing must never break a keypress."""
    assert notify("x", runner=FakeRunner(raises=FileNotFoundError())) is False


def test_any_exception_is_swallowed():
    assert notify("x", runner=FakeRunner(raises=RuntimeError("boom"))) is False


def test_never_blocks_forever():
    runner = FakeRunner()
    notify("x", runner=runner)
    assert runner.calls[0][1].get("timeout") is not None

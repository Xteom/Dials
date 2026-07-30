"""Shared fixtures. No test in this suite may require a running X server."""
import pytest


@pytest.fixture
def tmp_config_path(tmp_path):
    """Path to a config file inside an isolated dir (file does not exist yet)."""
    return tmp_path / "config.toml"


class FakeClock:
    """Injectable monotonic clock, in seconds."""

    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()

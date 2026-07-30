"""Launch confirmation and the post-launch window wait.

Two phases, both bounded, neither ever blocking the event loop:

  arm     -> notify, wait up to CONFIRM_TIMEOUT for Enter (or the same Dial key)
  confirm -> spawn, wait up to LAUNCH_WAIT_TIMEOUT for a matching NEW window

Nothing is launched on the first keypress, so a stray press never spawns a
process. Only one confirmation is ever pending, which is what keeps Enter
unambiguous.

The timeouts are expressed as absolute deadlines via `deadline()` so the daemon
can fold them into its select() timeout. There is no sleep loop anywhere here -
a blocking 10s wait would freeze every other Dial.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time as _time
from dataclasses import dataclass, replace

from dials import keys
from dials.config import Dial

CONFIRM_TIMEOUT = 5.0
LAUNCH_WAIT_TIMEOUT = 10.0


@dataclass(frozen=True)
class PendingLaunch:
    slot: str
    dial: Dial
    armed_at: float
    launched_at: float | None = None


def expand(token: str) -> str:
    """Expand `~` and `$VARS` in one argv token.

    Launch commands are run WITHOUT a shell (shlex.split + Popen), so nothing
    would expand these otherwise: a `launch` line containing `~/.mozilla/...`
    would hand Firefox a literal directory named `~` and silently create a junk
    profile. Expanding here makes `~` and `$HOME` mean what they look like.

    Handles `--opt=~/path` too, since os.path.expanduser only expands a LEADING
    tilde and `--profile=~/x` is a natural thing to write.
    """
    if "=" in token and not token.startswith("="):
        head, sep, tail = token.partition("=")
        return head + sep + expand(tail)
    return os.path.expanduser(os.path.expandvars(token))


def _spawn(command: str) -> None:
    subprocess.Popen(
        [expand(token) for token in shlex.split(command)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class LaunchCoordinator:
    def __init__(self, clock=_time.monotonic, spawner=_spawn, notifier=None):
        self._clock = clock
        self._spawn = spawner
        if notifier is None:
            from dials.notify import notify as notifier
        self._notify = notifier
        self.pending: PendingLaunch | None = None

    # ---- arming ----------------------------------------------------------

    def arm(self, dial: Dial) -> PendingLaunch:
        """Ask for confirmation. Does NOT launch anything."""
        self.pending = PendingLaunch(slot=dial.slot, dial=dial,
                                     armed_at=self._clock())
        self._notify(
            f"{dial.label} is not running",
            f"Press Enter to launch it  (Dial {dial.slot})",
            timeout_ms=int(CONFIRM_TIMEOUT * 1000),
        )
        return self.pending

    def cancel(self) -> None:
        self.pending = None

    # ---- confirmation ------------------------------------------------------

    def confirm_keycodes(self) -> tuple[int, ...]:
        """Keys temporarily grabbed while awaiting confirmation."""
        return (keys.RETURN_KEYCODE, keys.KP_ENTER_KEYCODE)

    def is_confirm(self, keycode: int) -> bool:
        """True if this keypress confirms the pending launch."""
        p = self.pending
        if p is None or p.launched_at is not None:
            return False
        if keycode in self.confirm_keycodes():
            return True
        return keycode == keys.keycode_for(p.slot)

    def confirm(self) -> bool:
        """Run the launch command and begin waiting for its window."""
        p = self.pending
        if p is None:
            return False
        if not p.dial.launch:
            self._notify(f"{p.dial.label}: no launch command configured")
            self.pending = None
            return False
        try:
            self._spawn(p.dial.launch)
        except Exception as exc:
            self._notify(f"{p.dial.label}: launch failed", str(exc))
            self.pending = None
            return False
        self.pending = replace(p, launched_at=self._clock())
        return True

    # ---- waiting -------------------------------------------------------------

    def waiting_since(self) -> float | None:
        return self.pending.launched_at if self.pending else None

    def window_found(self) -> None:
        """The launched window appeared; stop waiting."""
        self.pending = None

    def grabs_needed(self, installed: bool) -> bool:
        """Whether the temporary Enter grabs should currently be held."""
        p = self.pending
        return p is not None and p.launched_at is None

    # ---- deadlines -------------------------------------------------------

    def deadline(self) -> float | None:
        """Absolute wake time, or None when idle. Keeps select() infinite."""
        p = self.pending
        if p is None:
            return None
        if p.launched_at is None:
            return p.armed_at + CONFIRM_TIMEOUT
        return p.launched_at + LAUNCH_WAIT_TIMEOUT

    def check_timeout(self, now: float | None = None) -> str | None:
        """Expire a phase. Returns "confirm", "wait", or None.

        A wait timeout deliberately does NOT kill the spawned process: killing
        something merely for being slow is the more dangerous behavior.
        """
        p = self.pending
        if p is None:
            return None
        current = self._clock() if now is None else now
        if p.launched_at is None:
            if current - p.armed_at >= CONFIRM_TIMEOUT:
                self.pending = None
                return "confirm"
            return None
        if current - p.launched_at >= LAUNCH_WAIT_TIMEOUT:
            self._notify(f"{p.dial.label}: no window appeared",
                         "Giving up; the process was left running")
            self.pending = None
            return "wait"
        return None

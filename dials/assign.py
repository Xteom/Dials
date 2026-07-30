"""Assign mode: bind the focused window to the next numpad key.

The target window is SNAPSHOTTED when '.' is pressed. Whatever gains focus
afterwards is irrelevant - which is what allows the overwrite dialog to take
focus without changing what gets bound.

`resolve` only *describes* the outcome. An occupied slot yields an
OverwriteRequest and nothing is persisted until the caller confirms, so a
destructive reassignment can never happen silently.

Escape is deliberately NOT used as the cancel key: grabbing it would steal
Escape system-wide for five seconds. Pressing '.' again cancels.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass

from dials import keys
from dials.config import Dial, Defaults
from dials.geometry import Monitor, Rect

ASSIGN_TIMEOUT = 5.0


class AssignError(ValueError):
    """Raised when a capture cannot become a Dial."""


@dataclass(frozen=True)
class Capture:
    wid: int
    wm_class: str
    label: str
    monitor: str
    rect: tuple[float, float, float, float]
    at: float


@dataclass(frozen=True)
class OverwriteRequest:
    existing: Dial
    incoming: Dial


def derive_rect(win_rect: Rect, monitor: Monitor) -> tuple[float, float, float, float]:
    """Express a window's pixel rect as fractions of the monitor it sits on."""
    m = monitor.rect
    fx = (win_rect.x - m.x) / m.w if m.w else 0.0
    fy = (win_rect.y - m.y) / m.h if m.h else 0.0
    fw = win_rect.w / m.w if m.w else 1.0
    fh = win_rect.h / m.h if m.h else 1.0

    fx = min(max(fx, 0.0), 1.0)
    fy = min(max(fy, 0.0), 1.0)
    # Keep the rect inside the monitor and never zero-sized, since config
    # validation rejects both.
    fw = min(max(fw, 0.001), 1.0 - fx if fx < 1.0 else 0.001)
    fh = min(max(fh, 0.001), 1.0 - fy if fy < 1.0 else 0.001)
    return (round(fx, 4), round(fy, 4), round(fw, 4), round(fh, 4))


class AssignMode:
    def __init__(self, clock=_time.monotonic, notifier=None, defaults=None):
        self._clock = clock
        if notifier is None:
            from dials.notify import notify as notifier
        self._notify = notifier
        self._defaults = defaults or Defaults()
        self._capture: Capture | None = None
        self._armed_at: float | None = None

    @property
    def armed(self) -> bool:
        return self._capture is not None

    def arm(self, capture: Capture) -> None:
        self._capture = capture
        self._armed_at = self._clock()
        self._notify(
            "Assign mode",
            f"Press a numpad key to bind {capture.label or capture.wm_class!r}"
            "   ('.' again to cancel)",
            timeout_ms=int(ASSIGN_TIMEOUT * 1000),
        )

    def cancel(self) -> None:
        self._capture = None
        self._armed_at = None

    def deadline(self) -> float | None:
        if self._armed_at is None:
            return None
        return self._armed_at + ASSIGN_TIMEOUT

    def check_timeout(self, now: float | None = None) -> bool:
        """Expire the CAPTURE phase only.

        This timeout never touches a confirmation dialog: once a slot has been
        chosen the capture is complete, and a modal that vanished after five
        seconds would be unusable.
        """
        if self._armed_at is None:
            return False
        current = self._clock() if now is None else now
        if current - self._armed_at >= ASSIGN_TIMEOUT:
            self.cancel()
            return True
        return False

    def resolve(self, slot: str, existing: Dial | None):
        """Turn the snapshot into a Dial, or into an OverwriteRequest."""
        cap = self._capture
        if cap is None:
            raise AssignError("assign mode is not armed")
        if slot == keys.ASSIGN_SLOT:
            raise AssignError(f"'{keys.ASSIGN_SLOT}' is reserved for assign mode")
        if not keys.is_bindable(slot):
            raise AssignError(f"not a bindable slot: {slot!r}")
        if not cap.wm_class.strip():
            raise AssignError("window has no WM_CLASS class; refusing to bind")

        incoming = Dial(
            slot=slot,
            label=(cap.label.strip() or cap.wm_class),
            match_class=cap.wm_class,
            launch=None,
            icon="",
            monitor=cap.monitor,
            rect=cap.rect,
            on_focus_loss=self._defaults.on_focus_loss,
            pin_geometry=self._defaults.pin_geometry,
        )
        self.cancel()
        if existing is not None:
            return OverwriteRequest(existing=existing, incoming=incoming)
        return incoming

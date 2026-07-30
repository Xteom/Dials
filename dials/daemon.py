"""The Dials daemon: one X connection, one select() loop, zero polling.

Resource budget lives or dies here. The loop blocks in select() on the X file
descriptor with a timeout of None whenever nothing is armed, so at idle the
process is not scheduled at all - that is what produces 0.0% CPU and 0
wakeups/s. Every timeout in the design (assign 5s, confirm 5s, launch wait 10s,
activation 1.5s) is folded into that select() timeout via `select_timeout()`;
there is no periodic tick and no sleep loop anywhere.

Deliberately does NOT import: curses, gi/GTK, dials.configwrite, dials.tui.
"""
from __future__ import annotations

import os
import select
import signal
import sys
import time as _time

from dials import geometry, keys, monitors as monitors_mod, panels
from dials.assign import AssignMode, Capture, OverwriteRequest, derive_rect
from dials.config import Config, config_path, load, state_dir
from dials.grab import Debouncer, GrabManager
from dials.launcher import LaunchCoordinator
from dials.panels import FocusTracker
from dials.windows import WindowOps, choose, group_ids

PAUSE_FLAG = "paused"


class Daemon:
    """Wiring plus dispatch. X access is injected so this is unit-testable."""

    def __init__(self, config: Config, ops, monitors, clock=_time.monotonic,
                 notifier=None, spawner=None, confirm_runner=None):
        self.config = config
        self.ops = ops
        self.monitors = monitors
        self._clock = clock
        if notifier is None:
            from dials.notify import notify as notifier
        self._notify = notifier
        self._confirm_runner = confirm_runner or _spawn_confirm_dialog

        from dials.launcher import _spawn as default_spawn
        self.launcher = LaunchCoordinator(
            clock=clock, spawner=spawner or default_spawn, notifier=notifier
        )
        self.assign = AssignMode(clock=clock, notifier=notifier,
                                 defaults=config.defaults)
        self.debouncer = Debouncer(clock=clock)
        self.trackers: dict[str, FocusTracker] = {}
        self.recent: tuple[int, ...] = ()
        self.monitor_warnings: dict[str, str] = {}
        self._paused = False

    # ---- pause -----------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    # ---- config ----------------------------------------------------------

    def reload(self, config: Config) -> None:
        """Adopt new config and abandon every in-flight transition.

        An activation in flight for a Dial that may have changed must not be
        applied to the new definition.
        """
        self.config = config
        for tracker in self.trackers.values():
            tracker.forget()
        self.trackers.clear()
        self.assign.cancel()
        self.launcher.cancel()

    def _tracker(self, dial) -> FocusTracker:
        t = self.trackers.get(dial.slot)
        if t is None or t.on_focus_loss != dial.on_focus_loss:
            t = FocusTracker(dial.on_focus_loss, clock=self._clock)
            self.trackers[dial.slot] = t
        return t

    # ---- geometry --------------------------------------------------------

    def _rect_for(self, dial) -> geometry.Rect:
        mons = self.monitors.monitors()
        root = self.monitors.root_rect()
        monitor, warning = monitors_mod.pick(dial.monitor, mons, root)
        if warning and self.monitor_warnings.get(dial.slot) != warning:
            self.monitor_warnings[dial.slot] = warning
            self._notify(f"Dial {dial.slot}", warning)
        return geometry.resolve(dial.rect, monitor.rect, root)

    # ---- dispatch --------------------------------------------------------

    def handle_slot(self, slot: str, timestamp: int) -> str | None:
        """Run one Dial keypress. Returns the action taken, or None."""
        if self._paused:
            return None

        dial = self.config.dial(slot)
        if dial is None:
            return None

        try:
            windows = self.ops.list_windows()
            active = self.ops.active_window()
            chosen = choose(windows, dial.match_class,
                            active_id=active, recent=self.recent)
            found = chosen is not None
            hidden = bool(found and self.ops.is_hidden(chosen.wid))
            is_active = bool(found and active == chosen.wid)

            action = panels.decide(found, hidden, is_active)

            if action == panels.LAUNCH:
                self.launcher.arm(dial)
                return action

            if action == panels.HIDE:
                self._tracker(dial).forget()
                self.ops.iconify(chosen.wid)
                return action

            if panels.reapply_geometry(action, dial.pin_geometry):
                self.ops.apply_geometry(chosen.wid, self._rect_for(dial))
            if action == panels.SHOW:
                self.ops.apply_hints(chosen.wid,
                                     above=(dial.on_focus_loss == "above"))
            self._tracker(dial).activating(chosen.wid)
            self.ops.activate(chosen.wid, timestamp)
            return action
        except Exception:
            # Degrade never crash: a dead window or an X hiccup must not take
            # the daemon down mid-keypress.
            return None

    def handle_assign_key(self, slot: str) -> None:
        """A numpad key pressed while assign mode is armed."""
        try:
            existing = self.config.dial(slot) if keys.is_bindable(slot) else None
            result = self.assign.resolve(slot, existing)
        except Exception as exc:
            self._notify("Assign failed", str(exc))
            return
        if isinstance(result, OverwriteRequest):
            self._confirm_runner(result)
        else:
            self._persist(result)

    def arm_assign(self) -> None:
        """'.' pressed: snapshot the focused window, or cancel if already armed."""
        if self.assign.armed:
            self.assign.cancel()
            self._notify("Assign mode cancelled")
            return
        try:
            active = self.ops.active_window()
            if not active:
                self._notify("Assign mode", "no active window to bind")
                return
            windows = {w.wid: w for w in self.ops.list_windows()}
            info = windows.get(active)
            if info is None:
                self._notify("Assign mode", "active window is not manageable")
                return
            win_rect = self.ops.geometry(active) or geometry.Rect(0, 0, 800, 600)
            mons = self.monitors.monitors()
            root = self.monitors.root_rect()
            monitor = _monitor_containing(win_rect, mons, root)
            self.assign.arm(Capture(
                wid=active,
                wm_class=info.wm_class,
                label=self.ops.window_name(active),
                monitor=monitor.name,
                rect=derive_rect(win_rect, monitor),
                at=self._clock(),
            ))
        except Exception as exc:
            self._notify("Assign mode failed", str(exc))

    def _persist(self, dial) -> None:
        """Write a Dial. Imports the writer lazily so the daemon stays lean."""
        from dials.configwrite import upsert_dial
        try:
            self.config = upsert_dial(config_path(), dial)
            self._notify("Dial bound", f"{dial.slot} -> {dial.label}")
        except Exception as exc:
            self._notify("Could not save Dial", str(exc))

    # ---- focus watching --------------------------------------------------

    def on_active_window_changed(self) -> None:
        """Reconcile every tracker against the CURRENT active window.

        Reads the property fresh rather than trusting an event payload, so a
        stale or coalesced PropertyNotify cannot drive a wrong transition.
        """
        try:
            active = self.ops.active_window()
            windows = self.ops.list_windows()
        except Exception:
            return

        if active:
            self.recent = (active,) + tuple(w for w in self.recent if w != active)[:7]

        by_id = {w.wid: w for w in windows}
        for slot, tracker in list(self.trackers.items()):
            dial = self.config.dial(slot)
            if dial is None or tracker.window is None:
                continue
            info = by_id.get(tracker.window)
            group = group_ids(info, windows) if info else {tracker.window}
            if tracker.observe_active(active, group) == panels.HIDE:
                try:
                    self.ops.iconify(tracker.window)
                except Exception:
                    pass

        pending = self.launcher.pending
        if pending is not None and pending.launched_at is not None:
            chosen = choose(windows, pending.dial.match_class,
                            since=pending.launched_at)
            if chosen is not None:
                dial = pending.dial
                self.launcher.window_found()
                try:
                    self.ops.apply_geometry(chosen.wid, self._rect_for(dial))
                    self.ops.apply_hints(chosen.wid,
                                         above=(dial.on_focus_loss == "above"))
                    self._tracker(dial).activating(chosen.wid)
                    self.ops.activate(chosen.wid, 0)
                except Exception:
                    pass

    # ---- timing ----------------------------------------------------------

    def select_timeout(self) -> float | None:
        """Seconds until the nearest deadline, or None when nothing is armed.

        None at idle is the single most important line in this file for the
        resource budget.
        """
        now = self._clock()
        deadlines = [d for d in (
            self.assign.deadline(),
            self.launcher.deadline(),
            *(t.deadline() for t in self.trackers.values()),
        ) if d is not None]
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - now)

    def tick(self) -> None:
        """Expire whatever is due. Called only after a select() timeout."""
        self.assign.check_timeout()
        self.launcher.check_timeout()
        for tracker in self.trackers.values():
            tracker.check_timeout()


def _monitor_containing(rect: geometry.Rect, mons, root) -> geometry.Monitor:
    """The monitor whose rect contains the window's centre point."""
    cx, cy = rect.x + rect.w // 2, rect.y + rect.h // 2
    for m in mons:
        r = m.rect
        if r.x <= cx < r.x + r.w and r.y <= cy < r.y + r.h:
            return m
    for m in mons:
        if m.primary:
            return m
    return mons[0] if mons else geometry.Monitor("<root>", root, True, 0)


def _spawn_confirm_dialog(request: OverwriteRequest) -> None:
    """Launch dials-confirm as a separate process (keeps GTK out of here)."""
    import json
    import subprocess
    payload = json.dumps({
        "slot": request.incoming.slot,
        "existing": {"label": request.existing.label,
                     "match_class": request.existing.match_class,
                     "monitor": request.existing.monitor,
                     "rect": list(request.existing.rect)},
        "incoming": {"label": request.incoming.label,
                     "match_class": request.incoming.match_class,
                     "monitor": request.incoming.monitor,
                     "rect": list(request.incoming.rect)},
    })
    exe = os.path.join(os.path.dirname(sys.executable), "dials-confirm")
    try:
        subprocess.Popen([exe, payload], start_new_session=True)
    except Exception:
        from dials.notify import notify
        notify("Dial not replaced",
               f"Slot {request.incoming.slot} already holds "
               f"{request.existing.label}; confirmation dialog unavailable")


# ---- process entry point -------------------------------------------------

def main() -> int:
    from Xlib import X, display

    d = display.Display()
    root = d.screen().root
    ops = WindowOps(d, root)
    mons = monitors_mod.MonitorSource(d, root)
    mons.select_events()

    root.change_attributes(event_mask=X.PropertyChangeMask)
    d.sync()

    daemon = Daemon(config=load(), ops=ops, monitors=mons)

    grabs = GrabManager(d, root)
    failures = grabs.install(keys.SLOT_KEYCODES.values())
    for kc, masks in failures.items():
        print(f"warning: could not grab keycode {kc} for masks "
              f"{[hex(m) for m in masks]}", file=sys.stderr)

    state_dir().mkdir(parents=True, exist_ok=True)
    if (state_dir() / PAUSE_FLAG).exists():
        daemon.pause()
        grabs.remove_all()

    reload_requested = False

    def on_hup(_sig, _frame):
        nonlocal reload_requested
        reload_requested = True

    signal.signal(signal.SIGHUP, on_hup)

    active_atom = d.intern_atom("_NET_ACTIVE_WINDOW")
    confirm_grabs_held = False
    confirm_grabs = None          # initialised here, not inside a branch
    xfd = d.fileno()

    def release_confirm_grabs():
        """Release the temporary Enter grabs ONLY.

        Deliberately does not cancel the pending launch: this also runs on the
        normal confirm -> wait-for-window transition, and cancelling there would
        abandon the window the user just asked for.

        Must be reachable from every exit path - pause, reload, shutdown -
        because leaving `Return` grabbed would take Enter away system-wide.
        """
        nonlocal confirm_grabs_held
        if confirm_grabs_held and confirm_grabs is not None:
            confirm_grabs.remove_all()
        confirm_grabs_held = False

    while True:
        if reload_requested:
            reload_requested = False
            daemon.reload(load())
            paused = (state_dir() / PAUSE_FLAG).exists()
            if paused and not daemon.paused:
                daemon.pause()
                grabs.remove_all()
                # The confirm grabs are a SEPARATE set and would otherwise
                # survive the pause, leaving Return grabbed system-wide with a
                # confirmation nobody can reach - and, because is_confirm() is
                # checked before any pause gate, an Enter pressed for an
                # unrelated reason would still launch the app.
                release_confirm_grabs()
                daemon.launcher.cancel()
            elif not paused and daemon.paused:
                daemon.resume()
                grabs.install(keys.SLOT_KEYCODES.values())

        # Hold the temporary Enter grabs only while a confirmation is pending.
        need = daemon.launcher.grabs_needed(confirm_grabs_held)
        if need and not confirm_grabs_held and not daemon.paused:
            extra = GrabManager(d, root, masks=grabs.masks)
            failed = extra.install(daemon.launcher.confirm_keycodes())
            if len(failed) == len(daemon.launcher.confirm_keycodes()):
                # Both confirm keys unavailable: refuse to leave a pending
                # confirmation with no way to confirm it.
                extra.remove_all()
                daemon.launcher.cancel()
                from dials.notify import notify
                notify("Cannot confirm launch",
                       "Enter could not be grabbed; launch cancelled")
            else:
                confirm_grabs = extra
                confirm_grabs_held = True
        elif confirm_grabs_held and not need:
            release_confirm_grabs()

        timeout = daemon.select_timeout()
        try:
            ready, _, _ = select.select([xfd], [], [], timeout)
        except InterruptedError:
            continue
        if not ready:
            daemon.tick()
            continue

        # DO NOT "fix" this to range(max(1, pending_events)).
        # pending_events() is not a passive queue-length read: python-xlib's
        # protocol/display.py calls send_and_recv(recv=1) first, so it drains the
        # socket and then reports how many EVENTS resulted. Verified on this
        # machine: with select() reporting readable it returned 1, and an idle
        # connection fired select() 0/3 times - so there is no spin.
        # A 0 here means the readable bytes were a reply or an error rather than
        # an event; they have already been consumed, so returning to select() is
        # correct. Forcing next_event() when the queue is empty would BLOCK
        # indefinitely (measured), freezing every Dial and every timeout until
        # some unrelated event arrived.
        try:
            pending_events = d.pending_events()
        except Exception:
            pending_events = 0
        for _ in range(pending_events):
            try:
                event = d.next_event()
            except Exception:
                break

            if mons.handles(event):
                mons.invalidate()
                continue

            if event.type == X.PropertyNotify and event.atom == active_atom:
                daemon.on_active_window_changed()
                continue

            if event.type != X.KeyPress:
                continue

            keycode = event.detail
            if not daemon.debouncer.allow(keycode):
                continue                      # autorepeat

            if daemon.launcher.is_confirm(keycode):
                daemon.launcher.confirm()
                continue

            slot = keys.slot_for(keycode)
            if slot is None:
                continue
            if slot == keys.ASSIGN_SLOT:
                daemon.arm_assign()
            elif daemon.assign.armed:
                daemon.handle_assign_key(slot)
            else:
                daemon.handle_slot(slot, timestamp=event.time)

    return 0

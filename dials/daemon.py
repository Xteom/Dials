"""The Dials daemon: one X connection, one select() loop, zero polling.

Resource budget lives or dies here. The loop blocks in select() on the X file
descriptor with a timeout of None whenever nothing is armed, so at idle the
process is not scheduled at all - that is what produces 0.0% CPU and 0
wakeups/s. Every timeout in the design (assign 5s, confirm 5s, launch wait 10s,
activation 1.5s) is folded into that select() timeout via `select_timeout()`;
there is no periodic tick and no sleep loop anywhere.

Because the idle timeout is None, signals need a wakeup fd to be noticed at all:
under PEP 475 select() is auto-retried after a handler returns, so
InterruptedError never fires and a SIGHUP would otherwise sit unobserved until
some unrelated X event arrived. See `main()`.

Deliberately does NOT import: curses, gi/GTK, dials.configwrite, dials.tui.
"""
from __future__ import annotations

import logging
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

log = logging.getLogger(__name__)

PAUSE_FLAG = "paused"

#: Consecutive "X fd readable but zero events" rounds tolerated before the
#: connection is presumed dead. A peer-closed socket is permanently readable
#: while yielding nothing, which would otherwise spin at 100% CPU forever.
MAX_DEAD_READY = 200


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
        # KeyPress.time of the Enter that confirmed the pending launch. EWMH
        # wants a real user-activity timestamp for the post-launch activation,
        # and CurrentTime (0) is explicitly ruled out by the spec.
        self._confirm_timestamp = 0

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
        # Cancel first so a reload never leaves a stale capture armed, then
        # rebuild: AssignMode snapshots `defaults` at construction, so keeping
        # the old instance would bind post-reload Dials with the PRE-reload
        # on_focus_loss / pin_geometry.
        self.assign.cancel()
        self.assign = AssignMode(clock=self._clock, notifier=self._notify,
                                 defaults=config.defaults)
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

    def dispatch_key(self, keycode: int, timestamp: int) -> str | None:
        """Route one grabbed KeyPress. Returns a tag for what was done.

        Lives here rather than inline in `main()` so the ORDERING is testable
        without an X server. The order is load-bearing:

        * the Debouncer runs first, so holding a Dial key cannot toggle it
          repeatedly (server autorepeat is indistinguishable from real presses);
        * `is_confirm` runs BEFORE `slot_for`, so while a confirmation is armed
          the Enter keys confirm the launch instead of firing their own Dial.
          KP_Enter is both a confirm key and slot "enter", so getting this
          backwards would silently make Enter un-confirmable.

        Returns None when the press was ignored, "confirm" / "assign" / "bind"
        for the non-Dial paths, else the action from `handle_slot`.
        """
        try:
            if not self.debouncer.allow(keycode):
                return None                   # autorepeat

            if self.launcher.is_confirm(keycode):
                self.confirm_launch(timestamp)
                return "confirm"

            slot = keys.slot_for(keycode)
            if slot is None:
                return None
            if slot == keys.ASSIGN_SLOT:
                self.arm_assign()
                return "assign"
            if self.assign.armed:
                self.handle_assign_key(slot)
                return "bind"
            return self.handle_slot(slot, timestamp=timestamp)
        except Exception:
            # Degrade never crash: no keypress may take the daemon down. This
            # is the outer net for the paths handle_slot does not already wrap
            # (notably _persist's lazy import of the TOML writer).
            log.warning("dispatch failed for keycode %r", keycode, exc_info=True)
            return None

    def confirm_launch(self, timestamp: int) -> bool:
        """Confirm the pending launch, remembering the triggering timestamp.

        The timestamp is kept so the post-launch activation can pass a real
        user-activity time rather than CurrentTime. It may be up to
        LAUNCH_WAIT_TIMEOUT stale by the time the window appears, which is
        still strictly better than 0 under focus-stealing prevention.
        """
        self._confirm_timestamp = timestamp
        return self.launcher.confirm()

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
        """Write a Dial. Imports the writer lazily so the daemon stays lean.

        The import sits INSIDE the try: it is the one import in a keypress path,
        so a missing/broken tomli_w must degrade to a notification rather than
        propagate out of the event loop and kill the daemon.
        """
        try:
            from dials.configwrite import upsert_dial
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
                    self.ops.activate(chosen.wid, self._confirm_timestamp)
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

def _report_grab_failures(failures: dict[int, list[int]]) -> None:
    """Warn about grabs that could not be installed.

    Used by startup AND by the resume path: a Dial that silently fails to come
    back after `dials resume` is exactly as broken as one that never installed
    at all, so both must be equally visible.
    """
    for kc, masks in failures.items():
        print(f"warning: could not grab keycode {kc} for masks "
              f"{[hex(m) for m in masks]}", file=sys.stderr)


def main() -> int:
    import socket

    from Xlib import X, display
    from Xlib.error import ConnectionClosedError

    d = display.Display()
    root = d.screen().root
    ops = WindowOps(d, root)
    mons = monitors_mod.MonitorSource(d, root)
    mons.select_events()

    root.change_attributes(event_mask=X.PropertyChangeMask)
    d.sync()

    daemon = Daemon(config=load(), ops=ops, monitors=mons)

    grabs = GrabManager(d, root)
    _report_grab_failures(grabs.install(keys.SLOT_KEYCODES.values()))

    state_dir().mkdir(parents=True, exist_ok=True)
    if (state_dir() / PAUSE_FLAG).exists():
        daemon.pause()
        grabs.remove_all()

    # SIGHUP/SIGTERM must be able to break the select() block. Under PEP 475
    # select() is auto-retried after a handler returns, so InterruptedError never
    # fires; at idle the timeout is None by design, so without this the process
    # would stay blocked indefinitely after a signal (measured: 3.01s block while
    # the handler had already run at 0.4s). A wakeup fd turns a signal into
    # readable bytes that select() can see.
    #
    # This is also what makes `dials pause` safe: the reload check sits at the
    # top of the loop, so without a wakeup the first Dial keypress would be what
    # woke select() and it would be DISPATCHED BEFORE the pause engaged -
    # exactly backwards for a safety valve meant to protect a game session.
    wake_r, wake_w = socket.socketpair()
    for _s in (wake_r, wake_w):
        _s.setblocking(False)
    signal.set_wakeup_fd(wake_w.fileno())

    reload_requested = False
    stop_requested = False

    def on_hup(_sig, _frame):
        nonlocal reload_requested
        reload_requested = True

    def on_stop(_sig, _frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGHUP, on_hup)
    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)

    active_atom = d.intern_atom("_NET_ACTIVE_WINDOW")
    confirm_grabs_held = False
    confirm_grabs = None          # initialised here, not inside a branch
    xfd = d.fileno()
    wake_fd = wake_r.fileno()
    dead_ready = 0                # consecutive ready-but-no-events rounds

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

    try:
        while True:
            # Checked before anything else so an orderly stop never dispatches
            # one more keypress on its way out.
            if stop_requested:
                return 0

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
                    _report_grab_failures(
                        grabs.install(keys.SLOT_KEYCODES.values()))

            # Hold the temporary Enter grabs only while a confirmation is pending.
            need = daemon.launcher.grabs_needed(confirm_grabs_held)
            if need and not confirm_grabs_held and not daemon.paused:
                extra = GrabManager(d, root, masks=grabs.masks)
                # Keycode 104 (KP_Enter) is EXPECTED to fail every mask here: it
                # is already grabbed permanently as Dial slot "enter", and X
                # returns BadAccess for a duplicate grab. Only `Return` (36) can
                # newly succeed, so the guard must ask "did anything at all get
                # grabbed" rather than count keycodes - counting would degenerate
                # into "cancel iff Return failed on any single mask" and would
                # spuriously abandon a launch that Return could still confirm.
                extra.install(daemon.launcher.confirm_keycodes())
                if not extra.active:
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
                ready, _, _ = select.select([xfd, wake_fd], [], [], timeout)
            except InterruptedError:      # pre-PEP-475 safety only
                continue

            if wake_fd in ready:
                try:
                    wake_r.recv(4096)     # drain; the flags carry the meaning
                except BlockingIOError:
                    pass

            if not ready:
                daemon.tick()
                continue

            if xfd not in ready:
                # Signal-only wakeup: loop back so the flags are acted on now.
                # Skipping the X read also keeps `dead_ready` honest.
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
            except ConnectionClosedError:
                print("dialsd: X connection closed; exiting for restart",
                      file=sys.stderr)
                return 1
            except Exception:
                pending_events = 0

            # A peer-closed X socket is permanently readable while yielding no
            # events, which would spin at 100% CPU forever. python-xlib does
            # raise ConnectionClosedError on EOF, but this bounded guard also
            # covers any readable-yet-eventless state it does not: exit so
            # systemd (Restart=on-failure, PartOf=graphical-session.target) can
            # restart us with the next session.
            if pending_events == 0:
                dead_ready += 1
                if dead_ready > MAX_DEAD_READY:
                    print("dialsd: X fd readable with no events; assuming the "
                          "connection is dead, exiting for restart",
                          file=sys.stderr)
                    return 1
            else:
                dead_ready = 0

            for _ in range(pending_events):
                try:
                    event = d.next_event()
                except ConnectionClosedError:
                    print("dialsd: X connection closed; exiting for restart",
                          file=sys.stderr)
                    return 1
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

                daemon.dispatch_key(event.detail, timestamp=event.time)
    finally:
        # Orderly stop. Grabs being process-scoped only covers a CRASH; an
        # orderly stop must not rely on that, and leaving `Return` grabbed
        # would take Enter away system-wide until the next login.
        release_confirm_grabs()
        grabs.remove_all()
        signal.set_wakeup_fd(-1)
        wake_r.close()
        wake_w.close()

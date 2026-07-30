# Dials

Turns the NumLock-OFF numpad into 15 quake-style show/hide window panels on
Pop!_OS 22.04 (GNOME 42, X11). A **Dial** is one numpad key holding one window —
named after the One Piece shells that store energy and release it when pressed.

With NumLock **on**, the numpad types digits exactly as before. Nothing changes.

## Why this works

Numpad keys carry two keysyms on one keycode, selected by NumLock, and NumLock
is modifier `mod2`. A passive `XGrabKey` with a modifier mask that excludes
`mod2` therefore fires only while NumLock is off.

Two things this design gets right that a naive version does not:

- **Lock modifiers must be tolerated, not ignored.** X matches grab masks
  exactly, so CapsLock (`LockMask`) breaks a mask-0-only grab and silently kills
  every Dial. Dials grabs the cross product of tolerated lock modifiers —
  `{0, LockMask}` here, 32 grabs — never including `mod2`.
- **"Focused" means `_NET_ACTIVE_WINDOW`.** EWMH lets a WM set
  `_NET_WM_STATE_FOCUSED` on several windows at once, so it is a decoration hint,
  not an exclusive focus flag.

## Install

```sh
./install.sh            # daemon only
./install.sh --tray     # plus the top-bar indicator
```

## Use

| Action | Key |
| --- | --- |
| Toggle a Dial | its numpad key, NumLock off |
| Assign the focused window | `.` then a numpad key |
| Confirm a launch | `Enter` (or the Dial key again) |
| Type digits | NumLock on — unchanged |

Pressing a Dial's key does the useful thing in all three states: hidden → show,
visible but buried → raise, visible and focused → hide. So a buried window is
never lost behind a double-press.

```sh
dials list          # every slot
dials status        # paused or active, how many Dials are bound, config health
dials capture 9     # save a window's current position into its Dial
dials pause         # stand down for a game or remote desktop
dials              # curses numpad grid
```

## Configuration

Live config is `~/.config/dials/config.toml` — the only file anything reads.
`config/config.reference.toml` in this repo is a **reference snapshot**, read by
nothing; refresh it with `dials config export`.

Geometry is stored as a monitor name plus fractions, never absolute pixels,
because this machine's layout changes: `DP-1-1` was connected during one design
probe and gone by the next. An absent monitor falls back to primary.

## What it touches

- `~/.config/systemd/user/dialsd.service`, `dials-tray.service`
- `~/.local/share/dials/venv/`
- `~/.config/dials/config.toml` — seeded from the reference copy only if absent
- `~/.local/state/dials/` (pause flag, tray PID)

No apt packages, no dconf keys, no X keymap changes, no changes to Firefox's
`profiles.ini`.

`./uninstall.sh` stops and disables both units, deletes them, and removes the
venv and `~/.local/state/dials/`. It deliberately **keeps
`~/.config/dials/config.toml`** — that file is your configuration, not
installer-owned state, so delete it by hand if you want it gone. Stopping the
daemon releases every grab immediately, so the numpad is back to stock behaviour
the moment the service stops, uninstalled or not.

## Resource footprint

Measured directly from `.venv/bin/dialsd` (Task 22), running against isolated
`XDG_CONFIG_HOME`/`XDG_STATE_HOME` scratch directories seeded with the two
reference Dials (`6`, `9`), idle for 65 s with no keypresses and no NumLock
toggle:

| Process | PSS | RSS | Idle CPU | Wakeups/s (idle) |
| --- | --- | --- | --- | --- |
| `dialsd` | 10.6 MB | 16.9 MB | 0.000 % | 0.0 |
| `dials-tray` (opt-in) | 24.8 MB | 62.7 MB | 0.055 % | ~2 |

PSS (`/proc/<pid>/smaps_rollup`) is the fairer number for both processes: it
discounts pages shared with the rest of the system, which for the daemon means
the CPython/libc baseline every Python process pays, and for the tray means the
GTK libraries that are already resident because other GTK apps on this desktop
have them mapped.

The plan's original budget line (0.0 % CPU / ≤ 16 MB RSS for `dialsd`, ≤ 45 MB
RSS / ≤ 0.01 % CPU for `dials-tray`) does not survive contact with a real
measurement and should not be read as met:

- `dialsd`'s RSS (16.9 MB) sits fractionally above the plan's 16 MB line before
  a single Dial fires. That floor is the Python interpreter plus `python-xlib`
  being resident, not anything this project's code does at idle — PSS (10.6 MB)
  is the more honest figure for what Dials itself actually costs.
- `dials-tray`'s RSS (62.7 MB) is well above the plan's 45 MB line, but the
  majority of it is shared GTK libraries other GTK apps on this machine already
  keep resident; PSS (24.8 MB) is what the tray itself actually adds.
- `dials-tray`'s CPU (0.055 %) is above the plan's 0.01 % line. That cost is
  `Gtk.StatusIcon`'s own mainloop overhead at 1 Hz, not the polling logic the
  budget was written against — one tick of the actual `led_mask` read costs
  ~29 µs, roughly five orders of magnitude below the 1 s poll interval.

None of this is a regression to chase: `dialsd`'s idle CPU measured exactly
0.000 % over the sample window and **voluntary context switches were 0 over 65 s
of idle** — the concrete proof that `select()` blocks with a `None` timeout and
the process is not scheduled at all when nothing is armed. Startup reported no
grab failures. `dials-tray` remains opt-in specifically because it is the only
component in the project that polls.

## Design and verification

The full design is `docs/superpowers/specs/2026-07-28-dials-design.md`. Every
load-bearing X11 claim was verified on this machine before being written down;
the eight probe scripts are in `docs/probes/` and can be re-run if GNOME,
Firefox, or the monitor layout changes.

## Human verification required

A few checks depend on physical hardware and human judgment and cannot be
covered by the automated test suite or by this task's measurement pass. Do
these once, after choosing to install:

- **CapsLock regression guard.** With NumLock off and CapsLock **on**, press a
  Dial key. It must still fire. This is the probe-07 mask-0 regression check:
  a grab that only tolerates mask 0 silently stops working the moment CapsLock
  is on.
- **Autorepeat guard.** **Physically hold down** a Dial key (not a scripted
  keypress) and confirm the window toggles once, not repeatedly. Only real
  hardware autorepeat exercises this; no unit test can generate it.
- **RandR hotplug.** Plug in or unplug a monitor while Dials are in use, then
  press a Dial whose configured monitor just appeared or disappeared, and
  confirm it falls back to primary (or reappears on the named output)
  correctly.
- **Tray tooltip.** With the tray running (`--tray`), hover the icon and
  confirm the tooltip text changes between "Dials live" (NumLock off) and
  "Dials dormant" (NumLock on) as you toggle NumLock.

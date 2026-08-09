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

A Dial comes to **whichever workspace you are on** and lives on no other, so
switching workspace leaves it behind. It is reachable from anywhere by its key;
it is not present everywhere. (It was `_NET_WM_STATE_STICKY` — present on every
workspace — until that turned out to be a literal reading of the wrong wish.)

```sh
dials list          # every slot
dials status        # paused or active, Dials bound, live outputs, config health
dials capture 9     # save a window's current position into its Dial
dials pause         # stand down for a game or remote desktop
dials              # curses numpad grid
```

## Configuration

Live config is `~/.config/dials/config.toml` — the only file anything reads.
`config/config.reference.toml` in this repo is a **reference snapshot**, read by
nothing. `dials config export` refreshes it but **regenerates it from the live
config and drops every comment**, and the comments there are load-bearing — hand-edit
it, or `git checkout` the file if you overwrite it.

Geometry is stored as a monitor name plus fractions, never absolute pixels,
because this machine's layout changes: `DP-1-1` was connected during one design
probe and gone by the next. An absent monitor falls back to primary.

`monitor` accepts three forms, checked in this order:

```toml
monitor = "edid:AW3425DWM"     # the display's own EDID model name
monitor = "internal"           # the built-in panel, whatever its connector is
monitor = "connector:HDMI-0"   # an exact connector name, explicitly
monitor = "HDMI-0"             # bare string: still a connector name, as before
```

Prefer `edid:` or `internal` for anything that is not a laptop-only setup:
they name the *display*, not the socket it happens to occupy this boot — see
below for why that distinction turned out to matter. `dials status` lists the
live connector and EDID name of every attached display, so the right value is
read off, never guessed.

### If every Dial suddenly opens on the wrong screen

Run `dials status`. A Dial that is not landing where it was configured is named
there, with what it wanted and what is present:

```
monitors:  HDMI-1-0 (AW3425DWM), eDP-1 (internal)
monitor:   WARNING slot 9: monitor 'HDMI-0' absent; using primary 'eDP-1'
```

The usual cause is not a monitor being unplugged — it is **the connector name
changing under an unchanged physical display.** An output's connector name
depends on which GPU X treats as primary, and that in turn follows whichever
GPU the firmware marks `boot_vga`: the primary GPU's outputs keep their plain
names, the other GPU attaches as a secondary RandR provider and its outputs
gain that provider's index spliced in. NVIDIA primary → ultrawide `HDMI-0`,
panel `eDP-1-1`. Intel (`modesetting`) primary → ultrawide `HDMI-1-0`, panel
`eDP-1`. Same cable, same panel, both times.

**This is not `system76-power graphics` switching between `nvidia` and
`hybrid`.** That was the first, wrong theory: the mode read `hybrid` on both
the boot that broke and the boot that worked, so it is not the trigger.
Confirmed instead from the `*` marking the primary PCI device in
`~/.local/share/xorg/Xorg.1.log`, which moved between the two boots, and from
`/sys/bus/pci/devices/0000:01:00.0/boot_vga` in sysfs. What flips `boot_vga`
itself is not settled — connecting the external monitor before power-on is the
leading hypothesis, from two data points, not proven.

The fix is to stop naming the socket: point `monitor` at the display's own
EDID name (`edid:AW3425DWM`) or at `internal`, either of which survives the
rename because neither depends on which GPU is primary. Full account and
design: `docs/OPEN-PROBLEMS.md` §6 and
`docs/superpowers/specs/2026-08-09-monitor-identity-design.md`.

## What it touches

- `~/.config/systemd/user/dialsd.service`, `dials-tray.service`
- `~/.local/share/dials/venv/`
- `~/.config/dials/config.toml` — seeded from the reference copy only if absent
- `~/.local/state/dials/` (pause flag, tray PID)
- `~/.local/share/icons/hicolor/scalable/apps/dials-shell*.svg` — the three tray
  icons. They live in the icon theme because the tray looks them up by *name*; a
  path inside the repo would not be discoverable and would break if the checkout
  moved.

No apt packages, no dconf keys, no X keymap changes, no changes to Firefox's
`profiles.ini`.

**Two things a working setup needs that neither script installs**, because both
belong to software this project does not own:

- `gsettings set org.gnome.shell.extensions.pop-shell show-skip-taskbar false`,
  without which Dials appear in alt-tab and the workspace overview. `install.sh`
  checks and prints it; see the section below for why.
- The Firefox Dial's profile tuning at `~/.mozilla/firefox/<profile>/user.js`. See
  `docs/FIREFOX-DIAL6.md`.

The Firefox Dial's own profile is a separate matter: it has hand-written tuning at
`~/.mozilla/firefox/<profile>/user.js` that neither script installs or removes.
See `docs/FIREFOX-DIAL6.md`.

`./uninstall.sh` stops and disables both units, deletes them, and removes the
venv, the three tray icons, and `~/.local/state/dials/`. It deliberately **keeps
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

## Development

```sh
uv venv --python /usr/bin/python3.10 --system-site-packages --allow-existing .venv
VIRTUAL_ENV=.venv uv pip install -e . pytest
.venv/bin/python -m pytest -q
```

`.venv/` is git-ignored and is separate from the runtime venv `install.sh` creates
at `~/.local/share/dials/venv` — that one deliberately has no pytest.

Three constraints that are not obvious and each cost time once:

- **`--system-site-packages` is required**, because the tray and the confirm dialog
  reach the system PyGObject, which is ABI-locked to python3.10. Never `uv run` or
  `uv sync`: both recreate the venv *without* system site packages.
- **Run pytest from the repo root, and not from a directory that contains a `dials/`
  package.** `sys.path[0]` is the working directory, so a sibling checkout or a
  worktree silently shadows the installed package and you test the wrong copy.
  `PYTHONPATH` does not fix it — the editable-install finder runs first.
- **Mutation-test any fix that is one line in a caller.** This project has twice
  shipped a fix covered only by a test on the pure predicate, where reverting the
  caller left the whole suite green. Break the call site, watch a test fail, restore.

## Design and verification

The full design is `docs/superpowers/specs/2026-07-28-dials-design.md`. Every
load-bearing X11 claim was verified on this machine before being written down;
the ten probe scripts are in `docs/probes/` and can be re-run if GNOME,
Firefox, or the monitor layout changes.

`docs/IMPLEMENTATION-DECISIONS.md` records every decision taken while building
this and what each one implies; `docs/RETROSPECTIVE.md` records the process
failures worth not repeating; **`docs/OPEN-PROBLEMS.md` is what is still wrong or
unconfirmed** — read that before concluding something is broken.

## If Dials show up in alt-tab or the workspace overview

`_NET_WM_STATE_SKIP_TASKBAR` is necessary but **not sufficient** on Pop!_OS. GNOME
Shell honours it in both places, but `pop-shell` monkey-patches the overview and the
switcher so minimise-to-tray applications stay reachable — and its test matches
every Dial window. The hint meant to hide a Dial is what makes Pop Shell show it.

Fix — one command, applies immediately, no reload needed:

```sh
gsettings set org.gnome.shell.extensions.pop-shell show-skip-taskbar false
```

**Do not bother with `skiptaskbarhidden` rules in `~/.config/pop-shell/config.json`.**
Pop Shell documents them and its own predicate reads them, but `Config.reload()`
copies out `float` and `log_on_focus` and drops `skiptaskbarhidden` — so that list
is permanently empty and only Pop Shell's hardcoded exceptions ever apply. That is
an upstream bug, and it is the real reason Guake escapes while nothing you add can.
Any rules already there are harmless and become correct if it is ever fixed.

The trade: this key is system-wide, so applications that genuinely minimise to the
tray are hidden from the overview and alt-tab too. That is the feature being
switched off.

Also worth knowing: **Alt+F2 → `r` does not reload GNOME Shell on this install.**
`/usr/libexec/mutter-restart-helper` is not shipped, so mutter logs "Failed to start
restart helper" and keeps running the old process — which looks exactly like a
config that loaded and did nothing. Not needed for the fix above, but it matters for
anything read at extension-enable time; use `gnome-extensions disable … && enable …`
or reboot.

`install.sh` checks the gsettings key and prints the command if it is still `true`.
Full reasoning: the design doc's *Pop Shell interaction* section.

## The Firefox Dial

Slot 6 runs its own Firefox profile, which is tuned differently from the main one
— including one pref that is deliberately *not* copied from it, because it would
break WhatsApp Web. See `docs/FIREFOX-DIAL6.md` for why, and
`config/firefox-dial6-user.js.reference` for the prefs themselves. Read the first
before changing the second.

## Human verification required

These depend on physical hardware or human judgment and cannot be covered by the
test suite. Dials is installed and in daily use as of 2026-07-31, and the tray
icon is confirmed to render — everything below is still **outstanding**:

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

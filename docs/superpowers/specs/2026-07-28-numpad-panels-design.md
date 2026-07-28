# Numpad Panels — design

Date: 2026-07-28
System: Pop!_OS 22.04 LTS, GNOME 42 (mutter), X11, three monitors.

## Problem

The numeric keypad is only ever used with NumLock **on**, to type digits. With NumLock
**off** its eleven navigation keys (`KP_Home`, `KP_Up`, `KP_Prior`, …) are dead weight —
duplicate Home/PageUp/arrow keys that never get pressed.

That makes NumLock-off a completely free 15-key layer. This project turns it into a set of
quake-style dropdown panels: NumLock-off `9` shows Spotify overlaid on the current work and
hides it again; NumLock-off `6` does the same for a dedicated Firefox window; other keys are
bound to whatever else is wanted. NumLock-on typing is unaffected.

## Why this is possible at all

X11 assigns numpad keys **two keysyms on one keycode**, selected by NumLock, and NumLock is
exposed as modifier `mod2`:

```
keycode  81 = KP_Prior KP_9      # NumLock off -> KP_Prior, on -> KP_9
mod2        Num_Lock (0x4d)
```

A passive `XGrabKey` on a keycode with an **empty modifier mask** therefore matches only when
no modifiers are active — including no `mod2`. NumLock-on presses never match the grab and are
delivered to the focused application as normal digits.

This is the load-bearing assumption of the whole design, so it was verified empirically rather
than assumed. See `docs/probes/`.

## Verified findings

Every claim below was measured on this machine, not taken from documentation. The probe
scripts are kept in `docs/probes/` so they can be re-run if GNOME or Firefox changes.

| Claim | Result | Probe |
| --- | --- | --- |
| All 16 numpad keycodes grabbable with modifier mask 0 | Yes — no `BadAccess`, so mutter, pop-shell and Guake contend for none of them | `01` |
| NumLock **off** → grab fires | Yes, delivered as `(keycode 91, state 0)` | `01` |
| NumLock **on** → grab does not fire, digit types normally | Yes, nothing delivered | `01` |
| `firefox --class=NAME` sets WM_CLASS on Firefox 152 | Yes → `WM_CLASS = "firefox", "FFPanelProbe"`; the **class** (second) field carries the custom value | `02` |
| Geometry can be forced on a foreign window | Yes, pixel-exact via `configure()` | `02`, `04` |
| mutter honours `_NET_WM_STATE_ABOVE`, `STICKY`, `SKIP_TASKBAR`, `SKIP_PAGER` | Yes, all four, with source indication 1 or 2 | `03` |
| Panel hints survive a minimize/restore round trip | Yes, 4/4 | `03` |
| `_NET_WM_STATE` reports `HIDDEN` when minimized and `FOCUSED` when focused | Yes | `03` |
| Hide/show without `xdotool` | Yes — `WM_CHANGE_STATE`→`IconicState` and `_NET_ACTIVE_WINDOW` | `04` |
| Desktop notifications without a new apt package | Yes — `gdbus` → `org.freedesktop.Notifications` | `04` |

Two facts established from source rather than by probe:

- GNOME 42 excludes `skip_taskbar` windows from alt-tab. `mutter/src/core/window-private.h`:
  `#define META_WINDOW_IN_NORMAL_TAB_CHAIN(w) (meta_window_is_focusable (w) && META_WINDOW_IN_NORMAL_TAB_CHAIN_TYPE (w) && (!(w)->skip_taskbar))`
- Guake's top-bar icon comes from `Gtk.StatusIcon` (grep of `/usr/lib/python3/dist-packages/guake/`),
  and it does appear, so the legacy status-icon path works here with the already-enabled
  `ubuntu-appindicators@ubuntu.com` extension. No `gir1.2-appindicator3` package is needed.

**Consequence: the runtime needs no new system package.** `python-xlib` 0.29 and `gdbus` are
already present; `wmctrl` and `notify-send` are absent and deliberately not adopted.

## Key inventory

16 keycodes are grabbable. `.` is reserved for assign mode, leaving **15** bindable slots.

| Slot | Keycode | NumLock-off keysym | NumLock-on keysym |
| --- | --- | --- | --- |
| `7` `8` `9` | 79 80 81 | `KP_Home` `KP_Up` `KP_Prior` | `KP_7` `KP_8` `KP_9` |
| `4` `5` `6` | 83 84 85 | `KP_Left` `KP_Begin` `KP_Right` | `KP_4` `KP_5` `KP_6` |
| `1` `2` `3` | 87 88 89 | `KP_End` `KP_Down` `KP_Next` | `KP_1` `KP_2` `KP_3` |
| `0` | 90 | `KP_Insert` | `KP_0` |
| `.` — **reserved, assign mode** | 91 | `KP_Delete` | `KP_Decimal` |
| `/` `*` `-` `+` `enter` | 106 63 82 86 104 | same in both states | same in both states |

The five operator keys and numpad Enter carry one keysym regardless of NumLock. They are still
usable, because the daemon grabs on **keycode + empty mask**, not on keysym: with NumLock on,
`mod2` is set, the grab does not match, and the key types `+`/`-`/`*`/`/`/Enter as normal.

## Approach

**Chosen: a single python-xlib daemon plus a curses CLI**, mirroring the sibling `clipboard_history`
project (`clip`) — event-driven, no polling, no new packages, no `wmctrl`/`xdotool` at runtime.
Every mechanism it relies on is demonstrated working in `docs/probes/`.

**Rejected — `xbindkeys` / `sxhkd` plus shell scripts.** Needs a new package, and there is
nowhere to host the three-state toggle logic or the monitor-fraction geometry model. Each
keypress would fork a shell that re-parses config and re-queries X. Since probe `01` shows a
short xlib grab loop already does the job, the dependency buys nothing.

**Rejected for now — `keyd` at the evdev layer.** It would survive a move to Wayland, but today
it is strictly worse: it needs a root daemon, it cannot read window state (so the three-state
toggle is impossible), and it would have to track NumLock itself instead of reading `mod2` from
X. Recorded as the migration path if this machine ever moves to Wayland/COSMIC — `pop-cosmic`
is already installed.

## Architecture

Package `npad`, three entry points:

| Entry point | Role | Dependencies |
| --- | --- | --- |
| `npadd` | The daemon: grabs keys, drives windows | `python-xlib` only, no GTK |
| `npad` | CLI subcommands + curses TUI | stdlib only |
| `npad-tray` | Optional top-bar status icon, separate systemd unit | GTK via system `gi` |

The tray is a separate process and a separate unit so the always-on daemon never pays GTK's
~30 MB. Same reasoning `clip` uses to keep `clipd` GTK-free while `clip-popup` is GTK.

### Modules

Kept small and single-purpose, with I/O injected so logic is testable without an X server —
following `clip`'s established style.

| Module | Responsibility | Pure? |
| --- | --- | --- |
| `keys.py` | Slot ↔ keycode table, slot name parsing | yes |
| `geometry.py` | Monitor rect + fractions + struts → pixel rect; primary fallback | yes |
| `panels.py` | `PanelController` — the state machine; decides SHOW/RAISE/HIDE | yes (injected ops) |
| `config.py` | XDG paths, TOML load/save, `Binding` dataclass, validation, defaults inheritance | mostly |
| `icons.py` | `.desktop` `Icon=` → Nerd Font glyph, with per-key override | yes |
| `monitors.py` | RandR enumeration: name → rect | I/O |
| `windows.py` | EWMH: find by class, read state, hide/show/raise, set hints, set geometry | I/O |
| `grab.py` | `XGrabKey` install/remove, `BadAccess` reporting, event loop plumbing | I/O |
| `assign.py` | Assign-mode arming, capture, write-back | mixed |
| `launcher.py` | Confirm-then-launch, `.desktop` lookup, wait-for-window | mixed |
| `notify.py` | `gdbus` notification wrapper; best-effort, never raises | I/O |
| `cli.py` | `list`, `bind`, `unbind`, `capture`, `reload`, `status`, `config` | mixed |
| `tui.py` | curses numpad grid, window picker, per-key editor | I/O |
| `tray.py` | `Gtk.StatusIcon` showing running/paused | I/O |

### Keypress flow

```
KeyPress(keycode, state == 0)
  -> keys.slot_for(keycode)                     "9"
  -> config.binding("9")                        Binding(match_class="spotify", ...)
  -> windows.find(binding.match_class)          window id | None
       None  -> launcher.arm_confirm(binding)   notify, grab Return+KP_Enter for 5s
       found -> windows.read_state(win)
                  HIDDEN       -> SHOW   apply geometry + hints, then activate
                  not FOCUSED  -> RAISE  activate only
                  FOCUSED      -> HIDE   WM_CHANGE_STATE -> IconicState
```

### State machine

| Window state | Action | Geometry re-applied |
| --- | --- | --- |
| not found | confirm-then-launch | — |
| `_NET_WM_STATE_HIDDEN` | apply geometry + hints, activate | yes |
| visible, no `_NET_WM_STATE_FOCUSED` | activate only | only if `pin_geometry` |
| visible and `_NET_WM_STATE_FOCUSED` | iconify | — |

Three states rather than a strict two-state toggle, because a panel with
`on_focus_loss = "normal"` can be buried: pressing its key while it is buried must raise it,
not hide it. A strict toggle would need two presses to surface a buried window.

Geometry is **not** re-applied on a plain raise, so hand-nudging a window is not undone on
every keypress. `npad capture <slot>` saves the current position instead; `pin_geometry = true`
opts a key into strict enforcement.

### Focus-loss behavior

`on_focus_loss` is one setting with three values, not two independent flags. "Can be buried"
and "hides when focus leaves" are mutually exclusive — hiding on focus loss means it can never
be buried — so they are values of the same knob:

| Value | Behavior | Mechanism |
| --- | --- | --- |
| `normal` | Stays visible, ordinary stacking, can be buried | no hint |
| `above` | Stays visible, never buried | `_NET_WM_STATE_ABOVE` |
| `hide` | Hides as soon as focus leaves | root `PropertyNotify` on `_NET_ACTIVE_WINDOW` |

`hide` needs **no polling**: the daemon already has an X connection and subscribes to
`PropertyNotify` on the root window for `_NET_ACTIVE_WINDOW`. `above` and `normal` need no
watching at all — the WM does the work.

`_NET_WM_STATE_STICKY`, `SKIP_TASKBAR` and `SKIP_PAGER` are applied to every panel
unconditionally: panels must be reachable from any workspace and must never appear in alt-tab.

**Accepted cost:** per the mutter source above, `skip_taskbar` is the same flag the window list
uses, so panels get no taskbar entry either. Alt-tab exclusion is the requirement; taskbar
presence was optional, so this is the deliberate trade. Whether the cosmic-dock still marks the
*application* as running is a side effect to observe, not a requirement.

## Configuration

Hand-editable TOML, and the single source of truth — the CLI and TUI only edit this file.
Per-key values inherit from `[defaults]`.

### Config location

The canonical file lives **in this repository**, at `config/config.toml`, and
`~/.config/npad/config.toml` is a **symlink** to it:

```
~/.config/npad/config.toml -> ~/Documents/personal/pc_tweaks/numpad_panels/config/config.toml
```

The real file is kept in the repo rather than in `~/.config` so that the live configuration sits
in the workspace where it is version-controlled, diffable, and visible to future agents working
on this project. The symlink direction matters: a symlink committed *into* the repo pointing out
to `$HOME` would be an absolute path that breaks on any other machine and reads as a dangling
link in `git status`. Pointing inward keeps the repo self-contained.

Consequences, all intentional:

- The daemon and CLI open the XDG path as normal and never need to know about the symlink.
- Editing either path edits the same file.
- Config changes made through the TUI show up as ordinary git diffs in this project, so binding
  changes get committed alongside code.
- The config contains no secrets — app class names, launch commands, monitor names and
  fractions — so it is safe to commit. It is deliberately **not** in `.gitignore`.
- If the project folder is moved or deleted the symlink dangles. `npad status` reports this, and
  `install.sh --relink` repairs it after a move.

Runtime state — the pause flag and the tray PID file — stays in `~/.local/state/npad/` and is
**not** moved into the repo: it is machine state, not configuration. The last-good config is held
in memory by the running daemon and never written to disk.

### Schema

```toml
[defaults]
monitor       = "HDMI-0"              # ultrawide; falls back to primary if unplugged
rect          = [0.0, 0.0, 0.5, 1.0]  # x, y, w, h as fractions of the monitor
on_focus_loss = "hide"                # normal | above | hide
pin_geometry  = false
ignore_struts = false

[keys."9"]
label         = "Spotify"
match_class   = "spotify"             # WM_CLASS *class* field (second field)
launch        = "/snap/bin/spotify"
icon          = ""                   # nerd font glyph; auto-guessed from .desktop if omitted
monitor       = "DP-1-1"
rect          = [0.0, 0.0, 1.0, 0.6]
on_focus_loss = "hide"

[keys."6"]
label         = "Firefox panel"
match_class   = "FFPanel"
launch        = "firefox -P panel --class=FFPanel --no-remote --new-instance"
monitor       = "HDMI-0"
rect          = [0.25, 0.0, 0.5, 1.0]
on_focus_loss = "above"
```

`rect` fractions are resolved against the monitor rect **minus any panel struts intersecting
that monitor** (GNOME top bar, cosmic-dock), so `h = 1.0` does not slide under the top bar.
`ignore_struts = true` uses the raw monitor rect instead.

Default monitor is `HDMI-0`, the 3440×1440 ultrawide. The portrait `DP-1-1` may be removed from
the setup later, which is exactly why geometry is stored as monitor-name + fractions rather than
absolute pixels: an absent monitor falls back to primary instead of placing a window off-screen.

Current layout for reference:

| Output | Resolution | Position | Role |
| --- | --- | --- | --- |
| `HDMI-0` | 3440×1440 | +0+0 | ultrawide, default for panels |
| `eDP-1-1` | 2560×1440 | +373+1440 | laptop, primary, carries the top bar |
| `DP-1-1` | 768×1366 | +3440+497 | portrait, may be retired |

## Window identification

Match on the **class** field of `WM_CLASS`. For most apps that is enough: Spotify's Snap sets
`StartupWMClass=spotify` (see the sibling `spotify_width` project).

Firefox needs more care, because Firefox refuses to run two instances against one profile, so a
"panel" Firefox window cannot simply be a second window of the normal browser. Options weighed:

- **Chosen — dedicated panel profile with a custom class.**
  `firefox -P panel --class=FFPanel --no-remote --new-instance`. Probe `02` confirms `--class`
  works on Firefox 152, giving `WM_CLASS = "firefox", "FFPanel"` — unambiguous, stable across
  reboots, never confused with normal browsing. Cost: separate cookies, history and extensions,
  plus a second Firefox process.
- Rejected — matching a normal-profile window by title. Firefox window titles track the active
  tab, so the binding breaks the moment the tab changes.
- Rejected — binding a hand-picked X window id. Not stable across reboots; the binding dies with
  the window.

The panel profile is created once, by hand, and documented in the README. `install.sh` does not
create it — silently creating Firefox profiles is too surprising for an installer.

## Assign mode

Pressing `.` (NumLock off) binds the **currently focused window** to the next numpad key:

1. `.` → read `_NET_ACTIVE_WINDOW`, notify *"Assign mode: press a numpad key"*, arm for 5 s.
2. Next numpad key → write a binding: `label` from `_NET_WM_NAME`, `match_class` from `WM_CLASS`,
   `monitor` + `rect` derived from where the window already sits, remaining fields from
   `[defaults]`. Notify *"Bound 4 → Slack"*.
3. `.` again, or the 5 s timeout, cancels.

`Escape` is deliberately **not** grabbed as a cancel key — that would steal Escape
system-wide for those five seconds. Pressing `.` again is the cancel.

## Launch confirmation

An unbound-but-configured app is not launched on the first press, so a stray keypress never
spawns anything. Instead: notify *"Spotify not running — press Enter to launch"* and arm for 5 s.
Either main `Return` (keycode 36) or `KP_Enter` confirms.

While armed, `Return` and `KP_Enter` are grabbed with **mask 0 only**, and ungrabbed immediately
on the first press or at timeout. So plain Enter is intercepted only inside that short window,
and only unmodified — `Shift+Enter` and `Ctrl+Enter` are never touched. `Return` must be
`BadAccess`-checked at grab time like every other key, since it is more likely to be contended
than the numpad keys; if the grab fails, fall back to `KP_Enter` alone and log it.

If `enter` is itself bound to a panel, that binding is suspended for the duration of the confirm
window. This is documented rather than designed around, because reserving Enter permanently
would waste one of only 15 slots.

After confirmation: run `launch`, wait up to 10 s for a window matching `match_class`, then apply
geometry and hints and activate it. If no window appears, notify and give up.

## Interfaces

### CLI

```
npad list                 # table of all 15 slots, bound or not
npad bind <slot>          # bind interactively (opens the window picker)
npad unbind <slot>
npad capture <slot>       # save the matched window's current geometry into its binding
npad reload               # SIGHUP the daemon
npad pause                # release all grabs; numpad behaves stock in both NumLock states
npad resume               # re-install grabs
npad status               # daemon state, grab conflicts, matched windows, config link health
npad config               # print both the XDG path and the repo path it resolves to
npad                      # no args -> curses TUI
```

`pause` exists because these grabs are global: if a game or a remote-desktop session needs the
raw numpad keys, there has to be a way to stand down without stopping the service. It is a flag
file in the state dir, so it survives a daemon restart, and it is what the tray icon reflects.

### TUI

stdlib `curses`, no new dependency — matching `clip`'s lean dependency policy and carrying no
risk with the Python 3.10 / system-site-packages venv constraint. A numpad grid mirroring the
physical layout, arrow-key navigation, per-key editor, and a window picker listing everything in
`_NET_CLIENT_LIST` so an open window can be bound without knowing its class name.

Icons are **Nerd Font glyphs**, auto-guessed from the app's `.desktop` `Icon=` name with a
per-key `icon` override. Ghostty ships JetBrains Mono Nerd Font by default, so glyphs render with
no font install. Real bitmap icons via the Kitty graphics protocol are deferred: Ghostty supports
it, but driving it from inside a curses screen is fiddly and degrades badly in other terminals.

### Tray

Optional `npad-tray` unit showing a `Gtk.StatusIcon` in the top bar, so the daemon's running
state is visible next to the clock and Guake's icon. Proven feasible because Guake does exactly
this here. Opt-in via `install.sh --tray`, because it costs a GTK process the daemon itself avoids.

## Error handling

Follows `clip`'s degrade-never-crash rule — nothing in a keypress path may kill the daemon.

| Failure | Response |
| --- | --- |
| Window dies between find and act | X error logged and swallowed |
| Configured monitor absent | fall back to primary, then to screen 0 |
| Config fails to parse | keep last-good config in memory, notify once |
| Config symlink dangles (project moved) | treat as no config, run with `[defaults]` only, notify once, flag it in `npad status` |
| `launch` command fails | notify, give up |
| Window never appears within 10 s | notify, give up |
| `gdbus` notification fails | silent |
| Key grab returns `BadAccess` | log which slot, keep the other grabs, report in `npad status` |
| X server drops | `Restart=on-failure` plus `PartOf=graphical-session.target` |

`SIGHUP` re-reads config and re-installs grabs, so `npad reload` never needs a restart.

## Testing

pytest, with ops injected so no X server is required — mirroring `clip`'s
`runner=subprocess.run`-as-parameter style.

| Area | What is covered |
| --- | --- |
| `geometry.py` | fractions → pixels, strut subtraction, missing-monitor fallback, clamping |
| `panels.py` | the state machine table, every state → action pair |
| `config.py` | load/save round trip, defaults inheritance, validation, bad values |
| `keys.py` | slot ↔ keycode mapping both directions, reserved-slot handling |
| `icons.py` | `.desktop` → glyph mapping, override precedence |
| `assign.py` | arming, timeout, cancel, write-back |
| `launcher.py` | confirm arming/timeout, wait-for-window with a fake clock |
| `cli.py` | each subcommand against an injected config |

Not unit-tested, smoke-tested by hand: curses rendering, the GTK tray, and real X I/O. Same
split `clip` documents.

## Install and revert

`install.sh`, following `clip`'s pattern:

1. Check `python3.10`, `python-xlib`, `gdbus`, `uv`.
2. `uv venv --python /usr/bin/python3.10 --system-site-packages` at `~/.local/share/npad/venv`.
   The pinned interpreter and `--system-site-packages` matter only for the optional GTK tray, but
   the constraint is inherited from `clip`'s environment gotcha and kept for consistency.
   Use `uv pip`, never `uv run`/`uv sync`.
3. `uv pip install -e .`
4. Seed `config/config.toml` in the repo if absent, then symlink `~/.config/npad/config.toml` to
   it. If a **real file** already exists at the XDG path, move it into the repo as the seed and
   replace it with the symlink; never clobber an existing config silently. `--relink` repairs the
   symlink after the project folder moves.
5. Install and enable `npadd.service`; with `--tray`, also `npad-tray.service`.
6. Measure idle CPU and RSS and record them in the README, per the AGENTS.md daemon rules.

`uninstall.sh` stops and disables both units, removes the venv, and removes the
`~/.config/npad/` symlink. It leaves `config/config.toml` in the repo untouched — that is
version-controlled project content, not installed state.

**Revert plan.** Nothing outside the project is modified except: two `systemd --user` units in
`~/.config/systemd/user/`, a venv at `~/.local/share/npad/`, a symlink at
`~/.config/npad/config.toml`, and state under `~/.local/state/npad/`. No dconf keys, no system
packages, no changes to the X keymap. Stopping `npadd` releases every key grab immediately and restores stock numpad
behavior with NumLock off; nothing persists past process exit. The optional Firefox panel
profile, if created, is removed with `firefox -P` or by deleting its profile directory.

## Deferred

- **Crosshair click-to-pick** binding (`xdotool selectwindow`) — the assign-mode hotkey and the
  TUI window picker cover the need; this is a small addition if it turns out to be wanted.
- **Real bitmap app icons** in the TUI via the Kitty graphics protocol.
- **Alt-tab-hidden but taskbar-visible** — impossible with EWMH hints alone, since mutter reads
  one flag for both. Would need a GNOME Shell extension.
- **Wayland / COSMIC support** — would mean moving key handling to `keyd` at the evdev layer and
  replacing EWMH window control with a compositor-specific path.
- **Sliding animation.** An external client cannot animate a foreign window's geometry smoothly
  under mutter; panels snap into place. Only a Shell extension could do better.

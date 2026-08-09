# Implementation decisions — Dials

## What this document is for

Other documents already exist and this one deliberately does not repeat them:

| Document | Covers |
| --- | --- |
| `docs/superpowers/specs/2026-07-28-dials-design.md` | The **design** and its rationale, settled before any code |
| `docs/superpowers/specs/2026-08-09-monitor-identity-design.md` | The **design** for naming a Dial's *display* rather than its connector, and why the connector rename bites |
| `docs/superpowers/plans/2026-07-30-dials*.md` | The task breakdown and the code as planned |
| `docs/superpowers/plans/2026-08-09-monitor-identity.md` | The task breakdown for the monitor-identity feature |
| `docs/RETROSPECTIVE.md` | The **process** lessons and failure patterns |
| `docs/OPEN-PROBLEMS.md` | What is still wrong, unconfirmed, or worked around |
| `docs/FIREFOX-DIAL6.md` | Why the Firefox Dial's profile is tuned, and how |

What none of them records is the set of decisions forced by **executing** the
plan: what measurement contradicted, what was changed and why, what was
deliberately *not* changed, and what each choice implies for anyone editing this
code later. That is this document.

Section 8 covers the changes made after first install, once the thing was in daily
use. Those are the decisions the plan could not have anticipated, because they
came from watching it run.

Where a decision is already argued in the spec, it is named here only with its
consequence, not re-argued.

---

## 1. Decisions forced by measuring X, not reasoning about it

Every claim in this section was verified on the target machine. The probe scripts
in `docs/probes/` are the evidence and can be re-run.

### 1.1 Grab the cross product of tolerated lock modifiers, never `mod2`

X matches passive-grab modifier masks **exactly**. A mask-0-only grab therefore
stops matching the moment any *other* lock modifier is active: with CapsLock on,
every Dial silently died. Measured, then fixed by grabbing `{0, LockMask}` — 32
grabs for 16 keycodes — with the tolerated set derived at runtime from
`get_modifier_mapping()` rather than hardcoded.

**Implication.** Adding a key means adding masks, not just a keycode. The set must
never include whichever modifier holds `Num_Lock`, because that exclusion *is* the
project: include it and Dials fire while NumLock is on and the numpad stops typing
digits. `tests/test_grab.py` guards both halves, including the one input where the
guard is decisive (a modifier row holding `Num_Lock` **and** a tolerated key).

### 1.2 A same-client duplicate `GrabKey` is a silent replace, not `BadAccess`

The most expensive wrong belief in the project, and it was mine. The code, its
comments and its tests all asserted that grabbing an already-grabbed keycode
returns `BadAccess`, and concluded the temporary confirm-grab manager could never
disturb the permanent grabs. Measured:

```
client A, first grab                      OK
client A, SECOND grab (same kc + mask)    OK          <- silent replace
client B grabbing while A holds it        BadAccess   <- cross-client only
client B after ONE ungrab from A          OK          <- A's grab fully removed
```

`BadAccess` is a *cross-client* protection. The daemon and its temporary
`GrabManager` share one `Display`, so `KP_Enter` really was being grabbed and then
ungrabbed, deleting the permanent enter-slot grab on **every** confirmation cycle.

**Decision.** The temporary manager grabs `Return` only (`grab_keycodes()`).
`KP_Enter` is already permanently grabbed, so the daemon already receives it and
`is_confirm()` still accepts it (`confirm_keycodes()`). "What confirms" and "what
must be temporarily grabbed" are now separate accessors that document each other,
because conflating them is what caused this. Evidence:
`docs/probes/09-duplicate-grab-same-client.py`.

**Implication.** Never create a second `GrabManager` over keycodes the first one
holds on the same connection. If two managers must ever coexist, ownership has to
be tracked explicitly — but the better answer is not to need it.

**Knock-on, accepted.** The abandon guard — refuse to arm if `Return` could not be
grabbed at all — is now *conservative* rather than forced, because `KP_Enter`
remains permanently grabbed and could in principle still confirm. It was kept
anyway: it is the only path that tells you `Return` was taken by another client,
and arming while saying "press Enter" when the obvious key is contended is worse
than declining. The comment says so rather than implying the guard is necessary.

### 1.3 Geometry read and write disagree by the window frame

`geometry()` returns the **client** origin (`translate_coords`); `configure()` is
interpreted by mutter as the **frame** position under NorthWest gravity. For
server-side-decorated windows those differ by the titlebar. Measured:

```
_NET_FRAME_EXTENTS (l,r,t,b) : [0, 0, 37, 0]
client origin before          : (488, 1617, 500, 360)
client origin after round trip: (488, 1654, 500, 360)   drift dy = 37
```

Spotify — one of the two shipped Dials — is SSD with exactly those extents, so
every show placed it 37 px low and every `capture` compounded another 37 px.

**Decision.** Position with `_NET_MOVERESIZE_WINDOW` carrying **`StaticGravity`**
and the pager source indication, not a plain `ConfigureRequest`. Four candidates
were measured on one real SSD window before choosing — plain `configure()`, and
`_NET_MOVERESIZE_WINDOW` with gravity 0, NorthWest, and Static:

| Mechanism | Lands where asked | Fixed point |
| --- | --- | --- |
| `configure()` | no, `dy=+37` | no, drifts `+37` again |
| MOVERESIZE gravity 0 | no, `dy=+37` | no |
| MOVERESIZE NorthWest | no, `dy=+37` | no |
| **MOVERESIZE Static** | **yes** | **yes** |

Static is a fixed point on SSD, on CSD (no frame at all), and on a *minimized*
window — which matters, because that is the show path. Evidence:
`docs/probes/10-frame-extents-and-geometry-round-trip.py`.

**Implication.** The acceptance criterion for any geometry change is that
`geometry()` → `apply_geometry()` is a **fixed point**: a window told to go where
it already is must not move. Assert that, not just "the numbers look right".
Compensating by subtracting the frame extent would have been the wrong fix — the
docstring warns against it explicitly — because it works only for SSD windows and
breaks CSD ones.

**One residual, and it is mutter's, not ours.** mutter refuses to draw a
server-side titlebar off the top of the screen, so an SSD client is clamped to
`y >= top extent`: asked `y = 0/10/20/36` all land at 37, while `y >= 37` lands
exactly. So the reference config's `y = 0` Spotify rect still shows its client
37 px down — but *once*, deterministically, and `capture` no longer accumulates,
which was the actual bug. Every clamped landing is still a fixed point.

### 1.4 `pending_events()` drains the socket — do not "fix" the event loop

An external review called the drain loop critical and proposed
`range(max(1, pending_events))`. Measured before acting: `pending_events()` calls
`send_and_recv(recv=1)`, so it *reads* the socket and reports resulting events; an
idle connection fired `select()` 0/3 times, so there was no spin; and
`next_event()` on an empty queue **blocks indefinitely**. The proposed fix would
have frozen every Dial and every timeout.

A `0` means the readable bytes were a reply or error, already consumed — returning
to `select()` is correct. The loop carries a comment saying exactly this, because
the same change was proposed twice.

**But the snapshot count was still wrong**, for a different reason. Handlers make
dozens of synchronous round trips (`list_windows()` is ~5 per window) and
python-xlib queues events that arrive while it waits for those replies. Those
events were not in the snapshot and the socket was already drained, so the loop
returned to a `select()` that blocks with a `None` timeout — and a focus-change
`PropertyNotify` swallowed mid-handler could wait indefinitely, leaving an
`on_focus_loss="hide"` panel visible until some unrelated event arrived.

**Decision.** Drain *while* `pending_events()` is non-zero, which never calls
`next_event()` on an empty queue and therefore cannot block.

**Implication.** These two changes look nearly identical and differ decisively:
`range(max(1, pending))` blocks forever, `while pending_events()` cannot. The
comment in the loop distinguishes them by name, because the dangerous one was
proposed twice.

### 1.5 `translate_coords`' operand order was already right

The same review called the coordinate maths inverted and proposed swapping the
operands. Verified against `xwininfo` ground truth: as shipped it returned
`(742, 418)`, matching truth; the proposed swap returned `(-742, -418)`. In
python-xlib `self` is the **destination**. Applying that "fix" would have created
the exact bug it warned about.

### 1.6 `keysym_to_string` mis-reports some keysyms rather than returning `None`

It carries an explicit special-case list — `BackSpace`, `Tab`, `Clear`, `Return`,
`Pause`, `Escape`, `Delete`, **`Scroll_Lock`** — for which it returns
`chr(keysym & 0xff)`. So `Scroll_Lock` yields a truthy `'\x14'`, which shadowed the
authoritative name table in an `or` chain and silently dropped ScrollLock's
modifier from the tolerated set. `NoSymbol` (keysym 0) is likewise truthy as
`'\x00'`.

**Decision.** The table is consulted **first**, with an explicit `if not ks` guard
for `NoSymbol`.

**Implication.** Any keysym→name lookup here must be table-first. Latin-1
convenience functions are not a safe default for lock keys.

### 1.7 `_NET_WM_STATE_FOCUSED` is a decoration hint, not a focus flag

EWMH permits a WM to set it on several windows at once, so it cannot be the toggle
predicate. `_NET_ACTIVE_WINDOW` is single-valued by definition and is used
instead. On this mutter the property *did* behave exclusively when tested — the
stricter predicate was adopted anyway, because correctness by specification beats
correctness by observed behaviour the WM is not obliged to keep.

### 1.8 Activation is a request the WM may refuse

Confirmed accidentally and then deliberately: a de-iconified window did **not** win
`_NET_ACTIVE_WINDOW` when activated with a wall-clock timestamp. Hence three
decisions — send the triggering `KeyPress.time` (never `CurrentTime`), use source
indication 2 (pager), and **verify** the outcome rather than assume it.

**Implication.** The hide guard arms only once the active window has been *observed*
to equal the Dial's window. Arming on "we sent the request" is the bug this design
exists to prevent: a hide-on-focus-loss Dial would hide itself the instant it
appeared.

### 1.9 Nothing on this system reserves screen space

No managed window sets `_NET_WM_STRUT`; `_NET_WORKAREA` equals the full root box.
cosmic-dock is `dock-fixed=false` with autohide and intellihide, so it never
publishes struts.

**Decision.** Geometry resolves against the raw monitor rect. An earlier draft
intersected every monitor with `_NET_WORKAREA`, which is *wrong*: that property is
one desktop-wide rectangle, so a panel reserving one edge would have shrunk
monitors it never touched. The "it's free and starts working automatically"
justification was itself the error — it was not free, only invisible while every
inset is zero.

**Implication.** If reserved space ever appears, aggregate
`_NET_WM_STRUT_PARTIAL` per monitor. Do not reach for `_NET_WORKAREA`.

### 1.10 python-xlib version drift is real

Probes ran against the system's 0.29; the venv installs 0.33. `keysym_to_string`
behaves identically in both, but `randr.get_monitors` is **absent in 0.29 and
present in 0.33**. The RandR 1.2 path is kept deliberately, because the project's
floor is `python-xlib>=0.29` and 1.2 is therefore the portable choice — and because
1.2 enumerates *outputs*, which is why CRTC deduplication is required at all.

**Implication.** Any comment citing a library capability must name the version it
was checked against. Two documents disagreed on this until corrected.

### 1.11 XKB is unreachable, so the tray polls and nothing else does

The server has `XKEYBOARD` but python-xlib ships no XKB binding, so
`XkbSelectEvents` is out of reach without `ctypes`. NumLock state cannot be watched
event-driven.

**Decision.** The tray reads `led_mask` on a 1 Hz timer, and it is opt-in
*because* of that. Grabbing `Num_Lock` and replaying it was rejected: its failure
mode is NumLock stopping working, which is far worse than a 23 µs read.

---

## 2. Architectural decisions and what they cost

### 2.1 One `select()` with every deadline folded in

The daemon blocks on the X file descriptor with a `None` timeout whenever nothing
is armed. Every timeout — assign 5 s, confirm 5 s, launch-wait 10 s, activation
1.5 s — is expressed as that one timeout. There is no periodic timer anywhere.

**Measured: 0.000 % idle CPU, 0.0 voluntary context switches per second.**

**Implication.** Adding any feature that needs a timer must fold into
`select_timeout()`, and must return `None` when idle. A `sleep()` loop or a
recurring tick would forfeit the project's headline property. The 10 s launch wait
is deliberately *not* a sleep for exactly this reason.

### 2.2 A signal wakeup fd, because `select()` ignores signals

Under PEP 475 `select()` is auto-retried after a handler returns, so
`except InterruptedError` was dead code and — with a `None` timeout at idle — a
`SIGHUP` was invisible indefinitely. Measured: a full 3.01 s block while the
handler had already run at 0.4 s.

Worse than latency: because the reload check sits at the top of the loop, the first
Dial keypress after `dials pause` was what woke the loop, and it was *dispatched
before the pause engaged* — precisely backwards for a safety valve. A
`signal.set_wakeup_fd` self-pipe in the select set fixed both (0.40 s).

### 2.3 A dead X connection must exit, not spin

A peer-closed socket is permanently readable while yielding no events — measured 5
of 5 ready in 0.000 s — so the loop would spin at 100 % CPU with no exit. The
daemon now catches `ConnectionClosedError` *and* keeps a bounded
readable-but-eventless guard, exiting non-zero so `Restart=on-failure` and
`PartOf=graphical-session.target` bring it back with the next session.

**Implication.** In a project whose requirement is 0 % idle CPU, "cannot exit" is a
worse failure than "exits too eagerly".

### 2.4 Pure logic separated from X I/O, with I/O injected

Geometry, monitor selection, the action table, the focus state machine, window
selection and config validation are pure and tested without a display. This is why
400 tests run in under half a second and why the subtle logic could be
mutation-tested at all.

**Implication.** A handler that reaches for X directly instead of its injected seam
becomes untestable — this happened once (`capture`), and the symptom presented as
"missing tests" rather than as a design problem. If a seam exists, use it.

### 2.5 GTK never enters the daemon, and an automated guard enforces it

The tray and confirm dialog are separate processes. A subprocess test asserts that
importing `dials.daemon` pulls in none of `tomli_w`, `curses`, `gi`, `dials.tui`,
`dials.tray` or the TOML writer. It **must** run in a subprocess, because the test
suite imports several of those itself and an in-process check passes vacuously.

**Implication.** The ≤16 MB budget depends on that graph. For most of this project
the guard existed only as a claim I repeated — a single manual check, with three UI
modules landing afterwards. Assertions about resource behaviour need tests, not
memory.

### 2.6 Config: two plain files, no symlink

The live config is `~/.config/dials/config.toml`; the repo's
`config/config.reference.toml` is a snapshot nothing reads, refreshed by
`dials config export`. An earlier design symlinked the XDG path into the repo,
which made the running system depend on the project folder's location and required
a dangling-link failure mode, a `--relink` repair command and a health check —
three pieces of machinery existing only to support the link.

**Implication.** Mirroring is manual on purpose: automatic sync would mean the
daemon writing into a git working tree.

### 2.7 Writes are atomic, and every writer re-reads first

Temp file in the *same directory* (so `os.replace` stays on one filesystem),
`flush`, `fsync`, rename. Every write re-reads and merges, so a TUI session left
open in another terminal cannot silently revert a Dial written elsewhere.

**Implication.** A dropped `fsync` is not detectable by any test on tmpfs —
verified by mutation, 341 tests stayed green. That durability property is
code-review-only, which is why it is written down here.

---

### 2.8 "I could not read that" is a distinct value, not an empty list

`list_windows()` returned `[]` both for "this app has no windows" and for "the read
failed", while its own comment forbade exactly that conflation — an empty list makes
a Dial look unlaunched, so a keypress on a *running* app takes the LAUNCH branch and
confirming yields a duplicate instance.

**Decision.** Unknown is `None`; callers treat `None` as "do nothing this event"
rather than as "no windows".

**Implication.** This is the same class of bug as §4.3 and §1.6 — a sentinel that
is indistinguishable from a legitimate value. When a read can fail, the failure
needs its own representation, and `[]`, `0`, `False` and `""` are all
disqualified by being plausible successes.

## 3. Behavioural decisions

### 3.1 Three states, not a two-state toggle

Hidden → show; visible but not active → **raise**; visible and active → hide. A
Dial that can be buried must surface on one press, not two.

### 3.2 `on_focus_loss` is one setting with three values

`normal` / `above` / `hide`. "Can be buried" and "hides when focus leaves" are
mutually exclusive — hiding on focus loss means it can never be buried — so they
are values of one knob, not two flags.

### 3.3 Overlapping Dials are resolved by ordinary focus rules

There is no "one Dial at a time" mode. Showing a Dial is an ordinary focus change,
so a `hide` Dial hides, a `normal` one is buried, an `above` one stays up. This
removed code rather than adding it: nothing special-cases "another Dial".

### 3.4 Assign mode snapshots at `.` press

The captured window, class, monitor and rect are frozen when `.` is pressed, which
is what allows the overwrite dialog to take focus without changing what gets
bound. `resolve()` only *describes* the outcome; an occupied slot yields an
`OverwriteRequest` and persists nothing until confirmed.

`Escape` is deliberately not the cancel key — grabbing it would steal Escape
system-wide for five seconds. Pressing `.` again cancels.

### 3.5 A modal for destruction, a notification for a launch

Overwriting a Dial destroys one, so it gets a real focused window that shows both
sides and can be refused — and because it holds focus it needs **no global grabs at
all**. Launching is harmless and must not steal focus, so it gets a notification
plus a brief key grab. The two flows differ on purpose.

**Accepted risk, recorded.** Because that notification does not take focus, an
`Enter` pressed for an unrelated reason inside the 5 s window can confirm a launch.
This is a *new* accidental-launch path, mildly at odds with why confirmation was
wanted. Confirming with the same Dial key again would avoid grabbing `Return`
entirely; `Enter` was implemented because it was explicitly requested.

### 3.6 A Dial with no launch command does not arm at all

Arming used to happen for any not-found window, and the notification promised
"press Enter to launch it" without checking whether a command existed — only
`confirm()` noticed there was nothing to run. Since assign mode always produces
`launch=None`, **most user-created Dials** would take `Return` from the focused
application for five seconds and then say something untrue.

**Decision.** No launch command means no arming, no grab, and an honest message.

**Implication.** The intrusive part of this design — a global `Return` grab — now
only happens when it can actually accomplish something. This was the cheapest of
the five fixes and the one most likely to have been hit in daily use.

### 3.7 A launch timeout never kills the process

An app that shows its window at 11 s is a better outcome than a killed one.

### 3.8 Window selection is deterministic, and ids are never persisted

Managed windows matching the class field of `WM_CLASS`, excluding dialogs,
utilities, splashes, menus, tooltips and override-redirect; then prefer the active
window, then most-recently-active, then the **lowest id** purely for stability. A
transient owned by the chosen window counts as part of the Dial's group, which is
what stops a Dial hiding itself while its own dialog is open.

**Implication.** Nothing asserted that the *class* field (index 1) was used rather
than the instance field until late — mutating it to index 0 survived all 341 tests,
and would have broken all 15 Dials at once on a real desktop while CI stayed green.

### 3.9 Firefox gets a dedicated profile

Firefox refuses two instances on one profile, so a reliably identifiable panel
window needs its own profile plus `--class`. Referenced by name (`-P dial6`) rather
than by absolute path, to keep a machine-specific directory hash out of config.

**Implication.** `match_class` must stay in sync with `--class` in `launch`. That
pairing is the one thing not to break when editing that Dial.

### 3.10 Launch commands expand `~` and `$VARS`

They run without a shell (`shlex.split` then `Popen`), so nothing would otherwise
expand them and a `launch` line containing `~/.mozilla/...` would hand the app a
literal `~` directory. Expansion handles the `--opt=~/path` form too, because
`expanduser` only expands a *leading* tilde. An unset variable is left untouched so
a typo fails loudly rather than silently dropping an argument.

---

## 4. Decisions I got wrong and revised

Recorded because the reasoning errors are more reusable than the fixes.

### 4.1 Three resource budgets measured the wrong thing

- The tray's polling was justified with a *real* measurement — one `led_mask` read
  at 28.4 µs — while the tick actually forked `pgrep` at **17.6 ms**, 756× more,
  once a second. A precise measurement of the cheap half gave the design a false
  clean bill of health. Fixed by caching the PID and re-checking
  `/proc/<pid>/comm` (6 µs), which is also *more* correct than `pgrep -x dialsd`,
  since that matches any process of that name.
- `≤16 MB` and `≤45 MB` were stated against **RSS**, where most of both figures is
  shared libraries. PSS is the honest metric: daemon 10.6 MB, tray 24.8 MB.
- The tray's `≤0.01 % CPU` came from tick arithmetic that ignored
  `Gtk.StatusIcon`'s own mainloop, which *is* the entire 0.055 % residual and is
  outside any code here.

**Implication.** Measure the whole tick, not the interesting part of it; and name
the metric, because RSS and PSS answer different questions.

### 4.2 I asserted `BadAccess` where the truth was a silent replace

Section 1.2. The failure mode worth remembering is not the X detail but the
propagation: I stated it confidently, an implementer built on it, a reviewer
"verified the chain" — against the Python code rather than the server — and a test
was written asserting the broken behaviour as correct. **A confident upstream
assertion can defeat every downstream check.** Anything load-bearing about X now
gets a probe in `docs/probes/` that a test references.

### 4.3 A safety fallback that hides total breakage

Four times, a `try/except` written to "degrade never crash" was the thing
preventing anyone from noticing a feature was completely dead — most starkly the
confirm dialog, where an unpinned `Gdk` import failed against Gtk 3.0 and the
fallback reported a plausible "Dial not replaced" forever.

**Implication.** The spec said errors must be **logged** and swallowed. Silent
degradation and silent failure are indistinguishable from outside. If a handler
swallows, it must say so — and the severity must split by kind: `debug` for reads
that can legitimately race a dying window, `warning` for writes expected to
succeed.

### 4.4 `install.sh` claimed idempotency it did not have

`uv venv` refuses an existing venv, and the script's own closing message invites a
re-run with `--tray`. Following its advice failed at step two. `--allow-existing`
reuses the venv and preserves the editable install; `--clear` was rejected because
it would wipe it.

### 4.5 Documents that contradicted each other

The README stated measured footprints honestly while the spec's budget table still
asserted the original figures; the README listed the live config under "what it
touches" and then said uninstall reverses everything, when uninstall deliberately
preserves it. Both corrected.

---

## 5. Findings rejected, with the counter-evidence

Recorded so they are not re-litigated. Each was proposed confidently by an
adversarial reviewer and each was measured before being declined.

| Proposed | Why declined |
| --- | --- |
| `translate_coords` operands are inverted | As shipped matches `xwininfo` truth (742, 418); the swap yields (−742, −418) |
| Force a read with `range(max(1, pending_events))` | `next_event()` on an empty queue blocks indefinitely; would freeze every Dial |
| Atom interning is a resource-budget violation | `intern_atom` caches (8.3× cold/warm) and `apply_hints` runs only on a keypress; the budget concerns *idle* |
| The "do not change this" comment is over-defensive | It exists because that exact change was proposed twice and would hang the daemon |
| Trackers for removed Dials leak unboundedly | Keyed by slot, so bounded at 16, and `reload()` clears the dict wholesale |

---

## 6. Deliberately deferred, with implications

- **Only one launch may be pending.** Confirming a second launch before the first
  app's window appears orphans the first. Needs tight timing and self-corrects (the
  app is now running, so its Dial does show/raise). Multi-pending would ripple
  through launcher, daemon and tests.
- **`_monitor_containing` uses the centre point.** A window straddling two monitors
  may be assigned to the one you would not expect. Max-overlap is an improvement,
  not a defect — and rewriting live geometry maths at merge time is the wrong risk.
- **`dials status` reports no grab conflicts, monitor fallbacks or geometry
  mismatches.** Those live in daemon memory with no channel to the CLI. Closing it
  needs a state file or D-Bus. Documentation was corrected to stop promising them.
- **Geometry-mismatch detection is not merely unimplemented but unimplementable as
  designed**, because nothing reads back the geometry the WM granted.
- **The TUI fails below roughly 25–28 terminal rows** (`addwstr` error). Loud,
  non-destructive, and it will bite someone on a 24-row default.
- **`notify()` blocks the loop for up to 1 s.** Reduced from 5 s. A wedged
  notification daemon still costs responsiveness; the 5 s freeze it replaced was
  worse.
- **Geometry is applied at map time now, not focus time**, since
  `_NET_CLIENT_LIST` arrives first. An app that sizes itself after mapping could
  clobber a Dial's rect on first launch — strictly better than the previous
  behaviour of never adopting a window that lost the focus race.

---

## 7. What still needs a human

Five checks cannot be automated here and are listed in `README.md`:

1. Press a Dial with **CapsLock on** — the regression guard for §1.1.
2. **Physically hold** a Dial key — XTEST cannot reproduce server autorepeat, so the
   debounce is unit-tested but not end-to-end verified.
3. Plug or unplug a monitor — RandR hotplug subscription is verified as *registered*,
   not as *received*.
4. Hover the tray icon — the state logic is exhaustively tested; the on-screen
   tooltip string was never screenshot-confirmed.
5. Press a numpad key and watch a Dial actually land on the ultrawide by its
   `edid:` name — EDID-based placement is verified through `pick()`'s
   resolution logic against live X and through `dials status` naming the
   right display, but not through a real keypress confirming the window
   moves. Input injection to drive that keypress was blocked by an
   environment safety classifier, and was correctly not worked around.

Nothing in the *original* branch installed anything: no systemd unit enabled, no
live config created, no process left running, because activating this takes over
15 keys. It has since been installed deliberately with `./install.sh --tray`, and
section 8 records what daily use then revealed.

---

## 8. Decisions from first use

Everything above came from building the thing. This section came from running it,
which found four defects no test could have — three of them because the tests
asserted the design rather than the outcome.

### 8.1 `match_class` is case-sensitive, and getting it wrong is silent

Spotify's `WM_CLASS` is `("spotify", "Spotify")`: instance lowercase, class
capitalised. The shipped config matched lowercase `spotify` against the *class*
field, so it matched nothing — and the Dial then reported Spotify as not running
while it was on screen, offering to launch a second copy.

**Implication.** A wrong `match_class` is indistinguishable from "the app is
closed", which is the most confusing possible failure: the feature appears to work
and simply disagrees with reality. `xprop WM_CLASS` on the real window is the only
way to be sure, and the second field is the one that matters.

### 8.2 An already-visible window never went through SHOW, so it was never set up

The single root cause behind two separate complaints — "it opens at full screen"
and "it still shows in alt-tab".

Geometry and hints were both applied on SHOW. But SHOW only happens when a window
comes back from minimised, and a window that was *already visible* the first time
the daemon saw it never takes that path — every press on it is a RAISE or a HIDE.
So such a window was never placed and never made into a panel, no matter how many
times its Dial was pressed.

Two different fixes, because the two settings are not the same kind of thing:

- **Geometry** is position, so it stays under `pin_geometry` — but that now ships
  **on**. Leaving it off was protecting a hand-nudged window from being snapped
  back, which turned out to be the wrong trade: a panel that never places its
  window is not a panel. The nudge concern is real and is now the accepted cost,
  with `dials capture <slot>` as the way to make one permanent.
- **Hints** are window properties, so they are re-applied on SHOW *and* RAISE and
  are deliberately not gated on `pin_geometry` at all. A test asserts
  `reapply_hints` takes only the action, because adding a `pin_geometry` parameter
  would silently stop excluding pinned-off Dials from the switcher.

**Implication.** "Applied on show" is not the same as "applied", and the gap is
invisible in tests that start from a minimised window — which every test did.

### 8.3 `_NET_WM_STATE` is add/remove, so add-only code cannot undo itself

`apply_hints` only ever *added* `_NET_WM_STATE_ABOVE`. Changing a Dial from
`on_focus_loss = "above"` to `"normal"` therefore left its window pinned on top
until the application restarted, with the config silently disagreeing with the
screen. It now sends an explicit REMOVE.

**Implication.** For any add/remove protocol, "set to false" has to be written as
an operation, not as the absence of one. The old test — "ABOVE is not among the
atoms sent" — was satisfied by the broken behaviour; the replacement asserts the
action byte.

### 8.4 A predicate test is not a call-site test

Both 8.2 fixes were first covered only by tests on the pure predicate in
`panels.py`. Reverting `daemon.py` to the buggy `if action == panels.SHOW:` left
**all 389 tests green**. This is the same vacuous-coverage shape already recorded
in `RETROSPECTIVE.md`, recurring in new code.

**Implication.** When a fix is one line in a caller and one function in a pure
module, the caller is where the bug can come back. Mutation-test the call site,
not just the predicate. Three daemon-level tests now fail if that line regresses.

### 8.5 The tray icon is generated, and fitted by measurement

The first shell was a scallop, which is the wrong animal — a Dial is a conch. The
replacement is a logarithmic spiral, which cannot be hand-authored: the constants
live in `icons/generate.py` and the SVG is output.

Three things were learned the hard way and are pinned by comments or tests:

- **gdk-pixbuf identifies an image by sniffing its leading bytes**, so a comment
  long enough to push `<svg` out of that window makes the icon fail to load as
  "unrecognised format" — a broken image in the panel with nothing wrong in any
  log. Measured: an 11-byte leading comment loads, a ~470-byte one does not.
  `tests/test_tray.py` asserts `<svg ` is at byte 0.
- **A flat spiral has a circular silhouette**, and at 22px the silhouette is all
  you get, so it read as a disc. The coil is rotated and squashed on one axis to
  give it a conch's spindle profile.
- **The icon must be fitted, not positioned.** It first filled ~62% of the
  viewBox and looked shrunken beside every other indicator. `_fit` measures the
  drawn bounding box — including half of each stroke width, which a naive path
  bbox misses, and omitting it clipped the widest whorl — and emits the transform.
  Resizing is now one constant.

### 8.6 Two Dials may share one rect

Spotify and Slack both occupy the right half of the ultrawide. That is a supported
pattern, not a collision: one screen region, two keys, one window up at a time.
Nothing special-cases a Dial appearing over another, because showing one is an
ordinary focus change — which is why this needed no code at all.

### 8.7 The alt-tab and overview problem was never ours

Two rounds of hint fixes went into making Dial windows disappear from alt-tab and
from the workspace overview, and neither was the cause. Reading GNOME Shell 42.9's
own extracted sources settled it in minutes, after a day of theorising:

```js
workspace.js:1376   _isOverviewWindow(window) { return !window.skip_taskbar; }
altTab.js:53        .filter((w, i, a) => !w.skip_taskbar && a.indexOf(w) == i);
```

Shell honours the hint in both places, and alt-tab rebuilds its list on every open
— so the "hint was applied too late to be noticed" theory, which was mine and
which I had already written into the spec as the likely answer, was simply wrong.

`pop-shell` monkey-patches both functions so that minimise-to-tray applications
stay reachable, and its `is_valid_minimize_to_tray` predicate matches *any*
non-override-redirect NORMAL window with `skip_taskbar` and a real `WM_CLASS`.
That is a precise description of a Dial window. **The hint meant to hide a Dial is
what made Pop Shell show it.**

And Guake — whose identical properties had looked like the strongest evidence for
the timing theory — is simply on Pop Shell's hardcoded `SKIPTASKBAR_EXCEPTIONS`
allowlist, alongside Conky and plank.

**First decision, and it was wrong.** Three rules in
`~/.config/pop-shell/config.json`'s `skiptaskbarhidden`, one per Dial class,
preferred over the global `show-skip-taskbar` toggle because that toggle breaks the
feature for every genuine tray application. Targeted beats global — except the
targeted hook does not work.

**`skiptaskbarhidden` is dead code in this version of Pop Shell.** `ext.conf` starts
as `new Config.Config()`, whose constructor sets it to `[]`, and `Config.reload()` —
the only thing that ever repopulates it — copies out two of the parsed object's three
fields and drops that one:

```js
this.float = c.float;
this.log_on_focus = c.log_on_focus;     // skiptaskbarhidden never assigned
```

So `skiptaskbar_shall_hide()` only ever sees the hardcoded `SKIPTASKBAR_EXCEPTIONS`.
That, not hint timing and not anything about allowlists being special, is the real
reason Guake escapes and nothing user-configured can.

**Actual decision.** `gsettings set org.gnome.shell.extensions.pop-shell
show-skip-taskbar false`. Supported, user-level, reversible, and Pop Shell watches
the key, so it applies with no reload. The trade that made it second choice is real
and now accepted: it is system-wide, so genuine tray-minimising applications lose
their overview and alt-tab entry too. On this machine that appears to cost nothing —
Slack unmaps its window rather than setting the flag.

**Implication, method — and this is the reusable part.** The rules were written and
then *verified to match*, by replicating `skiptaskbar_shall_hide()` in Python against
the live windows, with Guake as a control. They matched. They did nothing.
**Verifying that a rule matches is not verifying that the rule is consulted.** The
replication faithfully reproduced the predicate and inherited its unstated
assumption: that `this.skiptaskbarhidden` holds what the file holds. Worse,
`reload()` had already been read earlier in the same session, for the question "does
it watch the file?", without noticing what it silently omits — the answer to the next
question was on screen and went unread.

**Implication, method.** Two speculative fixes shipped before anyone read the 60
lines of JavaScript that decide the behaviour. Both fixes were independently
correct and worth keeping, which is what made the guessing feel productive. When
the question is "why does this desktop do X", the desktop's source is on disk:
`gresource extract` on `libgnome-shell.so` and the extensions in
`/usr/share/gnome-shell/extensions/` are readable and authoritative. Read them
first.

**And then the fix appeared not to work, for a reason that was nothing to do with
it.** `Alt+F2` then `r` — the standard way to reload GNOME Shell on X11 — **fails
silently on this machine**: `/usr/libexec/mutter-restart-helper` is not shipped by
this Pop!_OS install, so mutter logs `Failed to start restart helper` and carries
on with the old process. The correct config sat unread on disk while the symptom
persisted. `gnome-extensions disable … && enable …` needs no helper binary and is
what the docs now say.

Two things made that hard to see, and both are worth remembering:

- **The obvious check was useless.** A *successful* Shell re-exec preserves the PID
  and the process start time, so "the PID is unchanged" is not evidence the restart
  failed — and neither is the converse. The journal line is the only evidence.
- **Two `gsettings` keys, one of them imaginary.** The first lookup used
  `show-skiptaskbar`, which does not exist, and `gsettings get` answered `No such
  key` — which reads like "the feature is absent" rather than "you typed the wrong
  name". The real key is `show-skip-taskbar`, and its value was `true` all along.
  Its value was never checked until the fix appeared to fail, which is one round
  trip later than it should have been.

### 8.8 Sticky was the wrong reading of "always in all workspaces"

The original requirement was *"this windows should always be in all workspaces"*,
and `_NET_WM_STATE_STICKY` implements that literally: the window reports
`_NET_WM_DESKTOP = 0xFFFFFFFF` and is present on every workspace. It shipped that
way and satisfied the sentence exactly.

It was still wrong, and the symptom showed it: a four-finger swipe switches
workspace, so switching away from Spotify showed you Spotify again — the Dials
followed. **"Reachable from any workspace" and "present on every workspace" are
different wishes**, and sticky grants the second in order to get the first.

**Decision.** Send `_NET_WM_DESKTOP` with the value of `_NET_CURRENT_DESKTOP` on
the same SHOW/RAISE condition as the hints, and remove `STICKY`. A Dial now appears
on whichever workspace you are on and exists on no other; pressing its key is still
how you reach it from anywhere, which was the actual requirement.

**Implication.** Three details had to be right, and each is a trap this project has
hit before in another form:

- `STICKY` is **actively removed**, not merely no longer added — otherwise every
  window made sticky by the previous version stays sticky forever. Identical to the
  `ABOVE` bug in §8.3, found the same day, which is why it was anticipated here
  rather than discovered.
- It is gated on SHOW **and** RAISE, for the reason in §8.2: an already-visible
  window never goes through SHOW, so it would have stayed stranded on whatever
  workspace it started on.
- Workspace `0` is a real index, so the unreadable-property guard is `is None` and
  not a truthiness test. `if not idx` would have silently skipped the most common
  workspace on the machine.

Both halves were mutation-tested before being believed: removing the call site
fails two daemon tests, and reverting to add-only sticky handling fails one
`WindowOps` test.

### 8.9 Known rough edge: an app that minimises to the tray reads as "not running"

Flatpak Slack unmaps its window and drops out of `_NET_CLIENT_LIST` when it
minimises to the system tray. `list_windows()` correctly does not see it, so the
Dial reports "not running" and offers to launch. Confirming happens to work, since
Slack is single-instance and re-running it restores the window — but the message
is wrong. Left as-is: distinguishing "closed" from "hidden in a tray" needs a
per-application notion of liveness that this design does not have, and the
consequence is a misleading sentence rather than a broken action.

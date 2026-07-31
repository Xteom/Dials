# Retrospective — building Dials

Kept because the failure patterns here are cheap to repeat and cheap to avoid.
The design spec is `docs/superpowers/specs/2026-07-28-dials-design.md`; the
task-by-task plans are in `docs/superpowers/plans/`. This file records only what
those two cannot: what went wrong in the process itself.

## Outcome

22 planned tasks, 360 tests, 0 skips. Every load-bearing X11 claim was verified
on the target machine before it was written down — the eight probe scripts in
`docs/probes/` are that evidence and can be re-run if GNOME, Firefox or the
monitor layout changes.

The headline architectural claim was measured, not asserted: the daemon idles at
**0.000 % CPU and 0.0 voluntary context switches per second**, because it blocks
in `select()` on the X file descriptor with a `None` timeout and folds every
timeout (assign 5 s, confirm 5 s, launch-wait 10 s, activation 1.5 s) into that
one call. There is no periodic timer anywhere in it.

## Eleven defects in the plan, none in the implementations

Every defect found during execution originated in the plan — the code written
from it matched byte-for-byte in each case. Four are worth remembering because
they share a shape:

| # | Defect | What it would have looked like |
| --- | --- | --- |
| 5 | The monitor cache could never keep its last-known-good value: `invalidate()` cleared it before the failure path could read it | Dials on the wrong monitor after any transient RandR blip |
| 11 | `protocol.event` was used without importing `Xlib.protocol.event`; with the module's actual imports that raises, and a broad `except` swallowed it | Daemon starts, grabs keys, does nothing, logs nothing |
| 12 | `keysym_to_string` special-cases `Scroll_Lock` and returns a truthy `'\x14'`, so an `or` shadowed the authoritative name table | Every Dial dead whenever ScrollLock is on |
| 19 | `Gtk` was pinned to 3.0 while `Gdk` was imported unpinned; with both Gdk 3 and 4 installed it resolves to 4.0 and the Gtk load fails | The overwrite dialog never appears, and its safety fallback reports a plausible "Dial not replaced" forever |

**The pattern: a safety fallback hiding total breakage.** In #11, #19 and twice
more, a `try/except` written to "degrade never crash" was the thing preventing
anyone from noticing the feature was completely dead. The spec's error table said
*logged* and swallowed; the plan implemented only the swallowing. Silent
degradation and silent failure are indistinguishable from outside — if a handler
swallows, it must say so.

## The most expensive lesson: spec → plan coverage

Four spec requirements were lost when the plan was written, not when it was
implemented:

- the launch waiter was to watch `_NET_CLIENT_LIST` and `MapNotify`, not only
  `_NET_ACTIVE_WINDOW` — so an app that mapped but lost the focus race was never
  placed, and was reported as never having started;
- a failed config reload was to keep the last-good config in memory — instead an
  unguarded `load()` on `SIGHUP` crashed the daemon into a systemd start-limit
  wedge;
- `dials status` was to report grab conflicts, monitor fallbacks and geometry
  mismatches — it reports none of them, and three documents promised all three;
- geometry-mismatch detection is not merely unimplemented but *unimplementable*
  as designed, because nothing ever reads back the geometry the WM granted.

In all four cases the code matched the plan exactly, so no task-scoped review
could have caught them — only the final whole-branch review did. **A spec→plan
coverage check before dispatching any task would have caught all four for almost
nothing.** That is the single highest-value process change available here.

## Tests that passed for the wrong reason

Five vacuous tests were found and fixed. They are worth listing because each
looked completely reasonable:

- `"unbound" in text or "-" in text` — the table header contains `focus-loss`, so
  the hyphen satisfied it on every call;
- `assert count >= 15` where the real count was 16, so dropping one row passed;
- an `isinstance` assertion identical to one two tests above it;
- a `list` subclass used as a test recorder: `Notes()` is falsy, so
  `notifier or default` silently discarded it and six assertions passed for free;
- a glyph assertion using `"S"` while the fixture label was `"Spotify"`.

Three habits caught these: mutation-test anything that guards a rare path
(break it deliberately, confirm the test fails, restore); prefer `is None` to
truthiness in any defaulting expression; and distrust `or` in an assertion.

Related: `_keysym_name` and the `capture` subcommand each had a real defect and
*zero* coverage, because every test bypassed them — one via an injected stub, one
because the handler reached for X directly instead of its own injection seam.
Code that cannot be reached by a test is code no review has actually checked.

## Measure the thing, not the thing next to it

Three resource numbers in the spec were wrong, and none of them was the code's
fault:

- the tray's polling was justified with a real measurement — one `led_mask` read
  at 28.4 µs — while the tick actually forked `pgrep` at 17.6 ms, 756× more, once
  a second. A precise measurement of the wrong half gave the design a false clean
  bill of health;
- `≤16 MB` and `≤45 MB` were stated against RSS, where most of both figures is
  shared libraries. PSS is the honest metric: daemon 10.6 MB, tray 24.8 MB;
- the tray's `≤0.01 % CPU` came from tick arithmetic that ignored
  `Gtk.StatusIcon`'s own mainloop, which *is* the entire 0.055 % residual and is
  outside any code here.

`README.md` states the measured values; the spec's budget table was corrected to
match rather than left contradicting it.

## On adversarial review

Two external adversarial passes produced four real defects and four wrong calls.
Three of the wrong calls would have introduced bugs if applied on trust:
"fixing" the event drain to force a read would have blocked the daemon
indefinitely; swapping `translate_coords`' operands would have produced the exact
negative coordinates it warned about. Both were confident claims about library
semantics made from memory.

The rule that made those passes worth running: **verify every claim by reading
the installed source or running code before acting on it.** The same passes also
found two things nothing else did — a `_first_seen` reset that broke post-launch
window identity, and config coercion that turned `pin_geometry = "false"` into
`True`.

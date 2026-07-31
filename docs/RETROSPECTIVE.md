# Retrospective — building Dials

Kept because the failure patterns here are cheap to repeat and cheap to avoid.
The design spec is `docs/superpowers/specs/2026-07-28-dials-design.md`; the
task-by-task plans are in `docs/superpowers/plans/`. This file records only what
those two cannot: what went wrong in the process itself.

## Outcome

22 planned tasks, 382 tests, 0 skips. Most load-bearing X11 claims were verified on
the target machine before being written down — the probe scripts in `docs/probes/`
are that evidence and can be re-run if GNOME, Firefox or the monitor layout changes.

**Two were not, and both were wrong.** They are the subject of the section below,
and they are the most useful thing in this file: the discipline was in place, and
the two claims that escaped it were the two I asserted from memory rather than
measured. A rule you apply to others' claims and not your own is not a rule.

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

## The worst failure: a confident assertion that defeated every later check

Found only by a third review pass, run deliberately with **no project context** —
no spec, no plan, no mention of prior reviews or known defects.

I told an implementer that a duplicate `XGrabKey` for a keycode this client already
holds returns `BadAccess`, and that the temporary confirm-grab manager was therefore
"safe by construction". Measured on the target machine:

```
client A, first grab                      OK
client A, SECOND grab (same kc + mask)    OK          <- silent replace
client B grabbing while A holds it        BadAccess   <- cross-client only
client B after ONE ungrab from A          OK          <- A's grab fully removed
```

`BadAccess` is a *cross-client* protection, and the daemon shares one `Display`
with its temporary manager. So every launch confirmation deleted the daemon's
permanent `KP_Enter` grab and the enter Dial died silently until restart.

**The chain is what matters.** The claim was mine. The implementer built on it. A
reviewer reported independently confirming it — and had, but against `grab.py`'s
Python rather than against the X server. A test was then written asserting the
broken behaviour as correct, so the suite actively defended the bug. Three
independent checks all passed because they all inherited one unverified premise.

Two lessons, both cheap:

- **Context is not free.** Every reviewer given the project's own framing checked
  the code against that framing. The pass that found this was given only the
  objective. Uncontexted review is the only kind that can catch a wrong premise,
  because context *is* the premise.
- **A claim about an external system needs a probe, not a sentence.** The
  probes in `docs/probes/` exist precisely because of claims like this — the rule
  was in force and I exempted my own assertion from it. Anything load-bearing about
  X now gets a probe that a test references.

The same pass found a second Critical: `geometry()` reads the **client** origin
while `apply_geometry()`'s `configure()` sets the **frame** position, so a round
trip moved an SSD window down by its titlebar (`_NET_FRAME_EXTENTS` top = 37 px,
measured drift = 37 px). It compounded on every `capture`, and it hit Spotify —
one of the two Dials actually shipped. Read/write asymmetry is invisible to any
test that only checks one direction; the fix criterion is that the round trip be a
**fixed point**.

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

# Hard-won gotchas

Each of these caused a real, confusing failure during actual debugging
sessions before being understood or fixed. Read this before assuming
something is broken in the ROM you're investigating rather than in how you're
driving the tool.

## Keyboard injection while paused (see SKILL.md's main section)

The single most time-wasting one: `run()`/`run_until()` leave the emulator
paused, and injecting keystrokes while paused does nothing visible at all.
`ts2068-debug` now raises `ControllerError` instead of failing silently, but
if you ever see that error, the fix is always `resume()` first, not a bug in
your reproduction.

## Typing immediately after `resume()` drops the first characters

A second real gotcha in the same area as the one above, found the same
session (porting a 2068-Leap test-suite harness): even once you've
correctly called `resume()` before typing, the ROM's keyboard-scan ISR
needs a brief real window to actually resume its normal polling rhythm.
Typing with zero gap after `resume()` — exactly the sequence this file's
own SKILL.md/README examples show — silently dropped the first 1-2
characters of the first line in testing: `BORDER 4` landed on screen as
`RDER 4`, tripping the ROM's own checker (`1 ERROR FOUND`). Confirmed via
screenshot diff, not just a register guess.

A CLI-per-command caller (`ts2068-debug type ...` as a separate process
each time) never hits this, because the subprocess-spawn/reconnect
overhead between commands already provides more than enough incidental
delay to mask it — which is exactly why this went unnoticed until a
persistent-session Python API caller (`resume()` then `type_program()`
in one process, no gap) hit it directly. `resume()` now sleeps ~200ms
after `exit-cpu-step` before returning, fixing this at the source for
every caller — but if you ever see a typed line's leading characters
mysteriously missing, this is the first thing to suspect, especially if
you've built your own lower-level sequence that bypasses `resume()`.

## `send-keys-ascii` truncates past 19 codes

ZRCP caps any single command at `REMOTE_MAX_PARAMETERS_COMMAND=20` total
arguments. `send-keys-ascii <delay> <code1> <code2> ...` counts the delay as
one of them, leaving 19 key codes per call — anything past the 19th character
in one call is silently dropped, never typed. `ts2068-debug key TEXT`
(`send_keys_ascii()`) already chunks into ≤19-code batches to stay correct,
but for typed program text prefer `type`/`type_line()`, which uses
`send-keys-string` instead and isn't subject to this cap at all.

## Screenshots must be `.bmp`

ZEsarUX's `save-screen` only ever writes BMP. Ask for `.png` or any other
extension and you get an empty, non-error reply and *no file at all* — not a
rejected command, a silent no-op. `ts2068-debug screenshot`/`save_screen()`
now reject non-`.bmp` paths up front and verify the file was actually
created, but the underlying lesson generalizes: ZRCP commands can succeed at
the protocol level (empty reply, no "Error" prefix) while doing nothing
useful. Don't treat "no error" as "it worked" for anything file-producing —
check the actual result.

## A large bounded run can take real wall-clock seconds

ZEsarUX runs at hardware-accurate Z80 speed by default. A `run
--max-instructions 5000000` genuinely took ~14 seconds of real time in one
session — that's not a hang in the tool, that's ~3.5M T-states of emulated
CPU time elapsing at roughly the rate a real TS2068 would take. `run()`
scales its own socket timeout to the requested instruction count so this
doesn't get mistaken for a broken connection, but budget for it: don't reach
for the largest instruction bound "to be safe" if a much smaller one already
reaches your target — bounded execution is about safety, not about
maximizing the limit.

## Never launch Fuse's GUI mid-session

Fuse (the other common ZX Spectrum emulator, used by some of these projects'
own older smoke-test scripts) opens a real window. If `DISPLAY` happens to
point at the operator's actual desktop rather than a genuine offscreen/Xvfb
display, that window steals keyboard focus on their real machine — this has
actually happened and interrupted a human mid-session. `ts2068-debug`/ZEsarUX
runs headless (`--vo null --ao null`) specifically to avoid this class of
problem; that's part of why it exists as an alternative to Fuse-based tooling
for anything interactive. If a project's own `make test` still shells out to
a Fuse-based script, that's fine to run as an existing, unmodified regression
suite — just don't reach for Fuse yourself for new investigation, and if you
do run that suite, verify what `DISPLAY` actually resolves to first.

## Isolate concurrent sessions with `TS2068_DEBUG_STATE_DIR`

CLI session state (host/port/PID/symbols) lives in `~/.ts2068-debug/state.json`
by default — a single shared file. If a regression suite or another
agent/session might already have a live `ts2068-debug` session running,
starting your own without isolating it will stomp on that shared state file
and confuse both sessions about which ZEsarUX process they're actually
talking to. Use a scratch directory and a distinct port:

```bash
TS2068_DEBUG_STATE_DIR=/tmp/ts2068-debug-scratch ts2068-debug --json start \
  --rom build/whatever_zesarux.bin --port 10001
```

Check `pgrep -fa zesarux` before starting a session if you suspect something
else might already be running against the same or a related ROM.

## Interrupt state and border color are structured data, not just text

`regs` now includes `IM`, `IFF1`/`IFF2` (as "set"/"clear"), and `TSTATES`
when present — these used to be silently dropped because ZRCP emits them as
bare tokens (`IM1`, `IFF--`) rather than `key=value` pairs. Use them directly
for ISR-only/flaky/timing-sensitive bug classes instead of guessing from PC
alone.

`timex` includes `border`/`port_fe` — many of these projects' own smoke-test
ROMs signal PASS/FAIL by writing a border color (commonly 4=green pass,
2=red fail) to the ULA port and halting. Read that field directly instead of
screenshotting and eyeballing a pixel color; it's exactly how
`2068-Leap/tools/run_smoke_rom_tests.sh` was rewritten to stop depending on
Fuse/X11 screenshots entirely.

## Reset does not mean what it sounds like

Two separate, both real, both found while porting a 2068-Leap test-suite
harness off Fuse:

1. **`reset --hard` (ZRCP `hard-reset-cpu`) does not reset PC/SP/AF at
   all.** Confirmed directly against the raw ZRCP socket, bypassing this
   client entirely: issuing `hard-reset-cpu` while paused left every
   register exactly as it was. Only plain `reset` (`reset-cpu`, the
   default, `hard=False`) actually sets `PC=0000 SP=ffff AF=ffff IM0`,
   matching real Z80 RESET semantics. Don't reach for `--hard` expecting
   a "more thorough" reset for this purpose — it's a different, narrower
   operation (exactly what it resets besides not-the-CPU-registers isn't
   pinned down here; treat it as unrelated to "get back to a known CPU
   state" until proven otherwise).

2. **Even a correct (soft) `reset` issued while the machine is still
   free-running races.** ZEsarUX keeps running in real time between your
   `reset` command and whatever you do next (see "hardware-accurate
   speed" above) — for a target deep in a converging loop this doesn't
   matter, but a ONE-SHOT target executed only once during a short,
   linear boot sequence (a fixture harness's own injection point right
   before its first real subroutine call, say) can reproducibly get
   missed: `run_until()`/`run --until` landed past it every single time
   in testing, never at it. Not a timing fluke — reproduced across many
   independent sessions.

**Fix**: `pause()` FIRST, then reset — removes the race entirely, since a
command issued while already in cpu-step mode is an ordinary synchronous
round trip with no free-running gap before your next action. This exact
sequence is now `reset_to_known_state()` / CLI `reset-known-state`: pause,
soft-reset, return the post-reset registers so you can assert `PC == 0`
directly. Use it before arming a breakpoint on anything reached only
once, early, during boot — `run_until()` alone is fine for anything the
CPU loops back into repeatedly (an idle/command loop, a keyboard-wait
state), where missing the first pass costs nothing.

## Breakpoints are persistent — clear them yourself for one-shot use

Not a tool bug, just easy to forget: a ZRCP breakpoint (`PC=<addr>H`)
stays armed until explicitly cleared, and fires every time execution
reaches that address again — standard debugger semantics, not one-shot.
Found the same 2068-Leap test-suite porting session, in a hand-rolled
`break` + `run` + poke-memory + `run` sequence (arm a breakpoint, run to
it, poke some bytes now that you're paused there, then resume to
continue past it): for a program whose execution legitimately revisits
that same address later (confirmed live: two structurally-identical
test fixtures behaved differently — one never revisited the breakpoint
address and ran to completion normally, the other did revisit it and
got stuck, TSTATES near 0, on the second `run()`), the still-armed
breakpoint re-fires and blocks further progress, looking exactly like a
hang.

`run_until()` already gets this right — it clears its own breakpoint
(`set-breakpoint <index>` with no condition) right after reaching the
target, before returning, precisely because it's designed for one-shot
"run until this point" use. A hand-rolled `break`/`set_breakpoint()` +
`run()` sequence must do the same if you want one-shot semantics: call
`clear-break` / `clear_breakpoint()` before your next `run()`. (The CLI
didn't expose `clear_breakpoint()` at all until this session — `break`
existed, clearing it didn't. Now both do.)

## If none of this explains what you're seeing

That's a real signal, not a dead end. If ZEsarUX can clearly do something
(check `docs/zesarux-analysis.md` in `~/ts2068-debug` for what ZRCP exposes)
and `ts2068-debug` doesn't expose it cleanly, that's a harness gap worth
fixing at the source — see SKILL.md's closing section.

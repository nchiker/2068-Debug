---
name: 2068-debug
description: Use ts2068-debug (a ZRCP/ZEsarUX-based runtime debugging and testing controller for Timex Sinclair 2068 ROM development, source at ~/ts2068-debug / github.com/nchiker/2068-Debug) whenever investigating a runtime bug — a hang, wrong output, corruption, or any behavior that only shows up when the ROM actually runs — in a TS2068 ROM project (2068-Leap, 2068-Forth, structured-basic-poc, or any sibling project built the same way). Make sure to reach for this skill instead of reading assembly alone and guessing, and instead of driving Fuse's GUI by hand, any time the task involves reproducing a bug live, inspecting registers/memory/interrupt state, single-stepping, setting breakpoints or watchpoints, typing a BASIC/Forth program into a running machine, or reading a ROM's own PASS/FAIL border-color convention. Also use it when someone reports "it hangs", "the emulator freezes", "wrong output", or asks to verify a fix actually works at runtime rather than just that it assembles.
---

# 2068-debug

`ts2068-debug` automates ZEsarUX's remote-control protocol (ZRCP) so you can
reproduce and diagnose TS2068 ROM bugs with real runtime evidence — registers,
memory, interrupt state, execution history — instead of guessing from source
alone or driving an emulator's GUI by hand. It never modifies ZEsarUX itself;
it's a thin automation layer on top of the stock emulator.

**Why this matters**: a ROM bug that "looks like a hang" from the outside is
often something else entirely — a statement that silently finished early, an
interpreter idling in a normal keyboard-wait loop, a resource leak that only
trips after N calls. You cannot tell these apart by reading assembly or by
staring at a frozen screen. You need to pause execution, read the actual
register/memory state, and check *where* the CPU is idling and *why*. That's
what this tool is for.

## When NOT to use it

Pure static analysis (does this label exist, does this assemble, does this
match the ABI contract) doesn't need a running emulator — use normal file
reading tools for that. Reach for `ts2068-debug` once you need to know what
the machine actually *does*, not just what the source says it should do.

If a bug turns out to be a known, purely static, checkable pattern in a
project's own BASIC/Forth dialect — not a real interpreter defect — the
right fix is a project-local pre-flight validator, not the debugger. See
`nchiker/2068-Leap` issue #3 ("Idea: a pre-flight dialect validator, in the
spirit of zmakebas+") for the concrete example: single-letter variable
names, 0-indexed `DIM`, a fixed label-table budget, and one syntactic
`PRINT` pattern were all discovered by injecting and running a program
under `ts2068-debug` when each is checkable from the source text alone.
That validator belongs in the project it validates (each TS2068 dialect —
2068-Leap, 2068-Forth, structured-basic-poc — differs), not in this
cross-project debugging skill.

## Setup

The tool is a pip-installed CLI + Python package:

```bash
cd ~/ts2068-debug && python3 -m pip install -e . --break-system-packages
```

It needs a combined Home+EXROM binary matching each project's own build
target (usually `build/<project>_zesarux.bin`, 16384+8192 bytes concatenated
— check that project's own Makefile if it's not obvious) and, ideally, a
`.sym` file so addresses resolve to real routine names instead of bare hex.

## Core workflow

1. **Start a session** against the project's own build:
   ```bash
   ts2068-debug --json start --rom build/whatever_zesarux.bin --symbols build/whatever.sym
   ```
2. **Reset**, then **run, bounded** to the point you care about — either a
   fixed instruction count or `--until SYMBOL_OR_ADDR`:
   ```bash
   ts2068-debug --json reset
   ts2068-debug --json run --until BASIC_COMMAND_LOOP --max-instructions 5000000
   ```
   Every `run` carries an explicit instruction limit — never let the machine
   run unbounded while you wait and hope. If you're chasing a suspected hang,
   bound the run, then inspect where execution actually stopped.
3. **Inspect real state**: `regs`, `mem ADDR LEN`, `trace --last N` (recent
   execution history, symbol-resolved), `timex` (TS2068 banking/video/AY
   state). Resolve the current PC to a symbol before concluding anything —
   `IO_READ_KEY.wait_hit` or a similar keyboard-wait routine means the machine
   is idling normally, not stuck; a PC repeating inside an unexpected loop is
   the real signal.
4. **Drive the machine like an operator** when you need to reproduce
   interactive behavior — type a program in, run it, read the screen. See
   **Keyboard injection** below; there's a critical gotcha here that will
   silently waste your time if you skip it.
5. **Read the [references/commands.md](references/commands.md) reference**
   for the full CLI/Python API surface (breakpoints, watchpoints, screenshots,
   the Python API for long-lived sessions) once you know roughly what you
   need — don't load it speculatively.

## Keyboard injection: the one gotcha that will bite you

`run()`/`run_until()` leave the emulator **paused** (ZEsarUX's cpu-step mode)
when they return — that's how bounded execution is implemented. But
`key`/`press`/`type` only update ZEsarUX's keyboard-matrix state; the ROM
code that actually scans that matrix and echoes a character to screen only
runs while the Z80 clock is ticking. Inject a keystroke while paused and
*nothing happens* — no error, no screen change, and (this used to be the
trap) no signal that anything went wrong.

`ts2068-debug` now refuses this outright (`ControllerError: cannot ... the
emulator is paused`), so you'll get a clear message instead of silent
confusion. The fix is always the same:

```bash
ts2068-debug --json resume     # exit cpu-step, let the Z80 clock actually run
ts2068-debug --json type "PRINT \"X=\"; T"
ts2068-debug --json type "RUN"
ts2068-debug --json screenshot /tmp/screen.bmp   # must be .bmp -- see reference
ts2068-debug --json pause      # re-freeze if you need to inspect state next
```

`resume()` is a deliberate, explicit action (not something to call
automatically inside a helper) precisely because this project's whole design
principle is bounded, inspectable execution — you choose when to let it run
free, and for how long, by pairing it with another bounded `run`/`pause` when
you're done.

## Debugging method

Don't hypothesize before you have evidence. The pattern that works:

1. Reproduce the exact reported input/program with a bounded run.
2. Capture PC, registers, and — if it's plausibly a hang — `trace` to see
   the actual recent execution path, not just the final state.
3. Resolve every address to a symbol before reasoning about it.
4. If you suspect a hang, explicitly rule out a normal wait-state first
   (keyboard wait, interrupt wait) — that single check has repeatedly turned
   out to be the actual explanation rather than a real infinite loop.
5. Only then form a hypothesis, and check it against the source at that
   specific symbol — not a general area of the file.
6. Re-run the exact same bounded reproduction after any fix, and compare.

See [references/gotchas.md](references/gotchas.md) for the other hard-won
gotchas (ZRCP's 20-argument keystroke cap, `.bmp`-only screenshots, running
isolated concurrent sessions, why a large bounded run can take real
wall-clock seconds, and why Fuse's GUI should stay off during a session).

## If ts2068-debug itself is missing something

If you hit a real capability gap in the tool while debugging — not "I don't
know the command" but "ZEsarUX can do this and ts2068-debug doesn't expose
it" — fix the harness itself in `~/ts2068-debug` (with a unit test and a
`docs/` update) rather than working around the gap manually for just this
session. Every TS2068 project sharing this tool benefits from that fix
compounding forward; a one-off workaround benefits nobody else. This has
already happened several times (timeout scaling for long runs, exposing
interrupt state in `regs`, the paused-keyboard-injection guard, the
`.bmp`-only screenshot check) — treat that as the normal, expected way this
tool grows, not an exception.

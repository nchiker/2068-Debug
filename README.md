# ts2068-debug

An automated debugging and regression-testing controller for Timex Sinclair 2068 ROM
development, built entirely on top of the unmodified [ZEsarUX](https://github.com/chernandezba/zesarux)
emulator's ZRCP (Remote Command Protocol). No custom Z80/TS2068 emulation — ZEsarUX does
all of that; `ts2068-debug` is the automation/observability/testing layer on top, meant
to be driven by Claude without a human operating the ZEsarUX GUI.

See `docs/architecture.md` for the design, `docs/zesarux-analysis.md` for the source-level
audit of what ZRCP already provides (everything required — no ZEsarUX modifications were
needed), `docs/zrcp.md` for a command cheat sheet, and `docs/testing.md` for the
regression-test framework.

`skill/` packages this tool as a Claude Code skill (see `skill/SKILL.md`) — when/why to
reach for it, the full command reference, and hard-won gotchas from real debugging
sessions (keyboard injection while paused, ZRCP's 20-argument keystroke cap, `.bmp`-only
screenshots, and more). Install it as a personal skill by copying `skill/` to
`~/.claude/skills/2068-debug/`.

## Install

Python 3.10+ and a local [ZEsarUX](https://github.com/chernandezba/zesarux) 13.0. Nothing else; no ZEsarUX changes
are needed for the tool itself.

```bash
git clone https://github.com/nchiker/2068-Debug.git && cd 2068-Debug
python3 -m pip install -e . --break-system-packages     # provides the `ts2068-debug` command
export TS2068_DEBUG_ZESARUX_DIR=~/Applications/ZEsarUX-13.0   # default; the directory containing `zesarux`
```

A ROM whose EXROM code runs from a chunk above 1 (2068-Leap's does) needs the TS2068 EXROM-mirroring
patch to ZEsarUX 13.0 -- see "Running a custom TS2068 ROM" in the `z80-skills` `ts2068-debug` skill
(`skills/ts2068-debug/references/emulators.md`, also copied to `skill/references/emulators.md` here).

## Quick start

```bash
# 1. Build a ROM as a ZEsarUX-shaped combined image (16384-byte HOME + 8192-byte EXROM). Example, 2068-Leap:
cd ~/2068-Leap && make build          # -> build/ts2068rom_zesarux.bin; symbols: build/test_basic.sym (HOME), build/exrom.sym (EXROM)

# 2. Start ZEsarUX headless and connect (paths are stored absolute)
ts2068-debug --json start --rom ~/2068-Leap/build/ts2068rom_zesarux.bin \
  --symbols ~/2068-Leap/build/test_basic.sym --symbols ~/2068-Leap/build/exrom.sym

# 3. Reset deterministically, set a breakpoint and run to it
ts2068-debug --json reset-known-state
ts2068-debug --json run --until V2_COMMAND_LOOP --max-instructions 5000000

# 4. Inspect: registers (PC symbolised), or everything useful for a hang in one call
ts2068-debug --json regs
ts2068-debug --json diagnose

# 5. Stop (works from any directory)
ts2068-debug --json stop
```

## Tests

```bash
python3 -m pytest tests/        # unit tests always run; live tests need ZEsarUX + a built ROM and skip otherwise
```

The live tests use `$TS2068_DEBUG_TEST_ROM`, else `~/2068-Leap/build/ts2068rom_zesarux.bin`; see `docs/testing.md`.

## Used by 2068-Leap

2068-Leap runs its headless suites through this tool and records the commit of this repository it is
verified against in `docs/v2/TESTING.md` ("Debugging tools in this workflow"). The tool is shared with the
other TS2068 ROM projects; keep CLI commands and `--json` shapes backward compatible.

Every command accepts `--json` for machine-readable output, e.g.:
```json
{"status": "ok", "pc": 37799, "registers": {"PC": 37799, "SP": 65410, ...}, "symbol": "editor_ready+3"}
```

## Command reference (Milestone 1-5 coverage)

| Command | Purpose |
|---|---|
| `start --rom PATH [--port N] [--symbols FILE ...]` | launch headless ZEsarUX, connect |
| `stop [--port N \| --pid P]` | terminate the session (no symbols file or original directory needed); `--port`/`--pid` stop an untracked emulator |
| `sessions` | list running ZEsarUX ZRCP processes and whether this state directory tracks them |
| `reset [--hard]` | soft/hard machine reset |
| `pause` / `resume` | enter/exit cpu-step mode |
| `step [--count N]` | single-step N opcodes |
| `run [--max-instructions N] [--until SYMBOL_OR_ADDR]` | bounded run, optionally to a target |
| `regs` | registers, PC resolved to nearest symbol |
| `mem ADDRESS LENGTH` | read memory (symbol or numeric address) |
| `mem-write ADDRESS HEX_BYTES` | write raw bytes (e.g. `mem-write 32768 deadbeef`) — force a state, patch a fixture, without going through typed program input |
| `mem-snapshot NAME ADDRESS LENGTH` / `mem-diff NAME [--merge-gap N] [--update]` | save a range, then report which bytes changed since (address, nearest symbol, before/after) |
| `diagnose [--trace N] [--stack N] [--resume]` | pause and capture registers, symbolised stack words, interrupt/banking state and recent trace in one call |
| `set-register REG VALUE` | set a register directly (e.g. `set-register HL 0x1234`) |
| `disassemble [ADDRESS] [--lines N]` | disassemble at a symbol/address, or the current PC if omitted |
| `break TARGET [--index N]` | set a breakpoint (condition `PC=<addr>H` under the hood) |
| `watch ADDRESS [--type read\|write\|readwrite] [--items N]` | memory watchpoint |
| `watchpoints` | list active memory watchpoints |
| `trace [--last N]` | bounded circular CPU-history dump, symbol-resolved |
| `key TEXT [--delay-ms N]` | inject ASCII keystrokes (chunked to stay under ZRCP's 20-argument command cap) |
| `press NAME [--hold-ms N]` | press+release a named key (ENTER, LEFT, BACKSPACE, ... — see `Ts2068Debug.KEY_CODES`) |
| `type TEXT` | type one or more lines (`\n`-separated) via `send-keys-string`, pressing ENTER after each — the reliable way to enter BASIC/Forth programs |
| `timex` | decoded HOME/EXROM/DOCK + video + AY-3-8912 state |
| `screenshot PATH` | save a real rendered BMP |
| `screen-hash` | deterministic SHA-256 of the rendered screen, for regression tests |
| `status` | session/process liveness, symbol count |

`ts2068-debug` never runs unbounded: every `run` carries an explicit ZRCP `limit`
(default `max_run_instructions`, 2,000,000 opcodes) — see `docs/architecture.md`. A
bounded `run` scales its own read timeout to the requested instruction count (real
ZEsarUX runs at hardware-accurate speed unless configured otherwise, so a full
2,000,000-opcode bound can legitimately take longer than a fixed short timeout).

## Reliability behaviour

- **Dropped or empty ZRCP replies** (seen under host load in parallel suites): read commands
  (`regs`, `mem`, `timex`) reconnect and retry, and a reply that is still empty or short is
  reported as an error showing the reply, not a bare `KeyError`. Commands with side effects
  (`run`, `step`, `key`, `mem-write`) are never resent automatically.
- **Orphaned emulators**: `start` refuses a busy port and cleans up its own emulator if ZRCP never
  answers; `ts2068-debug sessions` lists emulators and whether this state directory tracks them;
  `stop --port N` stops an untracked one (only a verified ZEsarUX process is ever signalled).

- **Trace after the fact**: `run`/`step` switch CPU-history recording on before executing (no measurable
  slowdown), so `trace` and `diagnose` can show the instructions leading up to a hang that has already
  happened. Previously history started at the first `trace` call and was empty.
- **Wrong ROM**: `start` refuses a missing `--rom` (ZEsarUX itself silently boots a default ROM instead).

## Concurrent/isolated sessions

CLI state (the active ZEsarUX host/port/PID/symbols) lives in `~/.ts2068-debug/state.json`
by default. Set `TS2068_DEBUG_STATE_DIR` (and a distinct `--port` on `start`) to run a
second, independent session alongside an existing one — e.g. one investigation session
per bug, or a scratch session for testing the harness itself without disturbing a live
debugging session:

```bash
TS2068_DEBUG_STATE_DIR=/tmp/ts2068-debug-scratch ts2068-debug --json start \
  --rom build/ts2068rom_zesarux.bin --port 10001
```

## Python API

For anything beyond a single CLI call — especially the regression-test runner, which
wants one long-lived session — use `ts2068_debug.controller.Ts2068Debug` directly:

```python
from ts2068_debug.controller import Ts2068Debug

with Ts2068Debug(zesarux_install_dir, romfile=rom_path) as dbg:
    dbg.load_symbols("rom/current_test_basic.sym")
    dbg.reset()
    dbg.run_until("editor_ready")
    print(dbg.get_registers().values)
```

`wait_until(predicate, max_polls=100, instructions_per_poll=10_000)` formalizes the
"bounded run, check real state, repeat" pattern instead of leaving every caller to
hand-roll it: `predicate` takes the `Ts2068Debug` instance (so it can call
`read_memory()`, `get_registers()`, etc.) and `wait_until` keeps running bounded chunks
until it returns true or the total poll budget (`max_polls * instructions_per_poll`,
still explicitly bounded) is exhausted, at which point it raises `ControllerError`.

## Status

Milestone 1 (connect/reset/pause/step/regs/mem) is fully implemented and verified
against a real running TS2068 instance — see `tests/test_integration_live.py`.
Milestones 2-5 (symbols, breakpoints, bounded execution, JSON, keyboard, regression
runner with automatic failure diagnostics, CPU history/trace, watchpoints, TS2068
banking/video/AY introspection, screenshots/screen-hash) are also implemented and
live-verified — see `tests/test_integration_testrunner.py` and `docs/zesarux-analysis.md`
§5b for captured live protocol traces. No ZEsarUX source modifications were required for
any of it.

`key` (`send-keys-ascii`) silently truncates past ZRCP's `REMOTE_MAX_PARAMETERS_COMMAND=20`
total arguments (19 key codes per delay), confirmed against ZEsarUX source and found while
typing a multi-line BASIC program that never appeared on screen. `send_keys_ascii()` now
chunks into <=19-code batches to stay correct for short bursts, but `type`/`type_line`/
`type_program` (via `send-keys-string`, not subject to the cap) are the reliable primitives
for entering program text; `press`/`press_key()` (via `send-keys-event`) handles discrete
named-key presses like ENTER/arrows/BACKSPACE.

Not yet built: joystick input automation (explicitly deferred by the original
requirements, and no project using this tool has exercised joystick input yet —
build it if and when a real need surfaces, not speculatively).

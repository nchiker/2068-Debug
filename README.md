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

## Quick start

```bash
# 1. Build the ROM (example: the ts2068rom project's own build, which already produces
#    a ZEsarUX-shaped combined home+exrom binary):
cd ~/ts2068rom && make
#    -> build/ts2068rom_zesarux.bin (16384 bytes Home ROM + 8192 bytes EXROM, concatenated
#       — see docs/zesarux-analysis.md §2 for why ZEsarUX wants exactly this shape)

# 2. Install ts2068-debug
cd ~/ts2068-debug && python3 -m pip install -e . --break-system-packages

# 3. Start ZEsarUX headless and connect
ts2068-debug --json start \
  --rom ~/ts2068rom/build/ts2068rom_zesarux.bin \
  --symbols ~/ts2068rom/rom/current_test_basic.sym

# 4. Reset the machine
ts2068-debug --json reset

# 5. Set a breakpoint (symbol or address) and run to it
ts2068-debug --json break editor_ready
ts2068-debug --json run --until editor_ready

# 6. Inspect registers (resolved to the nearest source symbol)
ts2068-debug --json regs

# 7. Run the regression tests (controller's own test suite; auto-skips the live
#    integration tests if no ZEsarUX/ROM is present)
python3 -m pytest tests/

# 8. Stop
ts2068-debug --json stop
```

Every command accepts `--json` for machine-readable output, e.g.:
```json
{"status": "ok", "pc": 37799, "registers": {"PC": 37799, "SP": 65410, ...}, "symbol": "editor_ready+3"}
```

## Command reference (Milestone 1-5 coverage)

| Command | Purpose |
|---|---|
| `start --rom PATH [--port N] [--symbols FILE ...]` | launch headless ZEsarUX, connect |
| `stop` | terminate the session |
| `reset [--hard]` | soft/hard machine reset |
| `pause` / `resume` | enter/exit cpu-step mode |
| `step [--count N]` | single-step N opcodes |
| `run [--max-instructions N] [--until SYMBOL_OR_ADDR]` | bounded run, optionally to a target |
| `regs` | registers, PC resolved to nearest symbol |
| `mem ADDRESS LENGTH` | read memory (symbol or numeric address) |
| `mem-write ADDRESS HEX_BYTES` | write raw bytes (e.g. `mem-write 32768 deadbeef`) — force a state, patch a fixture, without going through typed program input |
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

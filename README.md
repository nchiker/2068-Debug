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
| `break TARGET [--index N]` | set a breakpoint (condition `PC=<addr>H` under the hood) |
| `watch ADDRESS [--type read\|write\|readwrite] [--items N]` | memory watchpoint |
| `watchpoints` | list active memory watchpoints |
| `trace [--last N]` | bounded circular CPU-history dump, symbol-resolved |
| `key TEXT [--delay-ms N]` | inject ASCII keystrokes |
| `timex` | decoded HOME/EXROM/DOCK + video + AY-3-8912 state |
| `screenshot PATH` | save a real rendered BMP |
| `screen-hash` | deterministic SHA-256 of the rendered screen, for regression tests |
| `status` | session/process liveness, symbol count |

`ts2068-debug` never runs unbounded: every `run` carries an explicit ZRCP `limit`
(default `max_run_instructions`, 2,000,000 opcodes) — see `docs/architecture.md`.

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

## Status

Milestone 1 (connect/reset/pause/step/regs/mem) is fully implemented and verified
against a real running TS2068 instance — see `tests/test_integration_live.py`.
Milestones 2-5 (symbols, breakpoints, bounded execution, JSON, keyboard, regression
runner with automatic failure diagnostics, CPU history/trace, watchpoints, TS2068
banking/video/AY introspection, screenshots/screen-hash) are also implemented and
live-verified — see `tests/test_integration_testrunner.py` and `docs/zesarux-analysis.md`
§5b for captured live protocol traces. No ZEsarUX source modifications were required for
any of it.

Not yet built: a `wait <condition>` primitive for the test DSL described in the original
spec (currently expressed directly in Python via `run_until`/polling `read_memory` inside
a test body, which covers the same need); joystick input automation (explicitly
deferred by the original requirements); a dedicated `type` command distinct from `key`
(ASCII injection via `key` already covers typed BASIC/Forth text).

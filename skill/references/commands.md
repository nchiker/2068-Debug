# ts2068-debug command reference

Every CLI command accepts `--json` for machine-readable output. Errors surface
as `{"status": "error", "error": "..."}` (or a non-JSON message without
`--json`) rather than a stack trace.

## Session lifecycle

| Command | Purpose |
|---|---|
| `start --rom PATH [--port N] [--symbols FILE ...] [--zesarux-dir DIR]` | launch headless ZEsarUX (`--vo null --ao null`, no GUI window), connect |
| `stop` | terminate the session |
| `status` | session/process liveness, symbol count, PID, port |
| `reset [--hard]` | soft/hard machine reset |

State (host/port/PID/symbols) lives in `~/.ts2068-debug/state.json` by default.
Set `TS2068_DEBUG_STATE_DIR` (and a distinct `--port`) to run an isolated
second session alongside an existing one — e.g. your own investigation
session next to a regression suite or another agent's in-progress session
against the same or a different ROM:

```bash
TS2068_DEBUG_STATE_DIR=/tmp/ts2068-debug-scratch ts2068-debug --json start \
  --rom build/whatever_zesarux.bin --port 10001
```

## Execution control

| Command | Purpose |
|---|---|
| `pause` / `resume` | enter/exit cpu-step mode (paused/free-running) |
| `step [--count N]` | single-step N opcodes |
| `run [--max-instructions N] [--until SYMBOL_OR_ADDR]` | bounded run, optionally to a target |

`run` never executes unboundedly — it always carries an explicit ZRCP `limit`
(default 2,000,000 opcodes). The client scales its own read timeout to the
requested instruction count, since ZEsarUX runs at hardware-accurate speed by
default: a full 2,000,000-opcode bound can legitimately take several seconds
of real wall-clock time. That's not a hang in the tool — it's the emulator
doing real work.

`run`/`run_until` leave the emulator **paused** when they return (see
SKILL.md's keyboard-injection section for why this matters).

## Inspection

| Command | Purpose |
|---|---|
| `regs` | registers, PC resolved to nearest symbol; includes interrupt state (`IM`, `IFF1`/`IFF2` as "set"/"clear") and `VPS`/`TSTATES` when present |
| `mem ADDRESS LENGTH` | read memory (symbol or numeric address) |
| `trace [--last N]` | bounded circular CPU-history dump, symbol-resolved |
| `timex` | decoded HOME/EXROM/DOCK memory paging + video mode + AY-3-8912 state, plus `port_fe`/`border` (the ULA border-color port's low 3 bits) |

`IM`/`IFF1`/`IFF2` matter specifically for ISR-only, flaky, or
timing-sensitive bugs — a bug that only reproduces "sometimes" or only near
an interrupt boundary needs this, not just PC/registers.

`border`/`port_fe` matters because many of these projects' own smoke-test
ROMs signal PASS/FAIL by writing a border color (commonly 4=green pass,
2=red fail) to the ULA port and then halting — you can read that verdict
directly as a number instead of screenshotting and decoding a pixel color.

## Breakpoints and watchpoints

| Command | Purpose |
|---|---|
| `break TARGET [--index N]` | set a breakpoint (symbol or address; becomes a `PC=<addr>H` condition under the hood) |
| `watch ADDRESS [--type read\|write\|readwrite] [--items N]` | memory watchpoint |
| `watchpoints` | list active memory watchpoints |

## Keyboard (see SKILL.md for the pause/resume gotcha first)

| Command | Purpose |
|---|---|
| `key TEXT [--delay-ms N]` | inject ASCII keystrokes (chunked to stay under ZRCP's 20-argument command cap — anything relying on raw `send-keys-ascii` past 19 codes in one call is silently truncated) |
| `press NAME [--hold-ms N]` | press+release a named key (ENTER, LEFT, RIGHT, UP, DOWN, BACKSPACE, TAB, SPACE, etc. — see `Ts2068Debug.KEY_CODES`) |
| `type TEXT` | type one or more lines (`\n`-separated) via `send-keys-string`, pressing ENTER after each — the reliable way to enter BASIC/Forth program text (not subject to the 20-argument cap `key` has) |

## Screen

| Command | Purpose |
|---|---|
| `screenshot PATH` | save a real rendered screenshot — **path must end in `.bmp`**; ZEsarUX silently no-ops (empty reply, no file, no error) for any other extension |
| `screen-hash` | deterministic SHA-256 of the rendered screen, for regression tests |

## Python API

For anything beyond a single CLI call — especially a long-lived regression
runner that wants one persistent session — use
`ts2068_debug.controller.Ts2068Debug` directly:

```python
from ts2068_debug.controller import Ts2068Debug

with Ts2068Debug(zesarux_install_dir, romfile=rom_path) as dbg:
    dbg.load_symbols("build/whatever.sym")
    dbg.reset()
    dbg.run(max_instructions=3_000_000)
    dbg.resume()
    dbg.type_line('PRINT "X="; T')
    dbg.type_line("RUN")
    dbg.save_screen("/tmp/screen.bmp")
    dbg.pause()
    print(dbg.get_registers().values)
```

`type_program(lines)` types a whole list of lines, each followed by ENTER.
`send_keys_ascii`/`press_key`/`type_line` all raise `ControllerError` if
called while the emulator is paused.

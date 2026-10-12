# ts2068-debug command reference

Every CLI command accepts `--json` for machine-readable output. Errors surface
as `{"status": "error", "error": "..."}` (or a non-JSON message without
`--json`) rather than a stack trace.

## Session lifecycle

| Command | Purpose |
|---|---|
| `start --rom PATH [--port N] [--symbols FILE ...] [--zesarux-dir DIR]` | launch headless ZEsarUX (`--vo null --ao null`, no GUI window), connect. ROM and symbol paths are stored absolute. Refuses a busy port (naming the owner) and never leaves a half-started emulator running |
| `stop [--port N \| --pid P]` | terminate the session. Needs no symbols file and works from any directory. `--port`/`--pid` stop an emulator no state file tracks (only a verified ZEsarUX ZRCP process is ever signalled) |
| `sessions` | list running ZEsarUX ZRCP processes (pid, port) and whether this state directory tracks them — find orphans before choosing a port |
| `status` | session/process liveness, symbol count, PID, port |
| `reset [--hard]` | soft/hard machine reset — see gotchas.md "Reset does not mean what it sounds like": `--hard` does NOT reset PC/SP/AF, and even the correct soft reset races if issued while free-running |
| `reset-known-state` | pause, then soft-reset, then return registers — the only sequence that deterministically lands on PC=0000; use before arming a breakpoint on a one-shot early-boot target |

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
| `mem ADDRESS LENGTH` | read memory (symbol or numeric address); long reads are chunked, and a short/empty reply is retried then reported (not a `KeyError`) |
| `mem-write ADDRESS HEX_BYTES` | write raw bytes (e.g. `mem-write 32768 deadbeef`) — force a state or set up a fixture directly, not only through typed program input |
| `set-register REG VALUE` | set a register directly (e.g. `set-register HL 0x1234`) |
| `disassemble [ADDRESS] [--lines N]` | disassemble at a symbol/address, or the current PC if omitted |
| `trace [--last N]` | bounded circular CPU-history dump, symbol-resolved |
| `mem-snapshot NAME ADDRESS LENGTH` | save a memory range (in the state directory) to compare against later |
| `mem-diff NAME [--merge-gap N] [--update]` | live memory vs the snapshot: changed ranges with address, nearest symbol, before/after bytes. `--update` re-baselines. Replaces hand-rolled before/after dumps (session tables around SAVE/LOAD, a buffer around a routine) |
| `diagnose [--trace N] [--stack N] [--resume]` | one call for a hang or wrong result: pauses, then captures registers (PC symbolised), the top of the stack as words each resolved to `routine+offset` (return addresses), interrupt state, banking/border state and the last N executed instructions. Each section is captured independently (`errors` lists any that failed). Leaves the emulator paused unless `--resume` |
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
| `clear-break [--index N]` | clear a breakpoint's condition (disable it) -- **required** before your next `run` if you used `break`+`run` by hand to reach that exact target: PC is now sitting right on the still-armed condition, so an un-cleared breakpoint makes the next `run` re-trigger immediately and execute nothing (confirmed live: PC stuck exactly at the breakpoint address, ~0 TSTATES). `run_until()` already clears its own breakpoint internally; this is only needed for a hand-rolled break+run sequence (e.g. arming a breakpoint, then poking memory once you're there, then resuming). |
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

`write_memory(address, data: bytes)` writes raw bytes (the CLI's `mem-write`).
`set_register(name, value)` and `disassemble(address=None, lines=1)` are also
Python-API/CLI (`set-register`, `disassemble`) — `disassemble` resolves a
symbol name the same way every other address-taking method here does.

`wait_until(predicate, max_polls=100, instructions_per_poll=10_000)` formalizes
"bounded run, check real state, repeat" instead of leaving you to hand-roll a
polling loop each time — `predicate` takes the `Ts2068Debug` instance:

```python
dbg.wait_until(
    lambda d: d.resolve_address(d.get_registers().values["PC"]).startswith("IO_READ_KEY"),
    max_polls=20, instructions_per_poll=500_000,
)
```

Still bounded overall (`max_polls * instructions_per_poll`); raises
`ControllerError` if the condition never becomes true within that budget.

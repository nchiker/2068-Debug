# Architecture

```
Claude
   |
   | CLI ("ts2068-debug ...") / Python API (ts2068_debug.controller.Ts2068Debug)
   v
ts2068-debug
   |
   | ZRCP (plain-text TCP, line protocol)
   v
ZEsarUX (headless: --vo null --ao null --enable-remoteprotocol)
   |
   v
TS2068 emulation (Z80 core + Timex-specific hardware: banking, video modes, AY-3-8912)
   |
   v
ROM under development
```

ZEsarUX performs all actual Z80/TS2068 hardware emulation. `ts2068-debug` never
reimplements emulation — see `docs/zesarux-analysis.md` for the source-level audit that
established every required capability already exists in ZRCP.

## Package layout

```
ts2068_debug/
  zrcp.py        Raw ZRCP socket client: connect, send a command line, read a reply up
                 to the next prompt. Knows nothing about TS2068 semantics.
  process.py     Launches/stops the headless ZEsarUX subprocess.
  symbols.py     Parses assembler .sym equate files; nearest-symbol-below resolution
                 for turning a PC value into "label+offset".
  controller.py  Ts2068Debug: the JSON-shaped operation layer (reset/step/run/regs/mem/
                 breakpoints/keyboard/screenshot) built on top of zrcp.py + symbols.py.
  cli.py         argparse-based `ts2068-debug` command, with --json on every command.
```

## Session model

A `ts2068-debug start` invocation launches ZEsarUX as a detached background process and
records its PID/port/ROM/symbol-file paths in `~/.ts2068-debug/state.json`. Every
subsequent CLI invocation is a **fresh Python process** that reads that state file,
opens a new ZRCP socket to the already-running ZEsarUX, does one thing, and exits —
closing its own socket but leaving the emulator running. This keeps the CLI simple and
scriptable (one command, one JSON result, no daemon protocol of our own to design) while
still giving Claude a persistent emulator session across many small commands.

Long-lived callers (the regression test runner, a Python script) should instead use
`ts2068_debug.controller.Ts2068Debug` directly and keep one object/connection alive for
the whole test — avoids the per-command connect/reconnect overhead and avoids the
`enable-breakpoints`-is-not-idempotent-across-processes quirk documented in
`controller.py`'s `_ensure_breakpoints_enabled`.

## Bounded execution, by construction

Every `run` goes through ZRCP's own `run [verbose] [limit] ...` command with an explicit
opcode `limit` — `Ts2068Debug.run()` defaults this to `max_run_instructions` (2,000,000)
if the caller doesn't supply one. There is no code path that invokes ZRCP's `run` without
a limit. Additionally, ZRCP's `run` documentation notes the run loop is *owned by the
ZRCP connection*: if `ts2068-debug`'s socket ever disconnects, ZEsarUX's run loop dies
with it — a second, independent safety net against a hang.

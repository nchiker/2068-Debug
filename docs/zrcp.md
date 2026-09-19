# ZRCP quick reference (as used by ts2068-debug)

Full command inventory and source citations: `docs/zesarux-analysis.md`. This file is
the day-to-day cheat sheet for commands `ts2068-debug` actually issues.

## Connecting

```
--enable-remoteprotocol --remoteprotocol-port <N> --vo null --ao null
```
Launched from ZEsarUX's own install directory (required for stock-ROM lookups, even
with `--romfile` set). Banner + `command> ` prompt on connect; prompt becomes
`command@cpu-step> ` once `enter-cpu-step` has been sent.

## Registers

`get-registers` → one line of space-separated `KEY=value` tokens plus bare flag tokens:
```
PC=3f4e SP=fef4 AF=0044 BC=171f HL=5aff DE=001f IX=ffff IY=ffff AF'=ffff BC'=ffff
HL'=1d71 DE'=c078 I=00 R=6d  F=-Z---P-- F'=SZ5H3PNC MEMPTR=3f4e IM1 IFF-- VPS: 0
MMU=00000000000000000000000000000000
```
`set-register REG=VALUEh` — hex values use a trailing `H`, not a `0x` prefix.

## Memory

- `read-memory <addr> <len>` → flat hex string, no separators.
- `write-memory-raw` / `write-mapped-memory-raw` for writes.
- `load-binary file addr len`, `save-binary file addr len`.

## Execution control

- `enter-cpu-step` / `exit-cpu-step` — pause/resume.
- `cpu-step` / `cpu-step-over` — single opcode / step-over-call. Reply bundles the new
  register line, a `TSTATES: N` count, and a disassembly line for the new PC.
- `run [verbose] [limit] [no-stop-on-data] [update-immediately]` — bounded run while
  still in cpu-step mode. **Always pass `limit`** — `ts2068-debug` never calls this
  without one. Returns on breakpoint fire, socket data, or a menu-opening event.
  Dies if the ZRCP connection drops.
- `reset-cpu` / `hard-reset-cpu`.

## Breakpoints (condition-expression based, not bare-address)

`set-breakpoint <index> [condition]` — condition syntax lives in
`~/zesarux-source/src/menu/menu_debug_cpu.h` (`HELP_MESSAGE_CONDITION_BREAKPOINT`):
operator is a single `=` (not `==`), hex numeric literals use a trailing `H`
(`PC=91B6H`, not `PC=0x91B6`). Common variables: `PC`, `SP`, any register, `MRA`/`MWA`
(memory read/write address of last op), `PRA`/`PWA` (port), `OPCODE1..4`.

Must call `enable-breakpoints` once before any `set-breakpoint` will take effect —
**calling it twice in the same ZEsarUX process errors `"Already enabled"`**; since each
`ts2068-debug` CLI invocation is a fresh process with no memory of prior calls, the
`Ts2068Debug` controller tolerates that specific error text rather than treating it as
fatal (see `controller.py::_ensure_breakpoints_enabled`).

Related: `enable-breakpoint`/`disable-breakpoint` `<index>` (per-breakpoint),
`set-breakpointpasscount` (fire on Nth hit), `set-breakpointaction`, `get-breakpoints`.

## Memory watchpoints

`set-membreakpoint <address> <type> [items]` — type 0=disabled, 1=read, 2=write,
3=read/write, `items` = how many consecutive bytes. `get-membreakpoints`,
`clear-membreakpoints`.

## CPU history (bounded circular trace)

`cpu-history enabled yes`, then `started yes` to begin recording. `get-size` for how
much is recorded, `get-max-size`/`set-max-size` to bound memory use (satisfies "don't
create an enormous trace file by default" without `ts2068-debug` doing any truncation
itself), `get index`/`get-extended index` (0 = most recent), `get-pc start items`,
`find-pc address`, `restore index` (time-travel the live CPU state back to a history
entry — real reverse debugging).

## Keyboard

- `send-keys-ascii <ms> <asciicode1> [asciicode2] ...` — `ts2068-debug key "TEXT"`
  encodes each character's ordinal, space-separated. **Caps at
  `REMOTE_MAX_PARAMETERS_COMMAND=20` total arguments** (1 delay + up to 19 codes) —
  confirmed in ZEsarUX's `src/zrcp/remote.c`. Anything past the 19th character in a
  single call is silently dropped, never typed. `Ts2068Debug.send_keys_ascii()` chunks
  into <=19-code batches to stay correct, but for typed program text prefer
  `send-keys-string` below.
- `send-keys-string <ms> <string>` — takes the rest of the command line as one string,
  not subject to the 20-argument cap. `ts2068-debug type "TEXT"` (`type_line()`/
  `type_program()`) uses this per line, followed by an explicit ENTER, and is the
  reliable way to enter multi-line BASIC/Forth programs. Line-oriented — no embedded
  `\n`/`\r` in a single call.
- `send-keys-event <key> <event> [nomenu]` — named-key press/release, values from the
  `util_teclas` enum in ZEsarUX's `utils.h` (arrows, ENTER, BACKSPACE, etc.) — the right
  primitive for discrete key presses rather than ASCII text. `ts2068-debug press NAME`
  (`press_key()`) wraps this as a press-then-release with a configurable hold time.
- `set-ui-io-ports` — raw 8-row keyboard matrix + joystick byte, for cases needing exact
  simultaneous key combinations.

**The emulator must be free-running (not paused) for any of the above to have a visible
effect.** `run()`/`run_until()` leave the emulator in cpu-step mode (paused) when they
return — that's how bounded execution is implemented — and a send-keys-* command only
updates ZEsarUX's keyboard-matrix state; the ROM code that actually scans that matrix and
echoes a character to screen only runs while the Z80 clock is ticking. Call `resume()`
first (`Ts2068Debug.send_keys_ascii()`/`press_key()`/`type_line()` now raise a clear
`ControllerError` if you don't), inject the keys, then `pause()` again afterward if you
need to inspect frozen register/memory state. Confirmed live: a keystroke sent while
paused produces no error and no visible effect at all — not even a screen diff.

## TS2068 hardware state

`get-io-ports` reports, in one call: ULA data bus, Spectrum FE port, **Timex FF port**
(video mode + bit 7 = DOCK/EX-ROM select), **Timex F4 port** (per-8K-block HOME vs.
DOCK/EX paging bitmask), and the full **AY-3-8912** register bank (`Selected register:
N` + all 16 register values). `get-memory-pages verbose` independently reports the same
paging state as a human-readable per-segment table (`Long name: HOME|DOCK|EX|ROM`).
See `docs/zesarux-analysis.md` §4 for the exact bit semantics, confirmed from
`src/machines/timex.c`.

## Screen

`save-screen <path>` writes a real rendered BMP/SCR/PBM reflecting actual emulator
output. Path is resolved by the ZEsarUX process's own filesystem access — pass an
absolute path.

## Ending a session

`quit` closes the connection immediately (prints `Sayonara baby` with **no** following
prompt) — `ZRCPClient.quit()` is a special case, not routed through the normal
read-until-prompt `command()` path.

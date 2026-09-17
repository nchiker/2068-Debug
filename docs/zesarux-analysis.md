# ZEsarUX / ZRCP Analysis

Source inspected: `~/zesarux-source` (ZEsarUX 13.0, matches the binary installed at
`~/Applications/ZEsarUX-13.0`). All claims below are grounded in that source tree, not
guessed from `--help` output or documentation alone.

## 1. Architecture overview

- `src/zrcp/remote.c` (~7200 lines) implements the entire Remote Command Protocol: a
  plain-text, line-oriented TCP server. One command per line in, one text reply per
  command out (the reply may be multi-line; the protocol has no explicit framing beyond
  "read until the next `command> ` prompt reappears").
- The command dispatcher is one long `if/else strcmp(comando_sin_parametros, "...")`
  chain (~line 4010 onward). A parallel `items_ayuda[]` table (from line ~713) holds
  the canonical name, short alias, parameter syntax and help text for every command —
  this is the authoritative command reference, more complete and current than the
  emulator's `--help` CLI output.
- ZEsarUX is launched headless with:
  ```
  --enable-remoteprotocol --remoteprotocol-port <N> --vo null --ao null
  ```
  (flags parsed in `src/start.c` around lines 7123/7127). No X display is required for
  ZRCP itself — confirmed working in a prior session on this exact machine.
- **Must be launched from its own install directory** (`~/Applications/ZEsarUX-13.0`)
  or it fails to find bundled stock-ROM lookup files, even when `--romfile` supplies a
  custom ROM. This is an operational quirk of ZEsarUX's own resource loading, unrelated
  to ZRCP.
- Once connected, the socket presents a banner then a `command> ` prompt. Commands are
  newline-terminated ASCII; replies are plain text (JSON is not a native ZRCP format —
  `ts2068-debug` is responsible for the JSON translation layer per this project's
  requirements).

## 2. TS2068-specific machine setup

- `--machine TS2068` selects the machine (confirmed via `--help`'s machine list, which
  includes `TC2048`, `TC2068`, `TS1000`, `TS1500`, `TS2068`).
- `--romfile <path>` for a Timex machine reads **one file**, not two: `cpu.c`'s
  `MACHINE_IS_TIMEX_TS_TC_2068` branch loads the first 16384 bytes as the Home ROM and
  the *next* 8192 bytes, from the same file, as the EXROM. This differs from Fuse's
  separate `--rom-ts2068-0`/`--rom-ts2068-1` flags. Concretely:
  `cat home_rom.bin exrom.bin > combined.bin`, then `--romfile combined.bin`.
  (`~/ts2068rom/build/ts2068rom_zesarux.bin` in the existing ROM project is already
  built in exactly this combined shape — the debugger should reuse it rather than
  re-deriving the concatenation logic.)

## 3. Capability inventory (ZRCP command → requirement)

Everything below is sourced from `items_ayuda[]` in `remote.c`. None of it requires a
ZEsarUX modification for Milestone 1–4 scope.

| Requirement | ZRCP command(s) | Notes |
|---|---|---|
| Reset | `reset-cpu`, `hard-reset-cpu` | soft vs hard reset both exist |
| Machine select | `set-machine`/`sm`, `get-machines`, `get-current-machine`/`gcm` | |
| Pause/enter debug | `enter-cpu-step`/`encs` | puts CPU into step mode; required before `cpu-step`/breakpoints bite |
| Resume | `exit-cpu-step`/`ecs` | leaves step mode, runs freely (no ZRCP control until next break) |
| Bounded run | `run`/`r` `[verbose] [limit] [no-stop-on-data] [update-immediately]` | runs *while remaining in cpu-step mode*, controlled entirely from ZRCP; `limit` = opcode count bound; returns on breakpoint fire, socket data, or menu-opening event; **connection loss kills the run loop** — this is the natural mechanism for `ts2068-debug run --max-instructions N` |
| Single-step | `cpu-step`/`cs` (one opcode), `cpu-step-over`/`cso` (step over CALL) | |
| Registers (read) | `get-registers`/`gr` | text line, e.g. `PC=... SP=... AF=... ...` |
| Registers (write) | `set-register`/`sr` `REG=VALUE` | e.g. `set-register DE=3344H` |
| Memory read | `read-memory [address] [length]` | hex string reply; omitted length = 1 byte; omitted address = dump whole current zone |
| Memory write | `write-memory-raw`, `write-mapped-memory-raw` | raw bytes, current memory zone |
| Address breakpoints | `set-breakpoint`/`sb` `index [condition]` | **condition-expression** based, not just a bare address — e.g. `PC=8000h`; up to `MAX_BREAKPOINTS_CONDITIONS` slots; `enable-breakpoints`/`disable-breakpoints` gate them globally, `enable-breakpoint`/`eb` and `disable-breakpoint`/`db` gate individually |
| Breakpoint actions | `set-breakpointaction`/`sba` | e.g. auto-open menu, no-op, etc. — extra hook worth exposing later |
| Pass-count breakpoints | `set-breakpointpasscount`/`sbpc` | "fire on Nth hit" — built in, no extension needed |
| Memory watchpoints | `set-membreakpoint address type [items]` | type: 0=disabled,1=read,2=write,3=read/write; `get-membreakpoints`, `clear-membreakpoints` | exactly the read/write watchpoint primitive requested |
| CPU history / reverse debugging | `cpu-history` sub-actions: `enabled`, `started`, `clear`, `get index`, `get-extended index`, `get-pc start items`, `get-size`, `get-max-size`, `set-max-size`, `find-pc address`, `restore index` | a real **bounded circular buffer** already exists internally — `set-max-size` caps it, so "don't create an enormous trace file" is satisfied by construction, not by us truncating output |
| CPU transaction log | `cpu-transaction-log` | optional persistent log-to-file variant of history, with per-field toggles (address/opcode/registers/tstates/datetime) — useful for post-mortem dumps, not needed for the bounded in-memory case |
| Code coverage | `cpu-code-coverage` | bonus: address-set of everything executed since last clear |
| Stack inspection | `get-stack-backtrace [items]`, `extended-stack` | |
| Disassembly | `disassemble`/`d [address] [lines]` | needed to report "instruction responsible" in watchpoint reports |
| Keyboard | `send-keys-ascii time char...`, `send-keys-string time string`, `send-keys-event key event [nomenu]`, `set-ui-io-ports 9-hex-values` | `send-keys-event` uses the `util_teclas` enum in `utils.h` for named keys (arrows, ENTER, etc.) — the right primitive for `ts2068-debug key LEFT` |
| Screenshot | `save-screen file` | writes real BMP/SCR/PBM, reflects actual rendering |
| Deterministic screen state | `read-memory` on display file addresses, `get-io-ports`, `hexdump` | preferred over screenshots for regression testing per this project's own stated priority |
| Machine/banking state | `get-memory-pages`/`gmp [verbose]`, `get-paging-state`, `get-current-memory-zone`/`gcmz`, `get-memory-zones`/`gmz` | `gmp verbose` gives per-8K-block mapped source |
| TS2068 HOME/EXROM/DOCK | `get-io-ports` → reports `Timex FF port` and `Timex F4 port` directly | see §4 below — fully decodable, no extension needed |
| AY-3-8912 | `get-io-ports` | confirmed live (see §5): the same call that reports HOME/EXROM/DOCK ports also dumps all 16 AY-3-8912 register values and the currently selected register — no separate command, no extension needed |
| Interrupt state | `generate-nmi`; `cpu-transaction-log`/`cpu-history` implicitly capture IM/IFF transitions via register snapshots | no direct "get interrupt state" command; recoverable from register history around IRQ handling |
| Snapshots | `get-snapshot`, `put-snapshot`, `snapshot-save/-load`, `snapshot-inram-*` | in-RAM ring of snapshots — useful for fast reset-to-known-state in tests |
| Load binary into memory | `load-binary file addr len` | |
| Save binary | `save-binary file addr len` | |
| Version/build introspection | `get-version`, `get-buildnumber`, `get-cpu-core-name`, `get-os` | useful for `ts2068-debug status` |

## 4. TS2068 HOME/EXROM/DOCK — confirmed decodable, no extension needed

Traced in `src/machines/timex.c:90` (`timex_set_memory_pages`) and
`src/operaciones.c:9686` (port write handler):

- **Port `0xF4`** (`timex_port_f4`): one bit per 8K memory block (0–7). Bit=0 → block
  maps HOME RAM/ROM; bit=1 → block maps DOCK or EX-ROM (which of the two is decided by
  port FF bit 7, globally, for every bit-1 block).
- **Port `0xFF`** (`timex_port_ff`, the Timex video/paging port): bit 7 selects which
  cartridge bank the MMU uses for any block paged out of HOME — 0 = DOCK, 1 = EX-ROM.
  The low bits of the same port select video mode (confirmed in `debug.c` around the
  `Timex FF port` report and the existing 2068-Forth project's own ULAPlus/64COL
  research this session's memory already captured).
- `get-io-ports` (`debug.c:5785` `debug_get_ioports`) prints both `Timex FF port: XX`
  and `Timex F4 port: XX` directly. `ts2068-debug timex` can be a pure decode of this
  one ZRCP call — no ZEsarUX source modification is required for HOME/EXROM/DOCK state.
- `get-memory-pages verbose` additionally reports, per 8K block, which physical source
  (HOME/DOCK/EX/ROM, and which page number) is currently mapped — a second, cross-
  checkable source for the same fact.

## 5. AY-3-8912 — confirmed live, no gap

Verified against a real running TS2068 instance (`~/ts2068rom/build/ts2068rom_zesarux.bin`,
port 10123, headless): `get-io-ports` already appends the full AY state after the ULA/Timex
ports:
```
AY-3-8912 chip 0:

Selected register: 0
00:  FF
01:  FF
...
0F:  FF
```
So there is in fact no gap anywhere in the required capability list — every item in §3,
including AY register introspection, is available through existing, unmodified ZRCP
commands. `ts2068-debug` needs zero ZEsarUX source changes for the entire requirements
list as currently understood.

## 5b. Live verification notes (real ZEsarUX 13.0, TS2068, this session)

Ran a raw ZRCP probe against a real headless instance to confirm exact reply shapes
(not just command names) before building the controller on top of them:

- Banner: `Welcome to ZEsarUX remote command protocol (ZRCP)\nWrite help for available commands\n\ncommand> `
- `get-registers` reply (one line):
  `PC=0038 SP=fef6 AF=0044 BC=171f HL=5aff DE=001f IX=ffff IY=ffff AF'=ffff BC'=ffff HL'=1d71 DE'=c078 I=00 R=6c  F=-Z---P-- F'=SZ5H3PNC MEMPTR=31e6 IM1 IFF-- VPS: 0 MMU=00000000000000000000000000000000`
  — space-separated `KEY=value` tokens, plus bare flag tokens (`IM1`, `IFF--`) that
  don't follow the `KEY=value` shape and need special-cased parsing.
- `read-memory <addr> <len>` reply is a flat hex string, no spaces: `F3C30001FFFF...`.
- `cpu-step` reply bundles the *post-step* register line, a `TSTATES: N` suffix, and a
  disassembly line for the *new* PC on the next line — richer than calling
  `get-registers` + `disassemble` separately.
- `get-memory-pages verbose` reply is a repeated `Segment N / Long name / Short name /
  Start / End` block per 8K page — directly gives the live HOME/DOCK/EX mapping without
  decoding port values by hand, corroborating §4.
- Prompt flips from `command> ` to `command@cpu-step> ` while in step mode — the
  controller's reply reader must match both suffixes.
- `quit` closes the connection immediately after printing `Sayonara baby\n` (no prompt
  follows) — must be handled as a special case, not read-until-prompt.
- **Reproducible quirk**: `enter-cpu-step` → `reset-cpu` → `exit-cpu-step` → `reset-cpu`
  → `enter-cpu-step` fails the *second* `enter-cpu-step` with
  `Error. Can not enter cpu step mode. You can try closing the menu` — i.e. issuing
  `reset-cpu` while free-running (not in step mode) leaves ZEsarUX believing a menu is
  open. `close-all-menus` immediately before a retried `enter-cpu-step` reliably clears
  it. `Ts2068Debug._ensure_step_mode` retries this way automatically, so every caller
  (including `reset()` between regression tests) is unaffected. Confirmed via a raw
  ZRCP socket probe, independent of any of this project's own code — a real ZEsarUX
  behavior, not a bug in `ts2068-debug`.

## 6. Symbol/build integration

`~/ts2068rom`'s assembler emits plain equate files like `rom/current_test_basic.sym`:
```
ADD_POSITION: EQU 0x0000810D
ATTR_ADDR: EQU 0x00005800
```
One `NAME: EQU 0xHEXVALUE` pair per line — trivial to parse. Not every symbol is a code
address (some are byte constants, e.g. `ARGC_ARRAYNAME`), so `ts2068-debug`'s "nearest
symbol" resolution only considers symbols whose value fits a 16-bit address range and
should report the *closest-below* match with a `+offset`, same convention the project
spec asked for (`editor_cursor_left+17`).

## 7. Conclusion for Milestone 1

Every capability required for Milestone 1 (connect, select TS2068, reset, pause/step,
read registers, read memory, resume, clean shutdown) exists natively in ZRCP with no
ZEsarUX source changes. `ts2068-debug` is purely a translation/automation layer:
spawn-or-attach → line-protocol socket client → JSON-shaped Python API/CLI. Milestones
2–4 (symbols, breakpoints, watchpoints, keyboard, history, regression runner) are also
fully covered by existing ZRCP commands per §3. The only unresolved item for later
milestones is AY register introspection (§5), and even that has a non-blocking
workaround path.

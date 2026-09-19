"""High-level, JSON-shaped operations on top of the raw ZRCP client.

This is Milestone 1 + a slice of Milestone 2: connect, reset, pause/resume, single
step, bounded run, read registers/memory, symbol resolution. See
docs/zesarux-analysis.md for the ZRCP commands each operation is built from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .process import ZEsarUXProcess
from .symbols import SymbolTable
from .zrcp import ZRCPClient, ZRCPError

# "PC=0038" style tokens, plus bare flag tokens like IM1 / IFF-- that get-registers
# also emits (see docs/zesarux-analysis.md §5b for a captured live example).
_REG_KV_RE = re.compile(r"([A-Za-z']+)=(\S+)")
_HEX_REGS = {
    "PC", "SP", "AF", "BC", "HL", "DE", "IX", "IY",
    "AF'", "BC'", "HL'", "DE'", "I", "R", "MEMPTR",
}

# Bare (non key=value) tokens get-registers/cpu-step also emit. These previously fell
# through _REG_KV_RE entirely and were silently absent from parsed/JSON output -- a real
# gap for interrupt-related debugging (the z80-skills debug-z80 symptom router calls out
# "capture interrupt state" for ISR-only/flaky/timing-sensitive failures specifically).
_IM_RE = re.compile(r"(?<![A-Za-z])IM([0-2])(?![A-Za-z0-9])")
_IFF_RE = re.compile(r"(?<![A-Za-z])IFF([0-9-]{2})(?![A-Za-z0-9])")
_BARE_INT_RE = re.compile(r"\b(VPS|TSTATES):\s*(\d+)")


class ControllerError(Exception):
    pass


@dataclass
class Registers:
    values: dict = field(default_factory=dict)
    raw: str = ""

    def to_json(self) -> dict:
        return dict(self.values)


def parse_registers(reply: str) -> Registers:
    values: dict = {}
    for name, raw_val in _REG_KV_RE.findall(reply):
        if name in _HEX_REGS:
            try:
                values[name] = int(raw_val, 16)
            except ValueError:
                values[name] = raw_val
        else:
            values[name] = raw_val
    im_match = _IM_RE.search(reply)
    if im_match:
        values["IM"] = int(im_match.group(1))
    iff_match = _IFF_RE.search(reply)
    if iff_match:
        # Each character is '1'/'2' when that flip-flop is set, '-' when clear. Kept
        # as strings (like F/F') so they land in the CLI's "flags" bucket, not
        # "registers" -- bool would technically work too since bool is an int
        # subclass, but landing interrupt-enable state next to the bit-string flags
        # is the more natural grouping for a human or a caller scanning the reply.
        iff1, iff2 = iff_match.group(1)
        values["IFF1"] = "set" if iff1 != "-" else "clear"
        values["IFF2"] = "set" if iff2 != "-" else "clear"
    for name, raw_val in _BARE_INT_RE.findall(reply):
        values[name] = int(raw_val)
    return Registers(values=values, raw=reply)


class Ts2068Debug:
    def __init__(
        self,
        install_dir: str,
        port: int = 10000,
        romfile: Optional[str] = None,
        machine: str = "TS2068",
        max_run_instructions: int = 2_000_000,
    ):
        self.install_dir = install_dir
        self.port = port
        self.romfile = romfile
        self.machine = machine
        self.max_run_instructions = max_run_instructions
        self._proc = ZEsarUXProcess(install_dir, port=port, romfile=romfile, machine=machine)
        self._zrcp = ZRCPClient(port=port)
        self._breakpoints_enabled = False
        self.symbols = SymbolTable()

    @property
    def _in_step_mode(self) -> bool:
        # Ground truth lives on the ZRCP client (which prompt suffix it last saw),
        # not a locally tracked assumption -- correct even for a fresh object that
        # just attached to an already-running, already-paused-or-not session (e.g.
        # one `ts2068-debug` CLI invocation per process).
        return self._zrcp.in_step_mode

    # -- lifecycle -----------------------------------------------------

    def start(self, launch: bool = True) -> None:
        if launch:
            self._proc.start()
        self._zrcp.connect()

    def stop(self) -> None:
        self._zrcp.quit()
        if self._proc.running:
            self._proc.stop()

    def __enter__(self) -> "Ts2068Debug":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- symbols ---------------------------------------------------------

    def load_symbols(self, path: str) -> int:
        return self.symbols.load(path)

    def resolve_address(self, address: int) -> str:
        sym = self.symbols.resolve(address)
        return str(sym) if sym else f"${address:04X}"

    def _resolve_symbol_or_address(self, target) -> int:
        if isinstance(target, int):
            return target
        addr = self.symbols.address_of(target)
        if addr is None:
            try:
                return int(target, 0)
            except ValueError as exc:
                raise ControllerError(f"unknown symbol or address: {target!r}") from exc
        return addr

    # -- execution control -------------------------------------------------

    def _ensure_step_mode(self) -> None:
        if self._in_step_mode:
            return
        try:
            self._zrcp.command("enter-cpu-step")
        except ZRCPError as exc:
            # A reset-cpu issued while free-running can leave ZEsarUX believing a menu
            # is open, which blocks enter-cpu-step until it's explicitly closed.
            # Reproduced directly against a real ZEsarUX 13.0 instance this session;
            # see docs/zesarux-analysis.md §5b.
            if "menu" not in str(exc).lower():
                raise
            self._zrcp.command("close-all-menus")
            self._zrcp.command("enter-cpu-step")
        # No manual bookkeeping needed: self._in_step_mode reads straight off the
        # prompt suffix the command above just got back.

    def _ensure_breakpoints_enabled(self) -> None:
        # ZRCP's enable-breakpoints errors "Already enabled" on a second call within
        # the same process, but each CLI invocation is a fresh Python object with no
        # memory of that — probe get-debug-settings-free by just tolerating the error.
        if self._breakpoints_enabled:
            return
        try:
            self._zrcp.command("enable-breakpoints")
        except ZRCPError as exc:
            if "already enabled" not in str(exc).lower():
                raise
        self._breakpoints_enabled = True

    def reset(self, hard: bool = False) -> None:
        # `hard=True` (ZRCP `hard-reset-cpu`) is a documented trap, not a
        # stronger reset: confirmed directly against the raw ZRCP socket
        # (bypassing this client entirely) that it leaves PC/SP/AF exactly
        # as they were -- only plain `reset-cpu` (hard=False) actually
        # sets PC=0000, SP=ffff, AF=ffff, IM0, matching real Z80 RESET
        # semantics. See docs/gotchas... err, skill/references/gotchas.md
        # "Reset does not mean what it sounds like" for the full writeup
        # and how this was found (a 2068-Leap test-suite port that needed
        # a deterministic post-reset PC).
        self._zrcp.command("hard-reset-cpu" if hard else "reset-cpu")

    def reset_to_known_state(self) -> Registers:
        """Pause FIRST, then a plain (soft) reset -- the only sequence
        that reliably lands on a deterministic, real post-reset state
        (PC=0000 etc, confirmed live) rather than racing the machine's
        own free-running boot. Always uses the soft reset regardless of
        the `hard=` option `reset()` exposes: `hard-reset-cpu` is the
        trap documented on `reset()` above -- it does not touch PC/SP at
        all, so it cannot produce a "known state" by definition.

        Why this needs to be a named, separate method rather than baked
        silently into `start()`/`reset()`: connecting via ZRCP does not
        itself pause the CPU, and ZEsarUX (correctly, by design -- see
        `run`'s own "hardware-accurate speed" gotcha) keeps running in
        real time the whole while. `reset()` issued while still
        free-running races: the CPU can execute many more instructions in
        the round-trip time before your NEXT command than a short, linear
        boot sequence needs, so a one-shot target near the very start of
        boot (e.g. a fixture harness's own injection point immediately
        before its first real subroutine call) can be missed even though
        a reset was issued moments earlier. Pausing first removes the
        race entirely: once in cpu-step mode, `reset-cpu` takes effect
        as an ordinary synchronous command with no further free-running
        gap before your next action. Confirmed live: without this,
        `run_until()` on an early one-shot target reproducibly landed
        past it every time; with it, the same target's breakpoint fired
        correctly every time.

        Returns the post-reset registers (PC should read 0000) so a
        caller can assert on it directly instead of issuing a separate
        get_registers() call.
        """
        self.pause()
        self._zrcp.command("reset-cpu")
        return self.get_registers()

    def pause(self) -> None:
        self._ensure_step_mode()

    def resume(self) -> None:
        if self._in_step_mode:
            self._zrcp.command("exit-cpu-step")

    def step(self, count: int = 1) -> Registers:
        self._ensure_step_mode()
        reply = ""
        for _ in range(count):
            reply = self._zrcp.command("cpu-step")
        return parse_registers(reply)

    # Conservative floor on achievable opcodes/sec for a bounded `run`, used only to size
    # the socket read timeout generously enough that a legitimate long run (e.g. the
    # 2,000,000-opcode default) can't be mistaken for a hang. Real ZEsarUX/host throughput
    # is normally far higher; this only needs to avoid false positives, not be accurate.
    _MIN_RUN_OPCODES_PER_SEC = 20_000

    def _run_timeout(self, limit: int) -> float:
        return max(self._zrcp.timeout, limit / self._MIN_RUN_OPCODES_PER_SEC + 10)

    def run(self, max_instructions: Optional[int] = None, verbose: bool = False) -> str:
        """Bounded run. NEVER runs unbounded: falls back to self.max_run_instructions."""
        self._ensure_step_mode()
        limit = max_instructions if max_instructions is not None else self.max_run_instructions
        parts = ["run"]
        if verbose:
            parts.append("verbose")
        parts.append(str(limit))
        return self._zrcp.command(" ".join(parts), timeout=self._run_timeout(limit))

    def run_until(self, target, max_instructions: Optional[int] = None) -> Registers:
        """Set a one-shot breakpoint at target (symbol or address) and run to it."""
        addr = self._resolve_symbol_or_address(target)
        self._ensure_step_mode()
        self._ensure_breakpoints_enabled()
        self._zrcp.command(f"set-breakpoint 1 PC={addr:04X}H")
        self.run(max_instructions=max_instructions)
        self._zrcp.command("set-breakpoint 1")  # clear condition -> disabled
        return self.get_registers()

    def wait_until(
        self,
        predicate,
        max_polls: int = 100,
        instructions_per_poll: int = 10_000,
    ) -> int:
        """Poll `predicate(self)` between bounded runs until it's true.

        Formalizes a pattern this project's own test bodies and debugging sessions
        have repeatedly hand-rolled: bounded `run()` in a loop, checking real state
        (a memory value, a register, a symbol) after each chunk instead of guessing
        how long something takes. Still bounded overall -- the total instruction
        budget is `max_polls * instructions_per_poll`, just spent in checkpoints
        instead of one lump, so a wrong prediction fails fast and cheaply rather
        than burning the whole budget before you find out.

        `predicate` takes this Ts2068Debug instance so it can call read_memory(),
        get_registers(), etc. Returns the number of instructions actually run.
        Raises ControllerError if the condition never becomes true.
        """
        total = 0
        for _ in range(max_polls):
            if predicate(self):
                return total
            self.run(max_instructions=instructions_per_poll)
            total += instructions_per_poll
        if predicate(self):
            return total
        raise ControllerError(
            f"wait_until: condition not satisfied within {total} total bounded "
            f"instructions ({max_polls} polls of {instructions_per_poll} each)"
        )

    # -- inspection ----------------------------------------------------

    def get_registers(self) -> Registers:
        return parse_registers(self._zrcp.command("get-registers"))

    def set_register(self, register: str, value: int) -> None:
        self._zrcp.command(f"set-register {register}={value:04X}H")

    def read_memory(self, address, length: int) -> bytes:
        addr = self._resolve_symbol_or_address(address)
        reply = self._zrcp.command(f"read-memory {addr} {length}").strip()
        return bytes.fromhex(reply)

    def write_memory(self, address, data: bytes) -> None:
        """Write raw bytes via write-memory-raw.

        ZRCP has supported this since this project's own analysis pass
        (docs/zesarux-analysis.md), but nothing ever called it -- a debugger
        that can only read memory can't force a specific state to test a
        branch or set up a fixture directly, only through typed program input.
        """
        addr = self._resolve_symbol_or_address(address)
        self._zrcp.command(f"write-memory-raw {addr} {data.hex()}")

    def disassemble(self, address=None, lines: int = 1) -> str:
        if address is None:
            return self._zrcp.command(f"disassemble {lines}" if lines != 1 else "disassemble")
        # Every other address-taking command (read/write_memory, breakpoints) resolves a
        # symbol name here; this one previously passed the raw string straight to ZRCP,
        # which needs a numeric address -- would fail immediately on `disassemble SYMBOL`.
        addr = self._resolve_symbol_or_address(address)
        return self._zrcp.command(f"disassemble {addr} {lines}")

    def get_io_ports(self) -> str:
        return self._zrcp.command("get-io-ports")

    def get_memory_pages(self, verbose: bool = True) -> str:
        return self._zrcp.command("get-memory-pages verbose" if verbose else "get-memory-pages")

    def status(self) -> dict:
        return {
            "status": "ok",
            "process_running": self._proc.running,
            "in_step_mode": self._in_step_mode,
            "machine": self.machine,
            "port": self.port,
            "symbols_loaded": len(self.symbols),
        }

    # -- breakpoints (Milestone 2 slice, exercised here for run_until) ------

    def set_breakpoint(self, index: int, target) -> None:
        addr = self._resolve_symbol_or_address(target)
        self._ensure_breakpoints_enabled()
        self._zrcp.command(f"set-breakpoint {index} PC={addr:04X}H")

    def clear_breakpoint(self, index: int) -> None:
        self._zrcp.command(f"set-breakpoint {index}")

    # -- memory watchpoints (Milestone 4) -----------------------------------

    WATCH_READ = 1
    WATCH_WRITE = 2
    WATCH_READWRITE = 3

    def set_watchpoint(self, address, watch_type: int = WATCH_READWRITE, items: int = 1) -> None:
        addr = self._resolve_symbol_or_address(address)
        self._ensure_breakpoints_enabled()
        self._zrcp.command(f"set-membreakpoint {addr} {watch_type} {items}")

    def get_watchpoints(self) -> str:
        return self._zrcp.command("get-membreakpoints")

    def clear_watchpoints(self) -> None:
        self._zrcp.command("clear-membreakpoints")

    # -- CPU history / bounded circular trace (Milestone 4) -----------------

    def _history_ready(self) -> None:
        if getattr(self, "_history_enabled", False):
            return
        for sub_command in ("cpu-history enabled yes", "cpu-history started yes"):
            try:
                self._zrcp.command(sub_command)
            except ZRCPError as exc:
                if "already" not in str(exc).lower():
                    raise
        self._history_enabled = True

    def trace(self, last: int = 100) -> list[dict]:
        """Bounded circular trace of the most recently executed instructions.

        Each entry: pc, symbol, registers dict (from cpu-history's extended get).
        """
        self._history_ready()
        size = int(self._zrcp.command("cpu-history get-size").strip())
        n = min(last, size)
        entries = []
        for i in range(n):
            reply = self._zrcp.command(f"cpu-history get-extended {i}")
            regs = parse_registers(reply)
            pc = regs.values.get("PC")
            entries.append(
                {
                    "index": i,
                    "pc": pc,
                    "symbol": self.resolve_address(pc) if pc is not None else None,
                    "raw": reply,
                }
            )
        return entries

    # -- keyboard --------------------------------------------------------

    # ZRCP caps a command at REMOTE_MAX_PARAMETERS_COMMAND=20 arguments total (1 for
    # the delay + up to 19 key codes) -- confirmed in ~/zesarux-source/src/zrcp/remote.c.
    # A send-keys-ascii call with more codes than that is silently truncated: nothing
    # past the 19th character is ever typed. Chunk to stay under the limit.
    _SEND_KEYS_ASCII_CHUNK = 19

    # From ~/zesarux-source/src/utils.h enum util_teclas: named (non-ASCII) keys start
    # at 128 and increment in this declared order.
    KEY_CODES = {
        "SPACE": 128, "ENTER": 129, "HOME": 130, "END": 131, "DEL": 132,
        "SHIFT_L": 133, "SHIFT_R": 134, "CAPS_SHIFT": 135, "ALT_L": 136, "ALT_R": 137,
        "CONTROL_L": 138, "CONTROL_R": 139, "BACKSPACE": 140, "FIRE": 141,
        "LEFT": 142, "RIGHT": 143, "DOWN": 144, "UP": 145, "TAB": 146,
        "CAPS_LOCK": 147, "COMMA": 148, "PERIOD": 149,
    }

    def _require_running(self, action: str) -> None:
        """Refuse to inject input while the emulator is paused (cpu-step mode).

        Found live (confirmed independently twice in the same session): send-keys-*
        commands only update ZEsarUX's keyboard-matrix state -- the ROM code that
        actually scans that matrix and echoes a character to screen only executes
        while the Z80 clock is ticking. run()/run_until() leave the emulator PAUSED
        in cpu-step mode when they return (that's how bounded execution is
        implemented), so a caller who reasonably assumes "I just ran it, it's
        presumably still running" gets a silent no-op: no error, no exception,
        nothing on screen, and no way to tell a real failure from their own mistake.
        Fail loud instead -- call resume() first (see docs/zrcp.md's keyboard
        section for why, and note resume() itself is a deliberate, explicit action,
        not an unbounded run this method should trigger on the caller's behalf).
        """
        if self._in_step_mode:
            raise ControllerError(
                f"cannot {action}: the emulator is paused (cpu-step mode). "
                "run()/run_until() leave it paused when they return; keystrokes "
                "sent while paused are silently never scanned by the ROM. Call "
                "resume() first, inject the keys, then re-pause if you need to "
                "inspect frozen state afterward."
            )

    def send_keys_ascii(self, text: str, delay_ms: int = 100) -> None:
        self._require_running("send keys")
        codes = [str(ord(c)) for c in text]
        for i in range(0, len(codes), self._SEND_KEYS_ASCII_CHUNK):
            chunk = " ".join(codes[i : i + self._SEND_KEYS_ASCII_CHUNK])
            self._zrcp.command(f"send-keys-ascii {delay_ms} {chunk}")

    def press_key(self, name: str, hold_ms: int = 80) -> None:
        """Press and release a named (non-ASCII) key, e.g. ENTER, LEFT, BACKSPACE."""
        self._require_running("press a key")
        code = self.KEY_CODES.get(name.upper())
        if code is None:
            raise ControllerError(f"unknown key name: {name!r} (known: {sorted(self.KEY_CODES)})")
        import time as _time

        self._zrcp.command(f"send-keys-event {code} 1")
        _time.sleep(hold_ms / 1000)
        self._zrcp.command(f"send-keys-event {code} 0")

    def type_line(self, text: str, char_delay_ms: int = 60, settle_ms: int = 150) -> None:
        """Type one line of text (no embedded newline) and press ENTER.

        Uses send-keys-string, which -- unlike send-keys-ascii -- takes the whole
        remainder of the ZRCP command line as one string, so it isn't subject to the
        20-argument cap. Text must not contain '\\n'/'\\r' (ZRCP is line-oriented);
        use type_program() for multi-line input.
        """
        import time as _time

        self._require_running("type a line")
        if "\n" in text or "\r" in text:
            raise ControllerError("type_line() text must not contain newlines; use type_program()")
        if text:
            self._zrcp.command(f"send-keys-string {char_delay_ms} {text}")
        self.press_key("ENTER")
        _time.sleep(settle_ms / 1000)

    def type_program(self, lines: list[str], char_delay_ms: int = 60, settle_ms: int = 150) -> None:
        """Type each line (already split, no embedded newlines) followed by ENTER."""
        for line in lines:
            self.type_line(line, char_delay_ms=char_delay_ms, settle_ms=settle_ms)

    # -- screen ------------------------------------------------------------

    def save_screen(self, path: str) -> None:
        """Save the rendered screen as a BMP.

        ZEsarUX's `save-screen` only writes `.bmp` (confirmed live: a `.png` path
        gets an empty, non-error reply and no file at all -- silent no-op, not a
        rejected command). Enforce the extension and verify the file actually
        landed, rather than letting callers get a false "status": "ok" for a
        screenshot that was never written.
        """
        from pathlib import Path

        p = Path(path)
        if p.suffix.lower() != ".bmp":
            raise ControllerError(
                f"save_screen path must end in .bmp (ZEsarUX only writes BMP; "
                f"got {path!r})"
            )
        self._zrcp.command(f"save-screen {path}")
        if not p.is_file():
            raise ControllerError(
                f"save-screen reported success but {path} was not created "
                f"(check the directory exists and is writable by the ZEsarUX process)"
            )

    def screen_hash(self) -> str:
        """Deterministic hash of the rendered screen, for regression testing."""
        import hashlib
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.bmp"
            self.save_screen(str(path))
            return hashlib.sha256(path.read_bytes()).hexdigest()

    # -- TS2068-specific hardware state (Milestone 5) ------------------------

    def timex_state(self) -> dict:
        """Decode HOME/EXROM/DOCK + video mode + AY registers from get-io-ports.

        See docs/zesarux-analysis.md §4 for the bit semantics this is built from
        (src/machines/timex.c timex_set_memory_pages).
        """
        raw = self.get_io_ports()
        ff = self._extract_hex_field(raw, "Timex FF port")
        f4 = self._extract_hex_field(raw, "Timex F4 port")
        fe = self._extract_hex_field(raw, "Spectrum FE port")
        result = {
            "machine": "TS2068",
            "raw": raw,
        }
        if fe is not None:
            # Border/EAR/MIC port; low 3 bits are the last-written border color
            # (standard Spectrum ULA encoding) -- e.g. this project's own smoke
            # ROMs signal PASS/FAIL by writing 4 (green) or 2 (red) here before
            # halting. Exposed numerically so callers don't have to regex the
            # raw text or screenshot-and-decode a pixel color themselves.
            result["port_fe"] = fe
            result["border"] = fe & 0x07
        if ff is not None and f4 is not None:
            ex_rom_selected = bool(ff & 0x80)
            blocks = []
            for bit in range(8):
                paged_out = bool(f4 & (1 << bit))
                if not paged_out:
                    source = "HOME"
                else:
                    source = "EX" if ex_rom_selected else "DOCK"
                blocks.append({"block": bit, "start": bit * 0x2000, "source": source})
            result["memory"] = {
                "home": not any(b["source"] != "HOME" for b in blocks),
                "exrom_selected": ex_rom_selected,
                "blocks": blocks,
                "port_f4": f4,
                "port_ff": ff,
            }
        return result

    @staticmethod
    def _extract_hex_field(raw: str, label: str) -> Optional[int]:
        m = re.search(rf"{re.escape(label)}:\s*([0-9A-Fa-f]+)", raw)
        return int(m.group(1), 16) if m else None

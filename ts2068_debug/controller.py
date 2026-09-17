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
        self._in_step_mode = False
        self._breakpoints_enabled = False
        self.symbols = SymbolTable()

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
        self._in_step_mode = True

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
        self._zrcp.command("hard-reset-cpu" if hard else "reset-cpu")

    def pause(self) -> None:
        self._ensure_step_mode()

    def resume(self) -> None:
        if self._in_step_mode:
            self._zrcp.command("exit-cpu-step")
            self._in_step_mode = False

    def step(self, count: int = 1) -> Registers:
        self._ensure_step_mode()
        reply = ""
        for _ in range(count):
            reply = self._zrcp.command("cpu-step")
        return parse_registers(reply)

    def run(self, max_instructions: Optional[int] = None, verbose: bool = False) -> str:
        """Bounded run. NEVER runs unbounded: falls back to self.max_run_instructions."""
        self._ensure_step_mode()
        limit = max_instructions if max_instructions is not None else self.max_run_instructions
        parts = ["run"]
        if verbose:
            parts.append("verbose")
        parts.append(str(limit))
        return self._zrcp.command(" ".join(parts))

    def run_until(self, target, max_instructions: Optional[int] = None) -> Registers:
        """Set a one-shot breakpoint at target (symbol or address) and run to it."""
        addr = self._resolve_symbol_or_address(target)
        self._ensure_step_mode()
        self._ensure_breakpoints_enabled()
        self._zrcp.command(f"set-breakpoint 1 PC={addr:04X}H")
        self.run(max_instructions=max_instructions)
        self._zrcp.command("set-breakpoint 1")  # clear condition -> disabled
        return self.get_registers()

    # -- inspection ----------------------------------------------------

    def get_registers(self) -> Registers:
        return parse_registers(self._zrcp.command("get-registers"))

    def set_register(self, register: str, value: int) -> None:
        self._zrcp.command(f"set-register {register}={value:04X}H")

    def read_memory(self, address, length: int) -> bytes:
        addr = self._resolve_symbol_or_address(address)
        reply = self._zrcp.command(f"read-memory {addr} {length}").strip()
        return bytes.fromhex(reply)

    def disassemble(self, address: Optional[int] = None, lines: int = 1) -> str:
        if address is None:
            return self._zrcp.command(f"disassemble {lines}" if lines != 1 else "disassemble")
        return self._zrcp.command(f"disassemble {address} {lines}")

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

    def send_keys_ascii(self, text: str, delay_ms: int = 100) -> None:
        codes = " ".join(str(ord(c)) for c in text)
        self._zrcp.command(f"send-keys-ascii {delay_ms} {codes}")

    # -- screen ------------------------------------------------------------

    def save_screen(self, path: str) -> None:
        self._zrcp.command(f"save-screen {path}")

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
        result = {
            "machine": "TS2068",
            "raw": raw,
        }
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

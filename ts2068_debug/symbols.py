"""Symbol/equate table support, so Claude can reason in source symbols, not addresses.

Parses the assembler's ``NAME: EQU 0xHEXVALUE`` equate files (see
~/ts2068rom/rom/*.sym for the reference format this was built against).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_LINE_RE = re.compile(r"^\s*([A-Za-z_.][A-Za-z0-9_.]*)\s*:\s*EQU\s+0[xX]?([0-9A-Fa-f]+)\s*$")


@dataclass(frozen=True)
class ResolvedSymbol:
    name: str
    offset: int

    def __str__(self) -> str:
        return self.name if self.offset == 0 else f"{self.name}+{self.offset}"


class SymbolTable:
    def __init__(self):
        # name -> value (full range, not just 16-bit addresses)
        self._by_name: dict[str, int] = {}
        # sorted list of (value, name) restricted to plausible 16-bit code addresses,
        # used for nearest-symbol-below resolution.
        self._addr_sorted: list[tuple[int, str]] = []

    def load(self, path: str) -> int:
        """Load one .sym file. Returns the number of symbols parsed."""
        count = 0
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                m = _LINE_RE.match(raw_line)
                if not m:
                    continue
                name, hexval = m.group(1), m.group(2)
                value = int(hexval, 16)
                self._by_name[name] = value
                count += 1
        self._rebuild_addr_index()
        return count

    def _rebuild_addr_index(self) -> None:
        addrs = [(v, n) for n, v in self._by_name.items() if 0 <= v <= 0xFFFF]
        addrs.sort()
        self._addr_sorted = addrs

    def address_of(self, name: str) -> int | None:
        return self._by_name.get(name)

    def resolve(self, address: int) -> ResolvedSymbol | None:
        """Nearest-symbol-at-or-below `address`, e.g. editor_cursor_left+17."""
        if not self._addr_sorted:
            return None
        import bisect

        keys = [v for v, _ in self._addr_sorted]
        idx = bisect.bisect_right(keys, address) - 1
        if idx < 0:
            return None
        value, name = self._addr_sorted[idx]
        return ResolvedSymbol(name=name, offset=address - value)

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

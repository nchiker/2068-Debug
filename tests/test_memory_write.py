"""Regression coverage for write_memory() and disassemble()'s symbol resolution.

write_memory() was entirely missing until now -- ZRCP has supported
write-memory-raw since this project's own analysis pass, but nothing ever
called it. disassemble() existed but, unlike every other address-taking
method here (read_memory, set_breakpoint, ...), never resolved a symbol name
to an address before sending it to ZRCP -- would have failed immediately on
`disassemble("SOME_LABEL")`.
"""
from unittest.mock import MagicMock

from ts2068_debug.controller import Ts2068Debug


def _make_debug_with_mock_zrcp():
    dbg = Ts2068Debug.__new__(Ts2068Debug)
    dbg._zrcp = MagicMock()
    dbg._zrcp.in_step_mode = False
    dbg.symbols = MagicMock()
    dbg.symbols.address_of.return_value = None
    return dbg


def test_write_memory_sends_hex_bytes_to_write_memory_raw():
    dbg = _make_debug_with_mock_zrcp()
    dbg.write_memory(0x8000, bytes([0x3E, 0x05]))
    dbg._zrcp.command.assert_called_once_with("write-memory-raw 32768 3e05")


def test_write_memory_resolves_symbol_address():
    dbg = _make_debug_with_mock_zrcp()
    dbg.symbols.address_of.return_value = 0x4000
    dbg.write_memory("SOME_LABEL", bytes([0x00]))
    dbg._zrcp.command.assert_called_once_with("write-memory-raw 16384 00")


def test_disassemble_resolves_symbol_address():
    dbg = _make_debug_with_mock_zrcp()
    dbg.symbols.address_of.return_value = 0x4000
    dbg._zrcp.command.return_value = "4000 NOP"
    dbg.disassemble("SOME_LABEL", lines=2)
    dbg._zrcp.command.assert_called_once_with("disassemble 16384 2")


def test_disassemble_with_no_address_uses_current_pc():
    dbg = _make_debug_with_mock_zrcp()
    dbg._zrcp.command.return_value = "3f4e PUSH AF"
    dbg.disassemble()
    dbg._zrcp.command.assert_called_once_with("disassemble")
    dbg.symbols.address_of.assert_not_called()

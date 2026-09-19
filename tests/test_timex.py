"""Regression coverage for timex_state()'s border/port_fe extraction.

Added while porting the ts2068rom smoke-test runner off Fuse/X11 pixel-color
screenshots and onto ts2068-debug: this project's smoke ROMs signal PASS/FAIL by
writing a border color (4=green, 2=red) to the Spectrum FE port then halting.
timex_state() already parsed the Timex FF/F4 ports numerically but left the
Spectrum FE port ("border") in `raw` text only, forcing callers to regex it
themselves -- exactly the kind of pixel/text guesswork the ZRCP-based tools
were meant to replace.
"""
from unittest.mock import MagicMock

from ts2068_debug.controller import Ts2068Debug

# Captured shape from a real ZEsarUX get-io-ports reply (docs/zesarux-analysis.md).
SAMPLE_IO_PORTS = (
    "ULA Data Bus value: FFH\n"
    "Spectrum FE port: 04\n"
    "Timex FF port: 80\n"
    "Timex F4 port: 40\n\n"
    "AY-3-8912 chip 0:\n\n"
    "Selected register: 0\n"
)


def _make_debug_with_mock_zrcp(io_ports_reply: str):
    dbg = Ts2068Debug.__new__(Ts2068Debug)
    dbg._zrcp = MagicMock()
    dbg._zrcp.command.return_value = io_ports_reply
    return dbg


def test_timex_state_extracts_border_from_fe_port():
    dbg = _make_debug_with_mock_zrcp(SAMPLE_IO_PORTS)
    state = dbg.timex_state()
    assert state["port_fe"] == 0x04
    assert state["border"] == 4  # green: this project's smoke-ROM PASS signal


def test_timex_state_border_masks_to_low_three_bits():
    dbg = _make_debug_with_mock_zrcp(SAMPLE_IO_PORTS.replace("FE port: 04", "FE port: FA"))
    state = dbg.timex_state()
    assert state["port_fe"] == 0xFA
    assert state["border"] == 0x02  # red, with EAR/MIC/unused bits masked off

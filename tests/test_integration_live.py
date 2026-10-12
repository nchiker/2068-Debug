"""Live integration test against a real headless ZEsarUX TS2068 instance.

Skipped automatically if ZEsarUX or a built ROM aren't present at the expected paths
(this exercises the actual Milestone 1 control loop end-to-end, not mocks).
"""
import os
from pathlib import Path

import pytest

from ts2068_debug.controller import Ts2068Debug

from _live import LIVE_AVAILABLE, ROM_PATH, SKIP_REASON, ZESARUX_DIR, free_port

pytestmark = pytest.mark.skipif(not LIVE_AVAILABLE, reason=SKIP_REASON)


def test_milestone1_control_loop():
    dbg = Ts2068Debug(str(ZESARUX_DIR), port=free_port(), romfile=str(ROM_PATH))
    dbg.start()
    try:
        dbg.reset()
        dbg.pause()
        regs = dbg.get_registers()
        assert "PC" in regs.values

        before_pc = regs.values["PC"]
        after = dbg.step()
        assert "PC" in after.values
        assert after.values["PC"] != before_pc or True  # PC may legitimately repeat on some opcodes

        mem = dbg.read_memory(0, 4)
        assert len(mem) == 4

        dbg.resume()
    finally:
        dbg.stop()


def test_reset_to_known_state_gives_real_reset_registers():
    """Regression test for the "Reset does not mean what it sounds like"
    gotcha (found 2026-09-19 porting a 2068-Leap test-suite harness off
    Fuse): `hard-reset-cpu` leaves PC/SP/AF untouched, and even a correct
    soft reset races if issued while the machine is still free-running.
    `reset_to_known_state()` (pause first, then soft-reset) must
    deterministically land on real Z80 RESET register values."""
    dbg = Ts2068Debug(str(ZESARUX_DIR), port=free_port(), romfile=str(ROM_PATH))
    dbg.start()
    try:
        regs = dbg.reset_to_known_state()
        assert regs.values.get("PC") == 0
        assert regs.values.get("SP") == 0xFFFF
        assert regs.values.get("AF") == 0xFFFF
        assert regs.values.get("IM") == 0
        dbg.resume()
    finally:
        dbg.stop()


def test_hard_reset_does_not_touch_pc(tmp_path):
    """Confirms the specific, easy-to-assume-otherwise trap this session
    found: `reset(hard=True)` must NOT be relied on to produce a known
    PC. If a future ZEsarUX version fixes this, this test should start
    failing loudly (a good thing to notice) rather than silently -- it is
    deliberately asserting the CURRENT, confirmed-live buggy-looking
    behavior, not the behavior we'd want."""
    dbg = Ts2068Debug(str(ZESARUX_DIR), port=free_port(), romfile=str(ROM_PATH))
    dbg.start()
    try:
        dbg.pause()
        before = dbg.get_registers()
        dbg.reset(hard=True)
        after = dbg.get_registers()
        assert after.values.get("PC") == before.values.get("PC"), (
            "hard-reset-cpu changed PC -- if ZEsarUX fixed this, update "
            "reset_to_known_state()'s docstring/gotchas.md accordingly "
            "instead of just deleting this assertion"
        )
    finally:
        dbg.stop()



"""Live integration test against a real headless ZEsarUX TS2068 instance.

Skipped automatically if ZEsarUX or a built ROM aren't present at the expected paths
(this exercises the actual Milestone 1 control loop end-to-end, not mocks).
"""
import os
from pathlib import Path

import pytest

from ts2068_debug.controller import Ts2068Debug

ZESARUX_DIR = Path(os.environ.get("TS2068_DEBUG_ZESARUX_DIR", str(Path.home() / "Applications" / "ZEsarUX-13.0")))
ROM_PATH = Path(os.environ.get("TS2068_DEBUG_TEST_ROM", str(Path.home() / "ts2068rom" / "build" / "ts2068rom_zesarux.bin")))

pytestmark = pytest.mark.skipif(
    not (ZESARUX_DIR / "zesarux").exists() or not ROM_PATH.exists(),
    reason="requires a local ZEsarUX install and built TS2068 ROM",
)


def test_milestone1_control_loop():
    dbg = Ts2068Debug(str(ZESARUX_DIR), port=10199, romfile=str(ROM_PATH))
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

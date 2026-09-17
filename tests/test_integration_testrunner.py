"""Live exercise of the Milestone 3 regression-test runner against a real ROM.

Includes one deliberately-failing test to prove the automatic failure-diagnostics
capture (registers/PC/symbol/trace/TS2068 state) actually fires, per this project's
failure-behavior requirement.
"""
import os
from pathlib import Path

import pytest

from ts2068_debug.controller import Ts2068Debug
from ts2068_debug.testrunner import RegressionTest, expect, run_tests, summarize

ZESARUX_DIR = Path(os.environ.get("TS2068_DEBUG_ZESARUX_DIR", str(Path.home() / "Applications" / "ZEsarUX-13.0")))
ROM_PATH = Path(os.environ.get("TS2068_DEBUG_TEST_ROM", str(Path.home() / "ts2068rom" / "build" / "ts2068rom_zesarux.bin")))

pytestmark = pytest.mark.skipif(
    not (ZESARUX_DIR / "zesarux").exists() or not ROM_PATH.exists(),
    reason="requires a local ZEsarUX install and built TS2068 ROM",
)


def test_runner_reports_pass_and_fail_with_diagnostics():
    dbg = Ts2068Debug(str(ZESARUX_DIR), port=10198, romfile=str(ROM_PATH))
    dbg.start()
    try:

        def passes(d: Ts2068Debug) -> None:
            d.pause()
            regs = d.get_registers()
            expect(d, "PC is a 16-bit value", 0 <= regs.values["PC"] <= 0xFFFF, True)

        def fails(d: Ts2068Debug) -> None:
            d.pause()
            regs = d.get_registers()
            expect(d, "PC deliberately wrong", regs.values["PC"], -1)

        results = run_tests(dbg, [
            RegressionTest(name="pc_in_range", body=passes),
            RegressionTest(name="deliberate_failure", body=fails),
        ])
        summary = summarize(results)

        assert summary["total"] == 2
        assert summary["passed"] == 1
        assert summary["failed"] == 1

        failed = [r for r in results if not r.passed][0]
        assert failed.name == "deliberate_failure"
        assert "registers" in failed.diagnostics
        assert "pc" in failed.diagnostics
        assert "symbol" in failed.diagnostics
        assert "trace" in failed.diagnostics
        assert "timex" in failed.diagnostics
    finally:
        dbg.stop()

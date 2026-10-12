"""Live scenarios on the real V2 ROM: the failure modes the V2 debugging record asked to fix.

Each test uses its own port and state directory, so it can run next to other suites. Skipped
without ZEsarUX and a built ROM (see tests/_live.py).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _live import LIVE_AVAILABLE, ROM_PATH, SKIP_REASON, ZESARUX_DIR, free_port
from ts2068_debug.controller import ControllerError, Ts2068Debug
from ts2068_debug.process import find_zesarux_processes, port_in_use

pytestmark = pytest.mark.skipif(not LIVE_AVAILABLE, reason=SKIP_REASON)

# A real symbols file if the ROM sits in a 2068-Leap build directory (nearest-symbol checks).
SYM_PATH = ROM_PATH.parent / "test_basic.sym"


def cli(state_dir, *args, cwd=None, check=True):
    env = dict(os.environ, TS2068_DEBUG_STATE_DIR=str(state_dir), TS2068_DEBUG_ZESARUX_DIR=str(ZESARUX_DIR))
    r = subprocess.run([sys.executable, "-m", "ts2068_debug.cli", "--json", *args],
                       capture_output=True, text=True, env=env, cwd=cwd, timeout=120)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    if check:
        assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.returncode, out


def _gone(port, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if not any(p.port == port for p in find_zesarux_processes()) and not port_in_use(port):
            return True
        time.sleep(0.2)
    return False


def test_stop_works_from_another_directory_with_relative_symbols(tmp_path):
    if not SYM_PATH.is_file():
        pytest.skip("needs a .sym next to the ROM")
    port = free_port()
    cli(tmp_path, "start", "--rom", str(ROM_PATH), "--port", str(port),
        "--symbols", SYM_PATH.name, cwd=ROM_PATH.parent)
    try:
        state = json.loads((tmp_path / "state.json").read_text())
        assert Path(state["symbols"][0]).is_absolute()
        # a command from an unrelated directory still has its symbols
        _, regs = cli(tmp_path, "regs", cwd="/")
        assert regs["pc"] is not None
        # the old failure: stop needed build/exrom.sym relative to the original directory
        _, out = cli(tmp_path, "stop", cwd="/")
        assert out["result"] in ("terminated", "killed")
    finally:
        cli(tmp_path, "stop", cwd="/", check=False)
    assert _gone(port)


def test_busy_port_is_refused_and_does_not_disturb_the_owner(tmp_path):
    port = free_port()
    cli(tmp_path / "a", "start", "--rom", str(ROM_PATH), "--port", str(port))
    try:
        rc, out = cli(tmp_path / "b", "start", "--rom", str(ROM_PATH), "--port", str(port), check=False)
        assert rc == 1 and "already in use" in out["error"]
        _, regs = cli(tmp_path / "a", "regs")  # the owner is untouched
        assert regs["status"] == "ok"
    finally:
        cli(tmp_path / "a", "stop", check=False)
    assert _gone(port)


def test_missing_rom_is_refused_and_leaves_no_emulator_behind(tmp_path):
    port = free_port()
    rc, out = cli(tmp_path, "start", "--rom", str(tmp_path / "missing.bin"), "--port", str(port), check=False)
    try:
        assert rc == 1 and "ROM file not found" in out["error"], out
    finally:
        cli(tmp_path, "stop", "--port", str(port), check=False)  # never leak if the assertion fails
    assert _gone(port)


def test_untracked_emulator_is_listed_and_stopped_by_port(tmp_path):
    port = free_port()
    dbg = Ts2068Debug(str(ZESARUX_DIR), port=port, romfile=str(ROM_PATH))
    dbg.start()
    dbg._zrcp.close()  # leave it running with no state file: what a crashed session leaves
    try:
        _, out = cli(tmp_path, "sessions")
        mine = [s for s in out["sessions"] if s["port"] == port]
        assert mine and mine[0]["tracked"] is False
        _, stopped = cli(tmp_path, "stop", "--port", str(port))
        assert stopped["result"] in ("terminated", "killed")
    finally:
        dbg._proc.stop()
    assert _gone(port)
    rc, out = cli(tmp_path, "stop", "--port", "9", check=False)  # never signals an arbitrary target
    assert rc == 1


def test_memory_diff_and_diagnose_on_a_hung_program(tmp_path):
    """A routine that never returns: jr $ at $F000 with a return address on the stack."""
    port = free_port()
    dbg = Ts2068Debug(str(ZESARUX_DIR), port=port, romfile=str(ROM_PATH))
    dbg.start()
    try:
        if SYM_PATH.is_file():
            dbg.load_symbols(str(SYM_PATH))
        dbg.reset_to_known_state()
        before = dbg.snapshot_memory(0xF100, 16)
        dbg.write_memory(0xF000, bytes([0x18, 0xFE]))          # jr $
        dbg.write_memory(0xF100, bytes([0xAA, 0xBB, 0x00, 0x20]))
        dbg.set_register("SP", 0xF100)
        dbg.set_register("PC", 0xF000)
        dbg.run(max_instructions=2000)
        diff = dbg.diff_memory(before)
        assert diff["changed_bytes"] == 3 or diff["changed_bytes"] == 4
        assert diff["ranges"][0]["address"] == 0xF100
        assert diff["ranges"][0]["after"].startswith("aabb")
        d = dbg.diagnose(trace_last=8, stack_words=2)
        assert d["errors"] == {}, d["errors"]
        assert d["pc"] == 0xF000 and d["paused"]
        assert d["stack"][0]["value"] == 0xBBAA and d["stack"][1]["value"] == 0x2000
        assert {e["pc"] for e in d["trace"]} == {0xF000}      # the hang signature: one address
        assert "border" in d["timex"]
        # an unbounded wait on a condition that never holds fails with a bounded, readable error
        with pytest.raises(ControllerError, match="not satisfied"):
            dbg.wait_until(lambda x: x.read_memory(0xF200, 1) == b"\x01", max_polls=2, instructions_per_poll=500)
    finally:
        dbg.stop()
    assert _gone(port)


def test_set_register_accepts_a_symbol(tmp_path):
    if not SYM_PATH.is_file():
        pytest.skip("needs a .sym next to the ROM")
    port = free_port()
    cli(tmp_path, "start", "--rom", str(ROM_PATH), "--port", str(port), "--symbols", str(SYM_PATH))
    try:
        cli(tmp_path, "reset-known-state")
        symbol = next(l.split(":")[0] for l in SYM_PATH.read_text().splitlines()
                      if l.startswith("MATH_UMUL16:"))
        cli(tmp_path, "set-register", "PC", symbol)
        _, regs = cli(tmp_path, "regs")
        assert regs["symbol"] == symbol
        rc, out = cli(tmp_path, "set-register", "HL", "NO_SUCH_SYMBOL", check=False)
        assert rc == 1 and "unknown symbol" in out["error"]
    finally:
        cli(tmp_path, "stop", check=False)
    assert _gone(port)

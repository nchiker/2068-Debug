"""Reliability fixes from the V2 qualification/overnight debugging record (no emulator needed).

- an idempotent read survives a dropped ZRCP connection (retried after reconnect); a
  non-idempotent command is never silently resent
- get-registers / read-memory replies that arrive empty or short become a clear
  ControllerError (after retries), not a bare KeyError
- start() refuses a busy port and never leaves a launched emulator behind
- memory diff and diagnose behave on a mocked controller
"""
import socket
import threading
from unittest.mock import MagicMock

import pytest

from ts2068_debug.controller import ControllerError, Ts2068Debug
from ts2068_debug.zrcp import ZRCPClient, ZRCPTransportError


class FakeZrcp:
    """Tiny ZRCP server. `drop_first` connections close right after the first command."""

    def __init__(self, drop_first=0, reply="OK"):
        self.drop_first = drop_first
        self.reply = reply
        self.connections = 0
        self.commands = []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            self.connections += 1
            n = self.connections
            conn.sendall(b"Welcome\ncommand> ")
            f = conn.makefile("rb")
            line = f.readline()
            if not line:
                continue
            self.commands.append(line.decode().strip())
            if n <= self.drop_first:
                conn.shutdown(socket.SHUT_RDWR)  # makefile() holds the fd; close() alone would not drop it
                f.close()
                conn.close()
                continue
            conn.sendall(self.reply.encode() + b"\ncommand> ")
            for line in f:
                self.commands.append(line.decode().strip())
                conn.sendall(self.reply.encode() + b"\ncommand> ")

    def close(self):
        self.srv.close()


def test_idempotent_read_is_retried_after_a_dropped_connection():
    srv = FakeZrcp(drop_first=1, reply="PC=0038 SP=FF00")
    try:
        client = ZRCPClient(port=srv.port, timeout=2.0)
        client.connect()
        assert "PC=0038" in client.command("get-registers", retries=2)
        assert srv.connections == 2
    finally:
        srv.close()


def test_command_without_retries_is_not_resent():
    srv = FakeZrcp(drop_first=1)
    try:
        client = ZRCPClient(port=srv.port, timeout=2.0)
        client.connect()
        with pytest.raises(ZRCPTransportError):
            client.command("run 1000")
        assert srv.commands.count("run 1000") == 1
        assert srv.connections == 1
    finally:
        srv.close()


def _dbg(replies):
    dbg = Ts2068Debug.__new__(Ts2068Debug)
    dbg._zrcp = MagicMock()
    dbg._zrcp.in_step_mode = True
    dbg._zrcp.command.side_effect = list(replies)
    dbg.symbols = Ts2068Debug.__init__.__globals__["SymbolTable"]()
    return dbg


def test_get_registers_retries_an_empty_reply_then_succeeds():
    dbg = _dbg(["", "PC=0038 SP=FF00 AF=0000"])
    assert dbg.get_registers().values["PC"] == 0x38


def test_get_registers_reports_the_bad_reply_instead_of_keyerror():
    dbg = _dbg([""] * 3)
    with pytest.raises(ControllerError, match="no usable register set"):
        dbg.get_registers()


def test_read_memory_rejects_a_short_reply_after_retries():
    dbg = _dbg(["AABB"] * 3)
    with pytest.raises(ControllerError, match="expected 4"):
        dbg.read_memory(0x8000, 4)


def test_read_memory_chunks_large_reads():
    dbg = _dbg(["00" * 4096, "00" * 4096, "00" * 8])
    assert len(dbg.read_memory(0x4000, 8200)) == 8200
    assert [c.args[0] for c in dbg._zrcp.command.call_args_list] == [
        "read-memory 16384 4096", "read-memory 20480 4096", "read-memory 24576 8"]


def test_read_memory_refuses_to_run_past_64k():
    dbg = _dbg([])
    with pytest.raises(ControllerError, match="64K"):
        dbg.read_memory(0xFFF0, 32)


def test_diff_memory_groups_changes_and_resolves_symbols(tmp_path):
    dbg = _dbg([])
    sym = tmp_path / "t.sym"
    sym.write_text("TABLE: EQU 0x8000\n")
    dbg.symbols.load(str(sym))
    snap = {"address": 0x8000, "length": 8, "hex": "0001020304050607"}
    dbg.read_memory = lambda a, n: bytes.fromhex("00FF0203FFFF0607")
    out = dbg.diff_memory(snap)
    assert out["changed_bytes"] == 3
    assert [(r["address"], r["length"], r["before"], r["after"]) for r in out["ranges"]] == [
        (0x8001, 1, "01", "ff"), (0x8004, 2, "0405", "ffff")]
    assert out["ranges"][0]["symbol"] == "TABLE+1"
    merged = dbg.diff_memory(snap, merge_gap=2)
    assert len(merged["ranges"]) == 1 and merged["ranges"][0]["length"] == 5


def test_diagnose_reports_stack_symbols_and_isolates_failures(tmp_path):
    dbg = _dbg([])
    sym = tmp_path / "t.sym"
    sym.write_text("ROUTINE: EQU 0x9000\n")
    dbg.symbols.load(str(sym))
    dbg.pause = lambda: None
    dbg.get_registers = lambda: Ts2068Debug.__init__.__globals__["parse_registers"]("PC=9003 SP=FF00 AF=0000")
    dbg.read_memory = lambda a, n: bytes([0x10, 0x90, 0x00, 0x00])
    dbg.timex_state = lambda: (_ for _ in ()).throw(ControllerError("io ports unreadable"))
    dbg.trace = lambda last: [{"index": 0}]
    out = dbg.diagnose(trace_last=1, stack_words=2)
    assert out["symbol"] == "ROUTINE+3"
    assert out["stack"][0] == {"address": 0xFF00, "value": 0x9010, "symbol": "ROUTINE+16"}
    assert out["errors"] == {"timex": "io ports unreadable"}
    assert out["trace"] == [{"index": 0}]


def test_start_refuses_a_busy_port():
    srv = FakeZrcp()
    try:
        dbg = Ts2068Debug("/nonexistent", port=srv.port)
        with pytest.raises(ControllerError, match="already in use"):
            dbg.start(launch=True)
    finally:
        srv.close()


def test_run_enables_cpu_history_before_executing():
    """history records only from when it is enabled; a trace of an already-happened hang needs it on first"""
    dbg = Ts2068Debug.__new__(Ts2068Debug)
    dbg._zrcp = MagicMock()
    dbg._zrcp.in_step_mode = True
    dbg._zrcp.timeout = 10.0
    dbg.max_run_instructions = 1000
    dbg.run(max_instructions=100)
    cmds = [c.args[0] for c in dbg._zrcp.command.call_args_list]
    assert cmds.index("cpu-history enabled yes") < cmds.index("run 100")
    assert cmds.index("cpu-history started yes") < cmds.index("run 100")

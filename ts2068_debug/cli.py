"""ts2068-debug command-line entry point.

Milestone 1 command set: start, stop, reset, run, pause, step, regs, mem, status.
Every command supports --json for machine-readable output (see README/spec).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .controller import ControllerError, Registers, Ts2068Debug
from .zrcp import ZRCPError

STATE_DIR = Path(os.environ.get("TS2068_DEBUG_STATE_DIR", str(Path.home() / ".ts2068-debug")))
STATE_FILE = STATE_DIR / "state.json"

DEFAULT_ZESARUX_DIR = os.environ.get(
    "TS2068_DEBUG_ZESARUX_DIR", str(Path.home() / "Applications" / "ZEsarUX-13.0")
)


def _load_state() -> dict:
    if not STATE_FILE.exists():
        raise SystemExit(json.dumps({"status": "error", "error": "no running session (run `ts2068-debug start` first)"}))
    return json.loads(STATE_FILE.read_text())


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def _attach(state: dict) -> Ts2068Debug:
    dbg = Ts2068Debug(
        install_dir=state["install_dir"],
        port=state["port"],
        romfile=state.get("romfile"),
        machine=state.get("machine", "TS2068"),
    )
    dbg.start(launch=False)
    for sym_path in state.get("symbols", []):
        dbg.load_symbols(sym_path)
    return dbg


def _emit(as_json: bool, obj: dict, human: str) -> None:
    if as_json:
        print(json.dumps(obj))
    else:
        print(human)


def _registers_json(regs: Registers) -> dict:
    v = regs.values
    return {
        "status": "ok",
        "pc": v.get("PC"),
        "registers": {k: val for k, val in v.items() if isinstance(val, int)},
        "flags": {k: val for k, val in v.items() if isinstance(val, str)},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ts2068-debug")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--rom", required=True, help="combined home+exrom binary for ZEsarUX --romfile")
    p_start.add_argument("--port", type=int, default=10000)
    p_start.add_argument("--zesarux-dir", default=DEFAULT_ZESARUX_DIR)
    p_start.add_argument("--machine", default="TS2068")
    p_start.add_argument("--symbols", action="append", default=[], help="assembler .sym file (repeatable)")

    sub.add_parser("stop")
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("--hard", action="store_true")
    sub.add_parser(
        "reset-known-state",
        help="pause first, then a plain (soft) reset -- the only sequence "
             "that reliably lands on a deterministic PC=0000 rather than "
             "racing the machine's free-running boot; see controller.py's "
             "reset_to_known_state() and gotchas.md",
    )

    p_run = sub.add_parser("run")
    p_run.add_argument("--max-instructions", type=int, default=None)
    p_run.add_argument("--until", default=None, help="symbol or address to run until")

    sub.add_parser("pause")
    sub.add_parser("resume")

    p_step = sub.add_parser("step")
    p_step.add_argument("--count", type=int, default=1)

    sub.add_parser("regs")

    p_mem = sub.add_parser("mem")
    p_mem.add_argument("address")
    p_mem.add_argument("length", type=int)

    p_mem_write = sub.add_parser("mem-write")
    p_mem_write.add_argument("address")
    p_mem_write.add_argument("hex_bytes", help="raw bytes as hex, e.g. 3E05 for two bytes")

    p_setreg = sub.add_parser("set-register")
    p_setreg.add_argument("register", help="e.g. PC, HL, A")
    p_setreg.add_argument("value", help="decimal or 0x-prefixed hex")

    p_disasm = sub.add_parser("disassemble")
    p_disasm.add_argument("address", nargs="?", default=None, help="symbol or address; omit for current PC")
    p_disasm.add_argument("--lines", type=int, default=1)

    p_break = sub.add_parser("break")
    p_break.add_argument("target")
    p_break.add_argument("--index", type=int, default=1)

    p_key = sub.add_parser("key")
    p_key.add_argument("text")
    p_key.add_argument("--delay-ms", type=int, default=100)

    p_press = sub.add_parser("press")
    p_press.add_argument("name", help="named key, e.g. ENTER, LEFT, BACKSPACE (see Ts2068Debug.KEY_CODES)")
    p_press.add_argument("--hold-ms", type=int, default=80)

    p_type = sub.add_parser("type")
    p_type.add_argument("text", help="one or more lines (use \\n for a newline); each line is typed then ENTER is pressed")
    p_type.add_argument("--char-delay-ms", type=int, default=60)
    p_type.add_argument("--settle-ms", type=int, default=150)

    sub.add_parser("status")

    p_screenshot = sub.add_parser("screenshot")
    p_screenshot.add_argument("path")

    sub.add_parser("screen-hash")

    p_trace = sub.add_parser("trace")
    p_trace.add_argument("--last", type=int, default=100)

    p_watch = sub.add_parser("watch")
    p_watch.add_argument("address")
    p_watch.add_argument("--type", choices=["read", "write", "readwrite"], default="readwrite")
    p_watch.add_argument("--items", type=int, default=1)

    sub.add_parser("watchpoints")
    sub.add_parser("timex")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "start":
            dbg = Ts2068Debug(args.zesarux_dir, port=args.port, romfile=args.rom, machine=args.machine)
            dbg.start(launch=True)
            state = {
                "install_dir": args.zesarux_dir,
                "port": args.port,
                "romfile": args.rom,
                "machine": args.machine,
                "symbols": args.symbols,
                "pid": dbg._proc._proc.pid,
            }
            _save_state(state)
            _emit(args.json, {"status": "ok", "port": args.port}, f"started on port {args.port}")
            return 0

        if args.cmd == "stop":
            state = _load_state()
            dbg = _attach(state)
            dbg._zrcp.quit()
            pid = state.get("pid")
            if pid is not None:
                import signal
                import time as _time

                try:
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(25):
                        _time.sleep(0.2)
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            break
                    else:
                        os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            STATE_FILE.unlink(missing_ok=True)
            _emit(args.json, {"status": "ok"}, "stopped")
            return 0

        state = _load_state()
        dbg = _attach(state)
        try:
            if args.cmd == "reset":
                dbg.reset(hard=args.hard)
                _emit(args.json, {"status": "ok"}, "reset")
            elif args.cmd == "reset-known-state":
                regs = dbg.reset_to_known_state()
                out = _registers_json(regs)
                if "PC" in regs.values:
                    out["symbol"] = dbg.resolve_address(regs.values["PC"])
                _emit(args.json, out, regs.raw)
            elif args.cmd == "run":
                if args.until:
                    regs = dbg.run_until(args.until, max_instructions=args.max_instructions)
                    _emit(args.json, _registers_json(regs), regs.raw)
                else:
                    reply = dbg.run(max_instructions=args.max_instructions)
                    _emit(args.json, {"status": "ok", "reply": reply}, reply)
            elif args.cmd == "pause":
                dbg.pause()
                _emit(args.json, {"status": "ok"}, "paused")
            elif args.cmd == "resume":
                dbg.resume()
                _emit(args.json, {"status": "ok"}, "resumed")
            elif args.cmd == "step":
                regs = dbg.step(count=args.count)
                _emit(args.json, _registers_json(regs), regs.raw)
            elif args.cmd == "regs":
                regs = dbg.get_registers()
                out = _registers_json(regs)
                if "PC" in regs.values:
                    out["symbol"] = dbg.resolve_address(regs.values["PC"])
                _emit(args.json, out, regs.raw)
            elif args.cmd == "mem":
                data = dbg.read_memory(args.address, args.length)
                hex_str = data.hex()
                _emit(
                    args.json,
                    {"status": "ok", "address": args.address, "length": args.length, "hex": hex_str},
                    hex_str,
                )
            elif args.cmd == "mem-write":
                data = bytes.fromhex(args.hex_bytes)
                dbg.write_memory(args.address, data)
                _emit(
                    args.json,
                    {"status": "ok", "address": args.address, "length": len(data)},
                    "written",
                )
            elif args.cmd == "set-register":
                value = int(args.value, 0)
                dbg.set_register(args.register, value)
                _emit(args.json, {"status": "ok", "register": args.register, "value": value}, "set")
            elif args.cmd == "disassemble":
                reply = dbg.disassemble(address=args.address, lines=args.lines)
                _emit(args.json, {"status": "ok", "reply": reply}, reply)
            elif args.cmd == "break":
                dbg.set_breakpoint(args.index, args.target)
                _emit(args.json, {"status": "ok", "index": args.index, "target": args.target}, "breakpoint set")
            elif args.cmd == "key":
                dbg.send_keys_ascii(args.text, delay_ms=args.delay_ms)
                _emit(args.json, {"status": "ok"}, "sent")
            elif args.cmd == "press":
                dbg.press_key(args.name, hold_ms=args.hold_ms)
                _emit(args.json, {"status": "ok"}, "pressed")
            elif args.cmd == "type":
                lines = args.text.split("\\n")
                dbg.type_program(lines, char_delay_ms=args.char_delay_ms, settle_ms=args.settle_ms)
                _emit(args.json, {"status": "ok", "lines": len(lines)}, "typed")
            elif args.cmd == "screenshot":
                dbg.save_screen(args.path)
                _emit(args.json, {"status": "ok", "path": args.path}, args.path)
            elif args.cmd == "screen-hash":
                digest = dbg.screen_hash()
                _emit(args.json, {"status": "ok", "hash": digest}, digest)
            elif args.cmd == "trace":
                entries = dbg.trace(last=args.last)
                _emit(
                    args.json,
                    {"status": "ok", "entries": entries},
                    "\n".join(f"[{e['index']}] {e['symbol']}: {e['raw']}" for e in entries),
                )
            elif args.cmd == "watch":
                type_map = {"read": dbg.WATCH_READ, "write": dbg.WATCH_WRITE, "readwrite": dbg.WATCH_READWRITE}
                dbg.set_watchpoint(args.address, watch_type=type_map[args.type], items=args.items)
                _emit(args.json, {"status": "ok", "address": args.address, "type": args.type}, "watchpoint set")
            elif args.cmd == "watchpoints":
                reply = dbg.get_watchpoints()
                _emit(args.json, {"status": "ok", "reply": reply}, reply)
            elif args.cmd == "timex":
                out = dbg.timex_state()
                _emit(args.json, out, json.dumps(out))
            elif args.cmd == "status":
                out = dbg.status()
                pid = state.get("pid")
                if pid is not None:
                    try:
                        os.kill(pid, 0)
                        out["process_running"] = True
                    except OSError:
                        out["process_running"] = False
                    out["pid"] = pid
                _emit(args.json, out, json.dumps(out))
        finally:
            dbg._zrcp.close()  # leave the emulator process running, just close our socket
        return 0

    except (ControllerError, ZRCPError) as exc:
        _emit(args.json, {"status": "error", "error": str(exc)}, f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

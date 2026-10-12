"""ts2068-debug command-line entry point.

Every command supports --json for machine-readable output (see README/spec).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .controller import ControllerError, Registers, Ts2068Debug
from .process import find_zesarux_processes, is_emulator_pid, terminate_emulator
from .zrcp import ZRCPClient, ZRCPError

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


def _symbol_path(state: dict, sym_path: str) -> Path:
    """Resolve a stored symbols path. Sessions recorded before paths were made absolute stored
    them as typed (relative to the directory `start` ran in); resolve those against the
    recorded `cwd` so every later command works from any directory."""
    p = Path(sym_path)
    if not p.is_absolute() and state.get("cwd"):
        p = Path(state["cwd"]) / p
    return p


def _attach(state: dict, symbols: bool = True) -> Ts2068Debug:
    dbg = Ts2068Debug(
        install_dir=state["install_dir"],
        port=state["port"],
        romfile=state.get("romfile"),
        machine=state.get("machine", "TS2068"),
    )
    dbg.start(launch=False)
    if symbols:
        for sym_path in state.get("symbols", []):
            resolved = _symbol_path(state, sym_path)
            if not resolved.is_file():
                dbg._zrcp.close()
                raise ControllerError(
                    f"symbols file not found: {resolved} (recorded at start as {sym_path!r}); "
                    f"restart the session with an existing --symbols path"
                )
            dbg.load_symbols(str(resolved))
    return dbg


def _snapshot_file(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_.")
    if not safe or safe != name:
        raise ControllerError(f"snapshot name {name!r} must be letters, digits, '-', '_' or '.'")
    return STATE_DIR / "snapshots" / f"{safe}.json"


def _stop_session(args) -> dict:
    """Stop an emulator without touching symbols or the current directory.

    Default: the session in this state directory. `--port`/`--pid`: an emulator that no
    state file tracks (e.g. left by a crashed run) -- only a verified ZEsarUX ZRCP process
    is ever signalled, never an arbitrary PID.
    """
    state = None
    if args.port is None and args.pid is None:
        state = _load_state()
        port, pid = state["port"], state.get("pid")
    else:
        procs = find_zesarux_processes()
        if args.pid is not None:
            match = next((p for p in procs if p.pid == args.pid), None)
        else:
            match = next((p for p in procs if p.port == args.port), None)
        if match is None:
            raise ControllerError("no ZEsarUX ZRCP process matches that --port/--pid")
        port, pid = match.port, match.pid
    if port is not None:
        client = ZRCPClient(port=port, timeout=3.0)
        try:
            client.connect(retries=2)
            client.quit()
        except ZRCPError:
            client.close()
    result = terminate_emulator(pid, port) if pid is not None else "not-running"
    if state is not None or (STATE_FILE.exists() and _state_port() == port):
        STATE_FILE.unlink(missing_ok=True)
    return {"status": "ok", "port": port, "pid": pid, "result": result}


def _state_pid():
    try:
        return json.loads(STATE_FILE.read_text()).get("pid")
    except (OSError, ValueError):
        return None


def _state_port():
    try:
        return json.loads(STATE_FILE.read_text()).get("port")
    except (OSError, ValueError):
        return None


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

    p_stop = sub.add_parser(
        "stop",
        help="stop this state directory's session (needs no symbols file and works from any "
             "directory); --port/--pid stop an untracked emulator",
    )
    p_stop.add_argument("--port", type=int, default=None)
    p_stop.add_argument("--pid", type=int, default=None)
    sub.add_parser("sessions", help="list running ZEsarUX ZRCP processes and whether this state directory tracks them")
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

    p_msnap = sub.add_parser("mem-snapshot", help="save a memory range under NAME, to compare later with mem-diff")
    p_msnap.add_argument("name")
    p_msnap.add_argument("address")
    p_msnap.add_argument("length", type=int)

    p_mdiff = sub.add_parser("mem-diff", help="compare live memory with a mem-snapshot; reports changed ranges with symbols")
    p_mdiff.add_argument("name")
    p_mdiff.add_argument("--merge-gap", type=int, default=0, help="merge changed ranges separated by <= N unchanged bytes")
    p_mdiff.add_argument("--update", action="store_true", help="re-baseline the snapshot to the current memory afterwards")

    p_diag = sub.add_parser(
        "diagnose",
        help="pause and capture registers, symbolised stack, interrupt/banking state and recent trace in one call",
    )
    p_diag.add_argument("--trace", type=int, default=20)
    p_diag.add_argument("--stack", type=int, default=16)
    p_diag.add_argument("--resume", action="store_true", help="resume the emulator afterwards")

    p_setreg = sub.add_parser("set-register")
    p_setreg.add_argument("register", help="e.g. PC, HL, A")
    p_setreg.add_argument("value", help="decimal or 0x-prefixed hex")

    p_disasm = sub.add_parser("disassemble")
    p_disasm.add_argument("address", nargs="?", default=None, help="symbol or address; omit for current PC")
    p_disasm.add_argument("--lines", type=int, default=1)

    p_break = sub.add_parser("break")
    p_break.add_argument("target")
    p_break.add_argument("--index", type=int, default=1)

    p_clear_break = sub.add_parser(
        "clear-break",
        help="clear a breakpoint's condition (disable it) -- required "
             "before resuming past a breakpoint you just hit and reached "
             "your target with (a hand-rolled break+run sequence), or the "
             "next run() re-triggers immediately on the same still-armed "
             "condition and executes nothing; run_until() already does "
             "this internally",
    )
    p_clear_break.add_argument("--index", type=int, default=1)

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
            # Stored absolute: later commands (and `stop`) may run from any directory.
            rom = str(Path(args.rom).resolve())
            symbols = [str(Path(sp).resolve()) for sp in args.symbols]
            for sp in symbols:
                if not Path(sp).is_file():
                    raise ControllerError(f"symbols file not found: {sp}")
            dbg = Ts2068Debug(args.zesarux_dir, port=args.port, romfile=rom, machine=args.machine)
            dbg.start(launch=True)
            state = {
                "install_dir": args.zesarux_dir,
                "port": args.port,
                "romfile": rom,
                "machine": args.machine,
                "symbols": symbols,
                "cwd": os.getcwd(),
                "pid": dbg._proc.pid,
            }
            _save_state(state)
            _emit(args.json, {"status": "ok", "port": args.port}, f"started on port {args.port}")
            return 0

        if args.cmd == "stop":
            out = _stop_session(args)
            _emit(args.json, out, f"stopped ({out['result']})")
            return 0

        if args.cmd == "sessions":
            tracked = None
            if STATE_FILE.exists():
                tracked = _state_pid()
            sessions = [dict(p.to_json(), tracked=(p.pid == tracked)) for p in find_zesarux_processes()]
            _emit(
                args.json,
                {"status": "ok", "state_dir": str(STATE_DIR), "sessions": sessions},
                "\n".join(
                    f"pid {x['pid']} port {x['port']} {'tracked' if x['tracked'] else 'not tracked by ' + str(STATE_DIR)}"
                    for x in sessions
                ) or "no ZEsarUX ZRCP processes",
            )
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
            elif args.cmd == "mem-snapshot":
                snap = dbg.snapshot_memory(args.address, args.length)
                path = _snapshot_file(args.name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(snap))
                _emit(args.json, {"status": "ok", "name": args.name, "address": snap["address"],
                                  "length": snap["length"], "path": str(path)}, f"saved {path}")
            elif args.cmd == "mem-diff":
                path = _snapshot_file(args.name)
                if not path.is_file():
                    raise ControllerError(f"no snapshot named {args.name!r} (mem-snapshot it first)")
                snap = json.loads(path.read_text())
                out = dbg.diff_memory(snap, merge_gap=args.merge_gap)
                out["status"] = "ok"
                out["name"] = args.name
                if args.update:
                    path.write_text(json.dumps(dbg.snapshot_memory(snap["address"], snap["length"])))
                _emit(
                    args.json, out,
                    f"{out['changed_bytes']} byte(s) changed in {len(out['ranges'])} range(s)\n" + "\n".join(
                        f"  ${r['address']:04X} {r['symbol']} +{r['length']}: {r['before']} -> {r['after']}"
                        for r in out["ranges"]
                    ),
                )
            elif args.cmd == "diagnose":
                out = dbg.diagnose(trace_last=args.trace, stack_words=args.stack)
                if args.resume:
                    dbg.resume()
                out["status"] = "ok"
                _emit(args.json, out, json.dumps(out, indent=2))
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
            elif args.cmd == "clear-break":
                dbg.clear_breakpoint(args.index)
                _emit(args.json, {"status": "ok", "index": args.index}, "breakpoint cleared")
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

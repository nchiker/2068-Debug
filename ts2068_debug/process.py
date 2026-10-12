"""Launches and manages a headless ZEsarUX process for ts2068-debug to attach to."""
from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_PORT_RE = re.compile(r"--remoteprotocol-port\s+(\d+)")


@dataclass(frozen=True)
class EmulatorProcess:
    pid: int
    ppid: int
    port: int | None
    args: str

    def to_json(self) -> dict:
        # No "orphaned" flag here: the CLI's `start` exits after launching, so every
        # CLI-started emulator has ppid 1. Whether a process is orphaned depends on the
        # state directory(ies) that track it, which only the caller knows.
        return {"pid": self.pid, "ppid": self.ppid, "port": self.port, "args": self.args}


def find_zesarux_processes() -> list[EmulatorProcess]:
    """ZEsarUX processes started with the ZRCP server enabled (what this tool launches).

    Read-only: used to explain a busy port and to list emulators no session tracks (V2 parallel
    runs left three behind). Nothing is ever killed from this list implicitly.
    """
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid, ppid, args = int(parts[0]), int(parts[1]), parts[2]
        if "zesarux" not in args.split(None, 1)[0] or "--enable-remoteprotocol" not in args:
            continue
        m = _PORT_RE.search(args)
        found.append(EmulatorProcess(pid, ppid, int(m.group(1)) if m else None, args))
    return found


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def is_emulator_pid(pid: int, port: int | None = None) -> bool:
    """True only if `pid` is currently a ZEsarUX ZRCP process (and on `port`, if given).

    Guards stop/kill against a recycled PID after the emulator died on its own.
    """
    for proc in find_zesarux_processes():
        if proc.pid == pid and (port is None or proc.port in (None, port)):
            return True
    return False


def terminate_emulator(pid: int, port: int | None = None, grace_s: float = 5.0) -> str:
    """SIGTERM then SIGKILL one verified ZEsarUX process. Returns what happened."""
    if not is_emulator_pid(pid, port):
        return "not-running"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return "not-running"
    deadline = time.time() + grace_s
    while time.time() < deadline:
        time.sleep(0.2)
        if not is_emulator_pid(pid, port):
            return "terminated"
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return "killed"


class ZEsarUXProcess:
    def __init__(
        self,
        install_dir: str,
        port: int = 10000,
        romfile: str | None = None,
        machine: str = "TS2068",
        binary: str = "zesarux",
    ):
        self.install_dir = Path(install_dir)
        self.port = port
        self.romfile = romfile
        self.machine = machine
        self.binary = binary
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        if not self.install_dir.is_dir():
            raise FileNotFoundError(f"ZEsarUX install dir not found: {self.install_dir}")
        args = [
            f"./{self.binary}",
            "--noconfigfile",
            "--machine", self.machine,
            "--enable-remoteprotocol",
            "--remoteprotocol-port", str(self.port),
            "--vo", "null",
            "--ao", "null",
        ]
        if self.romfile:
            args += ["--romfile", str(Path(self.romfile).resolve())]
        # ZEsarUX must be launched from its own install directory to find its
        # bundled stock-ROM lookup files (confirmed in docs/zesarux-analysis.md).
        self._proc = subprocess.Popen(
            args,
            cwd=self.install_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def exit_code(self) -> int | None:
        return self._proc.poll() if self._proc is not None else None

    def stop(self, timeout: float = 5.0) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=timeout)
        self._proc = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

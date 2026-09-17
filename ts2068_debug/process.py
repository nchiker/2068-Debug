"""Launches and manages a headless ZEsarUX process for ts2068-debug to attach to."""
from __future__ import annotations

import subprocess
from pathlib import Path


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

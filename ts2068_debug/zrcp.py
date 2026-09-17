"""Thin client for ZEsarUX's ZRCP (Remote Command Protocol) TCP socket.

ZRCP is a line-oriented plain-text protocol: one command per line in, a plain-text
reply (possibly multi-line) followed by a fresh prompt out. See docs/zesarux-analysis.md
for how this was determined from the ZEsarUX source (src/zrcp/remote.c).
"""
from __future__ import annotations

import socket
import time


class ZRCPError(Exception):
    """Raised when ZRCP itself reports an error, or the socket misbehaves."""


class ZRCPClient:
    # Default prompt is "command" (src/settings.c: remote_prompt_command_string).
    # In cpu-step mode it becomes "command@cpu-step> " instead of "command> ".
    PROMPT_SUFFIXES = ("command> ", "command@cpu-step> ")

    def __init__(self, host: str = "127.0.0.1", port: int = 10000, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    def connect(self, retries: int = 30, retry_delay: float = 0.2) -> None:
        last_err = None
        for _ in range(retries):
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                sock.settimeout(self.timeout)
                self._sock = sock
                self._buf = b""
                # Consume the startup banner up to the first prompt.
                self._read_until_prompt()
                return
            except OSError as exc:
                last_err = exc
                time.sleep(retry_delay)
        raise ZRCPError(f"could not connect to ZRCP at {self.host}:{self.port}: {last_err}")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self) -> "ZRCPClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _read_until_prompt(self) -> str:
        assert self._sock is not None
        while True:
            for suffix in self.PROMPT_SUFFIXES:
                idx = self._buf.rfind(suffix.encode("ascii"))
                if idx != -1 and self._buf.endswith(suffix.encode("ascii")):
                    text = self._buf[:idx].decode("utf-8", errors="replace")
                    self._buf = b""
                    return text.strip("\n")
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as exc:
                raise ZRCPError(f"timed out waiting for ZRCP reply (partial: {self._buf!r})") from exc
            if not chunk:
                raise ZRCPError("ZRCP connection closed unexpectedly")
            self._buf += chunk

    def command(self, line: str) -> str:
        """Send one command line, return its reply text with the trailing prompt stripped."""
        if self._sock is None:
            raise ZRCPError("not connected")
        self._sock.sendall((line + "\n").encode("utf-8"))
        reply = self._read_until_prompt()
        if reply.startswith("Error") or reply.startswith("ERROR"):
            raise ZRCPError(f"{line!r} -> {reply}")
        return reply

    def quit(self) -> None:
        """Send `quit`. No prompt follows, so this bypasses command()'s reply reader."""
        if self._sock is None:
            return
        try:
            self._sock.sendall(b"quit\n")
        except OSError:
            pass
        self.close()

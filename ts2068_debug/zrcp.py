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
        # Ground truth for whether the emulator is paused (cpu-step mode), read
        # straight off which prompt suffix ZEsarUX actually sent -- not a locally
        # tracked assumption. Set on connect() (from the startup banner) and kept
        # current by every reply after that, so it's correct even for a caller that
        # attaches fresh each invocation (e.g. one `ts2068-debug` CLI call per
        # process) with no memory of prior calls in this session.
        self.in_step_mode = False

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
                    self.in_step_mode = suffix == "command@cpu-step> "
                    return text.strip("\n")
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as exc:
                partial = self._buf
                # The command that timed out is still in flight server-side (ZEsarUX has
                # no cancel); leaving this socket/buffer in place would corrupt every
                # later command's prompt detection with these stale bytes. Reconnect so
                # the client stays usable for follow-up debugging (pause/regs/status)
                # after a real hang, instead of wedging the whole session.
                self.close()
                try:
                    self.connect()
                except ZRCPError:
                    pass
                raise ZRCPError(f"timed out waiting for ZRCP reply (partial: {partial!r})") from exc
            if not chunk:
                raise ZRCPError("ZRCP connection closed unexpectedly")
            self._buf += chunk

    def command(self, line: str, timeout: float | None = None) -> str:
        """Send one command line, return its reply text with the trailing prompt stripped.

        `timeout`, when given, overrides the socket timeout for this call only (e.g. a
        bounded `run` for millions of opcodes can legitimately take far longer than the
        default 10s). The socket's original timeout is always restored afterwards, even
        on error, so a one-off long call doesn't leak a relaxed timeout onto later short
        commands (which would turn a real hang into a long hang before it's reported).
        """
        if self._sock is None:
            raise ZRCPError("not connected")
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            self._sock.sendall((line + "\n").encode("utf-8"))
            reply = self._read_until_prompt()
        finally:
            if timeout is not None:
                self._sock.settimeout(self.timeout)
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

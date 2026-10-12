"""Shared setup for the live tests (real headless ZEsarUX + a built TS2068 ROM).

ROM lookup order: $TS2068_DEBUG_TEST_ROM, then the 2068-Leap V2 combined image, then the
older ts2068rom project. Tests using these skip when ZEsarUX or a ROM is missing.
"""
import os
import socket
from pathlib import Path

ZESARUX_DIR = Path(os.environ.get("TS2068_DEBUG_ZESARUX_DIR", str(Path.home() / "Applications" / "ZEsarUX-13.0")))


def _find_rom() -> Path:
    candidates = [
        os.environ.get("TS2068_DEBUG_TEST_ROM"),
        str(Path.home() / "2068-Leap" / "build" / "ts2068rom_zesarux.bin"),
        str(Path.home() / "ts2068rom" / "build" / "ts2068rom_zesarux.bin"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return Path(candidates[1])


ROM_PATH = _find_rom()
LIVE_AVAILABLE = (ZESARUX_DIR / "zesarux").exists() and ROM_PATH.exists()
SKIP_REASON = "requires a local ZEsarUX install and a built TS2068 ROM"


def free_port() -> int:
    """A port nothing is listening on, so live tests never collide with parallel suites."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

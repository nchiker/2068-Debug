"""Regression coverage for save_screen()'s BMP-only requirement and success check.

Found while independently re-verifying a fix in 2068-Leap: `ts2068-debug screenshot
foo.png` reported {"status": "ok"} against a real ZEsarUX session, but no file was
ever created anywhere on disk. ZEsarUX's `save-screen` silently no-ops for a non-.bmp
path -- empty, non-error reply, no file -- so the client had no way to tell the two
apart without checking itself.
"""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ts2068_debug.controller import ControllerError, Ts2068Debug


def _make_debug_with_mock_zrcp():
    dbg = Ts2068Debug.__new__(Ts2068Debug)
    dbg._zrcp = MagicMock()
    return dbg


def test_save_screen_rejects_non_bmp_extension():
    dbg = _make_debug_with_mock_zrcp()
    with pytest.raises(ControllerError, match=r"\.bmp"):
        dbg.save_screen("/tmp/whatever/screenshot.png")
    dbg._zrcp.command.assert_not_called()


def test_save_screen_raises_if_file_not_actually_created():
    dbg = _make_debug_with_mock_zrcp()
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "screen.bmp")
        # Mocked ZRCP "succeeds" (returns normally) but never touches the filesystem --
        # exactly what a real silent no-op looks like from the caller's side.
        with pytest.raises(ControllerError, match="was not created"):
            dbg.save_screen(path)


def test_save_screen_succeeds_when_file_is_actually_written():
    dbg = _make_debug_with_mock_zrcp()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "screen.bmp"

        def fake_command(line):
            path.write_bytes(b"BM\x00\x00")  # minimal stand-in for a real BMP
            return ""

        dbg._zrcp.command.side_effect = fake_command
        dbg.save_screen(str(path))  # must not raise
        assert path.is_file()

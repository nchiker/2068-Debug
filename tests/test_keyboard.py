"""Regression coverage for the send-keys-ascii 20-argument ZRCP cap.

Found live: ZRCP's REMOTE_MAX_PARAMETERS_COMMAND=20 (~/zesarux-source/src/zrcp/remote.c)
silently truncates a send-keys-ascii command with more than 19 key codes -- anything
past the 19th character was never typed. Ts2068Debug.send_keys_ascii must chunk.
"""
from unittest.mock import MagicMock

from ts2068_debug.controller import Ts2068Debug


def _make_debug_with_mock_zrcp():
    dbg = Ts2068Debug.__new__(Ts2068Debug)
    dbg._zrcp = MagicMock()
    return dbg


def test_send_keys_ascii_chunks_under_20_args():
    dbg = _make_debug_with_mock_zrcp()
    text = "A" * 45  # comfortably over the 19-char-per-command chunk size
    dbg.send_keys_ascii(text, delay_ms=50)

    calls = [c.args[0] for c in dbg._zrcp.command.call_args_list]
    assert len(calls) == 3  # 45 / 19 -> 3 chunks (19, 19, 7)
    for call in calls:
        # "send-keys-ascii <delay> <code1> <code2> ..." must never exceed
        # REMOTE_MAX_PARAMETERS_COMMAND=20 total arguments (1 delay + <=19 codes).
        parts = call.split()
        assert parts[0] == "send-keys-ascii"
        arg_count = len(parts) - 1  # excluding the command name itself
        assert arg_count <= 20


def test_send_keys_ascii_short_text_single_call():
    dbg = _make_debug_with_mock_zrcp()
    dbg.send_keys_ascii("A", delay_ms=100)
    dbg._zrcp.command.assert_called_once_with("send-keys-ascii 100 65")


def test_type_line_rejects_embedded_newline():
    dbg = _make_debug_with_mock_zrcp()
    dbg.press_key = MagicMock()
    try:
        dbg.type_line("BORDER 4\nRUN")
        assert False, "expected ControllerError"
    except Exception as exc:
        assert "newline" in str(exc)

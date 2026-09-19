"""Regression coverage for the send-keys-ascii 20-argument ZRCP cap.

Found live: ZRCP's REMOTE_MAX_PARAMETERS_COMMAND=20 (~/zesarux-source/src/zrcp/remote.c)
silently truncates a send-keys-ascii command with more than 19 key codes -- anything
past the 19th character was never typed. Ts2068Debug.send_keys_ascii must chunk.
"""
import time
from unittest.mock import MagicMock

from ts2068_debug.controller import Ts2068Debug


def _make_debug_with_mock_zrcp():
    dbg = Ts2068Debug.__new__(Ts2068Debug)
    dbg._zrcp = MagicMock()
    # _in_step_mode reads straight off _zrcp.in_step_mode (ground truth from the
    # real ZRCP prompt suffix); a bare MagicMock attribute is truthy by default,
    # which would make every keyboard call below look "paused" to _require_running.
    dbg._zrcp.in_step_mode = False
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


def test_keyboard_calls_refuse_while_paused():
    # run()/run_until() leave the emulator in cpu-step mode (paused) when they
    # return -- send-keys-* commands sent while paused update ZEsarUX's keyboard
    # matrix but the ROM never scans it (the Z80 clock isn't ticking), so the
    # keystroke silently goes nowhere. Confirmed live, independently, twice in the
    # same debugging session before being traced to this. Must fail loud instead.
    dbg = _make_debug_with_mock_zrcp()
    dbg._zrcp.in_step_mode = True

    for call in (
        lambda: dbg.send_keys_ascii("A"),
        lambda: dbg.press_key("ENTER"),
        lambda: dbg.type_line("PRINT 1"),
    ):
        try:
            call()
            assert False, "expected ControllerError while paused"
        except Exception as exc:
            assert "paused" in str(exc)
    dbg._zrcp.command.assert_not_called()


def test_resume_settles_before_returning():
    """Regression test for the "typing immediately after resume() drops
    the first characters" gotcha (gotchas.md), found live 2026-09-19:
    with zero gap, `BORDER 4` typed right after `resume()` landed on
    screen as `RDER 4` (confirmed via screenshot diff against a real
    2068-Leap ROM) -- the ROM's keyboard-scan ISR needs a brief real
    window to resume normal polling. resume() must not return
    immediately after exit-cpu-step; it must hold for
    _RESUME_SETTLE_S first, so every caller (not just ones that happen
    to add their own delay) gets a session where typing works right
    away."""
    dbg = _make_debug_with_mock_zrcp()
    dbg._zrcp.in_step_mode = True

    t0 = time.monotonic()
    dbg.resume()
    elapsed = time.monotonic() - t0

    dbg._zrcp.command.assert_called_once_with("exit-cpu-step")
    assert elapsed >= dbg._RESUME_SETTLE_S, (
        f"resume() returned after only {elapsed:.3f}s, expected to hold "
        f"for at least _RESUME_SETTLE_S={dbg._RESUME_SETTLE_S}s"
    )


def test_resume_is_a_noop_when_already_running():
    """resume() while already free-running (not in step mode) must not
    send exit-cpu-step again, and -- since there was nothing to settle
    after -- must not pay the settle delay either."""
    dbg = _make_debug_with_mock_zrcp()
    dbg._zrcp.in_step_mode = False

    t0 = time.monotonic()
    dbg.resume()
    elapsed = time.monotonic() - t0

    dbg._zrcp.command.assert_not_called()
    assert elapsed < 0.05

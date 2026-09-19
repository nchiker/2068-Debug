"""Regression coverage for wait_until(), the formalized polling primitive.

Test bodies and debugging sessions this project has been through repeatedly
hand-rolled "run a bit, check real state, repeat" -- wait_until() names that
pattern once instead of leaving every caller to reimplement the bounded-loop
part correctly (or not) each time.
"""
from unittest.mock import MagicMock

import pytest

from ts2068_debug.controller import ControllerError, Ts2068Debug


def _make_debug_with_mock_zrcp():
    dbg = Ts2068Debug.__new__(Ts2068Debug)
    dbg._zrcp = MagicMock()
    dbg._zrcp.in_step_mode = False
    dbg._zrcp.timeout = 10.0
    dbg.max_run_instructions = 2_000_000
    return dbg


def _run_commands(mock_command):
    return [c.args[0] for c in mock_command.call_args_list if c.args[0].startswith("run ")]


def test_wait_until_returns_immediately_if_already_true():
    dbg = _make_debug_with_mock_zrcp()
    dbg.wait_until(lambda d: True, max_polls=5, instructions_per_poll=100)
    assert _run_commands(dbg._zrcp.command) == []  # never even ran


def test_wait_until_polls_until_predicate_true():
    dbg = _make_debug_with_mock_zrcp()
    state = {"n": 0}

    def predicate(d):
        state["n"] += 1
        return state["n"] >= 3  # true on the 3rd check

    total = dbg.wait_until(predicate, max_polls=10, instructions_per_poll=1000)
    assert total == 2000  # 2 bounded runs happened before the 3rd (true) check
    assert len(_run_commands(dbg._zrcp.command)) == 2


def test_wait_until_raises_when_budget_exhausted():
    dbg = _make_debug_with_mock_zrcp()
    with pytest.raises(ControllerError, match="not satisfied"):
        dbg.wait_until(lambda d: False, max_polls=3, instructions_per_poll=500)
    assert len(_run_commands(dbg._zrcp.command)) == 3

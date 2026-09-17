"""Milestone 3: a simple regression-testing framework built on Ts2068Debug.

A test is a Python callable that receives a live `Ts2068Debug` session and makes
assertions using its normal methods (or the `expect` helper below for a friendlier
failure report). On failure, the runner automatically captures registers, PC/symbol,
recent CPU history, and TS2068 banking state — everything Claude needs to diagnose the
failure without re-running anything, per this project's failure-behavior requirements.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

from .controller import Ts2068Debug


class ExpectationError(AssertionError):
    pass


def expect(dbg: Ts2068Debug, description: str, actual, expected) -> None:
    if actual != expected:
        raise ExpectationError(f"{description}: expected {expected!r}, got {actual!r}")


@dataclass
class TestResult:
    name: str
    passed: bool
    error: Optional[str] = None
    diagnostics: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "error": self.error,
            "diagnostics": self.diagnostics,
        }


@dataclass
class RegressionTest:
    name: str
    body: Callable[[Ts2068Debug], None]
    reset_hard: bool = False


def _collect_failure_diagnostics(dbg: Ts2068Debug) -> dict:
    diagnostics: dict = {}
    try:
        regs = dbg.get_registers()
        diagnostics["registers"] = regs.to_json()
        pc = regs.values.get("PC")
        if pc is not None:
            diagnostics["pc"] = pc
            diagnostics["symbol"] = dbg.resolve_address(pc)
    except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the real failure
        diagnostics["registers_error"] = str(exc)

    try:
        diagnostics["trace"] = dbg.trace(last=20)
    except Exception as exc:  # noqa: BLE001
        diagnostics["trace_error"] = str(exc)

    try:
        diagnostics["timex"] = dbg.timex_state()
    except Exception as exc:  # noqa: BLE001
        diagnostics["timex_error"] = str(exc)

    return diagnostics


def run_tests(dbg: Ts2068Debug, tests: list[RegressionTest]) -> list[TestResult]:
    results = []
    for test in tests:
        try:
            dbg.reset(hard=test.reset_hard)
            dbg.resume()
            test.body(dbg)
            results.append(TestResult(name=test.name, passed=True))
        except Exception as exc:  # noqa: BLE001 - a failing ROM test must not kill the runner
            diagnostics = _collect_failure_diagnostics(dbg)
            diagnostics["traceback"] = traceback.format_exc()
            results.append(
                TestResult(name=test.name, passed=False, error=str(exc), diagnostics=diagnostics)
            )
    return results


def summarize(results: list[TestResult]) -> dict:
    passed = sum(1 for r in results if r.passed)
    return {
        "status": "ok" if passed == len(results) else "fail",
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": [r.to_json() for r in results],
    }

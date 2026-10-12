# Testing

## Controller's own test suite

```
python3 -m pytest tests/
```

- `test_symbols.py`, `test_controller_parsing.py` — pure unit tests, no emulator needed.
- `test_reliability.py`, `test_cli_sessions.py` — unit tests with a fake ZRCP server: retry of
  dropped reads, no resend of side-effecting commands, memory diff, diagnose, `stop` without symbols.
- `test_integration_live.py`, `test_integration_testrunner.py`, `test_live_v2_scenarios.py` — real
  end-to-end tests against a live headless ZEsarUX TS2068 instance (each on a free port). The
  scenarios cover stop-from-another-directory, busy ports, failed starts leaving no emulator,
  untracked-emulator cleanup, and a hung program (memory diff + diagnose). Auto-skip if ZEsarUX
  or a built ROM isn't present. Defaults: `~/Applications/ZEsarUX-13.0` and, for the ROM,
  `$TS2068_DEBUG_TEST_ROM`, else `~/2068-Leap/build/ts2068rom_zesarux.bin` (`make build` there),
  else `~/ts2068rom/build/ts2068rom_zesarux.bin`; override with `TS2068_DEBUG_ZESARUX_DIR` /
  `TS2068_DEBUG_TEST_ROM`.

These are the tests that actually prove the ZRCP control loop works — not mocks. Every
capability claim in `docs/zesarux-analysis.md` was checked this way before being relied
on (see that doc's §5b for the raw captured replies).

## Regression-testing ROM behavior (Milestone 3)

`ts2068_debug.testrunner` provides a small framework on top of `Ts2068Debug`:

```python
from ts2068_debug.controller import Ts2068Debug
from ts2068_debug.testrunner import RegressionTest, expect, run_tests, summarize

def cursor_left_test(dbg):
    dbg.pause()
    dbg.run_until("editor_ready")
    dbg.press_key("LEFT")  # named-key press/release via send-keys-event; use
    # send_keys_ascii()/type_line() only for ASCII text, not discrete key presses
    cursor_x = int.from_bytes(dbg.read_memory("cursor_x", 1), "little")
    expect(dbg, "cursor_x after LEFT", cursor_x, 0)

dbg = Ts2068Debug(zesarux_dir, romfile=rom_path)
dbg.start()
try:
    dbg.load_symbols("rom/current_test_basic.sym")
    results = run_tests(dbg, [RegressionTest(name="cursor_left", body=cursor_left_test)])
    print(summarize(results))
finally:
    dbg.stop()
```

Each `RegressionTest.body` gets a live, already-reset `Ts2068Debug` session and just
calls its normal methods, using `expect(...)` for assertions with a readable message.
`run_tests` never lets one test's exception kill the run — every test result is
collected, `summarize()` gives a `PASS`/`FAIL` roll-up plus per-test diagnostics.

### What a failure captures automatically

On any exception (assertion or otherwise) inside a test body, `run_tests` immediately
snapshots, without any extra code in the test itself:
- full register set (`get_registers`)
- PC and its resolved nearest symbol (`resolve_address`)
- the last 20 entries of CPU history (`trace`) — a real reverse-debugging window, not a
  reconstruction
- TS2068 banking/video/AY state (`timex_state`)
- the Python traceback of the assertion itself

This is deliberately everything Claude needs to diagnose the failure — matching this
project's stated failure-behavior requirement: a failed test is a starting point for
autonomous debugging, not a stopping point.

## Workflow this loop is meant to support

```
modify source -> build ROM -> ts2068-debug start --rom <combined.bin> -> run tests
   -> PASS, or
   -> FAIL -> diagnostics already captured -> inspect trace/symbols/timex state
      -> narrow to the responsible routine -> fix source -> rebuild -> re-run
```

`ts2068-debug start`/`stop` bracket one long-lived ZEsarUX process; `run_tests` resets
before every individual test so tests don't leak state into each other, without paying
the cost of relaunching the emulator per test.

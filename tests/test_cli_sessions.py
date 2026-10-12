"""CLI state handling that must not need an emulator: stop without symbols, snapshot names."""
import importlib
import json

import pytest


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("TS2068_DEBUG_STATE_DIR", str(tmp_path))
    from ts2068_debug import cli as cli_module
    return importlib.reload(cli_module)


def test_stop_does_not_load_symbols_or_need_the_original_directory(cli, tmp_path, capsys, monkeypatch):
    # State as an old session left it: a RELATIVE symbols path that no longer resolves from here.
    cli.STATE_FILE.write_text(json.dumps({
        "install_dir": "/nonexistent", "port": 1, "symbols": ["build/exrom.sym"],
        "cwd": "/somewhere/else", "pid": None}))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--json", "stop"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "not-running"
    assert not cli.STATE_FILE.exists()


def test_stop_with_unknown_port_is_an_error_not_a_kill(cli, capsys):
    assert cli.main(["--json", "stop", "--port", "9"]) == 1
    assert "no ZEsarUX ZRCP process" in json.loads(capsys.readouterr().out)["error"]


def test_relative_symbols_resolve_against_the_recorded_cwd(cli, tmp_path):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "a.sym").write_text("X: EQU 0x1234\n")
    state = {"symbols": ["build/a.sym"], "cwd": str(tmp_path)}
    assert cli._symbol_path(state, "build/a.sym") == tmp_path / "build" / "a.sym"


def test_snapshot_names_cannot_escape_the_state_directory(cli):
    with pytest.raises(cli.ControllerError):
        cli._snapshot_file("../evil")
    assert cli._snapshot_file("before-save").parent.name == "snapshots"


def test_sessions_lists_without_a_state_file(cli, capsys):
    assert cli.main(["--json", "sessions"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and isinstance(out["sessions"], list)

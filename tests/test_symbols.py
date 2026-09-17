from ts2068_debug.symbols import SymbolTable


def test_load_and_resolve(tmp_path):
    sym_file = tmp_path / "test.sym"
    sym_file.write_text(
        "EDITOR_CURSOR_LEFT: EQU 0x000091B6\n"
        "EDITOR_CURSOR_RIGHT: EQU 0x000091F0\n"
        "ARGC_STR1: EQU 0x00000064\n"  # not a plausible 16-bit code address boundary case
    )
    table = SymbolTable()
    count = table.load(str(sym_file))
    assert count == 3

    assert table.address_of("EDITOR_CURSOR_LEFT") == 0x91B6
    assert table.address_of("MISSING") is None

    resolved = table.resolve(0x91B6 + 17)
    assert resolved is not None
    assert str(resolved) == "EDITOR_CURSOR_LEFT+17"

    exact = table.resolve(0x91B6)
    assert str(exact) == "EDITOR_CURSOR_LEFT"

    below_everything = table.resolve(0)
    assert below_everything is None

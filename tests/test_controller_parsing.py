from ts2068_debug.controller import parse_registers

# Captured live from a real ZEsarUX 13.0 TS2068 instance (docs/zesarux-analysis.md §5b).
SAMPLE_REPLY = (
    "PC=3f4e SP=fef4 AF=0044 BC=171f HL=5aff DE=001f IX=ffff IY=ffff "
    "AF'=ffff BC'=ffff HL'=1d71 DE'=c078 I=00 R=6d  F=-Z---P-- F'=SZ5H3PNC "
    "MEMPTR=3f4e IM1 IFF-- VPS: 0 MMU=00000000000000000000000000000000 TSTATES: 37\n"
    "  3F4E PUSH AF\n"
)


def test_parse_registers_hex_fields():
    regs = parse_registers(SAMPLE_REPLY)
    assert regs.values["PC"] == 0x3F4E
    assert regs.values["SP"] == 0xFEF4
    assert regs.values["HL"] == 0x5AFF
    assert regs.values["AF'"] == 0xFFFF


def test_parse_registers_flag_fields_kept_as_strings():
    regs = parse_registers(SAMPLE_REPLY)
    assert regs.values["F"] == "-Z---P--"
    assert regs.values["F'"] == "SZ5H3PNC"

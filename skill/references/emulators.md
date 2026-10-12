# Running a custom TS2068 ROM in ZEsarUX, EightyOne and Fuse

For any TS2068 ROM project (2068-Leap, 2068-Forth, 2068-Pascal, structured-basic-poc, ...).
Substitute your own images:

| Placeholder | Meaning |
|---|---|
| `HOME.bin` | 16,384-byte Home ROM |
| `EXROM.bin` | 8,192-byte EXROM (the Timex extension ROM) |
| `COMBINED.bin` | `HOME.bin` immediately followed by `EXROM.bin` (24,576 bytes): `cat HOME.bin EXROM.bin > COMBINED.bin` |

**ZEsarUX is the qualified emulator.** A ROM release is accepted on ZEsarUX results only: the
automated suites, `ts2068-debug`, and the release gates all run there. EightyOne and Fuse are courtesy
alternatives for people who prefer them; a result there is informative, never a qualification result,
and nothing in a release process should depend on them.

## ZEsarUX — qualified

| | |
|---|---|
| Local install | `~/Applications/ZEsarUX-13.0/zesarux` (ZEsarUX 13.0) |
| Needs | the TS2068 **EXROM mirroring patch**: real hardware repeats the 8K EXROM chip across all eight 8K chunks, stock ZEsarUX 13.0 mirrors only chunk 0 into chunk 1, so any ROM that runs EXROM code from another chunk (2068-Leap uses chunk 6) shows a blank screen on a stock build. The local install is the patched build (source `~/zesarux-source`, commit "Mirror TS2068 EXROM across all memory chunks"; the patch ships as `2068-Leap/patches/0001-zesarux-mirror-ts2068-exrom.patch`). A ROM that does not touch EXROM past chunk 1 runs on stock |
| Image | `COMBINED.bin` through `--romfile` (never `HOME.bin` alone) |

Interactive:

```sh
cd ~/Applications/ZEsarUX-13.0          # ZEsarUX must start from its own install directory
./zesarux --noconfigfile --machine TS2068 --romfile /absolute/path/COMBINED.bin
```

Add `--enableulaplus` for ULAplus palette demos. Use an absolute `--romfile` path.

Automated/headless (what `ts2068-debug start` launches, plus the ZRCP remote-control port):

```sh
./zesarux --noconfigfile --machine TS2068 --enable-remoteprotocol --remoteprotocol-port 10000 \
  --vo null --ao null --romfile /absolute/path/COMBINED.bin
```

Drive it with `ts2068-debug` (see `SKILL.md`). One emulator per port; give every parallel suite its own
`TS2068_DEBUG_STATE_DIR` and port.

## EightyOne — courtesy alternative

| | |
|---|---|
| Local install | `~/EightyOne/EightyOne.exe`, version 1.41, run with `wine ~/EightyOne/EightyOne.exe` (wine 9.0) |
| Images | `HOME.bin` as the ROM file, plus the EXROM wrapped as a **DCK** Timex cartridge. EightyOne 1.41 rejects a raw `EXROM.bin` in the cartridge field |

Build the cartridge: the 9-byte header `FE 00 00 00 00 00 00 02 00` followed by the 8,192 EXROM bytes
(8,201 bytes in all). 2068-Leap does this in `tools/make_eightyone_dck.sh EXROM.bin EXROM.dck`; the
chunk byte in the header (`02` in chunk position 6) is the chunk 2068-Leap's EXROM code runs from. Other
projects should confirm it matches where their EXROM is paged before relying on the result.

```sh
printf '\376\000\000\000\000\000\000\002\000' > EXROM.dck && cat EXROM.bin >> EXROM.dck
```

Configure (hardware dialog), then hard reset:

1. **Timex** tab, machine **TS2068**.
2. **Advanced Settings** → **ROM File** = `HOME.bin`; leave **Protect ROM from Writes** on.
3. **Interfaces** → **ROM Cartridge** = **Timex**, file = `EXROM.dck`.
4. Apply, then hard reset.

Prefer randomised power-on RAM: it exposes reads of uninitialised memory that a zero-filled start hides.
Delete `EightyOne.ini` (Options → Configuration → Reset To Default Settings) if a version upgrade
misbehaves.

## Fuse — courtesy alternative

| | |
|---|---|
| Local install | `/usr/local/bin/fuse` (Fuse 1.9.1, a local build; `~/fuse-build`), and the distribution's `/usr/bin/fuse-sdl` |
| Images | the two ROMs separately: `HOME.bin` as ROM 0, `EXROM.bin` as ROM 1 |

```sh
fuse --machine ts2068 --rom-ts2068-0 /absolute/path/HOME.bin --rom-ts2068-1 /absolute/path/EXROM.bin
```

(Settings `rom_ts2068_0` / `rom_ts2068_1`; set them in Options → Select ROMs → Timex TS2068 to make
them stick.) Upstream Fuse 1.9.1 does not expose ULAplus on Timex machines, so ULAplus programs need
ZEsarUX or a patched Fuse (`2068-Leap/patches/0001-Add-ULAplus-support-for-Timex-machines.patch`).
Fuse does not start a custom loader's tape playback by itself: open the `.tzx`/`.tap` in the tape
browser, type the load command, then press Play.

Do not script Fuse into a test or release gate: it needs an X display and its window-driven checks
were retired from the headless suites in favour of `ts2068-debug`.

## What was verified, and what was not

The ZEsarUX commands, the headless flags and the EXROM-mirroring requirement were exercised on this
machine (ZEsarUX 13.0 patched, `ts2068-debug` against the 2068-Leap V2 ROM). The EightyOne steps are
2068-Leap's `docs/eightyone_setup.md` (confirmed by an EightyOne user on Windows 1.41); the install
and wine version were checked here, the GUI steps were not repeated. The Fuse option names were
checked against the local Fuse 1.9.1 source and `--version`; Fuse and EightyOne windows were not
opened for this document. Hardware behaviour is a separate, untested question for all three.

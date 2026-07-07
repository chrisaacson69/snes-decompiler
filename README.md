# snes-decompiler

Generic tooling and method for **taking apart an SNES cartridge** — read the
internal header, detect the memory mapping, and map file offsets to 65C816
addresses so a ROM can be walked. No specific target yet; this is the reusable
substrate every future SNES title decompiler depends on (the SNES analog of the
NES `nes-render` / `koei-nes` shared layers).

Grounded in the **[SNESdev Wiki](https://snes.nesdev.org/wiki/SNESdev_Wiki)** —
every offset and rule traces to a wiki page, nothing is written from memory.

## Tools (`tools/`, stdlib-only, run with `py -3`)

Two layers, mirroring the NES side (generic CPU decoder + cart wiring):

| Tool | Layer | What it does |
|------|-------|--------------|
| `w65c816.py` | **CPU (65C816)** | Pure instruction decoder: the 256-opcode WDC matrix + `decode()` with **M/X flag width tracking** (immediate length depends on runtime REP/SEP state). **Zero SNES imports** — the 65C816 analog of the NES `disasm6502`; graduates to its own repo the moment a second 65C816 system needs it. `py -3 tools/w65c816.py` runs the selftest. |
| `disasm65816.py` | **SNES** | Thin driver: loads a ROM, detects the mapping, translates flat file offsets ↔ `(bank,addr)` via `mapper`, and streams `w65c816.decode()`. |
| `header_parse.py` | SNES | Auto-detects LoROM/HiROM/ExHiROM (checksum-complement identity + map-mode byte + title), dumps the header incl. **RESET vector = entry point**. |
| `mapper.py` | SNES | `cpu_to_file` / `file_to_cpu` for LoROM & HiROM + copier-header (.smc) stripping. |

```
py -3 tools/header_parse.py  game.sfc
py -3 tools/disasm65816.py   game.sfc                 # disassemble from RESET
py -3 tools/disasm65816.py   game.sfc --start 01:CD51 -n 30 --mx 8,8
```

The opcode table is cross-checked against 6502.org's 65c816opcodes reference, and
the decoder is verified end-to-end by disassembling a real cart's reset vector into
the canonical SNES boot sequence (CLC/XCE/SEI/REP/SEP + PPU/DMA register init).

## The teardown method

The four-step "look under the hood" checklist — header → mapping → memory map →
subsystems — and the architecture break vs. the NES KOEI work (65C816 vs 6502,
LoROM/HiROM, PPU modes 0–7, the separate SPC700 audio CPU, expansion chips) lives
in the vault: **`research/gaming/snes-cartridge-teardown.md`**. See `CONTEXT.md`
for how this repo is meant to be entered and extended.

## Status

Scaffold + verified header/mapper tooling. **Awaiting a target ROM** — when one
is chosen, that title gets its own repo (or a subfolder) that imports these tools.

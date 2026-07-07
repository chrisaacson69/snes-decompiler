# snes-decompiler

Generic tooling and method for **taking apart an SNES cartridge** — read the
internal header, detect the memory mapping, and map file offsets to 65C816
addresses so a ROM can be walked. No specific target yet; this is the reusable
substrate every future SNES title decompiler depends on (the SNES analog of the
NES `nes-render` / `koei-nes` shared layers).

Grounded in the **[SNESdev Wiki](https://snes.nesdev.org/wiki/SNESdev_Wiki)** —
every offset and rule traces to a wiki page, nothing is written from memory.

## Tools (`tools/`, stdlib-only, run with `py -3`)

| Tool | What it does |
|------|--------------|
| `header_parse.py <rom>` | Auto-detects LoROM/HiROM/ExHiROM by scoring the checksum-complement identity + map-mode byte + title, then dumps the header (title, chipset, sizes, checksum, **RESET vector = entry point**). |
| `mapper.py` | Library: `cpu_to_file` / `file_to_cpu` for LoROM & HiROM, copier-header (.smc) stripping. Importable by downstream disassembly. |

```
py -3 tools/header_parse.py path/to/game.sfc
```

## The teardown method

The four-step "look under the hood" checklist — header → mapping → memory map →
subsystems — and the architecture break vs. the NES KOEI work (65C816 vs 6502,
LoROM/HiROM, PPU modes 0–7, the separate SPC700 audio CPU, expansion chips) lives
in the vault: **`research/gaming/snes-cartridge-teardown.md`**. See `CONTEXT.md`
for how this repo is meant to be entered and extended.

## Status

Scaffold + verified header/mapper tooling. **Awaiting a target ROM** — when one
is chosen, that title gets its own repo (or a subfolder) that imports these tools.

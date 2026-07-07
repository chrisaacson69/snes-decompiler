# CONTEXT — snes-decompiler entry point

**What this is:** the reusable substrate for reverse-engineering *any* SNES cart —
header parsing, mapping detection, address↔offset math — plus the documented
teardown method. It's deliberately target-agnostic; a specific game imports it.

**Where it sits:** sibling repo of the vault, resolved by logical name
`snes-decompiler` via the vault's `.claude/local-paths.md`. Vault pointer:
`projects/game-annotation/snes/README.md`. Knowledge/thesis home:
`research/gaming/snes-cartridge-teardown.md`.

## Read order

1. This file.
2. `research/gaming/snes-cartridge-teardown.md` (vault) — the 4-step method +
   the architecture break vs. the NES KOEI decompilers.
3. `tools/header_parse.py` / `tools/mapper.py` — the working oracles.

## Current state

- ✅ `mapper.py` — LoROM/HiROM/ExHiROM offset↔address, copier-header stripping. Self-tested.
- ✅ `header_parse.py` — mapping auto-detect + full header dump + RESET entry point. Validated on a real cart.
- ✅ `w65c816.py` — **CPU-level** 65C816 decoder (256-opcode matrix + M/X width tracking). System-agnostic, zero SNES imports; the 65C816 analog of the NES `disasm6502`. Cross-checked vs 6502.org + selftest.
- ✅ `disasm65816.py` — **SNES-level** driver wiring `w65c816` to `mapper`. Verified end-to-end: decodes a real cart's RESET into the canonical boot sequence with correct M/X-dependent immediate lengths.
- ✅ **First target = `rot3k2-snes-decompiler`** (ROTK2 SNES). See that repo.

## Layering (why two files)

The CPU decoder (`w65c816.py`) knows the 65C816 and nothing else; the SNES wiring
(`disasm65816.py`, `mapper.py`, `header_parse.py`) is separate. Same split as the
NES side (generic `disasm6502` vs cart-specific tooling). Per the project SDK, a
shared lib is factored into its own repo **when a second consumer appears** — until
then `w65c816.py` stays here as a clean, dependency-free, graduation-ready module.

## Next steps (when a target lands)

1. `py -3 tools/header_parse.py <rom>` → lock mapping + entry vector.
2. Classify the architecture (per kernel) — is the logic native 65C816, or a
   bytecode VM like the KOEI NES titles? That choice picks the disassembly strategy.
3. Stand up a symbol-table registry (`.toml`) — the multi-pass-compilation-backwards
   discipline from the project SDK. Everything downstream points at it.
4. Build/adapt a 65C816 disassembler (M/X-width aware — the NES 6502 tool does not transfer).
5. Harvest confirmed findings back to the vault (topic memory + `research/gaming/`).

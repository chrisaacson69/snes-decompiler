# snes-decompiler — app-local rules

This repo is a **package in the vault's project process-table** (see
`projects/game-annotation/snes/` in the vault). It obeys, outermost first:

1. **Kernel** — user-global `~/.claude/CLAUDE.md`: grounding (#1 rule: never
   fabricate, reuse/convert > rebuild, missing grounding → find it or ask),
   verification-independence, classify-architecture-before-method.
2. **Project SDK** — the vault's `projects/CLAUDE.md`: RE is multi-pass
   compilation backwards (scan → symbol-table → link → typecheck); a registry IS
   the symbol table; single-writer + verifier; label-as-you-go; findings flow
   back to the vault.
3. **This file** — SNES specifics.

## SNES specifics

- **Ground every hardware claim in the [SNESdev Wiki](https://snes.nesdev.org/wiki/SNESdev_Wiki).**
  Do not write header offsets, memory-map ranges, PPU-mode semantics, or opcode
  behavior from memory. The tools already encode the header/mapping facts — reuse
  them, don't re-derive.
- **Classify before method (per kernel):** confirm mapping (LoROM/HiROM) and CPU
  mode first — it dictates offset math for the entire disassembly. `header_parse.py`
  is the oracle; trust its detection over the filename/extension.
- **65C816 is not the 6502.** 16-bit accumulator/index toggles (M/X flags),
  24-bit addressing, bank-relative code. A disassembler must track M/X width to
  decode immediates correctly — the NES `disasm6502` does **not** transfer as-is.
- **ROMs are immutable input, never committed** (`.gitignore` blocks `*.sfc/.smc`).
  They are the source of truth; derived symbol tables/annotations are the product.

## Entry point

Read `CONTEXT.md`, then the vault teardown page
(`research/gaming/snes-cartridge-teardown.md`). No target ROM yet.

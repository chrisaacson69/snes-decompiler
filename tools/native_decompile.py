#!/usr/bin/env python3
"""
native_decompile.py -- lift compiler-generated native 65C816 to structured C.

Target: KOEI's cdecl-compiled SNES code (the L'Empereur/Nobunaga-SNES family that
compiled its C source straight to native 65816 instead of to bytecode). The codegen
is regular enough to lift pattern-directed rather than with a general SSA decompiler:

  - calls are cdecl: caller PEA/PHA-pushes args (right-to-left), JSL/JSRs the callee,
    then PLA-pops the args. arg count is recovered from the trailing PLA run.
  - locals/args live on the hardware stack, addressed `$nn,S`. We track the stack
    delta from routine entry so each `$nn,S` resolves to a stable arg/local slot.
  - syscalls go through the ABI trampoline `JSL $C08000`; the LAST-pushed word is the
    syscall id ($0052), the rest are params -> rendered `sys_0xNN(params)`.

REUSE, not rebuild: instruction decode = snes-decompiler/w65c816; the control-flow
structuring (basic blocks -> dominators -> natural loops -> if/while regions) is ported
from koei-snes/tools/vm_struct.py (that DREAM structurer is CPU-agnostic; only the
per-instruction statement lift below is 65816-specific).

    py -3 tools/native_decompile.py <rom> --start C3:56B1
    py -3 tools/native_decompile.py <rom> --start C1:0987 --asm   # + raw disasm
"""
import argparse, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from mapper import strip_copier_header
from header_parse import detect
import w65c816

RET   = {"RTS", "RTL", "RTI", "STP"}
CALL  = {"JSR", "JSL"}
JUMP  = {"JMP", "JML"}
UNCOND= {"BRA", "BRL"}
COND  = {"BPL","BMI","BVC","BVS","BCC","BCS","BNE","BEQ"}
ABI   = 0x8000          # $C0:8000 ABI trampoline table base (syscall entry)


def load_syscalls(path):
    """id -> name from a syscall_names.toml ([[syscall]] blocks: `id = 0xNN` then `name = "…"`)."""
    import re
    out, cur = {}, None
    if path and os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            m = re.match(r"\s*id\s*=\s*0x([0-9A-Fa-f]+)", line)
            if m: cur = int(m.group(1), 16)
            n = re.match(r'\s*name\s*=\s*"([^"]+)"', line)
            if n and cur is not None: out[cur] = n.group(1); cur = None
    return out


def load_labels(path):
    """24-bit addr -> routine name from a labels.toml ([[label]] blocks: `addr = 0xBBOOOO`/`name`)."""
    import re
    out, cur = {}, None
    if path and os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            m = re.match(r"\s*addr\s*=\s*0x([0-9A-Fa-f]+)", line)
            if m: cur = int(m.group(1), 16)
            n = re.match(r'\s*name\s*=\s*"([^"]+)"', line)
            if n and cur is not None: out[cur] = n.group(1); cur = None
    return out


def opval(ins):
    """Numeric operand from the raw bytes (little-endian), or None."""
    raw, olen = ins["raw"], ins["len"] - 1
    if olen <= 0: return None
    return int.from_bytes(raw[1:1+olen], "little")


def push_bytes(ins):
    """How many bytes this instruction pushes(+)/pops(-) on the stack (0 if none)."""
    mn, m8, x8 = ins["mnem"], ins["m8"], ins["x8"]
    aw, xw = (1 if m8 else 2), (1 if x8 else 2)
    return {
        "PHA": aw, "PLA": -aw, "PHX": xw, "PLX": -xw, "PHY": xw, "PLY": -xw,
        "PHD": 2, "PLD": -2, "PHB": 1, "PLB": -1, "PHK": 1, "PHP": 1, "PLP": -1,
        "PEA": 2, "PEI": 2, "PER": 2,
    }.get(mn, 0)


class Decomp:
    def __init__(self, rom, mapping, bank, entry, m8=False, x8=False, syscalls=None, labels=None, wram=None):
        self.rom, self.mapping, self.bank, self.entry = rom, mapping, bank, entry
        self.m8_0, self.x8_0 = m8, x8
        self.syscalls = syscalls or {}
        self.labels = labels or {}
        self.wram = wram or {}
        from mapper import cpu_to_file
        self.c2f = lambda a: cpu_to_file(mapping, bank, a)
        self.ins = {}          # addr -> decoded instruction
        self.edges = {}        # addr -> [successor addr,...]
        self.decode_routine()
        self.build_blocks()
        self.doms()
        self.find_loops()

    # ---- 1. decode one routine (stay in bank; calls don't recurse) ----
    def decode_routine(self):
        work = [(self.entry, self.m8_0, self.x8_0)]
        while work:
            a, m8, x8 = work.pop()
            while a not in self.ins:
                foff = self.c2f(a)
                d, m8, x8 = w65c816.decode(self.rom, foff, self.bank, a, m8, x8)
                self.ins[a] = d
                mn, mode, tgt = d["mnem"], d["mode"], d["target"]
                nxt = (a + d["len"]) & 0xFFFF
                if mn in RET:
                    self.edges[a] = []; break
                if mn in CALL:                       # call returns -> fall through
                    self.edges[a] = [nxt]; a = nxt; continue
                if mn in JUMP:
                    if mode in ("ind","iax","ial") or tgt is None:
                        self.edges[a] = []            # computed jump: unresolved
                    else:
                        self.edges[a] = [tgt[1]]; work.append((tgt[1], m8, x8))
                    break
                if mn in UNCOND:
                    self.edges[a] = [tgt[1]]; work.append((tgt[1], m8, x8)); break
                if mn in COND:
                    self.edges[a] = [nxt, tgt[1]]     # [fall, taken]
                    work.append((tgt[1], m8, x8)); a = nxt; continue
                self.edges[a] = [nxt]; a = nxt

    # ---- 2. basic blocks ----
    def build_blocks(self):
        # A leader is: the entry, a branch/jump TARGET, or the fall-through right
        # after a *conditional* branch. Plain fall-through and returns-from-call
        # (JSR/JSL) stay INSIDE the block -- otherwise every instruction is a block.
        leaders = {self.entry}
        for a, sc in self.edges.items():
            nxt = (a + self.ins[a]["len"]) & 0xFFFF
            if len(sc) == 2:                          # conditional: both arms are leaders
                for s in sc:
                    if s in self.ins: leaders.add(s)
            elif len(sc) == 1 and sc[0] != nxt:       # unconditional jump/branch: target only
                if sc[0] in self.ins: leaders.add(sc[0])
        self.leaders = sorted(x for x in leaders if x in self.ins)
        self.blk_of = {}
        for L in self.leaders:
            v = L
            while True:
                self.blk_of[v] = L
                nxt = (v + self.ins[v]["len"]) & 0xFFFF
                sc = self.edges[v]
                if len(sc) != 1 or sc[0] != nxt or nxt in self.leaders or nxt not in self.ins:
                    break
                v = nxt
        self.bsucc = {L: [] for L in self.leaders}
        for L in self.leaders:                        # block successors = last insn's edges
            v = L
            while self.blk_of.get(v) == L:
                nxt = (v + self.ins[v]["len"]) & 0xFFFF
                sc = self.edges[v]
                # map every successor to its block leader so bsucc references only leaders
                # (a branch into mid-block -- jump table / overlap -- would otherwise KeyError)
                if len(sc) != 1 or sc[0] != nxt:
                    self.bsucc[L] = [self.blk_of[s] for s in sc if s in self.blk_of]; break
                if nxt not in self.ins or self.blk_of.get(nxt) != L:
                    self.bsucc[L] = [self.blk_of[nxt]] if nxt in self.blk_of else []; break
                v = nxt

    # ---- 3. dominators (Cooper/Harvey/Kennedy) ----
    def doms(self):
        order = self.leaders; oi = {L: i for i, L in enumerate(order)}
        preds = {L: [] for L in order}
        for L in order:
            for s in self.bsucc[L]: preds[s].append(L)
        self.preds = preds
        def inter(idom, a, b):
            while a != b:
                while oi[a] > oi[b]: a = idom[a]
                while oi[b] > oi[a]: b = idom[b]
            return a
        idom = {order[0]: order[0]}; changed = True
        while changed:
            changed = False
            for L in order:
                if L == order[0]: continue
                new = None
                for p in preds[L]:
                    if p in idom: new = p if new is None else inter(idom, new, p)
                if new is not None and idom.get(L) != new:
                    idom[L] = new; changed = True
        self.idom = idom

    def dominates(self, d, n):
        while n in self.idom and n != self.idom[n]:
            if n == d: return True
            n = self.idom[n]
        return n == d

    # ---- 4. natural loops from back edges ----
    def find_loops(self):
        self.loops = {}                               # header -> set(body blocks)
        for L in self.leaders:
            for s in self.bsucc[L]:
                if self.dominates(s, L):              # back edge L -> header s
                    body = {s, L}; stack = [L]
                    while stack:
                        n = stack.pop()
                        for p in self.preds[n]:
                            if p not in body: body.add(p); stack.append(p)
                    self.loops.setdefault(s, set()).update(body)

    # ---- 5. per-block statement lift (65816-specific) ----
    def block_addrs(self, L):
        addrs, v = [], L
        while self.blk_of.get(v) == L:
            addrs.append(v); v = (v + self.ins[v]["len"]) & 0xFFFF
        return addrs

    def sp_effect(self, addrs, i):
        """Stack-delta change and #instructions consumed at addrs[i]. Handles the
        `TSC;(CLC|SEC);(ADC|SBC) #N;TCS` SP-adjust idiom (bulk arg alloc/cleanup)."""
        d = self.ins[addrs[i]]; mn = d["mnem"]
        if mn == "TSC" and i + 3 < len(addrs):
            m1 = self.ins[addrs[i+1]]["mnem"]; op = self.ins[addrs[i+2]]; m3 = self.ins[addrs[i+3]]["mnem"]
            if m1 in ("CLC", "SEC") and op["mnem"] in ("ADC", "SBC") and m3 == "TCS":
                n = opval(op) or 0
                return (-n if op["mnem"] == "ADC" else n), 4   # ADC=SP+=n=pop; SBC=push
        return push_bytes(d), 1

    def entry_delta(self):
        """BFS the stack delta from routine entry to each block leader."""
        ed = {self.leaders[0]: 0}; work = [self.leaders[0]]
        while work:
            L = work.pop(); d = ed[L]; addrs = self.block_addrs(L); i = 0
            while i < len(addrs):
                chg, cons = self.sp_effect(addrs, i); d += chg; i += cons
            for s in self.bsucc[L]:
                if s not in ed: ed[s] = d; work.append(s)
        return ed

    def frame_name(self, entry_off):
        if entry_off >= 4:
            return "arg%d" % ((entry_off - 4) // 2)
        n = self.local_names.setdefault(entry_off, "v%d" % len(self.local_names))
        return n

    def cond_expr(self, br, lhs, rhs):
        z = (rhs in ("0", "$0", "0x0"))
        if br == "BEQ": return "%s == %s" % (lhs, rhs) if not z else "%s == 0" % lhs
        if br == "BNE": return "%s != %s" % (lhs, rhs) if not z else "%s != 0" % lhs
        if br == "BMI": return "%s < 0" % lhs if z else "%s < %s" % (lhs, rhs)
        if br == "BPL": return "%s >= 0" % lhs if z else "%s >= %s" % (lhs, rhs)
        if br == "BCC": return "%s < %s" % (lhs, rhs)
        if br == "BCS": return "%s >= %s" % (lhs, rhs)
        return "cc_%s" % br.lower()

    def call_args(self, pushed, argc):
        """Take the top `argc` pushed words (push order) as call args, coalescing an
        adjacent (bank, offset) pair into one 24-bit far pointer. KOEI banks are always
        $7E/$7F (WRAM) or $C0-$FF (ROM), so the high word is an unambiguous segment tell."""
        raw = pushed[-argc:] if argc else []
        del pushed[len(pushed) - argc:]
        merged, k = [], 0
        def lit(s):
            try: return int(s, 16) if isinstance(s, str) and s.startswith("0x") else None
            except ValueError: return None
        while k < len(raw):
            b = lit(raw[k]); off = lit(raw[k+1]) if k + 1 < len(raw) else None
            if b is not None and off is not None and (b in (0x7E, 0x7F) or 0xC0 <= b <= 0xFF):
                merged.append("0x%02X%04X" % (b, off)); k += 2      # bank:offset far pointer
            else:
                merged.append(raw[k]); k += 1
        return list(reversed(merged))                              # cdecl: source order

    def block_stmts(self, L, ed):
        regA, regX, regY = "a", "x", "y"
        delta = ed[L]; pushed = []; out = []; cmp = None; cond = None
        pending = 0                                    # trailing PLA cleanup words owed by a call
        addrs = self.block_addrs(L)
        i = 0
        while i < len(addrs):
            a = addrs[i]
            # bulk SP-adjust idiom (TSC;..;TCS): adjust delta, emit nothing
            chg, cons = self.sp_effect(addrs, i)
            if cons == 4:
                delta += chg; pending = 0; i += 4; continue
            d = self.ins[a]; mn, mode = d["mnem"], d["mode"]; val = opval(d)
            def mem():                                # symbolic memory/operand name
                if mode == "sr":
                    return self.frame_name(val - delta)
                if mode == "imm_m" or mode == "imm_x":
                    return "0x%X" % val
                if mode in ("idy","idly"):        # (dp),Y / [dp],Y — Y carries the field offset
                    return "dp_%02X[%s]" % (val & 0xFF, regY)
                if mode == "dpy":                 # dp,Y — direct-page indexed
                    return "dp_%02X[%s]" % (val & 0xFF, regY)
                if mode == "dpx":                 # dp,X — direct-page indexed
                    return "dp_%02X[%s]" % (val & 0xFF, regX)
                if mode in ("dp","idp","idx","idl"):
                    return "dp_%02X" % (val & 0xFF)
                if mode == "abx":
                    return (self.wram.get(val) or "M_%04X" % val) + "[%s]" % regX
                if mode == "aby":
                    return (self.wram.get(val) or "M_%04X" % val) + "[%s]" % regY
                if mode == "abs":
                    return self.wram.get(val) or ("M_%04X" % val)
                if mode in ("abl","alx"):
                    if (val >> 16) in (0x7E, 0x7F):
                        nm = self.wram.get(val & 0xFFFF)
                        if nm: return nm
                    return "M_%06X" % val
                return d["text"]
            if mn in ("PLA", "PLX", "PLY") and pending > 0:
                delta += push_bytes(d); pending -= 1; i += 1; continue   # call-arg cleanup
            pending = 0
            if mn == "LDA": regA = mem()
            elif mn == "LDX": regX = mem()
            elif mn == "LDY": regY = mem()
            elif mn == "STA": out.append("%s = %s;" % (mem(), regA))
            elif mn == "STX": out.append("%s = %s;" % (mem(), regX))
            elif mn == "STY": out.append("%s = %s;" % (mem(), regY))
            elif mn == "STZ": out.append("%s = 0;" % mem())
            elif mn in ("PHA","PHX","PHY","PEA","PEI"):
                e = ("0x%X" % val) if mn in ("PEA","PEI") else {"PHA":regA,"PHX":regX,"PHY":regY}[mn]
                pushed.append(e); delta += push_bytes(d)
            elif mn in ("PLA","PLX","PLY"):
                delta += push_bytes(d)
                if pushed: pushed.pop()
            elif mn in ("INC","INA"):
                if mode == "acc" or mn == "INA": regA = "(%s + 1)" % regA
                else: out.append("%s = %s + 1;" % (mem(), mem()))
            elif mn in ("DEC","DEA"):
                if mode == "acc" or mn == "DEA": regA = "(%s - 1)" % regA
                else: out.append("%s = %s - 1;" % (mem(), mem()))
            elif mn == "ADC": regA = "(%s + %s)" % (regA, mem())
            elif mn == "SBC": regA = "(%s - %s)" % (regA, mem())
            elif mn == "AND": regA = "(%s & %s)" % (regA, mem())
            elif mn == "ORA": regA = "(%s | %s)" % (regA, mem())
            elif mn == "EOR": regA = "(%s ^ %s)" % (regA, mem())
            elif mn == "ASL": regA = "(%s << 1)" % regA
            elif mn == "LSR": regA = "(%s >> 1)" % regA
            elif mn == "TAX": regX = regA
            elif mn == "TAY": regY = regA
            elif mn == "TXA": regA = regX
            elif mn == "TYA": regA = regY
            elif mn == "CMP": cmp = (regA, mem())
            elif mn == "CPX": cmp = (regX, mem())
            elif mn == "CPY": cmp = (regY, mem())
            elif mn in CALL:
                tgt = d["target"]
                # cdecl arg count: either a trailing PLA run OR the bulk TSC;..;TCS cleanup.
                argc = 0
                for b in addrs[i+1:]:
                    if self.ins[b]["mnem"] in ("PLA","PLX","PLY"): argc += 1
                    else: break
                if argc:
                    pending = argc                       # let the trailing PLAs pop `pushed`
                elif i + 1 < len(addrs):                 # bulk SP-adjust cleanup?
                    chg, cons = self.sp_effect(addrs, i + 1)
                    if cons == 4 and chg < 0: argc = (-chg) // 2
                args = self.call_args(pushed, argc)
                if tgt and (tgt[0] << 16 | tgt[1]) == (0xC0 << 16 | ABI) and args:
                    sid = args[0]; rest = ", ".join(args[1:])
                    sidn = int(sid, 16) if isinstance(sid, str) and sid.startswith("0x") else None
                    nm = self.syscalls.get(sidn) if sidn is not None else None
                    call = "%s(%s)" % (nm or "sys_%s" % sid, rest)
                else:
                    lbl = self.labels.get((tgt[0] << 16) | tgt[1]) if tgt else None
                    nm = lbl or ("sub_%02X%04X" % (tgt[0], tgt[1]) if tgt else "(*%s)" % regA)
                    call = "%s(%s)" % (nm, ", ".join(args))
                out.append(call + ";"); regA = "ret"
            elif mn in COND:
                if cmp: cond = self.cond_expr(mn, cmp[0], cmp[1])
                else:   cond = self.cond_expr(mn, regA, "0")
            elif mn in RET:
                out.append("return;")
            elif mn in UNCOND or mn in JUMP or mn in ("REP","SEP","CLC","SEC","XCE","NOP","PHP","PLP","PHB","PLB","PHK","PHD","PLD","TSC","TCS","TCD","TDC"):
                pass
            else:
                out.append("/* %s %s */" % (mn, d["text"]))
            i += 1
        return out, (cond or "cc")


def emit_c(dc, out=sys.stdout):
    dc.local_names = {}
    ed = dc.entry_delta()
    printed = set()

    def follow_of(L):
        s = dc.bsucc[L]
        if len(s) != 2: return None
        def chain(x):
            seen = []
            for _ in range(80):
                seen.append(x)
                if len(dc.bsucc.get(x, [])) != 1: break
                x = dc.bsucc[x][0]
            return seen
        ca, cb = chain(s[0]), set(chain(s[1]))
        for x in ca:
            if x in cb: return x
        return None

    def emit(L, stop, ind):
        pad = "    " * ind
        while L is not None and L != stop:
            if L in printed:
                out.write(pad + "goto L%04X;\n" % L); return
            printed.add(L)
            # loop header?
            if L in dc.loops:
                body = dc.loops[L]
                stmts, cond = dc.block_stmts(L, ed)
                out.write(pad + "L%04X: while (1) {\n" % L)
                for st in stmts: out.write(pad + "    " + st + "\n")
                s = dc.bsucc[L]
                if len(s) == 2:
                    taken, fall = s[1], s[0]
                    exit_succ = fall if fall not in body else (taken if taken not in body else None)
                    body_succ = taken if taken in body else fall
                    if exit_succ is not None:
                        ec = cond if exit_succ == taken else "!(%s)" % cond
                        out.write(pad + "    if (%s) break;\n" % ec)
                    emit(body_succ, L, ind + 1)
                    out.write(pad + "}\n")
                    L = exit_succ; continue
                else:
                    emit(s[0] if s else None, L, ind + 1)
                    out.write(pad + "}\n"); return
            stmts, cond = dc.block_stmts(L, ed)
            for st in stmts:
                if not st.startswith("return"): out.write(pad + st + "\n")
            if any(st.startswith("return") for st in stmts):
                out.write(pad + "return;\n"); return
            s = dc.bsucc[L]
            if len(s) == 2:
                foll = follow_of(L); taken, fall = s[1], s[0]
                out.write(pad + "if (%s) {\n" % cond)
                emit(taken, foll, ind + 1)
                if fall != foll:
                    out.write(pad + "} else {\n"); emit(fall, foll, ind + 1)
                out.write(pad + "}\n"); L = foll
            elif len(s) == 1:
                L = s[0]
            else:
                return

    _fn = dc.labels.get((dc.bank << 16) | dc.entry) or ("sub_%02X%04X" % (dc.bank, dc.entry))
    out.write("void %s() {\n" % _fn)
    emit(dc.leaders[0], None, 1)
    out.write("}\n")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("rom"); ap.add_argument("--start", required=True, help="BANK:ADDR routine entry")
    ap.add_argument("--mx", default="16,16", help="A,index widths at entry (default 16,16 = native game code)")
    ap.add_argument("--syscalls", default=None, help="syscall_names.toml (default: <rom-dir>/data/syscall_names.toml)")
    ap.add_argument("--asm", action="store_true", help="also print the raw disassembly")
    a = ap.parse_args(argv[1:])
    rom, _ = strip_copier_header(open(a.rom, "rb").read())
    score, f = detect(rom)
    if f is None or score < 4: sys.exit("no confident SNES header (score %d)" % score)
    mapping = f["mapping_guess"]
    b, ad = a.start.split(":"); bank, entry = int(b, 16), int(ad, 16)
    mw, xw = (s.strip() for s in a.mx.split(","))
    datadir = os.path.join(os.path.dirname(os.path.abspath(a.rom)), "data")
    syspath = a.syscalls or os.path.join(datadir, "syscall_names.toml")
    dc = Decomp(rom, mapping, bank, entry, m8=(mw == "8"), x8=(xw == "8"),
                syscalls=load_syscalls(syspath), labels=load_labels(os.path.join(datadir, "labels.toml")),
                wram=load_labels(os.path.join(datadir, "wram.toml")))
    if a.asm:
        print("== disassembly ==")
        for k in sorted(dc.ins): print(w65c816.render(dc.ins[k]))
        print("\n== C ==")
    emit_c(dc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

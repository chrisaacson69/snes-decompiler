#!/usr/bin/env python3
"""
native_callgraph.py -- call graph over a compiled-native SNES title.

Reuses the reachability walk (walk65816) + the per-routine decode (native_decompile.Decomp)
to build caller/callee edges across every reachable routine, then ranks nodes by fan-in.

The point (structure as the meta-tool): a *library function* is exactly a node with high
fan-in and low fan-out. So this ranking IS the labeling worklist -- naming the top nodes
buys readability across the most call sites. It also exposes the architecture: entry roots
(fan-in 0), the runtime-library cluster, and per-bank subsystems.

    py -3 tools/native_callgraph.py <rom>                 # ranked fan-in table + stats
    py -3 tools/native_callgraph.py <rom> --callers C1:F695   # who calls a routine
    py -3 tools/native_callgraph.py <rom> --dot cg.dot    # graphviz export
"""
import argparse, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from mapper import strip_copier_header
from header_parse import detect
import walk65816 as W
import native_decompile as ND


def build(rom, mapping, syscall_base=0x9550, n_syscalls=59):
    # entry set: RESET/NMI/IRQ + main + every syscall handler
    entries = [(0xC0, detect(rom)[1]["reset_vector"], True, True)]
    for v in (0x8447, 0x94FF):
        entries.append((0xC0, v, False, False))
    entries.append((0xC1, 0x0A2C, False, False))
    for i in range(1, n_syscalls + 1):
        w = rom[syscall_base + i*2] | (rom[syscall_base + i*2 + 1] << 8)
        if 0x8000 <= w <= 0xBFFF:
            entries.append((0xC0, w, False, False))
    r = W.walk(W.AddressSpace(rom, mapping, []), entries)
    routines = sorted(set(r["subs"]) | {(b, a) for b, a, _, _ in entries})
    callees = {R: set() for R in routines}
    indeg = {R: 0 for R in routines}
    for (b, a) in routines:
        try:
            dc = ND.Decomp(rom, mapping, b, a)
        except Exception:
            continue
        for ins in dc.ins.values():
            if ins["mnem"] in ("JSR", "JSL") and ins["target"]:
                tb, ta = ins["target"]
                if ins["mnem"] == "JSR":
                    tb = b
                callees[(b, a)].add((tb, ta))
        for c in callees[(b, a)]:
            if c in indeg:
                indeg[c] += 1
    return routines, callees, indeg


def dfs_postorder(root, callees):
    """Leaves-first (post-order) DFS from root. A node is emitted only after its whole
    subtree — so callees are always resolved before their caller. Revisits are deduped."""
    order, seen = [], set()
    def visit(R):
        if R in seen:
            return
        seen.add(R)
        for c in sorted(callees.get(R, ())):
            if c in callees:
                visit(c)
        order.append(R)
    visit(root)
    return order


def main(argv):
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--callers", default=None, help="BANK:ADDR — list callers of this routine")
    ap.add_argument("--dfs", default=None, help="BANK:ADDR — emit the post-order worklist for this subtree")
    ap.add_argument("--emit", type=int, default=12, help="how many unnamed nodes to emit (--dfs)")
    ap.add_argument("--dot", default=None, help="write a graphviz .dot")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args(argv[1:])
    rom, _ = strip_copier_header(open(a.rom, "rb").read())
    score, f = detect(rom)
    if f is None or score < 4:
        sys.exit("no confident SNES header")
    mp = f["mapping_guess"]
    routines, callees, indeg = build(rom, mp)
    edges = sum(len(c) for c in callees.values())

    if a.dfs:
        datadir = os.path.join(os.path.dirname(os.path.abspath(a.rom)), "data")
        labels = ND.load_labels(os.path.join(datadir, "labels.toml"))
        syscalls = ND.load_syscalls(os.path.join(datadir, "syscall_names.toml"))
        wram = ND.load_labels(os.path.join(datadir, "wram.toml"))
        sysaddr = {(0xC0, rom[0x9550 + i*2] | (rom[0x9550 + i*2 + 1] << 8)) for i in range(1, 60)}
        GEN = {(0xC1, 0xFB30), (0xC1, 0xFB39), (0xC1, 0xFB42), (0xC1, 0xFB50), (0xC1, 0xFA49)}
        named = lambda R: ((R[0] << 16) | R[1]) in labels or R in sysaddr
        b, ad = a.dfs.split(":"); root = (int(b, 16), int(ad, 16))
        order = dfs_postorder(root, callees)
        todo = [R for R in order if not named(R) and not (callees.get(R) and all(c in GEN for c in callees[R]))]
        print("== DFS post-order worklist: subtree of $%02X:%04X ==" % root)
        print("subtree routines: %d | named: %d | worklist (unnamed, dependency order): %d"
              % (len(order), sum(named(R) for R in order), len(todo)))
        print("emitting the first %d (deepest-first; callees already named above):\n" % min(a.emit, len(todo)))
        import io
        for R in todo[:a.emit]:
            callers = sorted(C for C in routines if R in callees.get(C, ()))
            cnames = " ".join((labels.get((c[0] << 16) | c[1]) or "sub_%02X%04X" % c) for c in callers[:5])
            buf = io.StringIO()
            try:
                ND.emit_c(ND.Decomp(rom, mp, R[0], R[1], syscalls=syscalls, labels=labels, wram=wram), buf)
                c = buf.getvalue().rstrip()
            except Exception as e:
                c = "<err %s>" % e
            print("----- $%02X:%04X  (called by: %s) -----\n%s\n" % (R[0], R[1], cnames or "(root)", c))
        return 0

    if a.callers:
        b, ad = a.callers.split(":"); tgt = (int(b, 16), int(ad, 16))
        callers = sorted(R for R, cs in callees.items() if tgt in cs)
        print("callers of $%02X:%04X (%d):" % (tgt[0], tgt[1], len(callers)))
        for b2, a2 in callers:
            print("  $%02X:%04X  (fan-out %d)" % (b2, a2, len(callees[(b2, a2)])))
        return 0

    if a.dot:
        with open(a.dot, "w") as fh:
            fh.write("digraph cg {\n  node [shape=box,fontsize=9];\n")
            for R, cs in callees.items():
                for c in cs:
                    fh.write('  "%02X%04X" -> "%02X%04X";\n' % (R[0], R[1], c[0], c[1]))
            fh.write("}\n")
        print("wrote", a.dot)
        return 0

    print("routines: %d   call edges: %d" % (len(routines), edges))
    lib = sorted(routines, key=lambda R: (-indeg[R], len(callees[R])))
    print("\n%7s %5s  routine     region" % ("callers", "calls"))
    for R in lib[:a.top]:
        b, ad = R
        region = ("runtime-lib" if (b == 0xC1 and ad >= 0xF000)
                  else "BIOS/ABI" if (b == 0xC0 and 0x8000 <= ad < 0xB800) else "")
        print("%7d %5d  $%02X:%04X  %s" % (indeg[R], len(callees[R]), b, ad, region))
    libreg = [R for R in routines if R[0] == 0xC1 and R[1] >= 0xF000]
    print("\nruntime-lib cluster ($C1:F000-FFFF): %d routines, %d total calls in"
          % (len(libreg), sum(indeg[R] for R in libreg)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

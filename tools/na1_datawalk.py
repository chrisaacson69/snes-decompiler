#!/usr/bin/env py -3
"""NA1 SNES data-walk: MMIO registers (hardware spec = reference ground truth) +
WRAM frontier by named-referrer convergence. Single self-contained pass."""
import sys, re
sys.path.insert(0, r"C:\Users\Chris.Isaacson\snes-decompiler\tools")
import native_decompile as ND, native_callgraph as CG, snes_regs
from mapper import strip_copier_header

DATA=r"C:\Users\Chris.Isaacson\na1-snes-decompiler\data"
ROM=r"C:\Users\Chris.Isaacson\na1-snes-decompiler\Nobunaga's Ambition (USA).sfc"
rom,_=strip_copier_header(open(ROM,"rb").read())
sc,f=ND.detect(rom); mp=f["mapping_guess"]
labels=ND.load_labels(DATA+r"\labels.toml")
wram=ND.load_labels(DATA+r"\wram.toml")
labeled={k&0xFFFF for k in wram}
routines,callees,indeg=CG.build(rom,mp)

WRITE={"STA","STX","STY","STZ","INC","DEC","ASL","LSR","ROL","ROR","TRB","TSB"}
READ ={"LDA","LDX","LDY","CMP","CPX","CPY","ADC","SBC","AND","ORA","EOR","BIT"}
MEMMODES={"dp","abs","abl","dpx","dpy","abx","aby","alx","aly","dix","diy","idp","idl"}
xref={}
for (bank,entry) in routines:
    nm=labels.get((bank<<16)|entry, "sub_%02X%04X"%(bank,entry))
    try: dc=ND.Decomp(rom,mp,bank,entry,syscalls=sc,labels=labels,wram=wram)
    except Exception: continue
    for a,d in dc.ins.items():
        mode,txt=d["mode"],d["text"]
        if mode not in MEMMODES: continue
        m=re.match(r"^\[?\$([0-9A-Fa-f]{2,6})",txt)
        if not m: continue
        val=int(m.group(1),16)
        if val>0xFFFF:
            if (val>>16) in (0x7E,0x7F): val=(val>>16<<16)|(val&0xFFFF)
            else: continue
        elif val>=0x8000: continue
        kind="w" if d["mnem"] in WRITE else ("r" if d["mnem"] in READ else None)
        if kind is None: continue
        xref.setdefault(val,{"r":set(),"w":set()})[kind].add(nm)

cmd=sys.argv[1] if len(sys.argv)>1 else "mmio"
if cmd=="mmio":
    hits=sorted(a for a in xref if (snes_regs.name(a&0xFFFF)[0] is not None) and (a&0xFFFF)>=0x2000 and (a&0xFFFF) not in labeled)
    print(f"# {len(hits)} referenced MMIO registers (SNES hardware spec) not yet in wram.toml")
    for a in hits:
        e=xref[a]; nref=len(e["r"]|e["w"])
        print(f'0x{a&0xFFFF:04X} {snes_regs.name(a&0xFFFF)[0]:16s} refs={nref}')
elif cmd=="mmiotoml":
    hits=sorted(a for a in xref if (snes_regs.name(a&0xFFFF)[0] is not None) and (a&0xFFFF)>=0x2000 and (a&0xFFFF) not in labeled)
    for a in hits:
        print(f'\n[[var]]\naddr = 0x{a&0xFFFF:04X}\nname = "{snes_regs.name(a&0xFFFF)[0]}"')
elif cmd=="wram":   # unlabeled WRAM ($0000-$7FFF or $7E:xxxx), NOT mmio, by named-referrer count
    minref=int(sys.argv[2]) if len(sys.argv)>2 else 4
    rows=[]
    for a,e in xref.items():
        lo=a&0xFFFF
        if snes_regs.name(lo)[0] is not None and lo>=0x2000: continue
        if lo in labeled: continue
        allsubs=sorted(e["r"]|e["w"]); named=[s for s in allsubs if not s.startswith("sub_")]
        if len(named)<minref: continue
        rows.append((len(named),a,e,allsubs))
    rows.sort(reverse=True)
    print(f"# unlabeled WRAM/DP addrs with >= {minref} NAMED referrers ({len(rows)} shown)")
    for n,a,e,subs in rows:
        disp=("$%06X"%a) if a>0xFFFF else ("$%04X"%a)
        rw=("R" if e["r"] else " ")+("W" if e["w"] else " ")
        named=[s for s in subs if not s.startswith("sub_")]
        print(f"{disp} named={n:2d} {rw}  {', '.join(named[:9])}")

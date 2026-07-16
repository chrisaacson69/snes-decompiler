#!/usr/bin/env py -3
"""NA1 SNES data-xref: group WRAM/abs memory refs by the NAMED sub that touches them.
Provenance model of /data-walk, adapted to the native (non-VM) SNES toolchain.
Reuses native_decompile.Decomp for decode; no new disassembler."""
import sys, re
sys.path.insert(0, r"C:\Users\Chris.Isaacson\snes-decompiler\tools")
import native_decompile as ND, native_callgraph as CG
from mapper import strip_copier_header

ROM=r"C:\Users\Chris.Isaacson\na1-snes-decompiler\Nobunaga's Ambition (USA).sfc"
rom,_=strip_copier_header(open(ROM,"rb").read())
sc,f=ND.detect(rom); mp=f["mapping_guess"]
labels=ND.load_labels(r"C:\Users\Chris.Isaacson\na1-snes-decompiler\data\labels.toml")
wram=ND.load_wram(r"C:\Users\Chris.Isaacson\na1-snes-decompiler\data\wram.toml") if hasattr(ND,"load_wram") else {}
routines,callees,indeg=CG.build(rom,mp)

WRITE={"STA","STX","STY","STZ","INC","DEC","ASL","LSR","ROL","ROR","TRB","TSB"}
READ ={"LDA","LDX","LDY","CMP","CPX","CPY","ADC","SBC","AND","ORA","EOR","BIT"}
MEMMODES={"dp","abs","abl","dpx","dpy","abx","aby","alx","aly","dix","diy","idp","idl"}

# address -> {addr:int, refs:{name:set('r'/'w')}}
xref={}
def add(addr, name, kind):
    e=xref.setdefault(addr, {"r":set(),"w":set()})
    e[kind].add(name)

for (bank,entry) in routines:
    name=labels.get((bank<<16)|entry, "sub_%02X%04X"%(bank,entry))
    try:
        dc=ND.Decomp(rom,mp,bank,entry,syscalls=sc,labels=labels,wram=wram)
    except Exception:
        continue
    for a,d in dc.ins.items():
        mn,mode,txt=d["mnem"],d["mode"],d["text"]
        if mode not in MEMMODES: continue
        m=re.match(r"^\[?\$([0-9A-Fa-f]{2,6})", txt)
        if not m: continue
        val=int(m.group(1),16)
        # normalize: abs/dp 16-bit stay as-is; abl already full 24-bit
        if val > 0xFFFF:
            # long: keep bank; only care about WRAM $7E/$7F here
            hb=val>>16
            if hb in (0x7E,0x7F): val=(hb<<16)|(val&0xFFFF)
            else: continue
        else:
            if not (val < 0x8000): continue   # abs >=$8000 = ROM code/imm, skip
        kind = "w" if mn in WRITE else ("r" if mn in READ else None)
        if kind is None: continue
        add(val, name, kind)

def is_labeled(addr):
    a = addr & 0xFFFF if addr>0xFFFF else addr
    return a in wram or addr in wram

cmd = sys.argv[1] if len(sys.argv)>1 else "hot"
if cmd=="stats":
    tot=len(xref); lab=sum(1 for a in xref if is_labeled(a))
    print(f"distinct data addrs referenced: {tot} | labeled: {lab} | unlabeled: {tot-lab}")
elif cmd=="hot":
    minref=int(sys.argv[2]) if len(sys.argv)>2 else 3
    rows=[]
    for a,e in xref.items():
        if is_labeled(a): continue
        subs=e["r"]|e["w"]
        if len(subs)<minref: continue
        rows.append((len(subs),a,e))
    rows.sort(reverse=True)
    print(f"# unlabeled WRAM/abs addrs with >= {minref} distinct named referrers")
    for n,a,e in rows:
        disp = ("$%06X"%a) if a>0xFFFF else ("$%04X"%a)
        allsubs=sorted(e["r"]|e["w"])
        gen=sum(1 for s in allsubs if s.startswith("sub_"))
        w="W" if e["w"] else " "; r="R" if e["r"] else " "
        print(f"{disp}  refs={n:2d} ({r}{w}) named={n-gen:2d}  {', '.join(allsubs[:8])}")
elif cmd=="refs":
    a=int(sys.argv[2],16)
    e=xref.get(a,{"r":set(),"w":set()})
    print(f"{'$%06X'%a if a>0xFFFF else '$%04X'%a}:")
    print("  writes:", ", ".join(sorted(e["w"])) or "(none)")
    print("  reads :", ", ".join(sorted(e["r"])) or "(none)")

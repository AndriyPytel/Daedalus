
import math, sys, collections
import pymupdf

def segments(page):
    out=[]
    lim=0.85*page.rect.width*page.rect.height
    for path in page.get_drawings():
        r=path["rect"]
        if r.width*r.height>lim: continue
        prev=None
        for it in path["items"]:
            if it[0]=="l": out.append((tuple(it[1]),tuple(it[2])))
            elif it[0]=="re":
                x0,y0,x1,y1=it[1]
                out += [((x0,y0),(x1,y0)),((x1,y0),(x1,y1)),((x1,y1),(x0,y1)),((x0,y1),(x0,y0))]
    return out

def spans(segs, angle_bucket=1.0, offset_bucket=1.0, join_gap=60.0, min_len=8.0):
    """Collinear segments merged into one span, bridging the text gap."""
    groups=collections.defaultdict(list)
    for a,b in segs:
        dx,dy=b[0]-a[0], b[1]-a[1]
        L=math.hypot(dx,dy)
        if L<0.5: continue
        ang=math.degrees(math.atan2(dy,dx))%180.0
        ux,uy=math.cos(math.radians(ang)),math.sin(math.radians(ang))
        # perpendicular offset of the line from origin
        off = -uy*a[0]+ux*a[1]
        key=(round(ang/angle_bucket), round(off/offset_bucket))
        t0=ux*a[0]+uy*a[1]; t1=ux*b[0]+uy*b[1]
        groups[key].append((min(t0,t1),max(t0,t1),ang,off,a,b))
    out=[]
    for key,items in groups.items():
        items.sort()
        cur=None
        for t0,t1,ang,off,a,b in items:
            if cur is None: cur=[t0,t1,ang,off]
            elif t0-cur[1]<=join_gap: cur[1]=max(cur[1],t1)
            else:
                out.append(tuple(cur)); cur=[t0,t1,ang,off]
        if cur: out.append(tuple(cur))
    res=[]
    for t0,t1,ang,off in out:
        L=t1-t0
        if L<min_len: continue
        ux,uy=math.cos(math.radians(ang)),math.sin(math.radians(ang))
        px,py=-uy,ux
        p0=(ux*t0+px*off, uy*t0+py*off)
        p1=(ux*t1+px*off, uy*t1+py*off)
        res.append({"len":L,"ang":ang,"p0":p0,"p1":p1})
    return res

def numeric(word):
    s=str(word).strip().strip("()").replace("Ø","").replace("R","")
    s=s.split()[0] if s.split() else ""
    try:
        v=float(s)
        return v if v>0 else None
    except ValueError:
        return None

def near_spans(sp, w, tol=14.0):
    """Spans whose line passes under the word and brackets it."""
    cx,cy=(w[0]+w[2])/2,(w[1]+w[3])/2
    out=[]
    for s in sp:
        ang=math.radians(s["ang"]); ux,uy=math.cos(ang),math.sin(ang)
        px,py=-uy,ux
        d=abs(px*(cx-s["p0"][0])+py*(cy-s["p0"][1]))
        t=ux*(cx-s["p0"][0])+uy*(cy-s["p0"][1])
        if d<=tol and 0.15*s["len"]<=t<=0.85*s["len"]:
            out.append((d,s))
    out.sort(key=lambda x:x[0])
    return [s for _,s in out]

def analyse(path):
    doc=pymupdf.open(path); page=doc[0]
    segs=segments(page); sp=spans(segs)
    words=page.get_text("words")
    pairs=[]
    for w in words:
        v=numeric(w[4])
        if v is None: continue
        for s in near_spans(sp,w)[:1]:
            pairs.append((w[4].strip(), v, s["len"], v/s["len"], s))
    # consensus on k = mm per point
    ks=sorted(p[3] for p in pairs)
    best=None
    for k in ks:
        agree=[p for p in pairs if abs(p[3]-k)/k<0.02]
        if best is None or len(agree)>len(best[1]): best=(k,agree)
    k=sum(p[3] for p in best[1])/len(best[1])
    return path, k, pairs, best[1], len(words)

for f in ["tests/modelmania/mm25-p1-1.pdf","tests/modelmania/mm25-p2.pdf",
          "tests/modelmania/model-mania-2024.pdf","tests/modelmania/model-mania_-in-person-2023.pdf"]:
    p,k,pairs,agree,nw=analyse(f)
    print("\n==",f.split("/")[-1],"  mm/pt =",round(k,5),"  pt/mm =",round(1/k,5),
          "  consensus %d/%d paired, %d numeric-ish words"%(len(agree),len(pairs),nw))
    for txt,v,L,kk,s in pairs:
        meas=L*k
        mark="OK " if abs(meas-v)<=0.6 else "   "
        print("   %s%-10s printed %8.2f   span %7.2f pt -> %8.2f mm   off %+6.2f" % (mark,txt,v,L,meas,meas-v))

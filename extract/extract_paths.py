import re,json,sys,pypdf
def mul(a,b):
    return (a[0]*b[0]+a[1]*b[2], a[0]*b[1]+a[1]*b[3],
            a[2]*b[0]+a[3]*b[2], a[2]*b[1]+a[3]*b[3],
            a[4]*b[0]+a[5]*b[2]+b[4], a[4]*b[1]+a[5]*b[3]+b[5])
def ap(m,x,y): return (m[0]*x+m[2]*y+m[4], m[1]*x+m[3]*y+m[5])
def run(path):
    data=pypdf.PdfReader(path).pages[0].get_contents().get_data().decode('latin1')
    toks=re.findall(r'(?:\[[^\]]*\]|\([^)]*\)|/[^\s/\[\]()<>]+|<[^>]*>|[^\s\[\]()<>/]+)',data)
    ctm=(1,0,0,1,0,0); stack=[]; nums=[]
    cur=[]; pt=None; start=None
    strokec=None; fillc=None
    segs=[]; rects=[]
    for t in toks:
        try:
            nums.append(float(t)); continue
        except ValueError: pass
        n=nums
        if t=='q': stack.append((ctm,strokec,fillc))
        elif t=='Q':
            if stack: ctm,strokec,fillc=stack.pop()
        elif t=='cm' and len(n)>=6: ctm=mul(tuple(n[-6:]),ctm)
        elif t in ('SCN','SC') and len(n)>=3: strokec=tuple(n[-3:])
        elif t in ('scn','sc') and len(n)>=3: fillc=tuple(n[-3:])
        elif t=='m' and len(n)>=2: pt=ap(ctm,n[-2],n[-1]); start=pt; cur=[pt]
        elif t=='l' and len(n)>=2: pt=ap(ctm,n[-2],n[-1]); cur.append(pt)
        elif t=='c' and len(n)>=6:
            pts=[ap(ctm,n[-6+2*i],n[-5+2*i]) for i in range(3)]
            cur.extend(pts); pt=pts[-1]
        elif t=='v' and len(n)>=4:
            pts=[ap(ctm,n[-4+2*i],n[-3+2*i]) for i in range(2)]
            cur.extend(pts); pt=pts[-1]
        elif t=='y' and len(n)>=4:
            pts=[ap(ctm,n[-4+2*i],n[-3+2*i]) for i in range(2)]
            cur.extend(pts); pt=pts[-1]
        elif t=='re' and len(n)>=4:
            x,y,w,h=n[-4:]
            corners=[ap(ctm,x,y),ap(ctm,x+w,y),ap(ctm,x+w,y+h),ap(ctm,x,y+h)]
            cur=list(corners); rects.append(dict(pts=corners,fill=fillc))
        elif t in ('S','s'):
            if len(cur)>=2: segs.append(dict(pts=cur,color=strokec))
            cur=[]
        elif t in ('f','F','f*','B','B*','b','b*'):
            if len(cur)>=3: rects.append(dict(pts=cur,fill=fillc))
            cur=[]
        elif t=='n': cur=[]
        if t not in ('m','l','c','v','y','re'): nums=[]
        else: nums=[]
    return segs,rects
if __name__=='__main__':
    segs,rects=run(sys.argv[1])
    print('segs',len(segs),'fills',len(rects))
    json.dump([dict(x0=s['pts'][0][0],y0=s['pts'][0][1],x1=s['pts'][-1][0],y1=s['pts'][-1][1],
                    color=s['color']) for s in segs],open('segs.json','w'))
    json.dump([dict(x0=min(p[0] for p in r['pts']),x1=max(p[0] for p in r['pts']),
                    y0=min(p[1] for p in r['pts']),y1=max(p[1] for p in r['pts']),fill=r['fill'])
               for r in rects],open('fills.json','w'))

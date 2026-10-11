import re,unicodedata,html,json,sys
# The word الله is drawn as a ligature glyph without a Unicode mapping; only its
# shadda mark (an isolated shadda-ligature presentation form) reaches the text
# layer, sitting in the gap between two words instead of over a letter.
ALLAH_MARKS = {'\ufc5e', '\ufc5f', '\ufc60', '\ufc61', '\ufc62', '\ufc63'}


def load(path):
    x=open(path,encoding='utf-8').read()
    raw=[(float(a),float(b),float(c),float(d),html.unescape(t)) for a,b,c,d,t in
         re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>',x)]
    gs=[]
    bases=[]
    for a,b,c,d,t in raw:
        n=unicodedata.normalize('NFKC',t)
        n=''.join(ch for ch in n if unicodedata.category(ch)!='Mn')
        if n.strip() and t not in ALLAH_MARKS:
            bases.append((a,b,c,d))
            gs.append([a,b,c,d,n])
    for a,b,c,d,t in raw:
        if t not in ALLAH_MARKS:
            continue
        covered=any(bx0-0.6<=a<=bx1+0.6 and abs(by0-b)<6 for bx0,by0,bx1,by1 in bases)
        if not covered:
            gs.append([a-0.5,b,a+0.5,d,' الله '])
    return gs
def labels(gs,gap=6.0,ytol=3.0,space=2.2):
    gs.sort(key=lambda g:(g[1],-g[0]))
    rows=[];cur=[gs[0]]
    for g in gs[1:]:
        if abs(g[1]-cur[-1][1])<=ytol: cur.append(g)
        else: rows.append(cur);cur=[g]
    rows.append(cur)
    out=[]
    for r in rows:
        r.sort(key=lambda g:-g[0])
        chunk=[r[0]]
        for prev,g in zip(r,r[1:]):
            d=prev[0]-g[2]
            if d>gap:
                out.append(chunk);chunk=[]
            elif d>space and chunk and chunk[-1][4]!=' ':
                chunk.append([g[0],g[1],g[2],g[3],' '])
            chunk.append(g)
        out.append(chunk)
    res=[]
    for ch in out:
        s=' '.join(''.join(g[4] for g in ch).split())
        if not s: continue
        ch=[g for g in ch if g[4]!=' ']
        if not ch: continue
        res.append(dict(text=s,x0=min(g[0] for g in ch),x1=max(g[2] for g in ch),
                        y0=min(g[1] for g in ch),y1=max(g[3] for g in ch)))
    res.sort(key=lambda r:(r['y0'],-r['x1']))
    return res
if __name__=='__main__':
    L=labels(load(sys.argv[1]))
    json.dump(L,open(sys.argv[2],'w'),ensure_ascii=False,indent=0)
    print(len(L))

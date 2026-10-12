/* The recorded transmission tree, shared by step 5 (the graph to confirm), step 6 and the standalone results page.

   ICMTree.make(o) -> {root, redraw, select, selectEdge, bring, setLens, setMapOnly, setFilter, setBranch}
     o.narrators {nid: {name, death:[lo,hi]|null, year?}}   o.nodes {nid: {tiers, date_source}} (optional: kinds and dates)
     o.attestations [{sanad: [nid source -> collector], collection, text?}]
     o.verdict {edgeProb, mapGraph, edges, sources: {nid: {posterior, log_Z}}, mapSource}  the model's results (step 6)
     o.onNode(nid) o.onBook(ai) o.onEdge(key) o.onOrigin()  o.prefKey  o.name(nid)  o.search (a search box)

   Two shapes: a tree of chains from their first narrator (no crossings; a narrator reached by two routes appears
   twice, hovering rings all his places), or one node per narrator under the teacher most chains give (other teachers
   dashed). Horizontal axis: time (right = older) or the place in the tree. Kinds of narrators can be hidden (their
   chains pass through, dotted); a circle closes its subtree. With a verdict: edges of the most probable tree in the
   accent colour, posterior below 0.5 red dashed, candidate sources ringed by their posterior, the unnamed origin as
   a node at the right edge. A lens colours every narrator by a value (the probability of a text state) and badges
   the books. */
(function () {
  const NS = 'http://www.w3.org/2000/svg', UNK = 'UNKNOWN_SOURCE';
  const FA = '۰۱۲۳۴۵۶۷۸۹', fa = x => String(x).replace(/[0-9]/g, d => FA[d]).replace(/\./g, '٫').replace(/-/g, '−');
  const sv = (t, a, txt) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; return e; };
  const hh = (t, a, ...kids) => { const e = document.createElement(t); for (const k in a || {}) { const v = a[k]; if (v == null || v === false) continue;
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else if (k === 'class') e.className = v; else if (v === true) e.setAttribute(k, ''); else e.setAttribute(k, v); }
    kids.flat(9).forEach(c => c != null && c !== false && e.append(c)); return e; };
  const norm = s => String(s).replace(/[يیى]/g, 'ي').replace(/[كک]/g, 'ك').replace(/[هةۀھ]/g, 'ه').replace(/[أإآ]/g, 'ا');
  const shortName = (s, n = 4) => { const w = String(s).replace(/\s*،\s*/g, ' ').split(/\s+/).filter(Boolean); return w.length > n ? w.slice(0, n).join(' ') + '…' : w.join(' '); };
  const yearTxt = d => Math.round(d[0]) === Math.round(d[1]) ? fa(Math.round(d[0])) : `${fa(Math.round(d[0]))}–${fa(Math.round(d[1]))}`;
  const pref = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } };
  const setPref = (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* a per-viewer convenience */ } };
  const CATS = [['exact', 'قطعی', 'var(--good)'], ['chosen', 'برگزیده یا استنباطی', 'var(--accent)'], ['auto', 'پیش‌فرض خودکار', 'var(--warn)'], ['name', 'فقط نام', 'var(--muted)'], ['anon', 'نامعلوم', 'var(--line)']];
  const DATES = [['dated', 'تاریخ‌دار'], ['undated', 'بی‌تاریخ']];

  function make(o) {
    const N = o.narrators, info = n => (o.nodes && o.nodes[n]) || N[n] || {}, ATT = o.attestations, V = o.verdict;
    const P = o.prefKey || 'icm.g', nm = o.name || (n => (N[n] && N[n].name) || n);
    const year = n => { const d = N[n] && N[n].death; return d ? (d[0] + d[1]) / 2 : (N[n] && N[n].year != null ? N[n].year : null); };
    const undated = n => { const ds = info(n).date_source; return ds === undefined ? !(N[n] && N[n].death) : !ds || ds === 'interpolated'; };
    const cat = n => { const t = info(n).tiers;
      if (!t || !t.length) return /^anon:/.test(n) ? 'anon' : /^(name|rel):/.test(n) ? 'name' : 'exact';
      return t.includes('exact') ? 'exact' : t.includes('chosen') || t.includes('derived') ? 'chosen' : t.includes('default') ? 'auto' : t.includes('anon') ? 'anon' : 'name'; };
    const color = n => CATS.find(c => c[0] === cat(n))[2];
    const count = {}; Object.keys(N).forEach(n => [cat(n), undated(n) ? 'undated' : 'dated'].forEach(k => { count[k] = (count[k] || 0) + 1; }));
    let mode = pref(P + 'mode', 'gen'), shape = pref(P + 'shape', 'tree'), zoom = +pref(P + 'zoom', '1') || 1, show;
    try { show = new Set(JSON.parse(pref(P + 'show', 'null')) || [...CATS, ...DATES].map(c => c[0])); } catch (_) { show = new Set([...CATS, ...DATES].map(c => c[0])); }
    const collapsed = { tree: new Set(), graph: new Set() };
    const visible = n => show.has(cat(n)) && show.has(undated(n) ? 'undated' : 'dated');
    let selected = null, selEdge = null, filter = '', branch = '', lens = null, mapOnly = false, LAST = null, SVG = null, edgeOf = new Map();
    const heads = [...new Set(ATT.map(a => a.sanad[0]))].sort((a, b) => ATT.filter(x => x.sanad[0] === b).length - ATT.filter(x => x.sanad[0] === a).length);
    const used = ai => {
      const a = ATT[ai];
      if (branch && a.sanad[0] !== branch) return false;
      if (!filter) return true;
      const q = norm(filter);
      return a.sanad.some(n => norm(nm(n)).includes(q)) || norm(a.collection || '').includes(q) || norm(a.text || '').includes(q);
    };

    function build() {
      const T = { key: '', kids: [] }, by = {}, paths = [], extra = [];
      const mkN = (key, nid) => by[key] ||= { key, nid, kids: [], atts: [], ends: [], skip: 0 };
      const seqs = ATT.map((a, ai) => { if (!used(ai)) return []; const s = []; let hid = 0; a.sanad.forEach(n => { if (!visible(n)) { hid++; return; } s.push([n, hid]); hid = 0; }); return s; });
      if (shape === 'tree') {
        seqs.forEach((s, ai) => {
          let cur = T; const p = [];                   // every unnamed narrator is his own node: two gaps are not one man
          s.forEach(([n, hid]) => { const k = cur.key + '>' + n, fresh = !by[k], x = mkN(k, n); if (fresh) { x.parent = cur; cur.kids.push(x); } x.skip = Math.max(x.skip, hid); x.atts.push(ai); p.push(cur = x); });
          if (cur !== T) cur.ends.push(ai);
          paths.push(p);
        });
      } else {
        const ec = {}, ps = {}, skip = {};
        seqs.forEach(s => s.forEach(([v, h], i) => { if (!i) return; const u = s[i - 1][0], k = u + '>' + v; ec[k] = (ec[k] || 0) + 1; (ps[v] ||= new Set()).add(u); skip[k] = Math.max(skip[k] || 0, h); }));
        const prim = {};
        Object.entries(ps).forEach(([v, us]) => { prim[v] = [...us].sort((a, b) => ec[b + '>' + v] - ec[a + '>' + v] || (year(a) ?? 1e4) - (year(b) ?? 1e4))[0]; });
        Object.keys(prim).forEach(v => { const seen = new Set([v]); for (let u = prim[v]; u != null; u = prim[u]) { if (u === v) { delete prim[v]; break; } if (seen.has(u)) break; seen.add(u); } });
        seqs.forEach(s => s.forEach(([n]) => mkN(n, n)));
        Object.values(by).forEach(x => { const u = prim[x.nid]; x.parent = u != null ? by[u] : T; x.parent.kids.push(x); if (u != null) x.skip = skip[u + '>' + x.nid]; });
        seqs.forEach((s, ai) => { s.forEach(([n]) => by[n].atts.push(ai)); if (s.length) by[s[s.length - 1][0]].ends.push(ai); paths.push(s.map(([n]) => by[n])); });
        Object.keys(ec).forEach(k => { const [u, v] = k.split('>'); if (prim[v] !== u) extra.push([by[u], by[v], skip[k]]); });
      }
      return { T, by, paths, extra };
    }
    function place(B) {
      const col = collapsed[shape], shown = [];
      let rows = 0;
      const go = (x, d) => {
        x.depth = d; shown.push(x);
        const ks = col.has(x.key) ? [] : x.kids;
        if (!ks.length) { x.row = rows++; return; }
        ks.forEach(k => go(k, d + 1));
        x.row = (ks[0].row + ks[ks.length - 1].row) / 2;
      };
      B.T.kids.forEach(r => { r.lo = rows; go(r, 0); r.hi = rows - 1; rows += 0.6; });
      return { rows, shown };
    }
    function order(B) {
      const size = x => x._n ??= x.kids.length ? x.kids.reduce((s, c) => s + size(c), 0) : 1;
      const bySize = x => { x.kids.sort((a, b) => size(b) - size(a)); x.kids.forEach(bySize); };
      bySize(B.T);
      if (!B.extra.length) return;
      /* barycentre passes: sibling subtrees move towards the rows their dashed partners sit on */
      const part = new Map(); B.extra.forEach(([u, v]) => { (part.get(u) || part.set(u, []).get(u)).push(v); (part.get(v) || part.set(v, []).get(v)).push(u); });
      for (let it = 0; it < 4; it++) {
        place(B);
        const bc = new Map(), sum = x => { let s = 0, c = 0; (part.get(x) || []).forEach(z => { s += z.row; c++; }); x.kids.forEach(k => { const r = sum(k); s += r[0]; c += r[1]; }); bc.set(x, c ? s / c : x.row); return [s, c]; };
        B.T.kids.forEach(sum);
        const re = x => { x.kids.sort((a, b) => bc.get(a) - bc.get(b)); x.kids.forEach(re); };
        re(B.T);
      }
    }

    const root = hh('div', { class: 'tree' }), tools = hh('div', { class: 'gtools small' }), box = hh('div', { class: 'gwrap' });
    const legend = hh('div', { class: 'glegend small' });
    root.append(tools, box, legend);
    let dots = {};
    function draw() {
      const B = build(); order(B); LAST = B;
      const { rows, shown } = place(B), col = collapsed[shape], time = mode === 'time';
      drawTools(B);
      if (!shown.length) { box.replaceChildren(hh('p', { class: 'muted', style: 'padding:16px' }, 'هیچ سندی با این جستجو یا این طریق نمی‌خواند.')); SVG = null; return; }
      const ys = shown.map(x => year(x.nid)).filter(v => v != null), lo = Math.floor(Math.min(...ys, 0) / 50) * 50, hi = Math.ceil(Math.max(...ys, 100) / 50) * 50;
      const maxD = Math.max(1, ...shown.map(x => x.depth));
      const unkKids = V && V.sources && V.sources[UNK] && V.mapGraph ? B.T.kids.filter(x => V.mapGraph[x.nid] === UNK && shown.includes(x)) : [];
      const UW = V && V.sources && V.sources[UNK] ? 120 : 0;
      const W = (time ? Math.max(1100, (hi - lo) * 3.4) : Math.max(900, (maxD + 1) * 160)) + UW, L = 250, R = W - 80 - UW, RH = 34, TOP = 34, H = TOP + rows * RH + 30;
      const X = x => time ? L + (R - L) * (1 - ((year(x.nid) ?? lo) - lo) / (hi - lo || 1)) : R - (R - L) * x.depth / maxD;
      const Y = x => TOP + x.row * RH;
      const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, width: Math.round(W * zoom), height: Math.round(H * zoom), class: 'gsvg' + (mapOnly ? ' maponly' : '') + (lens ? ' lensed' : ''), role: 'img', 'aria-label': 'درخت انتقال، از راست به چپ' });
      B.T.kids.forEach((r, i) => { if (i % 2) svg.append(sv('rect', { x: 0, y: TOP + (r.lo - 0.5) * RH, width: W, height: (r.hi - r.lo + 1) * RH, class: 'band' })); });
      if (time) for (let yv = Math.ceil(lo / 50) * 50; yv <= hi; yv += 50) {
        const xx = L + (R - L) * (1 - (yv - lo) / (hi - lo || 1)), major = yv % 100 === 0;
        svg.append(sv('line', { x1: xx, y1: 14, x2: xx, y2: H - 16, class: major ? 'grid' : 'grid minor' }));
        if (major) [10, H - 4].forEach(y => svg.append(sv('text', { x: xx, y, class: 'axis' }, fa(yv) + ' ق')));
      }
      const on = new Set(shown); edgeOf = new Map(); dots = {};
      const link = (x1, y1, x2, y2) => x2 < x1 - 34 && y1 !== y2   // bend next to the teacher, then run along the student's row: a tree never crosses
        ? `M${x1},${y1} C${x1 - 20},${y1} ${x1 - 20},${y2} ${x1 - 34},${y2} L${x2},${y2}` : `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
      const verdict = (u, v) => { const k = u + '>' + v, q = V && V.edgeProb ? V.edgeProb[k] : undefined; return { k, q, map: !!(V && V.mapGraph && V.mapGraph[v] === u), low: q !== undefined && q < 0.5 }; };
      if (unkKids.length || UW) {                     // the unnamed origin, above every first narrator
        const ux = W - 40, uy = unkKids.length ? unkKids.reduce((s, x) => s + Y(x), 0) / unkKids.length : TOP;
        unkKids.forEach(x => svg.append(sv('path', { d: link(ux, uy, X(x), Y(x)), class: 'e unk' })));
        const post = V.sources[UNK].posterior || 0, g = sv('g', { class: 'nd' });
        g.append(sv('title', {}, `مبدأ بی‌نام (بالاتر از همه‌ی راویان ثبت‌شده)\nاحتمال مبدأ بودن: ${fa(post.toFixed(3))}\n${fa(unkKids.length)} طریق در محتمل‌ترین درخت مستقیم به آن می‌رسند. برای توضیح بزنید.`));
        g.append(sv('circle', { cx: ux, cy: uy, r: 6 + 10 * Math.sqrt(post), class: 'unkc' + (V.mapSource === UNK ? ' mapsrc' : '') + (selected === UNK ? ' pick' : ''), 'data-origin': 1 }));
        g.append(sv('text', { x: ux, y: uy - 14 - 10 * Math.sqrt(post), class: 'lb', 'text-anchor': 'middle', direction: 'rtl', 'data-origin': 1 }, (V.mapSource === UNK ? '★ ' : '') + 'مبدأ بی‌نام'));
        svg.append(g);
      }
      shown.forEach(x => {
        if (x.parent === B.T) return;
        const d = link(X(x.parent), Y(x.parent), X(x), Y(x)), vd = verdict(x.parent.nid, x.nid);
        const p = sv('path', { d, class: 'e' + (x.skip ? ' skip' : '') + (vd.map ? ' map' : '') + (vd.low ? ' low' : '') + (selEdge === vd.k ? ' sel' : ''), 'stroke-width': 1 + Math.log2(x.atts.length) });
        if (vd.q !== undefined) p.setAttribute('stroke-opacity', (0.25 + 0.7 * vd.q).toFixed(2));
        edgeOf.set(x, p); svg.append(p);
        if (!x.skip && V && V.edges && V.edges[vd.k]) {
          const h = sv('path', { d, class: 'ehit', 'data-edge': vd.k });
          h.append(sv('title', {}, `${nm(x.parent.nid)} ← ${nm(x.nid)}\nاحتمال پسین ${vd.q === undefined ? '—' : fa(vd.q.toFixed(3))}${vd.map ? ' · در محتمل‌ترین درخت' : ''}\nبرای جزئیات یال بزنید.`));
          svg.append(h);
        } else if (x.skip) p.append(sv('title', {}, `${fa(x.skip)} راوی پنهان میان این دو`));
      });
      B.extra.forEach(([u, v, s]) => { if (on.has(u) && on.has(v)) { const vd = verdict(u.nid, v.nid); svg.append(sv('path', { d: link(X(u), Y(u), X(v), Y(v)), class: 'e sec' + (s ? ' skip' : '') + (vd.map ? ' map' : '') })); } });
      const hit = n => filter && norm(nm(n)).includes(norm(filter));
      shown.forEach(x => {
        const n = x.nid, g = sv('g', { class: 'nd' }), cx = X(x), cy = Y(x), closed = col.has(x.key) && x.kids.length, leaf = closed || !x.kids.length, d = N[n] && N[n].death;
        const below = x.atts.length - x.ends.length, src = V && V.sources && V.sources[n], lv = lens && lens.fill ? lens.fill(n) : undefined;
        g.append(sv('title', {}, `${nm(n)}\n${d ? (undated(n) ? 'حدود ' : 'ف ') + yearTxt(d) : 'بی‌تاریخ'}\n${fa(x.atts.length)} سند` + (src ? `\nنامزد مبدأ: احتمال ${fa((src.posterior || 0).toFixed(3))}` : '') +
          (lens && lens.tip ? '\n' + lens.tip(n) : '') + (x.kids.length ? `\nدایره را بزنید تا زیرشاخه ${closed ? 'باز' : 'بسته'} شود؛ نام را بزنید برای جزئیات.` : '\nبرای جزئیات بزنید.')));
        if (src) g.append(sv('circle', { cx, cy, r: 8 + 10 * Math.sqrt(src.posterior || 0), class: 'srcring' + (V.mapSource === n ? ' mapsrc' : '') }));
        const c = sv('circle', { cx, cy, r: cat(n) === 'anon' ? 3.5 : 5, class: (undated(n) ? 'und' : '') + (closed ? ' closed' : '') + (selected === n ? ' pick' : '') + (lv === null ? ' nolens' : ''),
          style: `--c:${lv ? lv : color(n)}`, 'data-tw': x.key });
        (dots[n] ||= []).push(c);
        const name = (V && V.mapSource === n ? '★ ' : '') + shortName(nm(n), leaf ? 5 : 4), cls = 'lb' + (selected === n ? ' sel' : '') + (hit(n) ? ' hit' : '');
        let t;
        if (leaf) {
          t = sv('text', { x: cx - 9, y: cy + 4, class: cls, 'text-anchor': 'start', direction: 'rtl' });
          t.append(sv('tspan', { 'data-id': n }, name + (closed ? `  (+${fa(below)} سند)` : '')));
          x.ends.forEach((ai, j) => {
            t.append(sv('tspan', { class: 'sepr' }, j ? '، ' : '  ·  '));
            t.append(sv('tspan', { class: 'book' + (selected === 'a' + ai ? ' sel' : ''), 'data-matn': ai }, shortName(ATT[ai].collection || '—', 3)));
            const bd = lens && lens.badge ? lens.badge(ai) : null;
            if (bd) t.append(sv('tspan', { class: 'badge', style: `fill:${bd.c}` }, ` [${bd.t}]`));
          });
        } else t = sv('text', { x: cx, y: time && x.depth % 2 ? cy + 17 : cy - 9, class: cls, 'text-anchor': 'middle', direction: 'rtl', 'data-id': n }, name);
        g.append(c, t);
        g.addEventListener('mouseenter', () => hot(x.atts, n)); g.addEventListener('mouseleave', () => hot(null));
        svg.append(g);
      });
      SVG = svg;
      box.replaceChildren(svg);
      hot(null);
      drawLegend();
    }
    /* the chains through the hovered node, else through the selection, stand out; a hovered narrator's other places are ringed */
    function hot(ais, twin) {
      if (!SVG) return;
      if (!ais) ais = selected === null || selected === UNK ? [] : /^a\d+$/.test(selected) ? [+selected.slice(1)] : ATT.map((a, i) => i).filter(i => ATT[i].sanad.includes(selected));
      SVG.classList.toggle('dim', ais.length > 0);
      edgeOf.forEach(p => p.classList.remove('hot'));
      ais.forEach(ai => (LAST.paths[ai] || []).forEach(y => { const e = edgeOf.get(y); if (e) e.classList.add('hot'); }));
      SVG.querySelectorAll('circle.twin').forEach(c => c.classList.remove('twin'));
      if (twin && (dots[twin] || []).length > 1) dots[twin].forEach(c => c.classList.add('twin'));
    }
    const keep = f => { const l = box.scrollLeft, t = box.scrollTop; f(); box.scrollLeft = l; box.scrollTop = t; };
    function reveal(q) {
      const e = box.querySelector(q); if (!e) return;
      const b = box.getBoundingClientRect(), r = e.getBoundingClientRect();
      box.scrollLeft += r.left + r.width / 2 - (b.left + b.width / 2); box.scrollTop += r.top + r.height / 2 - (b.top + b.height / 2);
    }
    /* open the closed subtrees above an attestation (or a narrator's first place) and bring it into view */
    function bring(id) {
      if (!LAST || id === UNK) return;
      const isM = /^a\d+$/.test(id);
      let ai = isM ? +id.slice(1) : ATT.findIndex((a, i) => (LAST.paths[i] || []).some(x => x.nid === id));
      if (ai >= 0 && !(LAST.paths[ai] || []).length && (branch || filter)) { branch = ''; filter = ''; draw(); ai = isM ? ai : ATT.findIndex((a, i) => (LAST.paths[i] || []).some(x => x.nid === id)); }
      if (ai < 0 || !(LAST.paths[ai] || []).length) return;
      const col = collapsed[shape];
      for (const x of LAST.paths[ai]) { if (!isM && x.nid === id) break; col.delete(x.key); }
      draw();
      reveal(isM ? `[data-matn="${ai}"]` : `[data-id="${CSS.escape(id)}"]`);
    }
    function drawLegend() {
      const it = (cls, t) => hh('span', {}, hh('i', { class: cls }), t);
      legend.replaceChildren(...[
        V ? it('map', 'یال در محتمل‌ترین درخت') : null, V ? it('low', 'احتمال پسین زیر ۰٫۵') : null, it('skip', 'راوی پنهان میان دو نام'),
        shape === 'graph' ? it('sec', 'استاد دیگر (خط‌چین)') : null, V && V.sources ? hh('span', {}, hh('i', { class: 'ring' }), 'نامزد مبدأ (اندازه = احتمال)، ★ محتمل‌ترین') : null,
        hh('span', {}, hh('i', { class: 'bk' }), 'نام کتاب (متن)'), hh('span', { class: 'muted' }, 'ضخامت یال = شمار سندها · دایره‌ی توخالی = بی‌تاریخ'),
        lens && lens.legend ? hh('span', { class: 'lensl' }, lens.legend) : null].filter(Boolean));
    }
    function drawTools(B) {
      const seg = (cur, opts, set) => hh('span', { class: 'seg' }, opts.map(([v, t]) => hh('button', { type: 'button', class: 'small' + (cur === v ? ' on' : ''), onclick: () => set(v) }, t)));
      const chip = ([k, t, c]) => hh('label', { class: 'fchip' + (show.has(k) ? ' on' : '') }, hh('input', { type: 'checkbox', checked: show.has(k), onchange: ev => {
        ev.target.checked ? show.add(k) : show.delete(k); setPref(P + 'show', JSON.stringify([...show])); setTimeout(() => keep(draw)); } }),
        k === 'undated' ? hh('i', { class: 'und' }) : c ? hh('i', { style: `background:${c}` }) : null, t, hh('span', { class: 'muted' }, ' ' + fa(count[k] || 0)));
      const depthOf = x => { let d = 0; for (let p = x.parent; p && p.key !== ''; p = p.parent) d++; return d; };
      const depthSel = hh('select', { title: 'بستن زیرشاخه‌ها', onchange: ev => { const k = +ev.target.value; collapsed[shape] = new Set(k ? Object.values(B.by).filter(x => x.kids.length && depthOf(x) === k - 1).map(x => x.key) : []); ev.target.value = ''; draw(); } },
        hh('option', { value: '' }, 'بستن زیرشاخه‌ها…'), hh('option', { value: '0' }, 'همه باز'), [1, 2, 3, 4, 5, 6].map(k => hh('option', { value: k }, `نمایش تا جای ${fa(k)} در سند`)));
      const zf = f => () => { zoom = Math.max(0.3, Math.min(2.5, f(zoom))); setPref(P + 'zoom', zoom); keep(draw); };
      const hasTiers = Object.keys(N).some(n => (info(n).tiers || []).length);
      const q = o.search === false ? null : hh('input', { type: 'search', value: filter, placeholder: 'جستجوی راوی، کتاب یا عبارت…', class: 'gq', oninput: ev => { filter = ev.target.value.trim(); clearTimeout(q._t); q._t = setTimeout(() => { draw(); const n = tools.querySelector('.gq'); if (n) { n.focus(); n.setSelectionRange(n.value.length, n.value.length); } }, 250); } });
      const br = heads.length > 1 && o.branches !== false ? hh('select', { class: 'gbr', title: 'نمایش یک طریق', onchange: ev => { branch = ev.target.value; draw(); } },
        hh('option', { value: '' }, 'همه‌ی طریق‌ها'), heads.map(h => hh('option', { value: h, selected: branch === h }, `${shortName(nm(h), 5)} (${fa(ATT.filter(a => a.sanad[0] === h).length)})`))) : null;
      tools.replaceChildren(
        hh('div', { class: 'toolbar' }, q, br, 'محور افقی: ', seg(mode, [['time', 'زمان'], ['gen', 'جای در درخت']], v => { mode = v; setPref(P + 'mode', v); draw(); }),
          ' شکل: ', seg(shape, [['tree', 'درخت بی‌تقاطع'], ['graph', 'هر راوی یک بار']], v => { shape = v; setPref(P + 'shape', v); draw(); }),
          V ? hh('label', { class: 'small', title: 'یال‌های بیرون از محتمل‌ترین درخت کم‌رنگ شوند' }, hh('input', { type: 'checkbox', checked: mapOnly, onchange: ev => { mapOnly = ev.target.checked; keep(draw); } }), ' فقط درخت بیشینه') : null,
          ' ', depthSel, ' ',
          hh('span', { class: 'seg' }, hh('button', { type: 'button', class: 'small', title: 'کوچک‌نمایی', onclick: zf(z => z / 1.25) }, '−'),
            hh('button', { type: 'button', class: 'small', title: 'پهنای کل درخت در کادر', onclick: () => { const vb = SVG && SVG.viewBox.baseVal; if (vb) zf(() => (box.clientWidth - 4) / vb.width)(); } }, 'کل درخت'),
            hh('button', { type: 'button', class: 'small', title: 'بزرگ‌نمایی', onclick: zf(z => z * 1.25) }, '+')),
          o.extraTools ? o.extraTools() : null),
        hh('div', { class: 'toolbar' }, 'نمایش: ', hasTiers ? [CATS.map(chip), hh('span', { class: 'muted' }, ' | ')] : null, DATES.map(chip)));
    }
    box.addEventListener('click', e => {
      if (e.target.closest('[data-origin]')) { if (o.onOrigin) o.onOrigin(); return; }
      const m = e.target.closest('[data-matn]');
      if (m) { if (o.onBook) o.onBook(+m.getAttribute('data-matn')); return; }
      const ed = e.target.closest('[data-edge]');
      if (ed) { if (o.onEdge) o.onEdge(ed.getAttribute('data-edge')); return; }
      const tw = e.target.closest('circle[data-tw]');
      if (tw) {
        const key = tw.getAttribute('data-tw'), x = LAST.by[key];
        if (x && x.kids.length) { const col = collapsed[shape]; col.has(key) ? col.delete(key) : col.add(key); keep(draw); }
        else if (x && o.onNode) o.onNode(x.nid);
        return;
      }
      const t = e.target.closest('[data-id]');
      if (t && o.onNode) o.onNode(t.getAttribute('data-id'));
    });
    box.addEventListener('wheel', e => {
      if (!(e.ctrlKey || e.metaKey) || !SVG) return;
      e.preventDefault();
      zoom = Math.max(0.3, Math.min(2.5, zoom * (e.deltaY < 0 ? 1.12 : 0.89))); setPref(P + 'zoom', zoom); keep(draw);
    }, { passive: false });
    draw();
    return {
      root, heads, redraw: () => keep(draw),
      select(id) { selected = id; selEdge = null; keep(draw); }, selectEdge(k) { selEdge = k; selected = null; keep(draw); },
      bring, setLens(l) { lens = l; keep(draw); }, setMapOnly(b) { mapOnly = b; keep(draw); },
      setFilter(q) { filter = q; draw(); }, setBranch(h) { branch = h; draw(); },
    };
  }
  window.ICMTree = { make, CATS };
})();

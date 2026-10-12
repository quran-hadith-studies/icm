const D = JSON.parse(document.getElementById('bundle').textContent);
const N = D.narrators, E = D.edges || {}, EP = D.graph_posterior.edge_prob || {}, MAPG = D.graph_posterior.map_graph || {};
const ROOT = '__ROOT__', UNK = 'UNKNOWN_SOURCE';
const sp = D.source_posterior;

/* ---------- Persian numerals and formatting ---------- */
const FA = '۰۱۲۳۴۵۶۷۸۹';
const fa = s => String(s).replace(/[0-9]/g, d => FA[d]).replace(/\./g, '٫').replace(/-/g, '−');
const NEG = Number.NEGATIVE_INFINITY;
const fmt = (x, d = 3) => (x === null || x === undefined || Number.isNaN(x)) ? '—'
  : (!Number.isFinite(x) ? (x < 0 ? '−∞' : '∞') : (x < 0 ? '\u2066' + fa(Number(x).toFixed(d)) + '\u2069' : fa(Number(x).toFixed(d))));
const pct = x => x === null || x === undefined ? '—' : fa((100 * x).toFixed(1)) + '٪';
function sci(x) {
  if (x === null || x === undefined) return '—';
  if (x === 0) return '۰';
  if (x >= 0.001) return fa(x.toFixed(3));
  const e = Math.floor(Math.log10(x)), m = x / Math.pow(10, e);
  return `<span dir="ltr" class="num">${fa(m.toFixed(1))}×۱۰<sup>${fa(e)}</sup></span>`;
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const lz = s => sp[s].log_Z === null ? NEG : sp[s].log_Z;
const UNK_NAME = 'مبدأ بی‌نام (بالاتر از همه‌ی راویان ثبت‌شده)';
const srcName = s => s === UNK ? UNK_NAME : (N[s] ? N[s].name : s);
const nm = id => id === ROOT ? (D.heading || D.family || 'حدیث') : id === UNK ? UNK_NAME : String(id).startsWith(UNK + '#') ? 'راوی بی‌نامِ میانیِ ویژه‌ی همین راوی' : (N[id] ? N[id].name : id);

/* ---------- variant units: Persian names, colours, and how to find them in a text ---------- */
const CLAUSES = {
  zaid_fi_kitab:      {fa: 'الزائد في كتاب الله', gloss: 'کسی که به کتاب خدا می‌افزاید', c: '--c1', keys: ['الزائد في كتاب', 'الزايد في كتاب', 'المغير لكتاب']},
  mukaddhib_bi_qadar: {fa: 'المكذّب بقدر الله', gloss: 'تکذیب‌کننده‌ی قدر الهی', c: '--c2', keys: ['المكذب بقدر']},
  mutasallit_jabarut: {fa: 'المتسلّط بالجبروت', gloss: 'چیره‌شونده با زور برای خوار کردن عزیزان خدا', c: '--c3', keys: ['المتسلط', 'المسلط']},
  mustahill_maharim:  {fa: 'المستحلّ لمحارم الله', gloss: 'حلال‌شمارنده‌ی حرام‌های خدا', c: '--c4', keys: ['المستحل محارم', 'المستحل لمحارم']},
  tarik_sunnati:      {fa: 'التارك لسنّتي', gloss: 'وانهنده‌ی سنت پیامبر', c: '--c5', keys: ['التارك لسنتي', 'التارك للسنة', 'التارك لسنة']},
  mustahill_itra:     {fa: 'المستحلّ من عترتي', gloss: 'حلال‌شمارنده‌ی آنچه خدا درباره‌ی عترت حرام کرده', c: '--c6', keys: ['المستحل من عترتي', 'المستحل لحرم', 'من عترتي ما حرم']},
  mutakabbir:         {fa: 'المتكبّر على عباد الله', gloss: 'گردن‌فراز بر بندگان خدا', c: '--c7', keys: ['المتكبر على عباد']},
  count_word:         {fa: 'شمار (سبعة / ستة / …)', gloss: 'عددی که حدیث با آن آغاز می‌شود', c: '--c8', keys: ['سبعة', 'ستة', 'خمسة', 'اربعة', 'أربعة']},
};
const COUNT_FA = {"sab'a": 'سبعة (هفت)', 'sitta': 'ستة (شش)', 'khamsa': 'خمسة (پنج)', "arba'a": 'أربعة (چهار)'};
const vName = spec => (CLAUSES[spec.name] || {}).fa || spec.name;
const vColor = spec => `var(${(CLAUSES[spec.name] || {}).c || '--accent'})`;
const CLS = {'ي': '[يیى]', 'ى': '[يیى]', 'ی': '[يیى]', 'ك': '[كک]', 'ک': '[كک]', 'ه': '[هةۀھ]', 'ة': '[هةۀھ]',
             'ا': '[اأإآ]', 'أ': '[اأإآ]', 'إ': '[اأإآ]', 'آ': '[اأإآ]'};
const keyRe = k => k.split('').filter(ch => ch !== ' ').map(ch => CLS[ch] || ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s?');
function highlight(text) {
  const spans = [];
  for (const [name, c] of Object.entries(CLAUSES)) {
    for (const k of c.keys) {
      const re = new RegExp(keyRe(k), 'g');
      let m;
      while ((m = re.exec(text))) spans.push([m.index, m.index + m[0].length, c.c, c.fa]);
    }
  }
  spans.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  let out = '', at = 0;
  for (const [s, e, col, title] of spans) {
    if (s < at) continue;
    out += esc(text.slice(at, s)) + `<mark class="cl" style="--cc:var(${col})" title="${esc(title)}">${esc(text.slice(s, e))}</mark>`;
    at = e;
  }
  return out + esc(text.slice(at));
}

/* ---------- the recorded chains (source -> collector, as each attestation gives it) ---------- */
const ATT = D.attestations;
const teachersOf = {}, studentsOf = {}, witnesses = {[ROOT]: ATT.length};
ATT.forEach(a => {
  new Set(a.sanad).forEach(n => { witnesses[n] = (witnesses[n] || 0) + 1; });
  a.sanad.forEach((v, i) => { if (!i) return; const u = a.sanad[i - 1]; (teachersOf[v] ||= new Set()).add(u); (studentsOf[u] ||= new Set()).add(v); });
});
const heads = [...new Set(ATT.map(a => a.sanad[0]))].sort((a, b) => ATT.filter(x => x.sanad[0] === b).length - ATT.filter(x => x.sanad[0] === a).length);
const edgeKey = (p, c) => p + '>' + c;

/* ---------- header ---------- */
const best = D.map_source;
const ranked = Object.keys(sp).sort((a, b) => lz(b) - lz(a));
const runner = ranked.find(s => s !== best);
const ks = D.kg_stats || {};
document.getElementById('genline').textContent =
  `${fa(D.attestations.length)} شاهد (متن) · ${fa(Object.keys(N).length)} راوی · ${fa(D.variants.length)} واحد اختلاف متن · تاریخ اجرا ${fa(D.generated)}`;
const CHIPS = [
  ['محتمل‌ترین مبدأ', `<span class="v ar">${esc(srcName(best))}</span>${best === UNK ? '<button class="lnk small" data-showtree="' + UNK + '">این یعنی چه؟</button>' : ''}`, 'راوی (یا منبعی) که بیشترین احتمال پسین را دارد که قدیمی‌ترین منبع قابل‌بازیابی همه‌ی این روایت‌ها باشد.'],
  ['احتمال پسین آن', `<span class="v num">${sci(sp[best].posterior)}</span>`, 'P(S=s | متن‌ها، سندها، رجال): احتمال مبدأ بودن، پس از دیدن همه‌ی داده‌ها و با درنظرگرفتن همه‌ی درخت‌های ممکن.'],
  ['لگاریتم عامل بیز در برابر رقیب', `<span class="v num">${fmt(lz(best) - lz(runner), 1)}</span>`, `تفاضل log Z با رقیب بعدی (${srcName(runner)}). عدد ۵ یعنی داده حدود ۱۵۰ برابر بیشتر از این گزینه پشتیبانی می‌کند؛ بیش از ۱۰ قاطع است.`],
  ['سهم محتمل‌ترین درخت', `<span class="v num">${pct(D.graph_posterior.map_mass)}</span>`, 'چه کسری از احتمال پسین (برای مبدأ برتر) روی یک درخت انتقال واحد، یعنی درخت بیشینه‌ی پسین (MAP)، متمرکز است.'],
  ['درخت‌های متمایز در نمونه', `<span class="v num">${fa(D.graph_posterior.n_distinct)} از ${fa(D.graph_posterior.n_particles)}</span>`, 'چند درخت متفاوت در میان ذرات نمونه‌برداری (SMC) باقی مانده‌اند. عدد کم یعنی پسین متمرکز است، ولی برآورد سهم درخت‌های رقیب نیز نامطمئن‌تر است.'],
  ['راویان پیوندخورده با کتب رجال', `<span class="v num">${fa(ks.linked ?? 0)} از ${fa(ks.total ?? 0)}</span>`, `راویانی که با اطمینان زیاد یا متوسط به زندگی‌نامه‌ای در پایگاه رجالی (${D.kg_source || '—'}) پیوند خورده‌اند؛ از ${fa(ks.edges_linkable ?? 0)} یال قابل‌بررسی، ${fa(ks.edges_attested ?? 0)} یال در اسناد مستقل آن پایگاه نیز آمده است.`],
];
document.getElementById('verdicts').innerHTML = CHIPS.map(([k, v, h]) =>
  `<div class="chip" title="${esc(h)}"><span class="k">${k}</span>${v}</div>`).join('');
document.getElementById('explainlist').innerHTML = CHIPS.map(([k, v, h]) => `<dt>${k}</dt><dd>${esc(h)}</dd>`).join('');

/* ---------- tree drawing: the chains as written, source on the right, as in step 5 of the wizard ----------
   Two shapes: a tree of chains from their first narrator (no crossings; a narrator reached by two routes appears twice),
   or one node per narrator under the teacher most chains give (other teachers dashed). Horizontal axis: time (right =
   older) or the place in the tree. Kinds of narrators can be hidden (their chains pass through, dotted); a subtree can
   be closed. Edges carry the model's verdict: blue = in the most probable tree, red dashes = posterior below 0.5. */
const BOX = document.getElementById('treebox'), TOOLS = document.getElementById('treetools');
const NS = 'http://www.w3.org/2000/svg';
const el = (n, a, t) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); if (t !== undefined && t !== null) e.textContent = t; return e; };
const pref = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } };
const setPref = (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* per-viewer convenience only */ } };
const isMatn = id => /^a\d+$/.test(id);
const normQ = s => String(s).replace(/[يیى]/g, 'ي').replace(/[كک]/g, 'ك').replace(/[هةۀھ]/g, 'ه').replace(/[أإآ]/g, 'ا');
const shortName = (s, n = 4) => { const w = String(s).replace(/\s*،\s*/g, ' ').split(/\s+/).filter(Boolean); return w.length > n ? w.slice(0, n).join(' ') + '…' : w.join(' '); };
const year = n => N[n] && N[n].death ? (N[n].death[0] + N[n].death[1]) / 2 : (N[n] ? N[n].year : null);
const undated = n => !N[n].date_source || N[n].date_source === 'interpolated';
const cat = n => {
  const t = N[n].tiers;
  if (!t) return /^anon:/.test(n) ? 'anon' : /^(name|rel):/.test(n) ? 'name' : 'exact';
  return t.includes('exact') ? 'exact' : t.includes('chosen') || t.includes('derived') ? 'chosen' : t.includes('default') ? 'auto' : t.includes('anon') ? 'anon' : 'name';
};
const CATS = [['exact', 'قطعی', 'var(--good)'], ['chosen', 'برگزیده یا استنباطی', 'var(--accent)'], ['auto', 'پیش‌فرض خودکار', 'var(--warn)'], ['name', 'فقط نام', 'var(--ink-faint)'], ['anon', 'نامعلوم', 'var(--line)']];
const DATES = [['dated', 'تاریخ‌دار'], ['undated', 'بی‌تاریخ']];
const color = n => CATS.find(c => c[0] === cat(n))[2];
const kindCount = {}; Object.keys(N).forEach(n => [cat(n), undated(n) ? 'undated' : 'dated'].forEach(k => { kindCount[k] = (kindCount[k] || 0) + 1; }));
let mode = pref('icmv.gmode', 'gen'), shape = pref('icmv.gshape', 'tree'), zoom = +pref('icmv.gzoom', '1') || 1, kinds;
try { kinds = new Set(JSON.parse(pref('icmv.gshow', 'null')) || [...CATS, ...DATES].map(c => c[0])); } catch (_) { kinds = new Set([...CATS, ...DATES].map(c => c[0])); }
const collapsed = {tree: new Set(), graph: new Set()};
const visible = n => kinds.has(cat(n)) && kinds.has(undated(n) ? 'undated' : 'dated');
let selected = null, selEdge = null, filter = '', branchFilter = '', LAST = null;
const used = ai => {
  const a = ATT[ai];
  if (branchFilter && a.sanad[0] !== branchFilter) return false;
  if (!filter) return true;
  const q = normQ(filter);
  return a.sanad.some(n => normQ(nm(n)).includes(q)) || normQ(a.collection || '').includes(q) || normQ(a.text || '').includes(q);
};

function build() {
  const T = {key: '', kids: []}, by = {}, paths = [], extra = [];
  const mkN = (key, nid) => by[key] ||= {key, nid, kids: [], atts: [], ends: [], skip: 0};
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
  return {T, by, paths, extra};
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
  return {rows, shown};
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

let SVGEL = null, edgeOf = null, dotsOf = null;
function drawTree() {
  const B = build(); order(B); LAST = B;
  const {rows, shown} = place(B), col = collapsed[shape], time = mode === 'time';
  if (!shown.length) { BOX.innerHTML = '<p class="empty" style="padding:16px">هیچ سندی با این جست‌وجو یا این طریق نمی‌خواند.</p>'; drawTools(B); return; }
  const ys = shown.map(x => year(x.nid)).filter(v => v != null), lo = Math.floor(Math.min(...ys, 0) / 50) * 50, hi = Math.ceil(Math.max(...ys, 100) / 50) * 50;
  const maxD = Math.max(1, ...shown.map(x => x.depth));
  const W = time ? Math.max(1100, (hi - lo) * 3.4) : Math.max(900, (maxD + 1) * 160), L = 250, R = W - 80, RH = 34, TOP = 34, H = TOP + rows * RH + 30;
  const X = x => time ? L + (R - L) * (1 - ((year(x.nid) ?? lo) - lo) / (hi - lo || 1)) : R - (R - L) * x.depth / maxD;
  const Y = x => TOP + x.row * RH;
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, width: Math.round(W * zoom), height: Math.round(H * zoom), class: 'gsvg', role: 'img', 'aria-label': 'درخت انتقال، از راست به چپ'});
  B.T.kids.forEach((r, i) => { if (i % 2) svg.append(el('rect', {x: 0, y: TOP + (r.lo - 0.5) * RH, width: W, height: (r.hi - r.lo + 1) * RH, class: 'band'})); });
  if (time) for (let yv = Math.ceil(lo / 50) * 50; yv <= hi; yv += 50) {
    const xx = L + (R - L) * (1 - (yv - lo) / (hi - lo || 1)), major = yv % 100 === 0;
    svg.append(el('line', {x1: xx, y1: 14, x2: xx, y2: H - 16, class: major ? 'grid' : 'grid minor'}));
    if (major) [10, H - 4].forEach(y => svg.append(el('text', {x: xx, y, class: 'axis'}, fa(yv) + ' ق')));
  }
  const on = new Set(shown); edgeOf = new Map(); dotsOf = {};
  const link = (x1, y1, x2, y2) => x2 < x1 - 34 && y1 !== y2   // bend next to the teacher, then run along the student's row: a tree never crosses
    ? `M${x1},${y1} C${x1 - 20},${y1} ${x1 - 20},${y2} ${x1 - 34},${y2} L${x2},${y2}` : `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
  const verdict = (u, v) => { const k = edgeKey(u, v), q = EP[k]; return {k, q, map: MAPG[v] === u, low: q !== undefined && q < 0.5}; };
  shown.forEach(x => {
    if (x.parent === B.T) return;
    const d = link(X(x.parent), Y(x.parent), X(x), Y(x)), vd = verdict(x.parent.nid, x.nid);
    const p = el('path', {d, class: 'e' + (x.skip ? ' skip' : '') + (vd.map ? ' map' : '') + (vd.low ? ' low' : '') + (selEdge === vd.k ? ' sel' : ''), 'stroke-width': 1 + Math.log2(x.atts.length)});
    edgeOf.set(x, p); svg.append(p);
    if (!x.skip && E[vd.k]) {
      const h = el('path', {d, class: 'ehit', 'data-edge': vd.k});
      h.append(el('title', {}, `${nm(x.parent.nid)} ← ${nm(x.nid)}\nاحتمال پسین ${vd.q === undefined ? '—' : fa(vd.q.toFixed(3))}${vd.map ? ' · در محتمل‌ترین درخت' : ''}\nبرای جزئیات یال بزنید.`));
      svg.append(h);
    } else if (x.skip) p.append(el('title', {}, `${fa(x.skip)} راوی پنهان میان این دو`));
  });
  B.extra.forEach(([u, v, s]) => { if (on.has(u) && on.has(v)) { const vd = verdict(u.nid, v.nid); svg.append(el('path', {d: link(X(u), Y(u), X(v), Y(v)), class: 'e sec' + (s ? ' skip' : '') + (vd.map ? ' map' : '')})); } });
  const hit = n => filter && normQ(nm(n)).includes(normQ(filter));
  shown.forEach(x => {
    const n = x.nid, g = el('g', {class: 'nd'}), cx = X(x), cy = Y(x), closed = col.has(x.key) && x.kids.length, leaf = closed || !x.kids.length, d = N[n].death;
    const below = x.atts.length - x.ends.length;
    g.append(el('title', {}, `${nm(n)}\n${d ? (undated(n) ? 'حدود ' : 'ف ') + fa(Math.round(d[0])) + (Math.round(d[0]) !== Math.round(d[1]) ? '–' + fa(Math.round(d[1])) : '') + ' ق' : 'بی‌تاریخ'}\n${fa(x.atts.length)} سند` +
      (x.kids.length ? `\nدایره را بزنید تا زیرشاخه ${closed ? 'باز' : 'بسته'} شود؛ نام را بزنید برای جزئیات.` : '\nبرای جزئیات بزنید.')));
    const c = el('circle', {cx, cy, r: cat(n) === 'anon' ? 3.5 : 5, class: (undated(n) ? 'und' : '') + (closed ? ' closed' : '') + (selected === n ? ' pick' : ''), style: `--c:${color(n)}`, 'data-tw': x.key});
    (dotsOf[n] ||= []).push(c);
    const name = shortName(nm(n), leaf ? 5 : 4), cls = 'lb' + (selected === n ? ' sel' : '') + (hit(n) ? ' hit' : '');
    let t;
    if (leaf) {
      t = el('text', {x: cx - 9, y: cy + 4, class: cls, 'text-anchor': 'start', direction: 'rtl'});
      t.append(el('tspan', {'data-id': n}, name + (closed ? `  (+${fa(below)} سند)` : '')));
      x.ends.forEach((ai, j) => {
        t.append(el('tspan', {class: 'sepr'}, j ? '، ' : '  ·  '));
        t.append(el('tspan', {class: 'book' + (selected === 'a' + ai ? ' sel' : ''), 'data-matn': ai}, shortName(ATT[ai].collection || '—', 3)));
      });
    } else t = el('text', {x: cx, y: time && x.depth % 2 ? cy + 17 : cy - 9, class: cls, 'text-anchor': 'middle', direction: 'rtl', 'data-id': n}, name);
    g.append(c, t);
    g.addEventListener('mouseenter', () => hot(x.atts)); g.addEventListener('mouseleave', () => hot(null));
    svg.append(g);
  });
  SVGEL = svg;
  BOX.replaceChildren(svg);
  hot(null);
  drawTools(B);
}
/* the chains through the hovered node, else through the selection, stand out */
function hot(ais) {
  if (!SVGEL) return;
  if (!ais) ais = selected === null ? [] : isMatn(selected) ? [+selected.slice(1)] : ATT.map((a, i) => i).filter(i => ATT[i].sanad.includes(selected));
  SVGEL.classList.toggle('dim', ais.length > 0);
  edgeOf.forEach(p => p.classList.remove('hot'));
  ais.forEach(ai => (LAST.paths[ai] || []).forEach(y => { const e = edgeOf.get(y); if (e) e.classList.add('hot'); }));
}
const keep = f => { const l = BOX.scrollLeft, t = BOX.scrollTop; f(); BOX.scrollLeft = l; BOX.scrollTop = t; };
function reveal(q) {
  const e = BOX.querySelector(q); if (!e) return;
  const b = BOX.getBoundingClientRect(), r = e.getBoundingClientRect();
  BOX.scrollLeft += r.left + r.width / 2 - (b.left + b.width / 2); BOX.scrollTop += r.top + r.height / 2 - (b.top + b.height / 2);
}
/* open the closed subtrees above an attestation (or a narrator's first place) and bring it into view */
function bring(id) {
  if (!LAST) return;
  const ai = isMatn(id) ? +id.slice(1) : ATT.findIndex((a, i) => a.sanad.includes(id) && (LAST.paths[i] || []).some(x => x.nid === id));
  if (ai < 0 || !(LAST.paths[ai] || []).length) return;
  const col = collapsed[shape];
  for (const x of LAST.paths[ai]) { if (!isMatn(id) && x.nid === id) break; col.delete(x.key); }
  drawTree();
  reveal(isMatn(id) ? `[data-matn="${ai}"]` : `[data-id="${CSS.escape(id)}"]`);
}

function drawTools(B) {
  const seg = (cur, opts, set) => `<span class="seg">${opts.map(([v, t]) => `<button type="button" data-seg="${set}" data-v="${v}" class="${cur === v ? 'on' : ''}">${t}</button>`).join('')}</span>`;
  const chip = ([k, t, c]) => `<label class="fchip${kinds.has(k) ? ' on' : ''}"><input type="checkbox" data-show="${k}"${kinds.has(k) ? ' checked' : ''}>` +
    (k === 'undated' ? '<i class="und"></i>' : c ? `<i style="background:${c}"></i>` : '') + `${t}<span class="muted"> ${fa(kindCount[k] || 0)}</span></label>`;
  const hasTiers = Object.values(N).some(n => n.tiers);
  TOOLS.innerHTML = `<div class="toolbar">محور افقی: ${seg(mode, [['time', 'زمان'], ['gen', 'جای در درخت']], 'mode')}
      شکل: ${seg(shape, [['tree', 'درخت بی‌تقاطع'], ['graph', 'هر راوی یک بار']], 'shape')}
      <select id="depthsel" aria-label="بستن زیرشاخه‌ها"><option value="">بستن زیرشاخه‌ها…</option><option value="0">همه باز</option>${[1, 2, 3, 4, 5, 6].map(k => `<option value="${k}">نمایش تا جای ${fa(k)} در سند</option>`).join('')}</select>
      <span class="seg"><button type="button" id="zout" title="کوچک‌نمایی" aria-label="کوچک‌نمایی">−</button><button type="button" id="zfit" title="پهنای کل درخت در کادر">کل درخت</button><button type="button" id="zin" title="بزرگ‌نمایی" aria-label="بزرگ‌نمایی">+</button></span></div>
    <div class="toolbar">نمایش: ${hasTiers ? CATS.map(chip).join('') + '<span class="muted"> | </span>' : ''}${DATES.map(chip).join('')}</div>`;
  TOOLS.querySelector('#depthsel').onchange = ev => {
    const k = +ev.target.value, depthOf = x => { let d = 0; for (let p = x.parent; p && p.key !== ''; p = p.parent) d++; return d; };
    collapsed[shape] = new Set(k ? Object.values(B.by).filter(x => x.kids.length && depthOf(x) === k - 1).map(x => x.key) : []);
    drawTree();
  };
}
TOOLS.addEventListener('click', e => {
  const s = e.target.closest('[data-seg]');
  if (s) { const v = s.getAttribute('data-v'); if (s.dataset.seg === 'mode') { mode = v; setPref('icmv.gmode', v); } else { shape = v; setPref('icmv.gshape', v); } drawTree(); return; }
  const z = e.target.closest('#zin,#zout,#zfit');
  if (!z) return;
  const vb = SVGEL && SVGEL.viewBox.baseVal;
  zoom = z.id === 'zin' ? Math.min(2.5, zoom * 1.25) : z.id === 'zout' ? Math.max(0.3, zoom / 1.25) : vb ? Math.max(0.3, Math.min(2.5, (BOX.clientWidth - 4) / vb.width)) : 1;
  setPref('icmv.gzoom', zoom); keep(drawTree);
});
TOOLS.addEventListener('change', e => {
  const k = e.target.getAttribute('data-show'); if (!k) return;
  e.target.checked ? kinds.add(k) : kinds.delete(k); setPref('icmv.gshow', JSON.stringify([...kinds])); keep(drawTree);
});
BOX.addEventListener('click', e => {
  const m = e.target.closest('[data-matn]');
  if (m) { selectNode('a' + m.getAttribute('data-matn')); return; }
  const ed = e.target.closest('[data-edge]');
  if (ed) { selectEdge(ed.getAttribute('data-edge')); return; }
  const tw = e.target.closest('circle[data-tw]');
  if (tw) {
    const key = tw.getAttribute('data-tw'), x = LAST.by[key];
    if (x && x.kids.length) { const col = collapsed[shape]; col.has(key) ? col.delete(key) : col.add(key); keep(drawTree); }
    else if (x) selectNode(x.nid);
    return;
  }
  const t = e.target.closest('[data-id]');
  if (t) selectNode(t.getAttribute('data-id'));
});
BOX.addEventListener('wheel', e => {
  if (!(e.ctrlKey || e.metaKey) || !SVGEL) return;
  e.preventDefault();
  zoom = Math.max(0.3, Math.min(2.5, zoom * (e.deltaY < 0 ? 1.12 : 0.89))); setPref('icmv.gzoom', zoom); keep(drawTree);
}, {passive: false});

/* ---------- inspector ---------- */
const INS = document.getElementById('inspect');
function barList(items) {
  return '<div class="bars">' + items.map(([lab, p]) =>
    `<div class="bar"><div class="track"><div class="fill" style="width:${Math.max(0, Math.min(1, p)) * 100}%"></div><div class="lab">${lab}</div></div><div class="pct">${fmt(p, 3)}</div></div>`).join('') + '</div>';
}
const nodeBtn = id => `<button class="lnk ar" data-go="${esc(id)}">${esc(nm(id))}</button>`;
const edgeBtn = (key, label) => `<button class="lnk" data-goedge="${esc(key)}">${label}</button>`;
const SRC_FA = {diagram: 'از نمودار', 'hadith-kg': 'از پایگاه رجالی', kg: 'از پایگاه رجالی', interpolated: 'برآورد (درون‌یابی در سند)', user: 'واردشده‌ی کاربر',
                'author-death': 'درگذشت مؤلف کتاب', noor: 'نور', imam: 'تاریخ امام', wikishia: 'ویکی‌شیعه', fixed: 'ثابت'};
const CONF_FA = {high: ['زیاد', 'hi'], medium: ['متوسط', 'me'], low: ['کم — تأییدنشده', 'lo'], none: ['یافت نشد', 'lo']};
function yearText(n) {
  if (n.year === null) return '—';
  const d = n.death;
  let s = d && d[0] !== d[1] ? `${fa(Math.round(d[0]))} تا ${fa(Math.round(d[1]))} ق` : `${fa(n.year)} ق`;
  if (n.date_approx) s = 'حدود ' + s;
  return `${s} <span class="tag">${esc(SRC_FA[n.date_source] || n.date_source || '—')}</span>`;
}
function bioBlock(id) {
  const n = N[id], kg = n.kg;
  if (!kg || !kg.record) return '<p class="empty">برای این نام زندگی‌نامه‌ای در پایگاه رجالی یافت نشد.</p>';
  const r = kg.record, [cf, cc] = CONF_FA[kg.confidence] || CONF_FA.none;
  const weak = !['high', 'medium'].includes(kg.confidence);
  return `<p class="help">${weak ? 'این پیوند ضعیف است و در مدل به کار نرفته؛ تنها به‌عنوان نامزد نمایش داده می‌شود.' : 'این اطلاعات در مدل به کار رفته است: سال وفات (برای ترتیب زمانی)، محل اقامت (هم‌شهری بودن) و شاگردی‌های مستند (امتیاز یال).'}</p>
    <dl class="dl" style="margin-top:6px">
      <dt>اطمینان پیوند</dt><dd><span class="tag ${cc}">${cf}</span> <span class="help">نام ${fmt(kg.name_score, 2)} · ارتباط سندی با همسایه‌ها ${fa(kg.context_links ?? 0)}</span></dd>
      <dt>نام کامل</dt><dd class="ar">${esc(r.name)}</dd>
      ${r.nickname ? `<dt>شهرت</dt><dd class="ar">${esc(r.nickname)}</dd>` : ''}
      <dt>وفات</dt><dd>${r.death ? fa(r.death) + ' ق' : (r.death_raw ? esc(fa(r.death_raw)) : '—')}${r.birth ? ' · تولد ' + fa(r.birth) + ' ق' : ''}</dd>
      ${r.iqama ? `<dt>محل اقامت</dt><dd class="ar">${esc(r.iqama)}</dd>` : ''}
      ${r.death_place ? `<dt>محل وفات</dt><dd class="ar">${esc(r.death_place)}</dd>` : ''}
      ${r.rank ? `<dt>درجه‌ی رجالی</dt><dd class="ar">${esc(r.rank)}</dd>` : ''}
      <dt>طبقه</dt><dd>${fa(r.tabaka)}</dd>
      <dt>شمار روایت‌ها در پایگاه</dt><dd>${fa(r.riwaya_qty)}${r.bukhari ? ' <span class="tag">در صحیح بخاری</span>' : ''}${r.muslim ? ' <span class="tag">در صحیح مسلم</span>' : ''}</dd>
      ${r.nasab ? `<dt>نسب</dt><dd class="ar">${esc(r.nasab)}</dd>` : ''}
    </dl>
    ${kg.alternatives && kg.alternatives.length ? `<p class="help">نامزدهای دیگر: ${kg.alternatives.map(a => `<span class="ar">${esc(a.name)}</span>${a.death ? ' (' + fa(a.death) + ')' : ''}`).join('، ')}</p>` : ''}`;
}
function selectNode(id, go) {
  selected = id; selEdge = null;
  if (isMatn(id)) inspectMatn(+id.slice(1)); else inspectNode(id);
  keep(drawTree);
  if (go) bring(id);
  if (window.innerWidth <= 900) document.getElementById('inspector').scrollIntoView({behavior: 'smooth', block: 'start'});
}
function selectEdge(key) {
  selEdge = key; selected = null; inspectEdge(key); keep(drawTree);
  if (window.innerWidth <= 900) document.getElementById('inspector').scrollIntoView({behavior: 'smooth', block: 'start'});
}
INS.addEventListener('click', e => {
  const a = e.target.closest('[data-go]'); if (a) { selectNode(a.getAttribute('data-go'), true); return; }
  const b = e.target.closest('[data-goedge]'); if (b) selectEdge(b.getAttribute('data-goedge'));
});
function inspectRoot() {
  INS.innerHTML = `<h3 class="ar">${esc(nm(ROOT))}</h3>
    <p>${fa(heads.length)} طریق (نخستین راویِ سند) ثبت شده است. نامزدهای «مبدأ» عبارت‌اند از: نخستین راویان، حلقه‌های مشترک زیر آنان، و یک مبدأ بی‌نام بالای همه‌ی راویان ثبت‌شده.</p>
    <dl class="dl"><dt>شاهدها</dt><dd>${fa(D.attestations.length)}</dd><dt>راویان</dt><dd>${fa(Object.keys(N).length)}</dd>
    <dt>طریق‌ها</dt><dd>${heads.map(c => nodeBtn(c)).join('، ')}</dd></dl>
    <p class="help">روی هر نام در درخت کلیک کنید تا اطلاعات راوی، روی هر یال برای اطلاعات پیوند استاد و شاگرد، و روی نام کتاب (نارنجی، در انتهای هر سند) برای متن کامل حدیث.</p>`;
}
/* the unnamed origin is not a narrator: say what it stands for, why it can win, and what pushes it up */
function inspectOrigin() {
  const lines = heads.map(h => [h, ATT.filter(a => a.sanad[0] === h).length]);
  const direct = lines.filter(([h]) => MAPG[h] === UNK), under = lines.filter(([h]) => MAPG[h] && MAPG[h] !== UNK);
  const late = lines.filter(([h]) => cat(h) === 'anon' || cat(h) === 'name' || (year(h) ?? 0) > 150);
  const runnerName = srcName(runner), bf = lz(UNK) - lz(runner);
  const lst = xs => xs.map(([h, k]) => `${nodeBtn(h)} <span class="help">(${fa(k)} سند)</span>`).join('، ');
  INS.innerHTML = `<h3 class="ar">${esc(UNK_NAME)}</h3>
    <p>این یک راوی در درخت نیست و مدل کسی را «پیدا» نکرده است. نقطه‌ای است بالاتر از نخستین راویِ همه‌ی سندها: جایی که گوینده‌ی حدیث (مثلاً پیامبر، که در سندها نیامده) یا منبعی کهن‌تر و ثبت‌نشده می‌نشیند. معنایش این است: <b>هیچ‌یک از راویان ثبت‌شده</b> منبع مشترک همه‌ی متن‌ها نیست.</p>
    <dl class="dl">
      ${sp[UNK] ? `<dt>احتمال پسین</dt><dd>${sci(sp[UNK].posterior)} · <bdi dir="ltr">log Z ${fmt(lz(UNK), 1)}</bdi></dd>` : ''}
      ${best === UNK && runner ? `<dt>در برابر رقیب</dt><dd><bdi dir="ltr">log BF ${fmt(bf, 1)}</bdi> در برابر ${esc(runnerName)}</dd>` : ''}
      <dt>نخستین راویان سندها</dt><dd>${fa(lines.length)} نام متفاوت</dd>
    </dl>
    <h3>سندها از چند نفر آغاز می‌شوند؛ این یعنی چه؟</h3>
    <p class="help">هر نخستین راوی سرِ یک «طریق» است. اگر یک راوی نام‌دار (مثلاً ${esc(runnerName)}) منبع همه باشد، باید همه‌ی طریق‌های دیگر هم در واقع از او گذشته باشند و سندشان نام او را انداخته باشد؛ مدل این افتادگی‌ها را در هر سند جریمه می‌کند. اگر مبدأ بی‌نام باشد، طریق‌ها تا بالا مستقل‌اند و هیچ سندی شکسته فرض نمی‌شود. هرچه طریق‌های مستقل بیشتر و شکستن سندها پرهزینه‌تر، این گزینه قوی‌تر.</p>
    <p class="help">در محتمل‌ترین درخت زیر این مبدأ، ${fa(direct.length)} طریق مستقیم به آن می‌رسند${under.length ? ` و ${fa(under.length)} طریق زیر راوی دیگری قرار گرفته‌اند (مثلاً ${under.slice(0, 3).map(([h]) => `${esc(nm(h))} زیر ${esc(nm(MAPG[h]))}`).join('، ')})` : ''}.</p>
    ${direct.length ? `<p class="help">مستقیم زیر مبدأ: ${lst(direct)}</p>` : ''}
    <h3>آیا فقط چون یک گره‌ی اضافه بالای همه است برنده می‌شود؟</h3>
    <p class="help">نه به خودیِ خود. درخت‌های زیر این مبدأ، درختی را هم که همه از ${esc(runnerName)} می‌گذرند در بر می‌گیرند؛ امتیاز (log Z) میانگینِ همه‌ی درخت‌هاست، نه بهترینشان، پس گزینه‌های بیشتر به خودی خود امتیاز نمی‌آورند و هر طریقی که مستقیم به این مبدأ وصل شود هزینه‌ی پیشین دارد. برتری‌اش از این است که درخت‌هایی با طریق‌های مستقل، سندهای ثبت‌شده را بهتر توضیح می‌دهند.</p>
    ${late.length ? `<h3>احتیاط</h3><p class="help">برخی «نخستین راویان» به راوی شناخته‌شده‌ای پیوند نخورده‌اند یا متأخرند. بعضی راوی‌اند و فقط نامشان پیوند نخورده، ولی بعضی اصلاً راوی اولیه نیستند: نام مؤلفی متأخر، تکه‌ای از متن که به جای نام خوانده شده، یا «موضع إرسال» (سندی که خودِ منبع بالایش را افتاده ثبت کرده). هیچ راوی کهنی نمی‌تواند بی‌فرضِ افتادگی منبع اینها باشد، پس اینها مبدأ را به بالا می‌رانند و بخشی از قوّت این نتیجه از آن‌هاست. می‌توانید در مرحله‌ی ۴ نامشان را درست کنید: ${lst(late)}</p>` : ''}`;
}
function inspectNode(id) {
  if (id === ROOT) return inspectRoot();
  if (id === UNK || !N[id]) return inspectOrigin();
  const n = N[id];
  const pp = Object.entries(n.parent_posterior || {}).sort((a, b) => b[1] - a[1]).map(([u, q]) => [esc(nm(u)), q]);
  const post = k => E[k] && E[k].posterior !== null && E[k].posterior !== undefined ? ' · ' + fmt(E[k].posterior, 2) : '';
  const rel = (u, v) => `<span class="help">(${edgeBtn(edgeKey(u, v), 'یال')}${post(edgeKey(u, v))})</span>`;
  const ins = [...(teachersOf[id] || [])], outs = [...(studentsOf[id] || [])];
  let anc = '';
  if (n.ancestral) {
    const items = [];
    D.variants.forEach((spec, k) => {
      const row = n.ancestral[k] || [];
      if (spec.n_states === 2) items.push([esc(vName(spec)), row[1] || 0]);
      else row.forEach((q, j) => { if (q > 0.005) items.push([esc(COUNT_FA[spec.labels[j]] || spec.labels[j]), q]); });
    });
    anc = barList(items);
  } else anc = '<p class="empty">این راوی در درخت‌های نمونه‌برداری‌شده زیر مبدأ برتر حضور ندارد.</p>';
  const same = (n.same_person || []).length ? `<dt>همان شخص در جای دیگر</dt><dd>${n.same_person.map(nodeBtn).join('، ')}</dd>` : '';
  INS.innerHTML = `
    <h3 class="ar">${esc(n.name)}</h3>
    <dl class="dl">
      <dt>سال وفات</dt><dd>${yearText(n)}</dd>
      ${n.locations && n.locations.length ? `<dt>شهر(ها)</dt><dd class="ar">${esc(n.locations.join('، '))}</dd>` : ''}
      <dt>استاد ثبت‌شده</dt><dd>${ins.length ? ins.map(u => `${nodeBtn(u)} ${rel(u, id)}`).join('، ') : 'سرآغاز یک طریق'}</dd>
      <dt>شاگردان ثبت‌شده</dt><dd>${outs.length ? outs.map(c => `${nodeBtn(c)} ${rel(id, c)}`).join('، ') : '—'}</dd>
      <dt>سندهای گذرنده</dt><dd>${fa(witnesses[id] || 0)}</dd>
      ${same}
      ${sp[id] ? `<dt>به‌عنوان مبدأ</dt><dd>احتمال پسین ${sci(sp[id].posterior)} · log Z ${fmt(lz(id), 1)}</dd>` : ''}
    </dl>
    <h3>زندگی‌نامه (کتب رجال)</h3>${bioBlock(id)}
    <h3>استادِ این راوی در انتقال این حدیث کیست؟</h3>
    <p class="help">توزیع پسین روی استادانِ ممکن: علاوه بر استاد ثبت‌شده، استادِ استاد (حذف یک واسطه) و راویان هم‌دوره‌ی هم‌طریق نیز نامزدند.</p>
    ${pp.length ? barList(pp) : '<p class="empty">—</p>'}
    <h3>متنی که این راوی احتمالاً نقل کرده</h3>
    <p class="help">احتمال وجود هر بند در متنِ نیاکانیِ این راوی، میانگین روی درخت‌های پسین. این متن مشاهده نشده؛ از متن‌های شاگردانش بازسازی شده است.</p>
    ${anc}`;
}
function inspectEdge(key) {
  const e = E[key];
  const [u, v] = key.split('>');
  if (!e) { INS.innerHTML = `<p class="empty">برای این یال داده‌ای ثبت نشده است.</p>`; return; }
  const parts = e.score_parts || {};
  INS.innerHTML = `
    <h3>یال انتقال</h3>
    <p class="ar" style="font-size:16px">${nodeBtn(u)} <span class="arr">←</span> ${nodeBtn(v)}</p>
    <p class="help">استاد در سمت راست، شاگرد در سمت چپ.</p>
    <dl class="dl">
      <dt>احتمال پسین</dt><dd>${fmt(e.posterior, 3)} ${e.in_map ? '<span class="tag yes">در محتمل‌ترین درخت</span>' : '<span class="tag no">خارج از محتمل‌ترین درخت</span>'}</dd>
      <dt>احتمال پیشین (ρ)</dt><dd>${fmt(e.prior, 3)} <span class="help">پیش از دیدن متن‌ها، فقط از شواهد سند و رجال</span></dd>
      <dt>امتیاز یال (η)</dt><dd>${fmt(e.score, 1)}</dd>
      <dt>اجزای امتیاز</dt><dd>
        <span class="tag ${parts.recorded ? 'yes' : 'no'}">ثبت در سند ${parts.recorded ? '+۳' : '۰'}</span>
        <span class="tag ${parts.same_city ? 'yes' : 'no'}">هم‌شهری ${parts.same_city ? '+۱' : '۰'}</span>
        <span class="tag ${parts.kg_teacher ? 'yes' : 'no'}">شاگردی مستند در رجال ${parts.kg_teacher ? '+۲' : '۰'}</span></dd>
      <dt>در اسناد مستقل پایگاه</dt><dd>${e.kg_chains === null || e.kg_chains === undefined ? 'یکی از دو سر یال پیوند نخورده است' : (e.kg_chains > 0 ? `در ${fa(e.kg_chains)} سند دیگر، همین استاد و شاگرد پشت‌سرهم آمده‌اند` : 'در اسناد پایگاه دیده نشد')}</dd>
      <dt>فاصله‌ی سال وفات</dt><dd>${e.gap_years === null ? '—' : fa(e.gap_years) + ' سال'}</dd>
      <dt>شاهدهای حامل این یال</dt><dd>${(e.witnesses || []).map(w => { const i = D.attestations.findIndex(a => a.id === w); return `<button class="lnk ar" data-go="a${i}">${esc(D.attestations[i].collection)}</button>`; }).join('، ') || '—'}</dd>
    </dl>
    <h3>استادهای نامزد برای این شاگرد (پیشین)</h3>
    ${barList((e.candidates || []).map(c => [esc(nm(c.node)), c.prior]))}
    <p class="help">مدل این گزینه‌ها را می‌سنجد: سند ممکن است واسطه‌ای را انداخته یا نامی را جابه‌جا کرده باشد.</p>`;
}
function rowTags(a) {
  return D.variants.map((spec, k) => {
    const x = a.row[k], st = `style="--cc:${vColor(spec)}"`;
    if (x < 0) return `<span class="tag na" title="متن کوتاه‌شده است؛ نامعلوم، نه غایب">${esc(vName(spec))}: نامعلوم</span>`;
    if (spec.n_states === 2) return `<span class="tag ${x === 1 ? 'cl' : 'no'}" ${st}>${esc(vName(spec))}: ${x === 1 ? 'هست' : 'نیست'}</span>`;
    return `<span class="tag cl" ${st}>${esc(COUNT_FA[spec.labels[x]] || spec.labels[x])}</span>`;
  }).join('');
}
const chainHtml = s => '<div class="chain">' + s.map((n, i) => `${i ? '<span class="arr">←</span>' : ''}<button class="${i === 0 ? 'src' : ''}" data-go="${esc(n)}">${esc(nm(n))}</button>`).join('') + '</div>';
function inspectMatn(i) {
  const a = D.attestations[i];
  INS.innerHTML = `
    <h3 class="ar">${esc(a.collection)}</h3>
    <div class="matntext">${highlight(a.text)}</div>
    <h3>بندهای متن</h3><div>${rowTags(a)}</div>
    <p class="help">«نامعلوم» برای متن‌های کوتاه‌شده («بنحوه»، «وساق الحديث») است: نبودِ بند در چنین متنی به معنای حذف آن نیست.</p>
    <h3>سند</h3>${chainHtml(a.sanad)}
    <dl class="dl" style="margin-top:10px">
      <dt>طول سند</dt><dd>${fa(a.sanad.length)} راوی</dd>
      <dt>وزن استقلال</dt><dd>${fmt(a.weight, 2)} <span class="help">${a.weight < 1 ? 'چند نسخه از همین کتاب در داده هست؛ هر کدام با این وزن به حساب می‌آید' : 'نسخه‌ی مستقل'}</span></dd>
      <dt>طریق</dt><dd>${nodeBtn(a.companion)}</dd>
    </dl>`;
}

/* ---------- other panels ---------- */
function textsPanel() {
  const key = Object.values(CLAUSES).map(c => `<span class="tag cl" style="--cc:var(${c.c})" title="${esc(c.gloss)}">${esc(c.fa)}</span>`).join('');
  const groups = D.companions.map(c => {
    const items = D.attestations.map((a, i) => [a, i]).filter(([a]) => a.companion === c);
    return `<h3 class="ar">${esc(nm(c))} · ${fa(items.length)} متن</h3>
      ${items.map(([a, i]) => `<div class="textcard"><div class="hd"><b>${esc(a.collection)}</b>
        <span>سند ${fa(a.sanad.length)} راوی</span><button class="lnk" data-showtree="a${i}">نمایش در درخت</button></div>
        <div class="matntext">${highlight(a.text)}</div></div>`).join('')}`;
  }).join('');
  return `<div class="card"><h2>متن‌های حدیث (${fa(D.attestations.length)} شاهد)</h2><div class="body">
    <p class="help">متن هر شاهد همان‌گونه که در نمودار آمده است. رنگ‌ها بندهایی هستند که مدل به‌عنوان «واحد اختلاف» می‌شناسد:</p>
    <div class="clkey">${key}</div><div class="texts">${groups}</div></div></div>`;
}
function sourcesPanel() {
  const rows = ranked.map(s => `<tr class="click${s === best ? ' best' : ''}" data-showtree="${esc(s)}" title="${s === UNK ? 'این یعنی چه؟' : 'نمایش در درخت'}"><td class="ar">${esc(srcName(s))}${s === UNK ? ' <span class="help">(این یعنی چه؟)</span>' : ''}</td>
    <td class="n">${fmt(lz(s), 1)}</td><td class="n">${sci(sp[s].posterior)}</td><td class="n">${fmt(sp[s].ess, 0)}</td>
    <td>${D.credible_set_95.includes(s) ? '<span class="tag yes">در مجموعه‌ی ۹۵٪</span>' : ''}</td></tr>`).join('');
  const br = D.branch_results ? Object.entries(D.branch_results).map(([line, r]) => !r.posterior
    ? `<tr><td class="ar">${esc(line)}</td><td class="n">${fa(r.n_attestations)}</td><td colspan="3" class="empty">تنها یک شاهد؛ مقایسه ممکن نیست</td></tr>`
    : `<tr><td class="ar">${esc(line)}</td><td class="n">${fa(r.n_attestations)}</td><td class="ar">${esc(r.map_source)}</td>
       <td class="n">${fmt(Math.max(...Object.values(r.posterior)), 3)}</td><td class="n">${fmt(r.log_BF_vs_next, 1)}</td></tr>`).join('') : '';
  const mo = D.matn_only ? Object.entries(D.matn_only).map(([line, r]) => `<tr><td class="ar">${esc(line)}</td>
    <td class="ar">${esc(r.matn_only.map_source)}</td><td class="ar">${esc((r['sanad_downweighted_0.1'] || {}).map_source || '—')}</td></tr>`).join('') : '';
  return `<div class="grid2">
    <div class="card"><h2>نامزدهای مبدأ — کل خانواده</h2><div class="scroll"><table>
      <thead><tr><th>مبدأ</th><th class="n">log Z</th><th class="n">احتمال پسین</th><th class="n">ESS</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="body"><p class="note">log Z (لگاریتم «شاهد») احتمال کل داده است اگر این راوی مبدأ باشد، میانگین روی همه‌ی درخت‌ها. −∞ یعنی تحت هیچ درخت نمونه‌برداری‌شده‌ای همه‌ی شاهدها از این راوی قابل دسترسی نیستند. ESS (اندازه‌ی مؤثر نمونه) کیفیت برآورد را نشان می‌دهد؛ زیر ۱۰ نامطمئن است.</p></div></div>
    <div class="card"><h2>درون هر طریق</h2><div class="scroll"><table>
      <thead><tr><th>طریق</th><th class="n">شاهد</th><th>محتمل‌ترین مبدأ</th><th class="n">احتمال</th><th class="n">log BF</th></tr></thead>
      <tbody>${br || '<tr><td colspan="5" class="empty">محاسبه نشده</td></tr>'}</tbody></table></div>
      <div class="body"><p class="note">در هر طریق جداگانه پرسیده‌ایم: قدیمی‌ترین منبع مشترکِ متن‌های همین طریق کیست؟ log BF فاصله با رقیب بعدی است.</p></div></div>
    <div class="card"><h2>متن به‌تنهایی چه می‌گوید؟</h2><div class="scroll"><table>
      <thead><tr><th>طریق</th><th>فقط متن</th><th>سند با وزن ۰٫۱</th></tr></thead>
      <tbody>${mo || '<tr><td colspan="3" class="empty">محاسبه نشده</td></tr>'}</tbody></table></div>
      <div class="body"><p class="note">اگر سهم سند را صفر کنیم، مبدأ به «حلقه‌ی مشترک» پایین می‌آید؛ آنچه مبدأ را تا صحابی بالا می‌برد ساختار اسناد است، نه شواهد متنی.</p></div></div>
  </div>`;
}
function graphPanel() {
  const unsure = Object.entries(E).filter(([k, e]) => e.posterior !== null && e.posterior !== undefined && e.posterior < 0.999)
    .sort((a, b) => a[1].posterior - b[1].posterior).slice(0, 40);
  const departs = Object.entries(MAPG).filter(([v, p]) => N[v] && !(teachersOf[v] || new Set()).has(p) && !(p === UNK && !teachersOf[v]));
  return `<div class="grid2">
    <div class="card"><h2>آیا محتمل‌ترین درخت پیدا شد؟</h2><div class="body">
      <dl class="dl"><dt>ذرات (نمونه‌ها)</dt><dd>${fa(D.graph_posterior.n_particles)}</dd>
        <dt>درخت‌های متمایز</dt><dd>${fa(D.graph_posterior.n_distinct)}</dd>
        <dt>سهم محتمل‌ترین درخت</dt><dd>${pct(D.graph_posterior.map_mass)}</dd>
        <dt>امتیاز لگاریتمی آن</dt><dd>${fmt(D.graph_posterior.map_log_score, 2)}</dd></dl>
      <p class="note">بله. یک درخت بیشینه‌ی پسین (MAP) یافت شد که ${pct(D.graph_posterior.map_mass)} از احتمال پسین را (برای مبدأ برتر) در خود دارد؛ یعنی پسین متمرکز است و با این مجموعه‌ی استادانِ نامزد، داده‌ها عملاً همان ساختار ثبت‌شده را تأیید می‌کنند. کم بودن شمار درخت‌های متمایز همچنین یعنی نمونه‌گیر (SMC) روی همین درخت جمع شده و برآورد سهم درخت‌های رقیب خود نامطمئن است.</p></div></div>
    <div class="card"><h2>یال‌هایی که مدل درباره‌شان مطمئن نیست</h2><div class="scroll"><table>
      <thead><tr><th>استاد ← شاگرد</th><th class="n">احتمال پسین</th></tr></thead><tbody>
      ${unsure.length ? unsure.map(([k, e]) => `<tr class="click" data-goedge="${esc(k)}"><td class="ar">${esc(nm(e.teacher))} ← ${esc(nm(e.student))}</td><td class="n">${fmt(e.posterior, 3)}</td></tr>`).join('')
        : '<tr><td colspan="2" class="empty">همه‌ی یال‌های ثبت‌شده احتمال پسین ≥ ۰٫۹۹۹ دارند.</td></tr>'}</tbody></table></div></div>
    <div class="card"><h2>جاهایی که درخت بیشینه با سند ثبت‌شده فرق دارد</h2><div class="scroll"><table>
      <thead><tr><th>شاگرد</th><th>استاد ثبت‌شده</th><th>استاد در درخت بیشینه</th></tr></thead><tbody>
      ${departs.length ? departs.map(([v, p]) => `<tr><td class="ar">${esc(nm(v))}</td><td class="ar">${esc([...(teachersOf[v] || [])].map(nm).join('، ') || '—')}</td><td class="ar">${esc(nm(p))}</td></tr>`).join('')
        : '<tr><td colspan="3" class="empty">درخت بیشینه دقیقاً همان ساختار ثبت‌شده است.</td></tr>'}</tbody></table></div></div>
  </div>`;
}
function sensPanel() {
  const g = D.global_results || {};
  const tbl = (obj, head) => {
    if (!obj) return '<p class="empty">محاسبه نشده</p>';
    return `<div class="scroll"><table><thead><tr><th>${head}</th><th>محتمل‌ترین مبدأ</th><th class="n">احتمال</th><th>دومی</th><th class="n">احتمال</th></tr></thead><tbody>
      ${Object.entries(obj).map(([k, v]) => { const t = Object.entries(v).sort((a, b) => b[1] - a[1]);
        return `<tr><td>${esc(SENS_FA[k] || fa(k))}</td><td class="ar">${esc(t[0][0])}</td><td class="n">${sci(t[0][1])}</td><td class="ar">${esc(t[1] ? t[1][0] : '')}</td><td class="n">${t[1] ? sci(t[1][1]) : ''}</td></tr>`; }).join('')}</tbody></table></div>`;
  };
  const pt = g.permutation_test;
  return `<div class="grid2">
    <div class="card"><h2>حساسیت به پیشینِ مبدأ</h2><div class="body">${tbl(g.prior_sensitivity, 'پیشین')}
      <p class="note">نتیجه با پیشین یکنواخت، پیشینِ مبتنی بر تاریخ، و پیشینی که به مبدأ ناشناخته وزن بیشتری می‌دهد، تغییر نمی‌کند.</p></div></div>
    <div class="card"><h2>حساسیت به نرخ تغییر متن و خطای سند</h2><div class="body">${tbl(g.rate_sensitivity, 'تنظیم')}</div></div>
    <div class="card"><h2>آزمون جایگشت</h2><div class="body">${pt ? `<dl class="dl">
      <dt>مبدأ آزموده</dt><dd class="ar">${esc(pt.source)}</dd><dt>احتمال مشاهده‌شده</dt><dd>${fmt(pt.observed, 3)}</dd>
      <dt>میانگین در جایگشت‌ها</dt><dd>${fmt(pt.null_mean, 3)}</dd><dt>p-value</dt><dd>${fmt(pt.p_value, 3)}</dd></dl>
      <p class="note">متن‌ها را میان سندها جابه‌جا کردیم. اگر متن نقشی در نتیجه داشت، احتمال باید افت می‌کرد؛ نکرد. با این رمزگذاریِ بندمحور، متن تقریباً هیچ تمایزی میان نامزدها ایجاد نمی‌کند و نتیجه بر ساختار اسناد استوار است.</p>` : '<p class="empty">محاسبه نشده</p>'}</div></div>
  </div>`;
}
const SENS_FA = {equal: 'یکنواخت', favour_unknown: 'ترجیح مبدأ ناشناخته', date_informed: 'مبتنی بر تاریخ',
  'mu=0.02': 'نرخ تغییر متن ۰٫۰۲', 'mu=0.08': 'نرخ تغییر متن ۰٫۰۸', 'mu=0.2': 'نرخ تغییر متن ۰٫۲',
  'sanad_error=0.02': 'خطای سند ۰٫۰۲', 'sanad_error=0.1': 'خطای سند ۰٫۱', 'sanad_error=0.2': 'خطای سند ۰٫۲'};
function methodPanel() {
  const rec = D.recovery_test;
  return `<div class="grid2">
    <div class="card"><h2>مدل</h2><div class="body">
      <p class="num" style="direction:ltr;text-align:left">P(S=s | X, C, M) ∝ P(S=s) · Z<sub>s</sub> ,&nbsp; Z<sub>s</sub> = Σ<sub>G</sub> P(X|G,s) · P(C|G,s) · P(G|s,M)</p>
      <ul>
        <li><b>متن (X):</b> هر بند در طول درخت با یک فرایند مارکوفِ حالت‌متناهی تغییر می‌کند. متن‌های نیاکانیِ دیده‌نشده با الگوریتم هرس (pruning) جمع زده می‌شوند، نه این‌که اول بازسازی شوند.</li>
        <li><b>سند (C):</b> هر سندِ ثبت‌شده خوانشی پرخطا از مسیرِ واقعیِ مبدأ تا شاهد است؛ با یک «مدل مارکوف پنهانِ جفتی» سنجیده می‌شود: تطابق، حذف واسطه، افزودن نام، جابه‌جایی نام.</li>
        <li><b>رجال (M):</b> استادانِ نامزدِ هر راوی با ترتیب زمانی و جغرافیا محدود می‌شوند. امتیاز هر یال (ثبت در سند، هم‌شهری بودن، شاگردیِ مستند در کتب رجال) با softmax به احتمال تبدیل می‌شود.</li>
        <li><b>محاسبه‌ی Z<sub>s</sub>:</b> شمارش کامل برای فضاهای کوچک، نمونه‌برداری اهمیت، یا SMC بازپخت‌شده — که این‌جا به کار رفته است.</li>
      </ul>
      <p class="note">هر نسخه‌ی باقی‌مانده از راوی‌اش با شاخه‌ای جداگانه آویخته است، تا دو نسخه‌ی متفاوت از یک راوی تناقض به حساب نیاید. نسخه‌های یک کتاب یک «گروه استقلال» دارند و با وزن ۱/تعداد وارد می‌شوند.</p></div></div>
    <div class="card"><h2>اعتبارسنجی</h2><div class="body">
      <p>الگوریتم هرس در برابر جمع‌زدن کامل (brute force) روی همه‌ی متن‌های نیاکانی آزموده شده است؛ مدل سند در برابر نرمال بودن و یکنوایی.</p>
      ${rec ? `<dl class="dl"><dt>شبیه‌سازی</dt><dd>${fa(12)} خانواده‌ی ساختگی با مبدأ معلوم</dd>
        <dt>مبدأ واقعی = محتمل‌ترین</dt><dd>${pct(rec.map_accuracy)}</dd><dt>پوشش مجموعه‌ی ۹۵٪</dt><dd>${pct(rec.coverage95)}</dd>
        <dt>میانگین احتمال مبدأ واقعی</dt><dd>${fmt(rec.mean_true_posterior, 3)}</dd></dl>` : ''}</div></div>
    <div class="card"><h2>محدودیت‌ها</h2><div class="body"><ul>
      <li>پسین زیر سلطه‌ی جمله‌ی سند است؛ هشت واحد اختلافِ بندمحور تقریباً ثابت‌اند و متن قدرت تفکیک کمی دارد. هم‌ترازیِ واژه‌به‌واژه‌ی متن‌ها گام بعدی است.</li>
      <li>خطای سند مستقل از جایگاه فرض شده: ساختگی بودنِ یک سندِ کامل تا صحابی مدل نشده، و همین فرض است که می‌تواند با نتیجه‌ی کل خانواده رقابت کند.</li>
      <li>پیوند با کتب رجال خودکار است: پیوندهای «زیاد» و «متوسط» به کار رفته‌اند و پیوندهای «کم» تنها نمایش داده می‌شوند. پایگاه بیشتر از منابع اهل سنت است و مؤلفان شیعه (مانند صدوق و کلینی) در آن کم‌اند.</li>
      <li>هویت راوی از نمودار گرفته شده: یک شخص که در چند شاخه آمده، چند گره جداگانه است (در جزئیات راوی «همان شخص در جای دیگر» نشان داده می‌شود).</li>
      <li><span class="ar">غیر واحد</span> («بیش از یک نفر») یک گره حساب شده، که در اصل چنین نیست.</li>
    </ul></div></div>
  </div>`;
}
function glossPanel() {
  const G = [
    ['مبدأ (منبعِ قدیمی‌ترینِ قابل‌بازیابی)', 'راوی‌ای که همه‌ی روایت‌های باقی‌مانده (یا روایت‌های یک طریق) از او سرچشمه گرفته‌اند. مدل ادعا نمی‌کند گوینده‌ی نخستین را می‌شناسد؛ فقط قدیمی‌ترین نقطه‌ای را می‌یابد که شواهد باقی‌مانده به آن می‌رسند.'],
    ['احتمال پسین P(S=s | داده)', 'احتمال این‌که s مبدأ باشد، پس از دیدن متن‌ها، سندها و اطلاعات رجالی، و با میانگین‌گیری روی همه‌ی درخت‌های انتقال و همه‌ی متن‌های نیاکانیِ ممکن.'],
    ['log Z (لگاریتم شاهد مدل)', 'لگاریتم احتمال کل داده‌ها با فرض مبدأ بودن s. اعداد منفی بزرگ عادی‌اند؛ آنچه اهمیت دارد اختلاف میان نامزدهاست.'],
    ['عامل بیز (log BF)', 'اختلاف log Z دو نامزد. ۱ ≈ ۳ برابر، ۵ ≈ ۱۵۰ برابر، ۱۰ ≈ ۲۲۰۰۰ برابر پشتیبانی بیشتر از نامزد اول.'],
    ['ESS (اندازه‌ی مؤثر نمونه)', 'نمونه‌های وزن‌دار معادل چند نمونه‌ی مستقل‌اند. زیر ۱۰ یعنی برآوردِ Z نامطمئن است.'],
    ['درخت بیشینه‌ی پسین (MAP)', 'محتمل‌ترین درخت انتقال برای مبدأ برتر. یال‌های آن در درخت با رنگ تأکید کشیده شده‌اند.'],
    ['احتمال پسین یال', 'احتمال این‌که در انتقال این حدیث، این استاد واقعاً استادِ این شاگرد بوده باشد. ضخامت یال در درخت متناسب با همین عدد است.'],
    ['احتمال پیشین یال (ρ)', 'پیش از دیدن متن‌ها: امتیاز هر استادِ نامزد با softmax به احتمال تبدیل می‌شود.'],
    ['امتیاز یال (η)', 'ثبت در سند +۳، هم‌شهری بودن +۱، شاگردیِ مستند در کتب رجال +۲؛ استادِ ناشناخته −۴. استادِ ناممکن از نظر زمانی کنار گذاشته می‌شود.'],
    ['سال وفات و منبع آن', 'سال قمری. «از نمودار»: در خود نمودار آمده. «از پایگاه رجالی»: از زندگی‌نامه‌ی پیوندخورده. «برآورد»: درون‌یابی خطی میان دو راویِ تاریخ‌دار در همان سند. این سال، ساعتِ مدل برای ترتیب زمانی است.'],
    ['شاهد', 'هر متنِ باقی‌مانده‌ی حدیث همراه با سندش، در یک کتاب.'],
    ['واحد اختلاف متن', 'بخشی از متن که میان روایت‌ها فرق می‌کند. این‌جا هفت بندِ «لعن‌شدگان» و عددِ آغاز حدیث.'],
    ['نامعلوم (NA)', 'متن کوتاه‌شده است («بنحوه»، «وساق الحديث»)؛ نبودِ بند به معنای حذف نیست و مدل آن را بی‌اطلاع حساب می‌کند.'],
    ['متن نیاکانی', 'متنی که یک راوی (که متنش به دست ما نرسیده) احتمالاً نقل کرده، بازسازی‌شده از متن‌های شاگردانش.'],
    ['وزن استقلال', 'نسخه‌های یک کتاب مستقل نیستند؛ هر کدام با وزن ۱ تقسیم بر شمار نسخه‌ها وارد می‌شود.'],
    ['حلقه‌ی مشترک', 'راوی‌ای که چند شاگرد مستقل از او نقل کرده‌اند؛ نقطه‌ی انشعاب در درخت.'],
    ['آزمون جایگشت', 'متن‌ها را تصادفی میان سندها جابه‌جا می‌کنیم؛ اگر نتیجه تغییر نکند، متن در آن نقشی ندارد.'],
    ['اطمینان پیوند رجالی', 'زیاد: نام تقریباً کامل منطبق، تاریخ سازگار، و دست‌کم یک همسایه‌ی سندی در پایگاه نیز استاد یا شاگرد اوست. متوسط: شواهد کمتر. کم: فقط نامزد؛ در مدل به کار نرفته.'],
  ];
  return `<div class="card"><h2>راهنمای اصطلاحات</h2><div class="body"><dl class="gloss">${G.map(([t, d]) => `<dt>${t}</dt><dd>${d}</dd>`).join('')}</dl></div></div>`;
}

/* ---------- tabs ---------- */
const TABS = [['tree', 'درخت'], ['texts', 'متن‌ها'], ['sources', 'مبدأ'], ['graph', 'درخت بیشینه'],
              ['sens', 'حساسیت'], ['method', 'روش و محدودیت‌ها'], ['gloss', 'راهنما']];
const nav = document.getElementById('tabs');
nav.innerHTML = TABS.map(([id, lab], i) => `<button role="tab" id="tab-${id}" aria-controls="panel-${id}" aria-selected="${i === 0}">${lab}</button>`).join('');
const built = {};
function show(id) {
  for (const [t] of TABS) { document.getElementById('panel-' + t).hidden = t !== id; document.getElementById('tab-' + t).setAttribute('aria-selected', String(t === id)); }
  if (!built[id]) {
    const el2 = document.getElementById('panel-' + id);
    const f = {texts: textsPanel, sources: sourcesPanel, graph: graphPanel, sens: sensPanel, method: methodPanel, gloss: glossPanel}[id];
    if (f) el2.innerHTML = f();
    built[id] = true;
  }
}
nav.addEventListener('click', e => { const b = e.target.closest('button'); if (b) show(b.id.slice(4)); });
document.addEventListener('click', e => {
  const st = e.target.closest('[data-showtree]');
  if (st) { show('tree'); selectNode(st.getAttribute('data-showtree'), true); return; }
  const tr = e.target.closest('tr[data-goedge]');
  if (tr) { show('tree'); selectEdge(tr.getAttribute('data-goedge')); }
});

/* ---------- controls ---------- */
const sel = document.getElementById('branchsel');
sel.innerHTML = '<option value="">همه‌ی طریق‌ها</option>' + heads.map(c => `<option value="${esc(c)}">${esc(nm(c))} (${fa(ATT.filter(a => a.sanad[0] === c).length)})</option>`).join('');
sel.addEventListener('change', () => { branchFilter = sel.value; drawTree(); });
document.getElementById('q').addEventListener('input', e => { filter = e.target.value.trim(); drawTree(); });

drawTree();
inspectRoot();
show('tree');

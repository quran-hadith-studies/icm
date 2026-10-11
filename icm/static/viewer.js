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
const srcName = s => s === UNK ? 'مبدأ ناشناخته (پیش از همه‌ی صحابه)' : (N[s] ? N[s].name : s);
const nm = id => id === ROOT ? 'حدیث «ستة لعنهم الله»' : id === UNK ? 'راوی ناشناخته‌ی میانی' : (N[id] ? N[id].name : id);

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

/* ---------- tree model ---------- */
const kids = {};
for (const [v, p] of Object.entries(D.recorded_parent)) (kids[p] = kids[p] || []).push(v);
for (const c of D.companions) (kids[ROOT] = kids[ROOT] || []).push(c);
const attAt = {};
D.attestations.forEach((a, i) => { (attAt[a.anchor] = attAt[a.anchor] || []).push(i); });
const witnesses = {};
(function count(v) { let n = (attAt[v] || []).length; for (const c of (kids[v] || [])) n += count(c); return witnesses[v] = n; })(ROOT);
const edgeKey = (p, c) => p + '>' + c;

/* ---------- header ---------- */
const best = D.map_source;
const ranked = Object.keys(sp).sort((a, b) => lz(b) - lz(a));
const runner = ranked.find(s => s !== best);
const ks = D.kg_stats || {};
document.getElementById('genline').textContent =
  `${fa(D.attestations.length)} شاهد (متن) · ${fa(Object.keys(N).length)} راوی · ${fa(D.variants.length)} واحد اختلاف متن · تاریخ اجرا ${fa(D.generated)}`;
const CHIPS = [
  ['محتمل‌ترین مبدأ', `<span class="v ar">${esc(srcName(best))}</span>`, 'راوی (یا منبعی) که بیشترین احتمال پسین را دارد که قدیمی‌ترین منبع قابل‌بازیابی همه‌ی این روایت‌ها باشد.'],
  ['احتمال پسین آن', `<span class="v num">${sci(sp[best].posterior)}</span>`, 'P(S=s | متن‌ها، سندها، رجال): احتمال مبدأ بودن، پس از دیدن همه‌ی داده‌ها و با درنظرگرفتن همه‌ی درخت‌های ممکن.'],
  ['لگاریتم عامل بیز در برابر رقیب', `<span class="v num">${fmt(lz(best) - lz(runner), 1)}</span>`, `تفاضل log Z با رقیب بعدی (${srcName(runner)}). عدد ۵ یعنی داده حدود ۱۵۰ برابر بیشتر از این گزینه پشتیبانی می‌کند؛ بیش از ۱۰ قاطع است.`],
  ['سهم محتمل‌ترین درخت', `<span class="v num">${pct(D.graph_posterior.map_mass)}</span>`, 'چه کسری از احتمال پسین (برای مبدأ برتر) روی یک درخت انتقال واحد، یعنی درخت بیشینه‌ی پسین (MAP)، متمرکز است.'],
  ['درخت‌های متمایز در نمونه', `<span class="v num">${fa(D.graph_posterior.n_distinct)} از ${fa(D.graph_posterior.n_particles)}</span>`, 'چند درخت متفاوت در میان ذرات نمونه‌برداری (SMC) باقی مانده‌اند. عدد کم یعنی پسین متمرکز است، ولی برآورد سهم درخت‌های رقیب نیز نامطمئن‌تر است.'],
  ['راویان پیوندخورده با کتب رجال', `<span class="v num">${fa(ks.linked ?? 0)} از ${fa(ks.total ?? 0)}</span>`, `راویانی که با اطمینان زیاد یا متوسط به زندگی‌نامه‌ای در پایگاه رجالی (${D.kg_source || '—'}) پیوند خورده‌اند؛ از ${fa(ks.edges_linkable ?? 0)} یال قابل‌بررسی، ${fa(ks.edges_attested ?? 0)} یال در اسناد مستقل آن پایگاه نیز آمده است.`],
];
document.getElementById('verdicts').innerHTML = CHIPS.map(([k, v, h]) =>
  `<div class="chip" title="${esc(h)}"><span class="k">${k}</span>${v}</div>`).join('');
document.getElementById('explainlist').innerHTML = CHIPS.map(([k, v, h]) => `<dt>${k}</dt><dd>${esc(h)}</dd>`).join('');

/* ---------- tree drawing: right-to-left node-link ---------- */
const SVG = document.getElementById('treesvg'), BOX = document.getElementById('treebox');
const NS = 'http://www.w3.org/2000/svg';
const ROW = 26, COLGAP = 34, PAD = 28;
const open = new Set([ROOT, ...D.companions, ...Object.keys(N)]);
let selected = null, selEdge = null, filter = '', branchFilter = '';
let view = {k: 1, x: 0, y: 0};
const el = (n, a, t) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); if (t !== undefined) e.textContent = t; return e; };
const isMatn = id => /^a\d+$/.test(id);
const childrenOf = v => isMatn(v) ? [] : (kids[v] || []).concat((attAt[v] || []).map(i => 'a' + i));
const labelOf = id => {
  if (isMatn(id)) { const t = D.attestations[+id.slice(1)].text; return t.length > 46 ? t.slice(0, 46) + '…' : t; }
  return nm(id);
};
const normQ = s => s.replace(/[يیى]/g, 'ي').replace(/[كک]/g, 'ك').replace(/[هةۀھ]/g, 'ه').replace(/[أإآ]/g, 'ا');
function matchesId(id) {
  if (!filter) return true;
  const q = normQ(filter);
  const own = isMatn(id) ? D.attestations[+id.slice(1)].text : nm(id);
  return normQ(own).includes(q) || childrenOf(id).some(matchesId);
}
const subtreeSize = id => childrenOf(id).reduce((n, c) => n + 1 + subtreeSize(c), 0);
const wCache = new Map();
function measure(id) {
  if (wCache.has(id)) return wCache.get(id);
  const t = el('text', {x: 0, y: -9999, class: 'lbl' + (isMatn(id) ? ' matn' : '')}, labelOf(id));
  SVG.appendChild(t);
  const w = t.getComputedTextLength() || labelOf(id).length * 8;
  SVG.removeChild(t); wCache.set(id, w); return w;
}
function layout() {
  const root = branchFilter || ROOT;
  const nodes = []; let slot = 0;
  (function walk(id, depth, parent) {
    const node = {id, depth, parent, w: measure(id)};
    nodes.push(node);
    const vis = open.has(id) ? childrenOf(id).filter(matchesId) : [];
    node.collapsed = !open.has(id) && childrenOf(id).length > 0;
    node.hidden_n = node.collapsed ? subtreeSize(id) : 0;
    if (!vis.length) { node.y = slot++ * ROW; return node; }
    const cs = vis.map(c => walk(c, depth + 1, id));
    node.y = (cs[0].y + cs[cs.length - 1].y) / 2;
    return node;
  })(root, 0, null);
  const maxD = Math.max(...nodes.map(n => n.depth));
  const colW = new Array(maxD + 1).fill(0);
  for (const n of nodes) colW[n.depth] = Math.max(colW[n.depth], n.w + 64);
  const xR = []; let acc = PAD;
  for (let d = 0; d <= maxD; d++) { xR[d] = acc; acc += colW[d] + COLGAP; }
  const width = acc + PAD;
  for (const n of nodes) n.x = width - xR[n.depth];
  return {nodes, width, height: (slot + 1) * ROW + PAD};
}
let LAYOUT = null;
function drawTree(keepView) {
  LAYOUT = layout();
  const {nodes} = LAYOUT, byId = new Map(nodes.map(n => [n.id, n]));
  while (SVG.firstChild) SVG.removeChild(SVG.firstChild);
  const g = el('g', {id: 'cam'}), gE = el('g', {}), gN = el('g', {});
  g.append(gE, gN); SVG.appendChild(g);
  for (const n of nodes) {
    if (!n.parent) continue;
    const p = byId.get(n.parent);
    const x1 = p.x - p.w - 20, y1 = p.y, x2 = n.x + 50, y2 = n.y, mx = (x1 + x2) / 2;
    const matn = isMatn(n.id), key = edgeKey(n.parent, n.id);
    const q = matn ? undefined : EP[key];
    const inMap = !matn && MAPG[n.id] === n.parent;
    const d = `M${x1.toFixed(1)},${y1.toFixed(1)} C${mx.toFixed(1)},${y1.toFixed(1)} ${mx.toFixed(1)},${y2.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`;
    if (!matn && n.parent !== ROOT) gE.appendChild(el('path', {class: 'ehit', d, 'data-edge': key}));
    gE.appendChild(el('path', {class: 'edge' + (inMap ? ' map' : '') + (q !== undefined && q < 0.5 ? ' low' : '') + (selEdge === key ? ' sel' : ''),
      'stroke-width': (q === undefined ? 1 : 0.8 + 1.8 * q).toFixed(2), d, opacity: q === undefined ? 0.45 : (0.35 + 0.65 * q).toFixed(2)}));
  }
  for (const n of nodes) {
    const linked = !isMatn(n.id) && N[n.id] && N[n.id].kg && ['high', 'medium'].includes(N[n.id].kg.confidence);
    const t = el('text', {x: n.x, y: n.y, 'text-anchor': 'start', direction: 'rtl',
      class: 'lbl' + (isMatn(n.id) ? ' matn' : '') + (linked ? ' linked' : '') + (selected === n.id ? ' sel' : '') +
             (filter && normQ(isMatn(n.id) ? D.attestations[+n.id.slice(1)].text : nm(n.id)).includes(normQ(filter)) ? ' hit' : ''),
      'data-id': n.id}, labelOf(n.id));
    t.appendChild(el('title', {}, isMatn(n.id) ? D.attestations[+n.id.slice(1)].text : nm(n.id)));
    gN.appendChild(t);
    if (!isMatn(n.id) && n.id !== ROOT) {
      const bits = [];
      if (N[n.id].year !== null) bits.push(fa(N[n.id].year));
      if (witnesses[n.id]) bits.push(fa(witnesses[n.id]) + '✎');
      if (bits.length) gN.appendChild(el('text', {x: n.x + 8, y: n.y, class: 'yr', 'text-anchor': 'start', direction: 'ltr'}, bits.join(' · ')));
    }
    if (childrenOf(n.id).length) {
      const kx = n.x - n.w - 12;
      gN.appendChild(el('circle', {cx: kx, cy: n.y, r: 6, class: 'knob' + (n.collapsed ? ' closed' : ''), 'data-tw': n.id}));
      if (n.collapsed) gN.appendChild(el('text', {x: kx, y: n.y + 0.5, class: 'count'}, fa(n.hidden_n > 99 ? 99 : n.hidden_n)));
    }
  }
  syncViewBox();
  if (!keepView) { fit(true); sizeBox(); }
  applyView();
}
function sizeBox() {
  const maxH = Math.max(300, Math.round(window.innerHeight * 0.78));
  const h = Math.max(300, Math.min(maxH, Math.round(LAYOUT.height * view.k + 24)));
  if (Math.abs(h - BOX.getBoundingClientRect().height) > 2) { BOX.style.height = h + 'px'; syncViewBox(); fit(true); }
}
function syncViewBox() { const r = BOX.getBoundingClientRect(); SVG.setAttribute('viewBox', `0 0 ${Math.max(1, Math.round(r.width))} ${Math.max(1, Math.round(r.height))}`); }
function applyView() { const c = SVG.querySelector('#cam'); if (c) c.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`); }
function fit(all) {
  const r = BOX.getBoundingClientRect();
  const kx = r.width / LAYOUT.width, ky = r.height / LAYOUT.height;
  view.k = Math.max(0.08, Math.min(1.6, all ? Math.min(kx, ky) : Math.min(1, kx)));
  view.x = r.width - LAYOUT.width * view.k; view.y = 6; applyView();
}
function zoomBy(f, cx, cy) {
  const r = BOX.getBoundingClientRect();
  const px = cx ?? r.width / 2, py = cy ?? r.height / 2, k2 = Math.max(0.08, Math.min(4, view.k * f));
  view.x = px - (px - view.x) * (k2 / view.k); view.y = py - (py - view.y) * (k2 / view.k); view.k = k2; applyView();
}
document.getElementById('zin').onclick = () => zoomBy(1.25);
document.getElementById('zout').onclick = () => zoomBy(0.8);
document.getElementById('zfit').onclick = () => fit(true);
BOX.addEventListener('wheel', e => {
  e.preventDefault();
  const r = BOX.getBoundingClientRect();
  if (e.ctrlKey || e.metaKey) zoomBy(e.deltaY < 0 ? 1.12 : 0.89, e.clientX - r.left, e.clientY - r.top);
  else { view.x -= e.deltaX; view.y -= e.deltaY; applyView(); }
}, {passive: false});
let drag = null;
BOX.addEventListener('pointerdown', e => {
  if (e.target.closest('text,circle,button,.ehit')) return;
  drag = {x: e.clientX, y: e.clientY, vx: view.x, vy: view.y}; BOX.classList.add('drag'); BOX.setPointerCapture(e.pointerId);
});
BOX.addEventListener('pointermove', e => { if (!drag) return; view.x = drag.vx + e.clientX - drag.x; view.y = drag.vy + e.clientY - drag.y; applyView(); });
const endDrag = () => { drag = null; BOX.classList.remove('drag'); };
BOX.addEventListener('pointerup', endDrag); BOX.addEventListener('pointercancel', endDrag);
SVG.addEventListener('click', e => {
  const knob = e.target.closest('[data-tw]');
  if (knob) { const v = knob.getAttribute('data-tw'); open.has(v) ? open.delete(v) : open.add(v); drawTree(true); return; }
  const ed = e.target.closest('[data-edge]');
  if (ed) { selectEdge(ed.getAttribute('data-edge')); return; }
  const t = e.target.closest('text[data-id]');
  if (t) selectNode(t.getAttribute('data-id'));
});
window.addEventListener('resize', () => { if (LAYOUT) { syncViewBox(); applyView(); } });

/* ---------- inspector ---------- */
const INS = document.getElementById('inspect');
function barList(items) {
  return '<div class="bars">' + items.map(([lab, p]) =>
    `<div class="bar"><div class="track"><div class="fill" style="width:${Math.max(0, Math.min(1, p)) * 100}%"></div><div class="lab">${lab}</div></div><div class="pct">${fmt(p, 3)}</div></div>`).join('') + '</div>';
}
const nodeBtn = id => `<button class="lnk ar" data-go="${esc(id)}">${esc(nm(id))}</button>`;
const edgeBtn = (key, label) => `<button class="lnk" data-goedge="${esc(key)}">${label}</button>`;
const SRC_FA = {diagram: 'از نمودار', 'hadith-kg': 'از پایگاه رجالی', interpolated: 'برآورد (درون‌یابی در سند)'};
const CONF_FA = {high: ['زیاد', 'hi'], medium: ['متوسط', 'me'], low: ['کم — تأییدنشده', 'lo'], none: ['یافت نشد', 'lo']};
function yearText(n) {
  if (n.year === null) return '—';
  const d = n.death;
  let s = d && d[0] !== d[1] ? `${fa(Math.round(d[0]))} تا ${fa(Math.round(d[1]))} ق` : `${fa(n.year)} ق`;
  if (n.date_approx) s = 'حدود ' + s;
  return `${s} <span class="tag">${SRC_FA[n.date_source] || '—'}</span>`;
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
function selectNode(id) {
  selected = id; selEdge = null;
  if (isMatn(id)) inspectMatn(+id.slice(1)); else inspectNode(id);
  drawTree(true);
  if (window.innerWidth <= 900) document.getElementById('inspector').scrollIntoView({behavior: 'smooth', block: 'start'});
}
function selectEdge(key) {
  selEdge = key; selected = null; inspectEdge(key); drawTree(true);
  if (window.innerWidth <= 900) document.getElementById('inspector').scrollIntoView({behavior: 'smooth', block: 'start'});
}
INS.addEventListener('click', e => {
  const a = e.target.closest('[data-go]'); if (a) { selectNode(a.getAttribute('data-go')); return; }
  const b = e.target.closest('[data-goedge]'); if (b) selectEdge(b.getAttribute('data-goedge'));
});
function inspectRoot() {
  INS.innerHTML = `<h3 class="ar">حدیث «ستة لعنهم الله»</h3>
    <p>${fa(D.companions.length)} طریق از صحابه ثبت شده است. ریشه‌ی نمودار خودِ حدیث است، نه یک راوی. نامزدهای «مبدأ» عبارت‌اند از: صحابه، حلقه‌های مشترک زیر آنان، و یک مبدأ ناشناخته بالای همه‌ی صحابه.</p>
    <dl class="dl"><dt>شاهدها</dt><dd>${fa(D.attestations.length)}</dd><dt>راویان</dt><dd>${fa(Object.keys(N).length)}</dd>
    <dt>طریق‌ها</dt><dd>${D.companions.map(c => nodeBtn(c)).join('، ')}</dd></dl>
    <p class="help">روی هر نام در درخت کلیک کنید تا اطلاعات راوی، روی هر یال برای اطلاعات پیوند استاد و شاگرد، و روی متن‌های نارنجی برای متن کامل حدیث.</p>`;
}
function inspectNode(id) {
  if (id === ROOT) return inspectRoot();
  const n = N[id], p = D.recorded_parent[id];
  const pp = Object.entries(n.parent_posterior || {}).sort((a, b) => b[1] - a[1]).map(([u, q]) => [esc(nm(u)), q]);
  const inKey = p ? edgeKey(p, id) : null;
  const outs = (kids[id] || []).map(c => [c, E[edgeKey(id, c)]]);
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
      <dt>استاد ثبت‌شده</dt><dd>${p ? nodeBtn(p) + ' · ' + edgeBtn(inKey, 'جزئیات یال') : 'سرآغاز یک طریق'}</dd>
      <dt>شاگردان ثبت‌شده</dt><dd>${outs.length ? outs.map(([c, e]) => `${nodeBtn(c)} <span class="help">(${edgeBtn(edgeKey(id, c), 'یال')}${e && e.posterior !== null && e.posterior !== undefined ? ' · ' + fmt(e.posterior, 2) : ''})</span>`).join('، ') : '—'}</dd>
      <dt>شاهدهای زیرین</dt><dd>${fa(witnesses[id])}</dd>
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
  const rows = ranked.map(s => `<tr class="${s === best ? 'best' : ''}"><td class="ar">${esc(srcName(s))}</td>
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
  const departs = Object.entries(MAPG).filter(([v, p]) => D.recorded_parent[v] !== p);
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
      ${departs.length ? departs.map(([v, p]) => `<tr><td class="ar">${esc(nm(v))}</td><td class="ar">${esc(nm(D.recorded_parent[v] || ''))}</td><td class="ar">${esc(nm(p))}</td></tr>`).join('')
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
  if (id === 'tree' && LAYOUT) { syncViewBox(); applyView(); }
}
nav.addEventListener('click', e => { const b = e.target.closest('button'); if (b) show(b.id.slice(4)); });
document.addEventListener('click', e => {
  const st = e.target.closest('[data-showtree]');
  if (st) { show('tree'); selectNode(st.getAttribute('data-showtree')); return; }
  const tr = e.target.closest('tr[data-goedge]');
  if (tr) { show('tree'); selectEdge(tr.getAttribute('data-goedge')); }
});

/* ---------- controls ---------- */
const sel = document.getElementById('branchsel');
sel.innerHTML = '<option value="">همه‌ی طریق‌ها</option>' + D.companions.map(c => `<option value="${esc(c)}">${esc(nm(c))}</option>`).join('');
sel.addEventListener('change', () => { branchFilter = sel.value; if (branchFilter) open.add(branchFilter); drawTree(); });
document.getElementById('q').addEventListener('input', e => { filter = e.target.value.trim(); drawTree(); });
document.getElementById('expandall').onclick = () => { open.add(ROOT); for (const v of Object.keys(N)) open.add(v); drawTree(); };
document.getElementById('collapseall').onclick = () => { open.clear(); open.add(ROOT); drawTree(); };

drawTree();
inspectRoot();
show('tree');
if (document.fonts) {
  const relayout = () => { wCache.clear(); drawTree(); };
  Promise.all([document.fonts.load('14px "Noto Naskh Arabic"', 'محمد'), document.fonts.load('600 14px "Noto Naskh Arabic"', 'محمد'),
               document.fonts.load('14px "Vazirmatn"', 'مبدأ'), document.fonts.ready]).then(relayout).catch(relayout);
}

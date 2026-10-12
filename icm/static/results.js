/* The results of an analysis, as sub-tabs: step 6 of the wizard and the standalone results page (scripts/make_html.py).

   ICMResults.mount(host, D, ctx)
     D    the viewer bundle (hadith_phylogeny.report.build_bundle; icm.infer adds tiers and date sources)
     ctx  what the wizard knows besides (all optional): R (step-6 results), units (the align stage's units, index-aligned
          with D.variants), texts {wid: {matn, marks}} (search stage, clause colours), clauses [text], nodes (graph stage),
          narratorCard(nid), goStep4(occ), standalone (url), tab, onTab(tab), help(anchor, body)

   Tabs: نتیجه (the verdict), درخت (the recorded chains with the model's results on them, and a details panel),
   متن‌ها (the texts, coloured by part, a chosen variant highlighted), مبدأ (every candidate source), درخت بیشینه (how
   concentrated the posterior over trees is), حساسیت, روش و محدودیت‌ها, راهنما. Choosing a variant unit (in درخت or متن‌ها)
   colours every narrator by the probability that his text had it and badges every book with what its text has. */
(function () {
  const UNK = 'UNKNOWN_SOURCE', UNK_NAME = 'مبدأ بی‌نام (بالاتر از همه‌ی راویان ثبت‌شده)';
  const FA = '۰۱۲۳۴۵۶۷۸۹';
  const fa = s => String(s).replace(/[0-9]/g, d => FA[d]).replace(/\./g, '٫').replace(/-/g, '−');
  const NEG = Number.NEGATIVE_INFINITY;
  const fmt = (x, d = 3) => (x === null || x === undefined || Number.isNaN(x)) ? '—'
    : (!Number.isFinite(x) ? (x < 0 ? '−∞' : '∞') : (x < 0 ? '⁦' + fa(Number(x).toFixed(d)) + '⁩' : fa(Number(x).toFixed(d))));
  const pct = x => x === null || x === undefined ? '—' : fa((100 * x).toFixed(1)) + '٪';
  const sci = x => {
    if (x === null || x === undefined) return '—';
    if (x === 0) return '۰';
    if (x >= 0.001) return fa(x.toFixed(3));
    const e = Math.floor(Math.log10(x)), m = x / Math.pow(10, e);
    return `<span dir="ltr" class="num">${fa(m.toFixed(1))}×۱۰<sup>${fa(e)}</sup></span>`;
  };
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const HUES = [210, 28, 140, 330, 48, 268, 178, 0, 95, 300, 235, 15], hue = i => HUES[((i % HUES.length) + HUES.length) % HUES.length];
  const ulab = l => ({ absent: 'ندارد', present: 'دارد', '*other*': 'دیگر' }[l] || (/^\d+$/.test(l) ? fa(l) : l));
  const shortName = (s, n = 4) => { const w = String(s).replace(/\s*،\s*/g, ' ').split(/\s+/).filter(Boolean); return w.length > n ? w.slice(0, n).join(' ') + '…' : w.join(' '); };
  /* the sitta family of the published page: its hand-named clauses */
  const CLAUSES = {
    zaid_fi_kitab: { fa: 'الزائد في كتاب الله', keys: ['الزائد في كتاب', 'الزايد في كتاب', 'المغير لكتاب'] },
    mukaddhib_bi_qadar: { fa: 'المكذّب بقدر الله', keys: ['المكذب بقدر'] },
    mutasallit_jabarut: { fa: 'المتسلّط بالجبروت', keys: ['المتسلط', 'المسلط'] },
    mustahill_maharim: { fa: 'المستحلّ لمحارم الله', keys: ['المستحل محارم', 'المستحل لمحارم'] },
    tarik_sunnati: { fa: 'التارك لسنّتي', keys: ['التارك لسنتي', 'التارك للسنة', 'التارك لسنة'] },
    mustahill_itra: { fa: 'المستحلّ من عترتي', keys: ['المستحل من عترتي', 'المستحل لحرم', 'من عترتي ما حرم'] },
    mutakabbir: { fa: 'المتكبّر على عباد الله', keys: ['المتكبر على عباد'] },
    count_word: { fa: 'شمار (سبعة / ستة / …)', keys: ['سبعة', 'ستة', 'خمسة', 'اربعة', 'أربعة'] },
  };
  const COUNT_FA = { "sab'a": 'سبعة (هفت)', sitta: 'ستة (شش)', khamsa: 'خمسة (پنج)', "arba'a": 'أربعة (چهار)' };
  /* Arabic letters as written (diacritics, hamza and ya/kaf forms) for finding a unit's words in a text */
  const DIA = '[\\u0610-\\u061A\\u064B-\\u065F\\u0670\\u06D6-\\u06ED\\u0640\\u200c]*';
  const LET = { 'ا': '[اأإآٱ]', 'أ': '[اأإآٱ]', 'إ': '[اأإآٱ]', 'آ': '[اأإآٱ]', 'ی': '[يیىئ]', 'ي': '[يیىئ]', 'ى': '[يیى]', 'ک': '[كک]', 'ك': '[كک]',
    'ه': '[هةۀھ]', 'ة': '[هةۀ]', 'ۀ': '[هةۀ]' };
  /* number units are labelled by the digit; the text has the word */
  const NUMW = { 3: 'ثلاث ثلاثه', 4: 'اربع اربعه', 5: 'خمس خمسه', 6: 'ست سته', 7: 'سبع سبعه', 8: 'ثمان ثمانیه', 9: 'تسع تسعه', 10: 'عشر عشره', 12: 'اثنا اثنی' };
  const altRe = forms => { const rs = forms.map(f => wordsRe(f)).filter(Boolean); return rs.length ? new RegExp(rs.map(r => '(?:' + r.source + ')(?![\\u0600-\\u06FF])').join('|'), 'g') : null; };
  const wordsRe = s => { const ws = String(s).replace(/[|]/g, ' ').trim().split(/\s+/).filter(w => w && w !== '*other*');
    return ws.length ? new RegExp(ws.map(w => [...w].map(ch => (LET[ch] || ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) + DIA).join('')).join('[\\s\\u200c]+(?:و' + DIA + '[\\s\\u200c]*)?'), 'g') : null; };

  function mount(host, D, ctx = {}) {
    const N = D.narrators, E = D.edges || {}, GP = D.graph_posterior || {}, EP = GP.edge_prob || {}, MAPG = GP.map_graph || {};
    const sp = D.source_posterior || {}, ATT = D.attestations, R = ctx.R;
    const lz = s => !sp[s] || sp[s].log_Z === null ? NEG : sp[s].log_Z;
    const nm = id => id === UNK ? UNK_NAME : String(id).startsWith(UNK + '#') ? 'راوی بی‌نامِ میانیِ ویژه‌ی همین راوی' : (N[id] ? N[id].name : id);
    const best = D.map_source, ranked = Object.keys(sp).sort((a, b) => lz(b) - lz(a)), runner = ranked.find(s => s !== best);
    const teachersOf = {}, studentsOf = {}, witnesses = {};
    ATT.forEach(a => {
      new Set(a.sanad).forEach(n => { witnesses[n] = (witnesses[n] || 0) + 1; });
      a.sanad.forEach((v, i) => { if (!i) return; const u = a.sanad[i - 1]; (teachersOf[v] ||= new Set()).add(u); (studentsOf[u] ||= new Set()).add(v); });
    });
    const heads = [...new Set(ATT.map(a => a.sanad[0]))].sort((a, b) => ATT.filter(x => x.sanad[0] === b).length - ATT.filter(x => x.sanad[0] === a).length);
    const wid = a => String(a.id).split('#')[0];
    const edgeKey = (p, c) => p + '>' + c;
    const tiers = id => (ctx.nodes && ctx.nodes[id] && ctx.nodes[id].tiers) || (N[id] && N[id].tiers) || [];
    const cat = id => { const t = tiers(id); if (!t.length) return /^anon:/.test(id) ? 'anon' : /^(name|rel):/.test(id) ? 'name' : 'exact';
      return t.includes('exact') ? 'exact' : t.includes('chosen') || t.includes('derived') ? 'chosen' : t.includes('default') ? 'auto' : t.includes('anon') ? 'anon' : 'name'; };
    const year = id => N[id] && N[id].death ? (N[id].death[0] + N[id].death[1]) / 2 : (N[id] ? N[id].year : null);

    /* variant units: their names, states and parts of the text */
    const U = k => ctx.units && ctx.units[k] && ctx.units[k].name === D.variants[k].name ? ctx.units[k] : null;
    const clauseOf = k => { const u = U(k); if (u && u.clause != null) return u.clause; const m = /^c(\d+)/.exec(D.variants[k].name); return m ? +m[1] : k; };
    const unitWords = k => { const n = D.variants[k].name; return CLAUSES[n] ? CLAUSES[n].fa : n.includes(':') ? n.slice(n.indexOf(':') + 1) : n; };
    const unitName = k => CLAUSES[D.variants[k].name] ? CLAUSES[D.variants[k].name].fa : `بخش ${fa(clauseOf(k) + 1)}: ${unitWords(k)}`;
    const stateName = (k, j) => { const l = D.variants[k].labels[j]; return COUNT_FA[l] || ulab(l); };
    const presence = k => D.variants[k].n_states === 2 && D.variants[k].labels[0] === 'absent';
    const SITTA = D.variants.some(v => CLAUSES[v.name]);
    const KIND_FA = { presence: 'بودن یا نبودن', lexical: 'واژه‌ی جایگزین', numeric: 'عدد' };

    /* ---------- skeleton: sub-tabs and panels ---------- */
    const TABS = [['result', 'نتیجه'], ['tree', 'درخت'], ['texts', 'متن‌ها'], ['sources', 'مبدأ'], ['graph', 'درخت بیشینه'],
      ['sens', 'حساسیت'], ['method', 'روش و محدودیت‌ها'], ['gloss', 'راهنما']];
    const root = document.createElement('div');
    root.className = 'rv';
    root.innerHTML = `<nav class="rvtabs" role="tablist">${TABS.map(([id, t]) => `<button type="button" role="tab" data-tab="${id}">${t}</button>`).join('')}</nav>
      ${TABS.map(([id]) => `<section class="rvp" data-p="${id}" hidden></section>`).join('')}`;
    host.replaceChildren(root);
    const panel = id => root.querySelector(`[data-p="${id}"]`);
    const built = {};
    let cur = null, tree = null, lens = null, INS = null, selected = null;
    const helpBtn = key => `<button type="button" class="info" data-help="${key}" title="راهنما">i</button>`;
    function show(id) {
      if (!TABS.some(t => t[0] === id)) id = 'result';
      cur = id;
      root.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === id)));
      root.querySelectorAll('.rvp').forEach(p => { p.hidden = p.dataset.p !== id; });
      if (!built[id]) { built[id] = true; ({ result: resultPanel, tree: treePanel, texts: textsPanel, sources: sourcesPanel, graph: graphPanel, sens: sensPanel, method: methodPanel, gloss: glossPanel })[id](panel(id)); }
      if (id === 'texts') drawTexts();
      if (ctx.onTab) ctx.onTab(id);
    }

    /* ---------- help texts ---------- */
    const HELP = {
      best: 'راوی (یا منبعی) که بیشترین احتمال پسین را دارد که قدیمی‌ترین منبع قابل‌بازیابی همه‌ی این روایت‌ها باشد.',
      post: 'P(S=s | متن‌ها، سندها، رجال): احتمال مبدأ بودن، پس از دیدن همه‌ی داده‌ها و با درنظرگرفتن همه‌ی درخت‌های ممکن.',
      bf: `تفاضل log Z با رقیب بعدی (${runner ? nm(runner) : '—'}). عدد ۵ یعنی داده حدود ۱۵۰ برابر بیشتر از این گزینه پشتیبانی می‌کند؛ بیش از ۱۰ قاطع است.`,
      mass: 'چه کسری از احتمال پسین (برای مبدأ برتر) روی یک درخت انتقال واحد، یعنی درخت بیشینه‌ی پسین (MAP)، متمرکز است.',
      distinct: 'چند درخت متفاوت در میان ذرات نمونه‌برداری (SMC) باقی مانده‌اند. عدد کم یعنی پسین متمرکز است، ولی برآورد سهم درخت‌های رقیب نیز نامطمئن‌تر است.',
      kg: 'راویانی که با اطمینان زیاد یا متوسط به زندگی‌نامه‌ای در پایگاه رجالی پیوند خورده‌اند.',
      tree: 'سندها همان‌گونه که ثبت شده‌اند، از نخستین راوی (راست) تا گردآورنده و کتاب (چپ). نتیجه‌ی مدل روی یال‌هاست: یال آبی در محتمل‌ترین درخت، یال قرمزِ خط‌چین احتمال پسین زیر ۰٫۵. حلقه دور یک راوی یعنی نامزد مبدأ است (اندازه = احتمال)؛ ★ محتمل‌ترین. «مبدأ بی‌نام» در لبه‌ی راست است و طریق‌هایی که در محتمل‌ترین درخت مستقیم به آن می‌رسند با خط‌چین به آن وصل‌اند. روی نام، یال یا نام کتاب بزنید تا جزئیات کنار درخت بیاید.',
      lens: 'یک واحد اختلاف متن را برگزینید: هر راوی به رنگ احتمالِ این‌که متنی که نقل کرده این حالت را داشته باشد درمی‌آید (پررنگ = محتمل)، و کنار نام هر کتاب، آنچه متن خودش دارد. دایره‌ی خط‌چین یعنی این راوی در درخت‌های نمونه‌برداری‌شده نیامده است.',
      texts: 'متن هر شاهد همان‌گونه که در کتاب آمده. رنگ‌ها بخش‌های حدیث مبدأاند (همان رنگ‌های مرحله‌ی ۳). با برگزیدن یک واحد اختلاف، واژه‌های آن در هر متن نشان داده می‌شود و برچسب کنار هر متن می‌گوید متن چه دارد.',
      sources: 'log Z (لگاریتم «شاهد») احتمال کل داده است اگر این راوی مبدأ باشد، میانگین روی همه‌ی درخت‌ها. −∞ یعنی تحت هیچ درخت نمونه‌برداری‌شده‌ای همه‌ی شاهدها از این راوی قابل دسترسی نیستند (مثلاً پس از او درگذشته‌اند). ESS (اندازه‌ی مؤثر نمونه) کیفیت برآورد را نشان می‌دهد؛ زیر ۱۰ نامطمئن است.',
      map: 'درخت بیشینه‌ی پسین (MAP) محتمل‌ترین درخت انتقال برای مبدأ برتر است. اگر سهمش بزرگ باشد، پسین روی یک ساختار متمرکز است؛ کم بودن شمار درخت‌های متمایز همچنین یعنی نمونه‌گیر روی چند درخت جمع شده و سهم رقیبان نامطمئن است.',
      unsure: 'یال‌های ثبت‌شده‌ای که احتمال پسینشان کمتر از ۰٫۹۹۹ است: جاهایی که مدل استاد دیگری (حذف یک واسطه، هم‌دوره‌ی هم‌طریق یا مبدأ بی‌نام) را محتمل‌تر یا هم‌اندازه می‌داند.',
      departs: 'شاگردانی که استادشان در درخت بیشینه هیچ‌یک از استادهای ثبت‌شده‌ی او در سندهای این خانواده نیست.',
    };
    root.addEventListener('click', ev => {
      const hb = ev.target.closest('[data-help]');
      if (hb) { ev.preventDefault(); ev.stopPropagation(); (ctx.help || localPop)(hb, HELP[hb.dataset.help] || hb.dataset.help); return; }
      const t = ev.target.closest('[data-tab]');
      if (t) { show(t.dataset.tab); return; }
      const st = ev.target.closest('[data-showtree]');
      if (st) { goTree(st.getAttribute('data-showtree')); return; }
      const ge = ev.target.closest('[data-goedge]');
      if (ge) { show('tree'); selectEdge(ge.getAttribute('data-goedge')); return; }
      const g = ev.target.closest('[data-go]');
      if (g) { show('tree'); selectNode(g.getAttribute('data-go'), true); return; }
      const nc = ev.target.closest('[data-card]');
      if (nc && ctx.narratorCard) { ctx.narratorCard(nc.getAttribute('data-card')); return; }
      const s4 = ev.target.closest('[data-step4]');
      if (s4 && ctx.goStep4) ctx.goStep4(s4.getAttribute('data-step4'));
    });
    root.addEventListener('change', ev => { if (ev.target.matches('.lenssel')) setLens(ev.target.value); });
    let POP = null;
    function localPop(anchor, body) {
      const again = POP && POP._a === anchor;
      if (POP) { POP.remove(); POP = null; }
      if (again) return;
      POP = document.createElement('div'); POP.className = 'pop'; POP.textContent = body; POP._a = anchor; document.body.append(POP);
      const r = anchor.getBoundingClientRect(), pr = POP.getBoundingClientRect();
      POP.style.left = Math.min(Math.max(8, r.right - pr.width), innerWidth - pr.width - 8) + 'px';
      POP.style.top = (r.bottom + 6 + pr.height > innerHeight - 8 && r.top - pr.height - 6 > 8 ? r.top - pr.height - 6 : r.bottom + 6) + 'px';
    }
    document.addEventListener('click', ev => { if (POP && !POP.contains(ev.target)) { POP.remove(); POP = null; } });
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && POP) { POP.remove(); POP = null; } });

    /* ---------- the variant lens ---------- */
    const lensOptions = () => `<option value="">— بدون رنگ‌آمیزی متن —</option>` + D.variants.map((v, k) => presence(k) || v.n_states < 2
      ? `<option value="${k}:1">${esc(unitName(k))}</option>`
      : `<optgroup label="${esc(unitName(k))}">${v.labels.map((l, j) => `<option value="${k}:${j}">${esc(unitName(k))} = ${esc(stateName(k, j))}</option>`).join('')}</optgroup>`).join('');
    const lensSel = () => `<label class="small lensw">واحد اختلاف متن: <select class="lenssel">${lensOptions()}</select> ${helpBtn('lens')}</label>`;
    const lensColor = (k, p) => `hsl(${hue(clauseOf(k))} ${Math.round(25 + 60 * p)}% ${Math.round(86 - 50 * p)}%)`;
    function setLens(v) {
      root.querySelectorAll('.lenssel').forEach(s => { s.value = v; });
      if (!v) lens = null;
      else {
        const [k, j] = v.split(':').map(Number);
        lens = { k, j,
          fill: n => { const a = N[n] && N[n].ancestral; if (!a || !a[k]) return null; return lensColor(k, a[k][j] || 0); },
          tip: n => { const a = N[n] && N[n].ancestral; return a && a[k] ? `«${unitName(k)}» = ${stateName(k, j)}: احتمال ${fa(Math.round(100 * (a[k][j] || 0)))}٪` : 'در درخت‌های نمونه‌برداری‌شده نیامده'; },
          badge: ai => { const x = ATT[ai].row[k]; return { t: x < 0 ? '؟' : stateName(k, x), c: x === j ? lensColor(k, 1) : 'var(--muted)' }; },
          legend: lensLegend(k, j) };
      }
      if (tree) tree.setLens(lens ? { fill: lens.fill, tip: lens.tip, badge: lens.badge, legend: lens.legend } : null);
      if (built.texts) drawTexts();
    }
    function lensLegend(k, j) {
      const s = document.createElement('span');
      s.innerHTML = `«${esc(unitName(k))}» = ${esc(stateName(k, j))}: ` + [0, 0.5, 1].map(p => `<span><b style="background:${lensColor(k, p)}"></b>${fa(Math.round(100 * p))}٪</span>`).join(' ');
      return s;
    }

    /* ---------- نتیجه ---------- */
    function resultPanel(el) {
      const ks = D.kg_stats || {}, gp = GP;
      const chips = [
        ['محتمل‌ترین مبدأ', `<span class="v ar">${best === UNK ? '★ ' : ''}${esc(nm(best))}</span>`, 'best', best === UNK ? '<button type="button" class="lnk small" data-showtree="' + UNK + '">این یعنی چه؟</button>' : `<button type="button" class="lnk small" data-showtree="${esc(best)}">در درخت</button>`],
        ['احتمال پسین آن', `<span class="v num">${sci(sp[best] ? sp[best].posterior : null)}</span>`, 'post'],
        ['لگاریتم عامل بیز در برابر رقیب', `<span class="v num">${runner ? fmt(lz(best) - lz(runner), 1) : '—'}</span>`, 'bf', runner ? `<span class="small muted">در برابر ${esc(shortName(nm(runner), 5))}</span>` : ''],
        ['سهم محتمل‌ترین درخت', `<span class="v num">${pct(gp.map_mass)}</span>`, 'mass'],
        ['درخت‌های متمایز در نمونه', `<span class="v num">${fa(gp.n_distinct ?? '—')} از ${fa(gp.n_particles ?? '—')}</span>`, 'distinct'],
        ['راویان پیوندخورده با کتب رجال', `<span class="v num">${fa(ks.linked ?? 0)} از ${fa(ks.total ?? 0)}</span>`, 'kg']];
      const top = ranked.slice(0, 12);
      el.innerHTML = `<div class="card"><h2 class="ar">${esc(D.heading || D.family || '')}</h2>
          <div class="small muted">${fa(ATT.length)} شاهد (متن) · ${fa(Object.keys(N).length)} راوی · ${fa(D.variants.length)} واحد اختلاف متن · تاریخ اجرا ${fa(D.generated || '')}</div>
          <div class="vchips">${chips.map(([k, v, h, x]) => `<div class="vchip"><span class="k">${k} ${helpBtn(h)}</span>${v}${x || ''}</div>`).join('')}</div>
          ${R && R.credible_set_95 ? `<p class="small">مجموعه‌ی اطمینان ۹۵٪: <span class="ar">${R.credible_set_95.map(esc).join('، ')}</span></p>` : ''}
        </div>
        ${best === UNK ? `<div class="card"><h2>${esc(UNK_NAME)}: این یعنی چه؟</h2><div class="body">${originHtml()}</div></div>` : ''}
        <div class="card"><h2>نامزدهای مبدأ ${helpBtn('sources')}</h2><div class="scroll"><table class="rvt">
          <thead><tr><th>نامزد</th><th>احتمال پسین</th><th class="n">log Z</th></tr></thead><tbody>
          ${top.map(s => `<tr class="click${s === best ? ' best' : ''}" data-showtree="${esc(s)}"><td class="ar">${esc(nm(s))}</td>
            <td><div class="pbar"><i style="width:${Math.round(100 * (sp[s].posterior || 0))}%"></i></div> ${sci(sp[s].posterior)}</td><td class="n">${fmt(lz(s), 1)}</td></tr>`).join('')}
          </tbody></table></div>${ranked.length > top.length ? `<p class="small muted">${fa(ranked.length - top.length)} نامزد دیگر در زبانه‌ی «<button type="button" class="lnk" data-tab="sources">مبدأ</button>».</p>` : ''}</div>
        ${R ? `<div class="card"><h2>اجرا</h2><p class="small muted">محاسبه: ${({ browser: 'در مرورگر یک کاربر', helper: 'روی رایانه‌ی کمکی', server: 'روی سرور' })[(R.provenance || { kind: 'server' }).kind] || '—'}${R.provenance && R.provenance.workers ? ` (${fa(R.provenance.workers)} پردازه)` : ''}؛ نتیجه‌ی رایانه‌های کاربران بازتولید مستقل نشده است و اعتبارش همان ورود کاربر است.</p>
          <p class="small muted">${esc(R.preset || '')} — ${fa(R.n_particles ?? '—')} ذره، ${fa(R.n_steps ?? '—')} دما، ${fa(R.seconds ?? '—')} ثانیه. این نتیجه به شجره‌ی تأییدشده و پارامترهای ثابت مدل وابسته است؛ زبانه‌ی «<button type="button" class="lnk" data-tab="method">روش و محدودیت‌ها</button>» را بخوانید.${ctx.standalone ? ` · <a href="${esc(ctx.standalone)}" target="_blank" rel="noopener">صفحه‌ی جدا (برای چاپ یا هم‌رسانی)</a>` : ''}</p></div>` : ''}`;
    }
    /* the unnamed origin is not a narrator: what it stands for, why it can win, and what pushes it up */
    function originHtml() {
      const lines = heads.map(h => [h, ATT.filter(a => a.sanad[0] === h).length]);
      const direct = lines.filter(([h]) => MAPG[h] === UNK), under = lines.filter(([h]) => MAPG[h] && MAPG[h] !== UNK);
      const late = lines.filter(([h]) => cat(h) === 'anon' || cat(h) === 'name' || (year(h) ?? 0) > 150);
      const rn = runner ? nm(runner) : 'یک راوی نام‌دار';
      const lst = (xs, s4) => xs.map(([h, k]) => `${nodeBtn(h)} <span class="help">(${fa(k)} سند)</span>${s4 ? step4Btn(h) : ''}`).join('، ');
      return `<p>این یک راوی در درخت نیست و مدل کسی را «پیدا» نکرده است. نقطه‌ای است بالاتر از نخستین راویِ همه‌ی سندها: جایی که گوینده‌ی حدیث (مثلاً پیامبر، که در سندها نیامده) یا منبعی کهن‌تر و ثبت‌نشده می‌نشیند. معنایش این است: <b>هیچ‌یک از راویان ثبت‌شده</b> منبع مشترک همه‌ی متن‌ها نیست.</p>
        <dl class="dl">${sp[UNK] ? `<dt>احتمال پسین</dt><dd>${sci(sp[UNK].posterior)} · <bdi dir="ltr">log Z ${fmt(lz(UNK), 1)}</bdi></dd>` : ''}
          ${best === UNK && runner ? `<dt>در برابر رقیب</dt><dd><bdi dir="ltr">log BF ${fmt(lz(UNK) - lz(runner), 1)}</bdi> در برابر ${esc(rn)}</dd>` : ''}
          <dt>نخستین راویان سندها</dt><dd>${fa(lines.length)} نام متفاوت</dd></dl>
        <h3>سندها از چند نفر آغاز می‌شوند؛ این یعنی چه؟</h3>
        <p class="help">هر نخستین راوی سرِ یک «طریق» است. اگر یک راوی نام‌دار (مثلاً ${esc(rn)}) منبع همه باشد، باید همه‌ی طریق‌های دیگر هم در واقع از او گذشته باشند و سندشان نام او را انداخته باشد؛ مدل این افتادگی‌ها را در هر سند جریمه می‌کند. اگر مبدأ بی‌نام باشد، طریق‌ها تا بالا مستقل‌اند و هیچ سندی شکسته فرض نمی‌شود.</p>
        <p class="help">در محتمل‌ترین درخت زیر این مبدأ، ${fa(direct.length)} طریق مستقیم به آن می‌رسند${under.length ? ` و ${fa(under.length)} طریق زیر راوی دیگری قرار گرفته‌اند (مثلاً ${under.slice(0, 3).map(([h]) => `${esc(nm(h))} زیر ${esc(nm(MAPG[h]))}`).join('، ')})` : ''}.</p>
        ${direct.length ? `<p class="help">مستقیم زیر مبدأ: ${lst(direct)}</p>` : ''}
        <h3>آیا فقط چون یک گره‌ی اضافه بالای همه است برنده می‌شود؟</h3>
        <p class="help">نه به خودیِ خود. درخت‌های زیر این مبدأ، درختی را هم که همه از ${esc(rn)} می‌گذرند در بر می‌گیرند؛ امتیاز (log Z) میانگینِ همه‌ی درخت‌هاست، نه بهترینشان، و هر طریقی که مستقیم به این مبدأ وصل شود هزینه‌ی پیشین دارد. برتری‌اش از این است که درخت‌هایی با طریق‌های مستقل، سندهای ثبت‌شده را بهتر توضیح می‌دهند.</p>
        ${late.length ? `<h3>احتیاط</h3><p class="help">برخی «نخستین راویان» به راوی شناخته‌شده‌ای پیوند نخورده‌اند یا متأخرند: نام مؤلفی متأخر، تکه‌ای از متن که به جای نام خوانده شده، یا «موضع إرسال». هیچ راوی کهنی نمی‌تواند بی‌فرضِ افتادگی منبع اینها باشد، پس اینها مبدأ را به بالا می‌رانند و بخشی از قوّت این نتیجه از آن‌هاست. پیشنهادهای مرحله‌ی ۴ بسیاری از آن‌ها را درست می‌کنند: ${lst(late, true)}</p>` : ''}`;
    }

    /* ---------- درخت ---------- */
    function treePanel(el) {
      el.innerHTML = `<div class="explorer"><div class="card treecard"><h2>درخت انتقال، آن‌گونه که در اسناد ثبت شده است ${helpBtn('tree')}</h2><div class="treehost"></div></div>
        <div class="card insp"><h2>جزئیات</h2><div class="body"></div></div></div>`;
      INS = el.querySelector('.insp .body');
      const sources = Object.fromEntries(Object.entries(sp).map(([s, v]) => [s, { posterior: v.posterior, log_Z: v.log_Z }]));
      tree = ICMTree.make({ narrators: N, nodes: ctx.nodes, attestations: ATT, prefKey: 'icm.r', name: nm,
        verdict: { edgeProb: EP, mapGraph: MAPG, edges: E, sources, mapSource: best },
        onNode: id => selectNode(id), onBook: ai => selectNode('a' + ai), onEdge: k => selectEdge(k), onOrigin: () => selectNode(UNK),
        extraTools: () => { const s = document.createElement('span'); s.innerHTML = lensSel(); const sel = s.querySelector('select'); sel.value = lens ? `${lens.k}:${lens.j}` : ''; return s; } });
      el.querySelector('.treehost').append(tree.root);
      if (lens) tree.setLens({ fill: lens.fill, tip: lens.tip, badge: lens.badge, legend: lens.legend });
      if (selected) inspect(selected); else inspectRoot();
      INS.addEventListener('click', ev => {
        const a = ev.target.closest('[data-go]'); if (a) { ev.stopPropagation(); selectNode(a.getAttribute('data-go'), true); return; }
        const b = ev.target.closest('[data-goedge]'); if (b) { ev.stopPropagation(); selectEdge(b.getAttribute('data-goedge')); }
      });
    }
    function goTree(id) { show('tree'); selectNode(id, true); }
    function selectNode(id, go) {
      selected = id;
      if (!built.tree) show('tree');
      inspect(id);
      tree.select(id);
      if (go) tree.bring(id);
      if (window.innerWidth <= 900) root.querySelector('.insp').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    function selectEdge(key) {
      selected = null;
      if (!built.tree) show('tree');
      inspectEdge(key); tree.selectEdge(key);
      if (window.innerWidth <= 900) root.querySelector('.insp').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    const inspect = id => /^a\d+$/.test(id) ? inspectMatn(+id.slice(1)) : id === UNK || !N[id] ? (id === UNK ? inspectOrigin() : inspectRoot()) : inspectNode(id);
    const nodeBtn = id => `<button type="button" class="lnk ar" data-go="${esc(id)}">${esc(nm(id))}</button>`;
    const edgeBtn = (key, label) => `<button type="button" class="lnk" data-goedge="${esc(key)}">${label}</button>`;
    const step4Btn = id => { const o = ctx.goStep4 && ctx.nodes && ctx.nodes[id] && (ctx.nodes[id].occ || [])[0]; return o ? ` <button type="button" class="lnk small" data-step4="${esc(o)}" title="در مرحله‌ی ۴ باز شود">↗ مرحله‌ی ۴</button>` : ''; };
    const barList = items => '<div class="bars">' + items.map(([lab, p, c]) =>
      `<div class="bar"><div class="track"><div class="fill" style="width:${Math.max(0, Math.min(1, p)) * 100}%${c ? ';background:' + c : ''}"></div><div class="lab">${lab}</div></div><div class="pct">${fmt(p, 3)}</div></div>`).join('') + '</div>';
    const SRC_FA = { diagram: 'از نمودار', 'hadith-kg': 'گراف دانش', kg: 'گراف دانش', interpolated: 'برآورد از جای سند', user: 'واردشده‌ی کاربر',
      'author-death': 'درگذشت مؤلف کتاب', noor: 'نور', imam: 'تاریخ امام', wikishia: 'ویکی‌شیعه', fixed: 'ثابت' };
    const TIER_FA = { exact: 'قطعی (از منبع)', chosen: 'انتخاب کاربر', derived: 'استنباط از سند پیشین', default: 'خودکار', name: 'فقط نام', anon: 'نامعلوم' };
    const CONF_FA = { high: ['زیاد', 'hi'], medium: ['متوسط', 'me'], low: ['کم — تأییدنشده', 'lo'], none: ['یافت نشد', 'lo'] };
    function yearText(n) {
      if (!n.death && n.year == null) return '—';
      const d = n.death;
      const s = d && Math.round(d[0]) !== Math.round(d[1]) ? `${fa(Math.round(d[0]))} تا ${fa(Math.round(d[1]))} ق` : `${fa(d ? Math.round(d[0]) : n.year)} ق`;
      return `${n.date_source === 'interpolated' ? 'حدود ' : ''}${s} <span class="tag">${esc(SRC_FA[n.date_source] || n.date_source || '—')}</span>`;
    }
    function bioBlock(id) {
      const n = N[id], kg = n.kg;
      if (!kg || !kg.record) return '';
      const r = kg.record, [cf, cc] = CONF_FA[kg.confidence] || CONF_FA.none;
      return `<dl class="dl" style="margin-top:6px"><dt>اطمینان پیوند</dt><dd><span class="tag ${cc}">${cf}</span></dd>
        <dt>نام کامل</dt><dd class="ar">${esc(r.name)}</dd>${r.nickname ? `<dt>شهرت</dt><dd class="ar">${esc(r.nickname)}</dd>` : ''}
        ${r.iqama ? `<dt>محل اقامت</dt><dd class="ar">${esc(r.iqama)}</dd>` : ''}${r.rank ? `<dt>درجه‌ی رجالی</dt><dd class="ar">${esc(r.rank)}</dd>` : ''}
        ${r.tabaka != null || r.tabaqa != null ? `<dt>طبقه</dt><dd>${esc(fa(r.tabaka ?? r.tabaqa))}</dd>` : ''}</dl>`;
    }
    function inspectRoot() {
      INS.innerHTML = `<h3 class="ar">${esc(D.heading || D.family || '')}</h3>
        <p>${fa(heads.length)} طریق (نخستین راویِ سند) ثبت شده است. نامزدهای «مبدأ» عبارت‌اند از: نخستین راویان، حلقه‌های مشترک زیر آنان، و یک مبدأ بی‌نام بالای همه‌ی راویان ثبت‌شده.</p>
        <dl class="dl"><dt>شاهدها</dt><dd>${fa(ATT.length)}</dd><dt>راویان</dt><dd>${fa(Object.keys(N).length)}</dd>
        <dt>طریق‌ها</dt><dd>${heads.map(nodeBtn).join('، ')}</dd></dl>
        <p class="help">روی هر نام در درخت بزنید تا اطلاعات راوی، روی هر یال برای پیوند استاد و شاگرد، و روی نام کتاب (نارنجی، در انتهای هر سند) برای متن کامل حدیث.</p>`;
    }
    function inspectOrigin() { INS.innerHTML = `<h3 class="ar">${esc(UNK_NAME)}</h3>${originHtml()}`; }
    function inspectNode(id) {
      const n = N[id];
      const pp = Object.entries(n.parent_posterior || {}).sort((a, b) => b[1] - a[1]).map(([u, q]) => [esc(nm(u)), q]);
      const post = k => E[k] && E[k].posterior != null ? ' · ' + fmt(E[k].posterior, 2) : '';
      const rel = (u, v) => `<span class="help">(${edgeBtn(edgeKey(u, v), 'یال')}${post(edgeKey(u, v))})</span>`;
      const ins = [...(teachersOf[id] || [])], outs = [...(studentsOf[id] || [])], t = tiers(id);
      let anc = '';
      if (n.ancestral) {
        const rows = D.variants.map((spec, k) => {
          const row = n.ancestral[k] || [];
          const items = presence(k) ? [[esc(unitName(k)), row[1] || 0, lensColor(k, 1)]]
            : row.map((q, j) => [esc(unitName(k)) + ' = ' + esc(stateName(k, j)), q, lensColor(k, 1)]).filter(x => x[1] > 0.005);
          return items;
        }).flat();
        anc = barList(rows);
      } else anc = '<p class="empty">این راوی در درخت‌های نمونه‌برداری‌شده زیر مبدأ برتر حضور ندارد.</p>';
      INS.innerHTML = `
        <h3 class="ar">${esc(n.name)}${sp[id] ? ' <span class="tag src">نامزد مبدأ</span>' : ''}</h3>
        <dl class="dl">
          <dt>سال وفات</dt><dd>${yearText(n)}</dd>
          ${t.length ? `<dt>هویت</dt><dd>${t.map(x => esc(TIER_FA[x] || x)).join('، ')} <span class="small muted" dir="ltr">${esc(id)}</span>${step4Btn(id)}</dd>` : ''}
          ${n.locations && n.locations.length ? `<dt>شهر(ها)</dt><dd class="ar">${esc(n.locations.join('، '))}</dd>` : ''}
          <dt>استاد ثبت‌شده</dt><dd>${ins.length ? ins.map(u => `${nodeBtn(u)} ${rel(u, id)}`).join('، ') : 'سرآغاز یک طریق'}</dd>
          <dt>شاگردان ثبت‌شده</dt><dd>${outs.length ? outs.map(c => `${nodeBtn(c)} ${rel(id, c)}`).join('، ') : '—'}</dd>
          <dt>سندهای گذرنده</dt><dd>${fa(witnesses[id] || 0)}</dd>
          ${sp[id] ? `<dt>به‌عنوان مبدأ</dt><dd>احتمال پسین ${sci(sp[id].posterior)} · <bdi dir="ltr">log Z ${fmt(lz(id), 1)}</bdi></dd>` : ''}
        </dl>
        ${ctx.narratorCard && /^(noor|kg):\d+$/.test(id) ? `<p><button type="button" class="small" data-card="${esc(id)}">کارت راوی (رجال)</button></p>` : bioBlock(id)}
        <h3>استادِ این راوی در انتقال این حدیث کیست؟</h3>
        <p class="help">توزیع پسین روی استادانِ ممکن: علاوه بر استاد ثبت‌شده، استادِ استاد (حذف یک واسطه) و راویان هم‌دوره‌ی هم‌طریق نیز نامزدند.</p>
        ${pp.length ? barList(pp) : '<p class="empty">—</p>'}
        <h3>متنی که این راوی احتمالاً نقل کرده</h3>
        <p class="help">احتمال هر حالتِ هر واحد اختلاف در متنِ نیاکانیِ این راوی، میانگین روی درخت‌های پسین؛ بازسازی‌شده از متن‌های شاگردانش، نه دیده‌شده. واحدی را در «واحد اختلاف متن» برگزینید تا همه‌ی درخت به رنگ آن درآید.</p>
        ${anc}`;
    }
    function inspectEdge(key) {
      const e = E[key], [u, v] = key.split('>');
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
          <dt>فاصله‌ی سال وفات</dt><dd>${e.gap_years == null ? '—' : fa(e.gap_years) + ' سال'}</dd>
          <dt>شاهدهای حامل این یال</dt><dd>${(e.witnesses || []).map(w => { const i = ATT.findIndex(a => a.id === w); return i < 0 ? '' : `<button type="button" class="lnk ar" data-go="a${i}">${esc(ATT[i].collection)}</button>`; }).filter(Boolean).join('، ') || '—'}</dd>
        </dl>
        <h3>استادهای نامزد برای این شاگرد (پیشین)</h3>
        ${barList((e.candidates || []).map(c => [esc(nm(c.node)), c.prior]))}
        <p class="help">مدل این گزینه‌ها را می‌سنجد: سند ممکن است واسطه‌ای را انداخته یا نامی را جابه‌جا کرده باشد.</p>`;
    }
    const rowTags = a => D.variants.map((spec, k) => {
      const x = a.row[k], cc = `style="--h:${hue(clauseOf(k))}"`;
      if (x < 0) return `<span class="tag na" title="متن کوتاه‌شده است؛ نامعلوم، نه غایب">${esc(unitName(k))}: نامعلوم</span>`;
      return `<span class="tag cl${presence(k) && x === 0 ? ' off' : ''}" ${cc}>${esc(unitName(k))}: ${esc(stateName(k, x))}</span>`;
    }).join('');
    const chainHtml = s => '<div class="chainx">' + s.map((n, i) => `${i ? '<span class="arr">←</span>' : ''}<button type="button" class="${i === 0 ? 'src' : ''}" data-go="${esc(n)}">${esc(nm(n))}</button>`).join('') + '</div>';
    function inspectMatn(i) {
      const a = ATT[i];
      INS.innerHTML = `
        <h3 class="ar">${esc(a.collection)}</h3>
        <div class="matntext ar">${textHtml(i)}</div>
        <h3>واحدهای اختلاف این متن</h3><div>${rowTags(a)}</div>
        <p class="help">«نامعلوم» برای متن‌های کوتاه‌شده («بنحوه»، «وساق الحديث») است: نبودِ بخش در چنین متنی به معنای حذف آن نیست.</p>
        <h3>سند</h3>${chainHtml(a.sanad)}
        <dl class="dl" style="margin-top:10px">
          <dt>طول سند</dt><dd>${fa(a.sanad.length)} راوی</dd>
          <dt>وزن استقلال</dt><dd>${fmt(a.weight, 2)} <span class="help">${a.weight < 1 ? 'چند نسخه از همین کتاب در داده هست؛ هر کدام با این وزن به حساب می‌آید' : 'نسخه‌ی مستقل'}</span></dd>
          <dt>طریق</dt><dd>${nodeBtn(a.sanad[0])}</dd>
        </dl>`;
    }

    /* ---------- texts: clause colours (search stage) and the chosen unit's words ---------- */
    function textHtml(i) {
      const a = ATT[i], t = ctx.texts && ctx.texts[wid(a)];
      const text = t ? t.matn : a.text || '', cp = Array.from(text), marks = t ? (t.marks || []) : SITTA ? sittaMarks(text) : [];
      const cl = new Array(cp.length).fill(-1), un = new Array(cp.length).fill(false);
      marks.forEach(([x, y, k]) => { for (let j = x; j < y && j < cp.length; j++) cl[j] = k; });
      if (lens) {
        const k = lens.k, x = a.row[k], u = U(k);
        const words = x < 0 ? null : presence(k) ? (x === 1 ? unitWords(k) : null) : D.variants[k].labels[x];
        const num = u && u.kind === 'numeric' && words && NUMW[words] ? NUMW[words].split(' ') : null;
        const re = !words || CLAUSES[D.variants[k].name] ? null : num ? altRe(num) : wordsRe(words);
        if (u && u.unit_kind === 'clause' && x === 1) { for (let j = 0; j < cp.length; j++) if (cl[j] === u.clause) un[j] = true; }
        else if (re) {
          const s = cp.join(''), c0 = clauseOf(k), want = m => { const ci = cl[Array.from(s.slice(0, m.index)).length]; return ci === c0; };
          const ms = [...s.matchAll(re)], pick = ms.filter(want).length ? ms.filter(want) : ms;
          pick.slice(0, 3).forEach(m => { const p0 = Array.from(s.slice(0, m.index)).length, p1 = p0 + Array.from(m[0]).length; for (let j = p0; j < p1; j++) un[j] = true; });
        } else if (x >= 0 && CLAUSES[D.variants[k].name]) sittaKeys(text, D.variants[k].name).forEach(([p0, p1]) => { for (let j = p0; j < p1; j++) un[j] = true; });
      }
      let out = '', j = 0;
      while (j < cp.length) {
        let q = j; while (q < cp.length && cl[q] === cl[j] && un[q] === un[j]) q++;
        let seg = esc(cp.slice(j, q).join(''));
        if (un[j]) seg = `<mark class="ul">${seg}</mark>`;
        if (cl[j] >= 0) seg = `<span class="hl" style="--h:${hue(cl[j])}" title="بخش ${fa(cl[j] + 1)}${ctx.clauses && ctx.clauses[cl[j]] ? ': ' + esc(ctx.clauses[cl[j]]) : ''}">${seg}</span>`;
        out += seg; j = q;
      }
      return out;
    }
    const CLS = { 'ي': '[يیى]', 'ى': '[يیى]', 'ی': '[يیى]', 'ك': '[كک]', 'ک': '[كک]', 'ه': '[هةۀھ]', 'ة': '[هةۀھ]', 'ا': '[اأإآ]', 'أ': '[اأإآ]', 'إ': '[اأإآ]', 'آ': '[اأإآ]' };
    const keyRe = k => k.split('').filter(ch => ch !== ' ').map(ch => CLS[ch] || ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s?');
    function sittaKeys(text, name) {
      const out = []; const s = text;
      for (const k of (CLAUSES[name] || { keys: [] }).keys) { const re = new RegExp(keyRe(k), 'g'); let m; while ((m = re.exec(s))) { const p0 = Array.from(s.slice(0, m.index)).length; out.push([p0, p0 + Array.from(m[0]).length]); } }
      return out;
    }
    function sittaMarks(text) {
      const out = []; Object.keys(CLAUSES).forEach((n, k) => sittaKeys(text, n).forEach(([a, b]) => out.push([a, b, k]))); return out.sort((a, b) => a[0] - b[0]);
    }
    let textsEl = null;
    function textsPanel(el) {
      el.innerHTML = `<div class="card"><h2>متن‌های حدیث (${fa(ATT.length)} شاهد) ${helpBtn('texts')}</h2>
        <div class="toolbar">${lensSel()}</div><div class="clkey"></div><div class="texts"></div></div>`;
      textsEl = el;
      el.querySelector('.lenssel').value = lens ? `${lens.k}:${lens.j}` : '';
    }
    function drawTexts() {
      if (!textsEl) return;
      const nc = ctx.clauses ? ctx.clauses.length : 0;
      textsEl.querySelector('.clkey').innerHTML = nc ? 'بخش‌های حدیث مبدأ: ' + ctx.clauses.map((c, i) => `<span class="tag cl" style="--h:${hue(i)}" title="${esc(c)}">${fa(i + 1)}. ${esc(shortName(c, 4))}</span>`).join('') : '';
      textsEl.querySelector('.texts').innerHTML = heads.map(h => {
        const items = ATT.map((a, i) => [a, i]).filter(([a]) => a.sanad[0] === h);
        return `<h3 class="ar">${esc(nm(h))} · ${fa(items.length)} متن</h3>` + items.map(([a, i]) => {
          const x = lens ? a.row[lens.k] : null;
          return `<div class="textcard"><div class="hd"><b class="ar">${esc(a.collection)}</b><span class="small muted">سند ${fa(a.sanad.length)} راوی</span>
            ${lens ? `<span class="tag cl${x === lens.j ? '' : ' off'}" style="--h:${hue(clauseOf(lens.k))}">${x < 0 ? 'نامعلوم' : esc(stateName(lens.k, x))}</span>` : ''}
            <button type="button" class="lnk small" data-showtree="a${i}">نمایش در درخت</button></div>
            <div class="matntext ar">${textHtml(i)}</div></div>`;
        }).join('');
      }).join('');
    }

    /* ---------- مبدأ ---------- */
    function sourcesPanel(el) {
      const rows = ranked.map(s => `<tr class="click${s === best ? ' best' : ''}" data-showtree="${esc(s)}"><td class="ar">${esc(nm(s))}</td>
        <td class="n">${fmt(lz(s), 1)}</td><td class="n">${sci(sp[s].posterior)}</td><td class="n">${fmt(sp[s].ess, 0)}</td>
        <td>${(D.credible_set_95 || []).includes(s) ? '<span class="tag yes">در مجموعه‌ی ۹۵٪</span>' : ''}</td></tr>`).join('');
      const br = D.branch_results ? Object.entries(D.branch_results).map(([line, r]) => !r.posterior
        ? `<tr><td class="ar">${esc(line)}</td><td class="n">${fa(r.n_attestations)}</td><td colspan="3" class="empty">تنها یک شاهد</td></tr>`
        : `<tr><td class="ar">${esc(line)}</td><td class="n">${fa(r.n_attestations)}</td><td class="ar">${esc(r.map_source)}</td>
           <td class="n">${fmt(Math.max(...Object.values(r.posterior)), 3)}</td><td class="n">${fmt(r.log_BF_vs_next, 1)}</td></tr>`).join('') : '';
      el.innerHTML = `<div class="card"><h2>نامزدهای مبدأ — کل خانواده ${helpBtn('sources')}</h2><div class="scroll"><table class="rvt">
          <thead><tr><th>مبدأ</th><th class="n">log Z</th><th class="n">احتمال پسین</th><th class="n">ESS</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
          <p class="small muted">روی هر ردیف بزنید تا در درخت نشان داده شود.</p></div>
        <div class="card"><h2>درون هر طریق</h2>${br ? `<div class="scroll"><table class="rvt">
          <thead><tr><th>طریق</th><th class="n">شاهد</th><th>محتمل‌ترین مبدأ</th><th class="n">احتمال</th><th class="n">log BF</th></tr></thead><tbody>${br}</tbody></table></div>`
          : '<p class="small muted">در این اجرا محاسبه نشده است: پرسشِ «قدیمی‌ترین منبع مشترکِ متن‌های همین طریق کیست؟» برای هر طریق جداگانه، اجرایی جدا می‌خواهد.</p>'}</div>`;
    }

    /* ---------- درخت بیشینه ---------- */
    function graphPanel(el) {
      const unsure = Object.entries(E).filter(([, e]) => e.posterior != null && e.posterior < 0.999).sort((a, b) => a[1].posterior - b[1].posterior).slice(0, 40);
      const departs = Object.entries(MAPG).filter(([v, p]) => N[v] && !(teachersOf[v] || new Set()).has(p) && !(p === UNK && !teachersOf[v]));
      const mm = GP.map_mass;
      const read = mm == null ? '' : mm >= 0.5 ? `پسین متمرکز است: ${pct(mm)} از احتمال (برای مبدأ برتر) روی یک درخت است.`
        : mm >= 0.1 ? `پسین میان چند درخت پخش است؛ محتمل‌ترین درخت ${pct(mm)} را دارد.` : `پسین پراکنده است؛ محتمل‌ترین درخت تنها ${pct(mm)} را دارد و نتیجه‌ی هر یال را باید با احتمالش خواند.`;
      el.innerHTML = `<div class="grid2">
        <div class="card"><h2>محتمل‌ترین درخت ${helpBtn('map')}</h2><div class="body">
          <dl class="dl"><dt>ذرات (نمونه‌ها)</dt><dd>${fa(GP.n_particles ?? '—')}</dd><dt>درخت‌های متمایز</dt><dd>${fa(GP.n_distinct ?? '—')}</dd>
            <dt>سهم محتمل‌ترین درخت</dt><dd>${pct(mm)}</dd><dt>امتیاز لگاریتمی آن</dt><dd>${fmt(GP.map_log_score, 2)}</dd></dl>
          <p class="note">${read} در زبانه‌ی «<button type="button" class="lnk" data-tab="tree">درخت</button>» گزینه‌ی «فقط درخت بیشینه» یال‌های بیرون از آن را کم‌رنگ می‌کند.</p></div></div>
        <div class="card"><h2>یال‌هایی که مدل درباره‌شان مطمئن نیست ${helpBtn('unsure')}</h2><div class="scroll"><table class="rvt">
          <thead><tr><th>استاد ← شاگرد</th><th class="n">احتمال پسین</th></tr></thead><tbody>
          ${unsure.length ? unsure.map(([k, e]) => `<tr class="click" data-goedge="${esc(k)}"><td class="ar">${esc(nm(e.teacher))} ← ${esc(nm(e.student))}</td><td class="n">${fmt(e.posterior, 3)}</td></tr>`).join('')
            : '<tr><td colspan="2" class="empty">همه‌ی یال‌های ثبت‌شده احتمال پسین ≥ ۰٫۹۹۹ دارند.</td></tr>'}</tbody></table></div></div>
        <div class="card"><h2>جاهایی که درخت بیشینه با سند ثبت‌شده فرق دارد ${helpBtn('departs')}</h2><div class="scroll"><table class="rvt">
          <thead><tr><th>شاگرد</th><th>استاد(های) ثبت‌شده</th><th>استاد در درخت بیشینه</th></tr></thead><tbody>
          ${departs.length ? departs.map(([v, p]) => `<tr class="click" data-showtree="${esc(v)}"><td class="ar">${esc(nm(v))}</td><td class="ar">${esc([...(teachersOf[v] || [])].map(nm).join('، ') || '—')}</td><td class="ar">${esc(nm(p))}</td></tr>`).join('')
            : '<tr><td colspan="3" class="empty">درخت بیشینه دقیقاً همان ساختار ثبت‌شده است.</td></tr>'}</tbody></table></div></div></div>`;
    }

    /* ---------- حساسیت ---------- */
    const SENS_FA = { equal: 'یکنواخت', favour_unknown: 'ترجیح مبدأ ناشناخته', date_informed: 'مبتنی بر تاریخ',
      'mu=0.02': 'نرخ تغییر متن ۰٫۰۲', 'mu=0.08': 'نرخ تغییر متن ۰٫۰۸', 'mu=0.2': 'نرخ تغییر متن ۰٫۲',
      'sanad_error=0.02': 'خطای سند ۰٫۰۲', 'sanad_error=0.1': 'خطای سند ۰٫۱', 'sanad_error=0.2': 'خطای سند ۰٫۲' };
    function sensPanel(el) {
      const g = D.global_results || {};
      const tbl = obj => `<div class="scroll"><table class="rvt"><thead><tr><th>تنظیم</th><th>محتمل‌ترین مبدأ</th><th class="n">احتمال</th><th>دومی</th><th class="n">احتمال</th></tr></thead><tbody>
        ${Object.entries(obj).map(([k, v]) => { const t = Object.entries(v).sort((a, b) => b[1] - a[1]);
          return `<tr><td>${esc(SENS_FA[k] || fa(k))}</td><td class="ar">${esc(t[0][0])}</td><td class="n">${sci(t[0][1])}</td><td class="ar">${esc(t[1] ? t[1][0] : '')}</td><td class="n">${t[1] ? sci(t[1][1]) : ''}</td></tr>`; }).join('')}</tbody></table></div>`;
      const pt = g.permutation_test, any = g.prior_sensitivity || g.rate_sensitivity || pt;
      const missing = [['حساسیت به پیشینِ مبدأ', 'اگر به همه‌ی نامزدها احتمال پیشینِ برابر ندهیم (مثلاً به مبدأ بی‌نام وزن بیشتر، یا بر پایه‌ی تاریخ)، آیا نتیجه تغییر می‌کند؟', g.prior_sensitivity],
        ['حساسیت به نرخ تغییر متن و خطای سند', 'با نرخ‌های تند‌تر یا کندتر برای دگرگونی متن و خطای ثبت سند، مبدأ برتر همان می‌ماند؟', g.rate_sensitivity],
        ['آزمون جایگشت', 'متن‌ها را تصادفی میان سندها جابه‌جا می‌کنیم؛ اگر احتمال مبدأ افت نکند، متن در نتیجه نقشی ندارد و نتیجه بر ساختار سندها استوار است.', pt]];
      el.innerHTML = `${any ? '' : '<div class="card"><p>این آزمون‌ها در اجرای این خانواده محاسبه نشده‌اند (هر کدام چند اجرای دیگرِ مدل می‌خواهد). آنچه هر کدام نشان می‌داد:</p></div>'}
        <div class="grid2">${missing.map(([t, d, v]) => `<div class="card"><h2>${t} ${helpBtn(d)}</h2><div class="body">
          ${!v ? '<p class="empty">محاسبه نشده</p>' : v === pt ? `<dl class="dl"><dt>مبدأ آزموده</dt><dd class="ar">${esc(pt.source)}</dd><dt>احتمال مشاهده‌شده</dt><dd>${fmt(pt.observed, 3)}</dd>
            <dt>میانگین در جایگشت‌ها</dt><dd>${fmt(pt.null_mean, 3)}</dd><dt>p-value</dt><dd>${fmt(pt.p_value, 3)}</dd></dl>` : tbl(v)}</div></div>`).join('')}</div>`;
    }

    /* ---------- روش و محدودیت‌ها، راهنما ---------- */
    function methodPanel(el) {
      const rec = D.recovery_test, kinds = {};
      D.variants.forEach((v, k) => { const u = U(k), kk = (u && u.kind) || (presence(k) ? 'presence' : 'lexical'); kinds[kk] = (kinds[kk] || 0) + 1; });
      el.innerHTML = `<div class="grid2">
        <div class="card"><h2>مدل</h2><div class="body">
          <p class="num" style="direction:ltr;text-align:left">P(S=s | X, C, M) ∝ P(S=s) · Z<sub>s</sub> ,&nbsp; Z<sub>s</sub> = Σ<sub>G</sub> P(X|G,s) · P(C|G,s) · P(G|s,M)</p>
          <ul>
            <li><b>متن (X):</b> هر واحد اختلاف (اینجا ${fa(D.variants.length)} واحد: ${Object.entries(kinds).map(([k, n]) => `${fa(n)} ${KIND_FA[k] || k}`).join('، ')}) در طول درخت با یک فرایند مارکوفِ حالت‌متناهی تغییر می‌کند. متن‌های نیاکانیِ دیده‌نشده با الگوریتم هرس جمع زده می‌شوند.</li>
            <li><b>سند (C):</b> هر سندِ ثبت‌شده خوانشی پرخطا از مسیرِ واقعیِ مبدأ تا شاهد است؛ با یک «مدل مارکوف پنهانِ جفتی» سنجیده می‌شود: تطابق، حذف واسطه، افزودن نام، جابه‌جایی نام.</li>
            <li><b>رجال (M):</b> استادانِ نامزدِ هر راوی با ترتیب زمانی محدود می‌شوند (کسی که پیش از مبدأ درگذشته در درخت نیست). امتیاز هر یال (ثبت در سند، هم‌شهری بودن، شاگردیِ مستند در کتب رجال) با softmax به احتمال تبدیل می‌شود.</li>
            <li><b>محاسبه‌ی Z<sub>s</sub>:</b> SMC بازپخت‌شده برای هر نامزد مبدأ.</li>
          </ul>
          <p class="note">هر نسخه‌ی باقی‌مانده با شاخه‌ای جداگانه از گردآورنده‌اش آویخته است، تا دو نسخه‌ی متفاوت از یک راوی تناقض به حساب نیاید. نسخه‌های یک کتاب یک «گروه استقلال» دارند و با وزن ۱/تعداد وارد می‌شوند. هر راوی بی‌نامِ میانی، شخصی جداست.</p></div></div>
        <div class="card"><h2>اعتبارسنجی</h2><div class="body">
          <p>الگوریتم هرس در برابر جمع‌زدن کامل روی همه‌ی متن‌های نیاکانی آزموده شده است؛ مدل سند در برابر نرمال بودن و یکنوایی.</p>
          ${rec ? `<dl class="dl"><dt>مبدأ واقعی = محتمل‌ترین</dt><dd>${pct(rec.map_accuracy)}</dd><dt>پوشش مجموعه‌ی ۹۵٪</dt><dd>${pct(rec.coverage95)}</dd>
            <dt>میانگین احتمال مبدأ واقعی</dt><dd>${fmt(rec.mean_true_posterior, 3)}</dd></dl>` : '<p class="small muted">آزمون بازیابی مبدأ روی خانواده‌های ساختگی برای این اجرا انجام نشده است.</p>'}</div></div>
        <div class="card"><h2>محدودیت‌ها</h2><div class="body"><ul>
          <li>نتیجه به شجره‌ی تأییدشده وابسته است: هویت راویان (مرحله‌ی ۴)، تاریخ‌ها و سندهایی که در شجره آمده‌اند. نخستین راویانی که راوی نیستند (نام مؤلف، تکه‌ای از متن) مبدأ را به بالا می‌رانند.</li>
          <li>خطای سند مستقل از جایگاه فرض شده: ساختگی بودنِ یک سندِ کامل مدل نشده است.</li>
          <li>واحدهای اختلاف خودکار از هم‌ترازی (مرحله‌ی ۳) آمده‌اند؛ اگر بیشترشان در همه‌ی متن‌ها یکسان باشند، متن قدرت تفکیک کمی دارد و نتیجه بر سندها استوار است.</li>
          <li>پیوند با کتب رجال خودکار است؛ پیوندهای کم‌اطمینان تنها پیشنهادند.</li>
          <li>پارامترهای مدل (نرخ تغییر متن، خطای سند، امتیاز یال‌ها) ثابت‌اند و برای این خانواده برآورد نشده‌اند.</li>
        </ul></div></div></div>`;
    }
    function glossPanel(el) {
      const G = [
        ['مبدأ (منبعِ قدیمی‌ترینِ قابل‌بازیابی)', 'راوی‌ای که همه‌ی روایت‌های باقی‌مانده از او سرچشمه گرفته‌اند. مدل ادعا نمی‌کند گوینده‌ی نخستین را می‌شناسد؛ فقط قدیمی‌ترین نقطه‌ای را می‌یابد که شواهد باقی‌مانده به آن می‌رسند.'],
        ['مبدأ بی‌نام', 'نقطه‌ای بالاتر از نخستین راوی همه‌ی سندها؛ یعنی هیچ‌یک از راویان ثبت‌شده منبع مشترک همه نیست.'],
        ['احتمال پسین P(S=s | داده)', 'احتمال این‌که s مبدأ باشد، پس از دیدن متن‌ها، سندها و اطلاعات رجالی، با میانگین‌گیری روی همه‌ی درخت‌های انتقال و متن‌های نیاکانیِ ممکن.'],
        ['log Z (لگاریتم شاهد مدل)', 'لگاریتم احتمال کل داده‌ها با فرض مبدأ بودن s. اعداد منفی بزرگ عادی‌اند؛ آنچه اهمیت دارد اختلاف میان نامزدهاست.'],
        ['عامل بیز (log BF)', 'اختلاف log Z دو نامزد. ۱ ≈ ۳ برابر، ۵ ≈ ۱۵۰ برابر، ۱۰ ≈ ۲۲۰۰۰ برابر پشتیبانی بیشتر از نامزد اول.'],
        ['ESS (اندازه‌ی مؤثر نمونه)', 'نمونه‌های وزن‌دار معادل چند نمونه‌ی مستقل‌اند. زیر ۱۰ یعنی برآوردِ Z نامطمئن است.'],
        ['درخت بیشینه‌ی پسین (MAP)', 'محتمل‌ترین درخت انتقال برای مبدأ برتر. یال‌های آن در درخت با رنگ تأکید کشیده شده‌اند.'],
        ['احتمال پسین یال', 'احتمال این‌که در انتقال این حدیث، این استاد واقعاً استادِ این شاگرد بوده باشد.'],
        ['احتمال پیشین یال (ρ)', 'پیش از دیدن متن‌ها: امتیاز هر استادِ نامزد با softmax به احتمال تبدیل می‌شود.'],
        ['امتیاز یال (η)', 'ثبت در سند +۳، هم‌شهری بودن +۱، شاگردیِ مستند در کتب رجال +۲؛ استادِ بی‌نام −۴. استادِ ناممکن از نظر زمانی کنار گذاشته می‌شود.'],
        ['سال وفات و منبع آن', 'سال قمری؛ از پایگاه راویان، گراف دانش، تاریخ مؤلف، یا واردشده‌ی کاربر. «برآورد»: از جای راوی در سندها میان دو راویِ تاریخ‌دار.'],
        ['شاهد', 'هر متنِ باقی‌مانده‌ی حدیث همراه با سندش، در یک کتاب.'],
        ['واحد اختلاف متن', 'بخشی از متن که میان روایت‌ها فرق می‌کند: بودن یا نبودن یک بخش یا چند واژه، واژه‌ای که جای دیگری نشسته، یا عددی دیگر. از هم‌ترازی مرحله‌ی ۳ آمده‌اند.'],
        ['نامعلوم', 'متن کوتاه‌شده است («بنحوه»، «وساق الحديث»)؛ نبودِ بخش به معنای حذف نیست و مدل آن را بی‌اطلاع حساب می‌کند.'],
        ['متن نیاکانی', 'متنی که یک راوی (که متنش به دست ما نرسیده) احتمالاً نقل کرده، بازسازی‌شده از متن‌های شاگردانش.'],
        ['وزن استقلال', 'نسخه‌های یک کتاب مستقل نیستند؛ هر کدام با وزن ۱ تقسیم بر شمار نسخه‌ها وارد می‌شود.'],
        ['حلقه‌ی مشترک', 'راوی‌ای که چند شاگرد مستقل از او نقل کرده‌اند؛ نقطه‌ی انشعاب در درخت.'],
      ];
      el.innerHTML = `<div class="card"><h2>راهنمای اصطلاحات</h2><div class="body"><dl class="gloss">${G.map(([t, d]) => `<dt>${t}</dt><dd>${d}</dd>`).join('')}</dl></div></div>`;
    }

    show(ctx.tab || 'result');
    return { show, selectNode: id => goTree(id) };
  }
  window.ICMResults = { mount };
})();

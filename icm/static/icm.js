'use strict';
const BASE = document.body.dataset.base;
const API = BASE + '/api';
const $app = document.getElementById('app');
const FA = '۰۱۲۳۴۵۶۷۸۹';
const fa = x => String(x).replace(/[0-9]/g, d => FA[d]);
const pct = x => fa(Math.round(100 * x)) + '٪';


/* ---------- computing in the user's browser ---------- */
const STATUS_EXTRA = { awaiting: 'منتظر محاسبه' };
function rpc(w, msg) {
  return new Promise((res, rej) => {
    const id = Math.random().toString(36).slice(2);
    const on = e => { if (e.data.id !== id) return; w.removeEventListener('message', on); e.data.ok ? res(e.data.value) : rej(new Error(e.data.error)); };
    w.addEventListener('message', on); w.postMessage({ id, ...msg });
  });
}
async function computeInBrowser(pid, ui) {
  const t0 = performance.now();
  const inp = await api(`/project/${pid}/compute-input`);
  const nW = Math.max(1, Math.min((navigator.hardwareConcurrency || 2) - 1, 6));
  const workers = Array.from({ length: nW }, () => new Worker(BASE + '/static/model_worker.js'));
  try {
    ui(`بارگذاری مفسر پایتون در ${fa(nW)} پردازه…`, 0.02);
    await Promise.all(workers.map(w => rpc(w, { cmd: 'boot', base: BASE })));
    const payload = JSON.stringify({ graph: inp.graph, texts: inp.texts, preset: inp.preset, seed: inp.seed });
    const infos = (await Promise.all(workers.map(w => rpc(w, { cmd: 'init', payload })))).map(x => JSON.parse(x));
    const n = infos[0].sources.length;
    const light = new Array(n), owner = new Array(n);
    let next = 0, done = 0;
    await Promise.all(workers.map(async w => {
      while (next < n) {
        const i = next++;
        light[i] = JSON.parse(await rpc(w, { cmd: 'source', i })); owner[i] = w;
        done++; ui(`${fa(done)} از ${fa(n)} نامزد منبع محاسبه شد (${infos[0].sources[i].slice(0, 28)})`, 0.05 + 0.9 * done / n);
      }
    }));
    let best = 0;
    light.forEach((d, i) => { if ((d.log_Z ?? -Infinity) > (light[best].log_Z ?? -Infinity)) best = i; });
    ui('ساخت خلاصه‌ی پسین…', 0.96);
    const bj = await rpc(owner[best], { cmd: 'graphs', i: best });
    const secs = (performance.now() - t0) / 1000;
    const prov = { workers: nW, seconds: Math.round(secs), agent: navigator.userAgent.slice(0, 100) };
    const out = JSON.parse(await rpc(workers[0], { cmd: 'finish', light: JSON.stringify(light), best: bj, seconds: secs, provenance: JSON.stringify({ kind: 'browser', ...prov }) }));
    ui('ارسال نتیجه…', 0.99);
    await post(`/project/${pid}/compute-result`, { key: inp.key, results: out.results, viz: out.bundle, provenance: prov });
  } finally { workers.forEach(w => w.terminate()); }
}

/* ---------- helpers ---------- */
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'dir' || k === 'title' || k === 'href' || k === 'type' || k === 'value' || k === 'placeholder' || k === 'name' || k === 'src' || k === 'target' || k === 'style' || k === 'colspan' || k === 'rowspan') e.setAttribute(k, v);
    else if (v === true) e.setAttribute(k, '');
    else e[k] = v;
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
async function api(path, opts) {
  const r = await fetch(API + path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} const err = new Error(m); err.status = r.status; throw err; }
  return r.json();
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ICM': '1' }, body: JSON.stringify(body) });
const cache = new Map();
async function stage(pid, run, name) {
  const k = `${pid}|${run}|${name}`;
  if (!cache.has(k)) cache.set(k, api(`/project/${pid}/runs/${run}/${name}`));
  try { return await cache.get(k); } catch (e) { cache.delete(k); throw e; }
}
function modal(content) {
  const m = document.getElementById('modal'); const b = document.getElementById('modal-body');
  b.replaceChildren(content); m.hidden = false;
}
document.getElementById('modal-x').onclick = () => document.getElementById('modal').hidden = true;
document.getElementById('modal').addEventListener('click', e => { if (e.target.id === 'modal') e.target.hidden = true; });
const STATUS = { draft: 'منتظر تصمیم شما', running: 'در حال اجرا', finalized: 'نهایی', failed: 'خطا', awaiting: 'منتظر محاسبه' };
const CLS = { witness: 'همان متن', variant: 'نسخه‌ی دیگر', extended: 'در متن بلندتر', abridged: 'خلاصه', partial: 'بخشی', different: 'متفاوت' };
const TIER = { exact: 'قطعی', high: 'بالا', medium: 'متوسط', low: 'پایین', derived: 'استنباطی', none: 'بی‌نامزد' };
const STEPS = [['seed', '۱ حدیث مبدأ'], ['selection', '۲ جستجو و انتخاب'], ['alignment', '۳ هم‌ترازی'], ['identities', '۴ راویان'], ['phylogeny', '۵ شجره'], ['analysis', '۶ تحلیل']];
const FLAG_FA = {
  pool_capped: 'تعداد نامزدها بسیار زیاد بود؛ فقط آن‌هایی که نشانه‌ی بیشتری داشتند سنجیده شدند.',
  seed_short: 'متن حدیث مبدأ کوتاه است و جستجو کم‌اعتماد است.', family_large: 'خانواده بسیار بزرگ است.',
  heads_capped: 'تعداد سرزنجیره‌ها از حد گذشت؛ فقط پرشاهدترها نامزد منبع شدند.', units_capped: 'فقط آگاهی‌بخش‌ترین واحدهای تغییر نگه داشته شد.',
  chronology: 'ناسازگاری زمانی: استاد پس از شاگرد درگذشته است (هویت یکی از آن‌ها را بررسی کنید).', repeated_node: 'یک راوی دو بار در یک سند آمده بود.',
  chain_too_short: 'سندی با کمتر از دو راوی.', witness_without_chain: 'شاهدی بدون سند خوانا؛ فقط در گردآورنده‌اش لنگر شد.',
  unknown_witness: 'شاهد انتخاب‌شده در نتایج جستجو نیست.', no_chain: 'برای این شاهد سندی خوانده نشد.' };
const flagText = f => FLAG_FA[f.kind] ? (f.kind === 'chronology' ? FLAG_FA[f.kind] + ' — ' + f.message : FLAG_FA[f.kind]) : f.message;
let ME = { user: null, admin: false }, timer = null;

function route() {
  const m = location.pathname.slice(BASE.length).split('/').filter(Boolean);
  if (m[0] !== 'p' || !m[1]) return {};
  return m[2] === 'r' ? { pid: m[1], run: m[3] || null, step: m[4] || null } : { pid: m[1], run: null, step: m[2] || null };
}
const stepUrl = (pid, run, step) => `/p/${pid}${run ? '/r/' + run : ''}${step ? '/' + step : ''}`;
function go(path) { history.pushState({}, '', BASE + path); render(); }
/* after a decision: back to the project's own address, which opens the step that now needs the user */
function reload(pid) { cache.clear(); history.pushState({}, '', BASE + stepUrl(pid)); render(); }
window.addEventListener('popstate', render);

/* ---------- top level ---------- */
async function render() {
  clearTimeout(timer);
  ME = await api('/me').catch(() => ME);
  const who = document.getElementById('who');
  who.replaceChildren(ME.user ? `${ME.user}${ME.admin ? ' (مدیر)' : ''}` : el('a', { href: '/account/login?next=' + encodeURIComponent(location.pathname) }, 'ورود'));
  const r = route();
  try { r.pid ? await project(r.pid, r.run) : await home(); }
  catch (e) { $app.replaceChildren(el('div', { class: 'card' }, 'خطا: ' + e.message)); }
}

/* ---------- home ---------- */
async function home() {
  const idx = await api('/index');
  const q = el('input', { type: 'text', placeholder: 'شناسه‌ی حدیث، نشانی /h/i/… یا چند کلمه از متن', id: 'q' });
  const out = el('div');
  const go_ = async () => {
    out.replaceChildren(el('p', { class: 'muted' }, 'در حال جستجو…'));
    const rs = await api('/search?limit=60&q=' + encodeURIComponent(q.value));
    out.replaceChildren(rs.length ? el('table', {}, rs.map(h => hadithRow(h))) : el('p', { class: 'muted' }, 'چیزی پیدا نشد.'));
  };
  q.addEventListener('keydown', e => { if (e.key === 'Enter') go_(); });
  const pre = new URLSearchParams(location.search).get('seed');
  if (pre) { q.value = pre; setTimeout(go_, 0); }
  $app.replaceChildren(
    el('div', { class: 'card' }, el('h2', {}, 'شروع از یک حدیث'),
      el('p', { class: 'muted' }, 'یک حدیث را انتخاب کنید؛ سامانه خانواده‌ی آن را می‌یابد، شما شاهدها و راویان را تأیید می‌کنید و در پایان قدیمی‌ترین منبع قابل‌بازیابی برآورد می‌شود.'),
      el('div', { class: 'toolbar' }, q, el('button', { class: 'primary', onclick: go_ }, 'جستجو')), out),
    el('div', { class: 'card' }, el('h2', {}, 'پروژه‌ها'),
      idx.projects.length ? el('table', {}, el('tr', {}, ...['عنوان', 'حدیث مبدأ', 'سازنده', 'وضعیت', 'مرحله', 'به‌روزرسانی'].map(t => el('th', {}, t))),
        idx.projects.map(p => el('tr', {}, el('td', {}, el('a', { href: `${BASE}/p/${p.pid}`, onclick: ev => { ev.preventDefault(); go('/p/' + p.pid); } }, p.title)),
          el('td', { dir: 'ltr' }, p.seed), el('td', {}, p.owner), el('td', {}, el('span', { class: 'chip ' + p.status }, STATUS[p.status] || p.status)),
          el('td', {}, (STEPS.find(s => s[0] === p.step) || [0, p.step])[1]), el('td', { class: 'small muted', dir: 'ltr' }, p.updated.replace('T', ' ').slice(0, 16)))))
        : el('p', { class: 'muted' }, 'هنوز پروژه‌ای نیست.')));
}
function hadithRow(h) {
  return el('tr', {},
    el('td', {}, el('b', {}, h.title || ''), el('div', { class: 'small muted' }, [h.author, h.tradition === 'sunni' ? 'اهل سنت' : 'شیعه', h.page ? 'ص ' + fa(h.page) : ''].filter(Boolean).join(' · ')),
      h.projects.map(p => el('div', {}, el('a', { href: `${BASE}/p/${p.pid}`, onclick: ev => { ev.preventDefault(); go('/p/' + p.pid); } }, 'پروژه‌ی موجود'), ' ', el('span', { class: 'chip ' + p.status }, STATUS[p.status])))),
    el('td', { class: 'ar', dir: 'rtl' }, h.matn),
    el('td', {}, el('button', { class: 'primary', disabled: !ME.user, title: ME.user ? '' : 'برای ساخت پروژه وارد شوید', onclick: async () => {
      if (h.projects.length && !confirm('برای این حدیث پروژه‌ای هست. پروژه‌ی تازه بسازم؟')) return;
      const r = await post('/project', { seed: h.wid }); go('/p/' + r.pid);
    } }, 'ساخت پروژه')));
}

/* ---------- project ---------- */
let P = null;
async function project(pid, run) {
  P = await api('/project/' + pid);
  const p = P.project, readonly = !!run, runId = run || p.current_run || 'current';
  const can = ME.user && (ME.user === p.owner || ME.admin) && !readonly;
  const cur = run ? 'analysis' : ({ search: 'selection', selection: 'selection', identities: 'identities', phylogeny: 'phylogeny', analysis: 'analysis', done: 'analysis' }[p.step] || 'selection');
  const after = ['identities', 'phylogeny', 'analysis', 'done'];
  const reached = s => run ? true : ({ seed: true, selection: true, alignment: after.includes(p.step), identities: after.includes(p.step),
    phylogeny: after.slice(1).includes(p.step), analysis: after.slice(2).includes(p.step) })[s];
  const want = route().step;
  let step = STEPS.some(x => x[0] === want) && reached(want) ? want : cur;
  const tabs = STEPS.map(([k, t]) => reached(k)
    ? el('a', { class: 'step' + (step === k ? ' cur' : '') + (k === cur && !run ? ' now' : ''), href: BASE + stepUrl(pid, run, k), title: k === cur && !run ? 'مرحله‌ای که منتظر شماست' : '',
        onclick: ev => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return; ev.preventDefault(); if (step === k) return;
          step = k; history.pushState({}, '', BASE + stepUrl(pid, run, k)); tabs.forEach(e => e.classList.toggle('cur', e.dataset.k === k)); draw(); } }, t)
    : el('div', { class: 'step off', title: 'هنوز به این مرحله نرسیده‌اید' }, t));
  tabs.forEach((e, i) => e.dataset.k = STEPS[i][0]);
  const runSel = el('select', { onchange: ev => go(stepUrl(pid, ev.target.value === 'current' ? null : ev.target.value, step)) },
    el('option', { value: 'current' }, 'نسخه‌ی کنونی'), P.runs.slice().reverse().map(r => el('option', { value: r.run, selected: r.run === run }, `${r.run} — ${r.created.replace('T', ' ').slice(0, 16)}`)));
  const status = el('div'), main = el('div');
  $app.replaceChildren(
    el('div', { class: 'card' }, el('div', { class: 'toolbar', style: 'margin:0' },
      el('h2', { style: 'margin:0' }, p.title), el('span', { class: 'chip ' + p.status }, STATUS[p.status] || p.status),
      el('span', { class: 'muted small' }, 'سازنده: ' + p.owner), el('span', { class: 'grow' }), 'نسخه: ', runSel,
      readonly ? el('span', { class: 'chip partial' }, 'فقط‌خواندنی') : null), status),
    el('nav', { class: 'steps' }, tabs), main);
  statusBox(status, p, pid);
  const draw = async () => {
    main.replaceChildren(el('p', { class: 'muted' }, 'در حال بارگذاری…'));
    try { await panels[step](main, { pid, p, runId, can, readonly }); }
    catch (e) { main.replaceChildren(el('div', { class: 'card' }, e.status === 404 ? 'این مرحله هنوز آماده نیست.' : 'خطا: ' + e.message)); }
  };
  await draw();
  if (P.job || p.status === 'running') {
    const poll = async () => {
      const q = await api('/project/' + pid).catch(() => null);
      if (q && (q.project.step !== p.step || q.project.status !== p.status || q.project.current_run !== p.current_run || (!q.job && P.job))) { cache.clear(); render(); return; }
      if (q) { P = q; statusBox(status, q.project, pid); }
      timer = setTimeout(poll, 3000);
    };
    timer = setTimeout(poll, 3000);
  }
}
/* the project's state, under its title: progress while a job runs, errors, and (folded) decisions and log */
function statusBox(box, p, pid) {
  const j = P.job, pr = j && j.progress;
  const err = (P.last_job && P.last_job.error) || p.error;
  box.replaceChildren(...[
    j ? el('div', { class: 'toolbar' }, el('span', { class: 'small' }, (j.status === 'queued' ? 'در صف… ' : '') + (pr ? `${pr.stage}: ${pr.msg || ''}` : '')),
      el('div', { class: 'bar grow' }, el('i', { style: `width:${Math.round(100 * (pr ? pr.frac : 0))}%` }))) : null,
    err ? el('div', { class: 'small', style: 'color:var(--bad)' }, err) : null,
    p.status === 'failed' && ME.user && (ME.user === p.owner || ME.admin) ? el('button', { onclick: async () => { await post(`/project/${pid}/rerun`, {}); reload(pid); } }, 'اجرای دوباره') : null,
    el('details', { class: 'small' }, el('summary', { class: 'muted' }, `تصمیم‌ها و گزارش اجرا (${fa(P.runs.length)} اجرا، هیچ نسخه‌ای پاک نمی‌شود)`),
      ...Object.entries(P.decisions).map(([s, list]) => el('div', {}, { search: 'جستجو', selection: 'انتخاب شاهدها', identities: 'راویان', phylogeny: 'شجره' }[s] + ': ', list.length ? list.map(d => `v${fa(d.version)} (${d.user})`).join('، ') : '—')),
      el('div', { class: 'log' }, P.log.slice().reverse().map(l => el('div', {}, `${l.ts.slice(11, 19)} ${l.stage}: ${l.msg}`))))].filter(Boolean));
}

const panels = {
  async seed(main, c) {
    const S = await stage(c.pid, c.runId, 'search');
    main.replaceChildren(card('حدیث مبدأ', hadithBlock(S.seed, true), partsHelp(),
      el('table', {}, el('tr', {}, ...['بخش', 'متن بخش', 'شکل یکسان‌شده (برای جستجو)'].map(t => el('th', {}, t))),
        S.clauses.map(cl => el('tr', {}, el('td', {}, swatch(cl.i)), el('td', { class: 'ar' }, clauseRaw(S, cl.i)), el('td', { class: 'ar small muted' }, cl.text))))));
  },

  /* ----- 2: search and selection ----- */
  async selection(main, c) {
    const S = await stage(c.pid, c.runId, 'search');
    const dec = await api(`/project/${c.pid}/decision/selection`);
    const sdec = await api(`/project/${c.pid}/decision/search`);
    const pre = new Set(['witness', 'variant', 'extended', 'abridged']);
    const sel = new Set(dec.selected || S.candidates.filter(x => pre.has(x.suggested)).map(x => x.wid));
    const show = new Set(['witness', 'variant', 'extended', 'abridged', 'partial']);
    let filter = '';
    const clausesSel = new Set(sdec.part_query || S.clauses.map(x => x.i));
    const extra = el('input', { type: 'text', placeholder: 'شناسه‌ی حدیث برای افزودن (مثلاً v02:800316143)', dir: 'ltr' });
    const list = el('div'), count = el('span', { class: 'muted' });
    const byClass = {}; S.candidates.forEach(x => byClass[x.suggested] = (byClass[x.suggested] || 0) + 1);
    const shown = () => S.candidates.filter(x => show.has(x.suggested) && (!filter || (x.title || '').includes(filter) || x.matn.includes(filter)));

    /* per part: in how many of the selected witnesses it is found, and by which queries */
    const covCells = S.clauses.map(() => el('td', { style: 'min-width:170px' }));
    const coverage = () => {
      const chosen = S.candidates.filter(x => sel.has(x.wid));
      S.clauses.forEach(cl => {
        const n = { present: 0, partial: 0, changed: 0, absent: 0 };
        chosen.forEach(x => { const q = x.parts[cl.i]; n[q ? q.state : 'absent']++; });
        const tot = Math.max(chosen.length, 1);
        covCells[cl.i].replaceChildren(el('div', { class: 'sbar', style: `--h:${hue(cl.i)}`, title: `کامل ${fa(n.present)} · بخشی ${fa(n.partial)} · دگرگون ${fa(n.changed)} · نیامده ${fa(n.absent)}` },
          ...['present', 'partial', 'changed', 'absent'].map(k => n[k] ? el('i', { class: k, style: `width:${100 * n[k] / tot}%` }) : null)),
          el('div', { class: 'small muted' }, `کامل در ${fa(n.present)}، بخشی در ${fa(n.partial + n.changed)}، نیامده در ${fa(n.absent)}`));
      });
    };
    const upd = () => { count.textContent = `${fa(sel.size)} انتخاب‌شده از ${fa(S.candidates.length)} نامزد`; coverage(); };
    const queriesOf = i => S.queries.filter(q => q.round === 1 && q.source === S.seed.wid && q.clause === i);
    const draw = () => {
      upd();
      list.replaceChildren(el('div', { class: 'grid-wrap', style: 'max-height:75vh' }, el('table', { class: 'cands' },
        el('tr', {}, ...['', 'دسته', 'کتاب و راه یافتن', 'شباهت', 'بخش‌ها', 'متن (رنگ = بخش حدیث مبدأ)'].map(t => el('th', {}, t))),
        shown().map(x => candRow(x, S, sel, c.can, upd)))));
    };
    const chips = Object.keys(CLS).map(k => el('label', { class: 'small' }, el('input', { type: 'checkbox', checked: show.has(k), onchange: ev => { ev.target.checked ? show.add(k) : show.delete(k); draw(); } }), ' ', el('span', { class: 'chip ' + k }, `${CLS[k]} ${fa(byClass[k] || 0)}`), ' '));
    const r1 = S.rounds[0] || {}, r2 = S.rounds[1];
    const nSig = sig => S.candidates.filter(x => x.signals.includes(sig)).length;
    main.replaceChildren(
      card('حدیث مبدأ و بخش‌هایش', hadithBlock(S.seed, true), partsHelp(),
        el('table', {}, el('tr', {}, el('th', {}, 'بخش'), el('th', { title: 'بخش‌های تیک‌خورده در دور نخست جستجو پرسیده می‌شوند' }, 'در جستجو'), el('th', {}, 'متن بخش'), el('th', {}, 'در شاهدهای انتخاب‌شده'), el('th', {}, 'پرسش‌های جستجو')),
          S.clauses.map(cl => { const qs = queriesOf(cl.i);
            return el('tr', {}, el('td', {}, swatch(cl.i)),
              el('td', {}, el('input', { type: 'checkbox', checked: clausesSel.has(cl.i), disabled: !c.can, title: 'این بخش در جستجو پرسیده شود', onchange: ev => { ev.target.checked ? clausesSel.add(cl.i) : clausesSel.delete(cl.i); } })),
              el('td', { class: 'ar' }, clauseRaw(S, cl.i)), covCells[cl.i],
              el('td', {}, qs.length ? el('details', {}, el('summary', { class: 'small' }, `${fa(qs.length)} پرسش، ${fa(qs.reduce((t, q) => t + q.hits, 0))} یافته`),
                qs.map(q => el('div', { class: 'small', dir: 'ltr', style: 'text-align:left' }, `${q.corpus} · ${q.expr} → ${q.hits}${q.capped ? ' (capped)' : ''}`)))
                : el('span', { class: 'small muted' }, clausesSel.has(cl.i) ? 'بی‌واژه‌ی کمیاب' : 'پرسیده نشد')));
          })),
        el('details', { class: 'help' }, el('summary', {}, 'تیک «در جستجو» برای چیست؟'),
          el('p', {}, 'فقط برای یافتن نامزدها. هر بخش تیک‌خورده جداگانه در پیکره پرسیده می‌شود. اگر بخشی بسیار رایج باشد (مثل «قال رسول الله») و نامزدهای بی‌ربط بیاورد، تیکش را بردارید، یا اگر فقط حدیث‌هایی را می‌خواهید که بخش خاصی را دارند، فقط آن را نگه دارید؛ سپس «جستجوی دوباره» را بزنید.'),
          el('p', {}, 'این تیک روی مرحله‌ی بعد اثری ندارد: مقایسه‌ی هر نامزد با حدیث مبدأ و هم‌ترازی همیشه با همه‌ی بخش‌ها انجام می‌شود. آنچه به مرحله‌ی بعد می‌رود، شاهدهایی است که پایین‌تر تیک می‌زنید.')),
        c.can ? el('div', { class: 'toolbar' }, extra, el('button', { onclick: async () => {
          const all = clausesSel.size === S.clauses.length;
          await post(`/project/${c.pid}/decide/search`, { payload: { part_query: all ? null : [...clausesSel], extra_ids: [...(sdec.extra_ids || []), ...(extra.value.trim() ? [extra.value.trim()] : [])] } });
          reload(c.pid);
        } }, 'جستجوی دوباره با بخش‌های تیک‌خورده')) : null,
        S.flags.map(f => el('div', { class: 'small', style: 'color:var(--warn)' }, flagText(f)))),
      card('نامزدها: حدیث‌هایی که بخشی از حدیث مبدأ را دارند',
        el('p', { class: 'muted small' }, 'هر نامزد بخش‌به‌بخش با حدیث مبدأ سنجیده شد (بی‌توجه به ترتیب بخش‌ها) و سامانه دسته‌ای پیشنهاد کرد. شاهدهایی که تیک می‌زنید ورودی مرحله‌های بعدند: هم‌ترازی متن‌ها، خواندن سندها و ساخت شجره. چهار دسته‌ی نخست از پیش تیک خورده‌اند؛ تصمیم با شماست.'),
        el('details', { class: 'help' }, el('summary', {}, 'جستجو چگونه انجام شد و دسته‌ها و ستون‌ها چه معنایی دارند؟'),
          el('ol', {},
            el('li', {}, `دور نخست: برای هر بخش، دو واژه‌ی کمیاب آن با هم و یکی دو عبارت سه‌واژه‌ای کمیاب در دو پیکره (v02 و گراف دانش اهل سنت) پرسیده شد (${fa(S.queries.filter(q => q.round === 1).length)} پرسش)، به‌علاوه‌ی گروه‌بندی‌های موجود: گروه/خوشه/مرتبط‌های نور (${fa(nSig('rel:noor_group') + nSig('rel:noor_cluster') + nSig('rel:noor_related'))} نامزد)، مجموعه‌های هم‌خوانی v02 (${fa(nSig('rel:consensus'))}) و گروه‌های گراف دانش (${fa(nSig('rel:kg_group'))}). ${fa(r1.n_candidates || 0)} نامزد پیدا شد.`),
            r2 ? el('li', {}, `دور دوم: از متن ${fa(Math.min(S.params.expand_max, S.candidates.filter(x => pre.has(x.suggested)).length))} شاهدِ با عبارت‌بندی متفاوت دوباره جستجو شد تا روایت‌هایی که با حدیث مبدأ واژه‌ی مشترک کمتری دارند هم پیدا شوند (${fa(r2.n_candidates)} نامزد خام، ${fa(r2.n_scored)} ماند).`) : null,
            el('li', {}, `پالایش: نامزدی که کمتر از ${fa(Math.round(S.params.prefilter * 100))}٪ از رشته‌حرف‌های حدیث مبدأ را داشت و هیچ بخشی را هم کامل نداشت، کنار رفت (${fa(S.rounds.reduce((t, r) => t + r.n_dropped, 0))} نامزد). متن‌های کاملاً یکسان یک ردیف شدند.`)),
          el('table', { class: 'small' },
            el('tr', {}, el('td', {}, el('span', { class: 'chip witness' }, CLS.witness)), el('td', {}, 'بیشتر حدیث مبدأ (≥۶۰٪) و بیشتر این متن (≥۶۰٪) بر هم منطبق‌اند و واژه‌های جایگزین کم‌اند (≤۲۰٪).')),
            el('tr', {}, el('td', {}, el('span', { class: 'chip variant' }, CLS.variant)), el('td', {}, 'دست‌کم نیمی از حدیث مبدأ و دو بخش کامل، با افزوده‌ها، افتادگی‌ها یا واژه‌های دیگر (جایگزین ≤۳۰٪).')),
            el('tr', {}, el('td', {}, el('span', { class: 'chip extended' }, CLS.extended)), el('td', {}, 'حدیث مبدأ (≥۷۵٪) درون متنی بلندتر آمده است.')),
            el('tr', {}, el('td', {}, el('span', { class: 'chip abridged' }, CLS.abridged)), el('td', {}, 'این متن (≥۷۵٪) بخشی از حدیث مبدأ است، ولی کمتر از ۶۰٪ آن را دارد.')),
            el('tr', {}, el('td', {}, el('span', { class: 'chip partial' }, CLS.partial)), el('td', {}, 'دست‌کم یک بخش کامل مشترک است؛ بیشتر متن چیز دیگری است.')),
            el('tr', {}, el('td', {}, el('span', { class: 'chip different' }, CLS.different)), el('td', {}, 'شباهت قابل‌استفاده‌ای نیست (پیش‌فرض پنهان است).'))),
          el('p', {}, el('b', {}, 'شباهت: '), '«از حدیث مبدأ» = چند درصد واژه‌های حدیث مبدأ در این متن پیدا شد؛ «از این متن» = چند درصد این متن با حدیث مبدأ منطبق شد. اولی پایین یعنی بخش‌هایی نیامده؛ دومی پایین یعنی این متن افزوده دارد.'),
          el('p', {}, el('b', {}, 'بخش‌ها: '), 'یک خانه برای هر بخش حدیث مبدأ به رنگ همان بخش: ', strip([{ clause: 0, state: 'present', cov: 1 }], S), ' کامل (≥۸۰٪ واژه‌ها)، ', strip([{ clause: 0, state: 'partial', cov: .5 }], S), ' بخشی (۴۰–۸۰٪)، ', strip([{ clause: 0, state: 'changed', cov: 0 }], S), ' دگرگون (واژه‌های جایگزین)، ', strip([{ clause: 0, state: 'absent', cov: 0 }], S), ' نیامده.'),
          el('p', {}, el('b', {}, 'متن: '), 'جای هر بخش در متن نامزد به رنگ همان بخش است؛ متن بی‌رنگ در حدیث مبدأ نیست (افزوده‌ی این روایت).')),
        el('div', { class: 'toolbar' }, ...chips, el('input', { type: 'text', placeholder: 'پالایش با نام کتاب یا کلمه', oninput: ev => { filter = ev.target.value; draw(); } }), count,
          c.can ? el('button', { class: 'small', onclick: () => { shown().forEach(x => sel.add(x.wid)); draw(); } }, 'تیک همه‌ی نمایش‌داده‌ها') : null,
          c.can ? el('button', { class: 'small', onclick: () => { shown().forEach(x => sel.delete(x.wid)); draw(); } }, 'برداشتن تیک نمایش‌داده‌ها') : null),
        list,
        el('div', { class: 'toolbar' }, c.can ? el('button', { class: 'primary', disabled: !sel.size, onclick: async () => {
          await post(`/project/${c.pid}/decide/selection`, { payload: { selected: [...sel] } }); reload(c.pid);
        } }, 'تأیید انتخاب و ادامه') : el('span', { class: 'muted' }, ME.user ? 'فقط سازنده‌ی پروژه یا مدیر می‌تواند تصمیم بگیرد.' : 'برای تصمیم‌گیری وارد شوید.'),
          el('span', { class: 'small muted' }, `اجرای جستجو ${fa(S.seconds)} ثانیه؛ دورهای گسترش: ${fa(S.rounds.length)}`))));
    draw();
  },

  /* ----- 3: alignment ----- */
  async alignment(main, c) {
    const [A, S] = await Promise.all([stage(c.pid, c.runId, 'align'), stage(c.pid, c.runId, 'search')]);
    const seed = A.seed, W = A.witnesses;
    const grid = Object.fromEntries(A.grids.map(g => [g.clause, g]));
    const old = A.units.some(u => u.cols === undefined);       // written by the aligner before stage version 2
    const units = A.units.map((u, k) => ({ ...u, k, cols: u.cols !== undefined ? u.cols : unitCols(u) }));
    const colUnits = {};
    units.forEach(u => { if (u.cols) for (let j = u.cols[0]; j <= u.cols[1]; j++) (colUnits[`${u.clause}|${j}`] ||= []).push(u.k); });
    const clauseUnit = {}; units.forEach(u => { if (u.unit_kind === 'clause') clauseUnit[u.clause] = u.k; });
    const cname = cl => cl.kind === 'seed' ? clauseRaw(S, cl.i) : cl.text;
    const hadith = wid => wid === S.seed.wid ? S.seed : S.candidates.find(x => x.wid === wid || (x.copies || []).includes(wid));
    let view = pref('icm.alview', 'one'), onlyDiff = pref('icm.aldiff', '0') === '1', pick = null;

    /* the cells of one witness in one part: per grid column, or one cell when the part is not in its text */
    const cellsOf = (cl, w, keep) => {
      const g = grid[cl.i], sg = A.segments[`${cl.i}|${w.wid}`] || {};
      const hu = `--h:${hue(cl.i)}`;
      if (g && g.columns.length && g.witnesses.includes(w.wid)) {
        const idx = g.columns.map((col, j) => col[w.wid] != null ? j : -1).filter(j => j >= 0);
        const lo = idx[0], hi = idx[idx.length - 1];
        return keep.map(j => {
          const col = g.columns[j], t = col[w.wid], r = refTok(g, j, seed);
          const cls = j < lo || j > hi ? 'na' : t == null ? (r == null ? 'gap' : 'om') : r == null ? 'ins' : mkey(t) === mkey(r) ? 'same' : 'diff';
          const on = pick !== null && (colUnits[`${cl.i}|${j}`] || []).includes(pick);
          const td = el('td', { class: cls + (on ? ' pk' : '') + (j === keep[0] ? ' sep' : ''), style: hu,
            title: cls === 'om' ? `«${r}» در این شاهد نیامده` : cls === 'ins' ? 'در حدیث مبدأ نیست' : cls === 'diff' ? `حدیث مبدأ: ${r}` : '' }, t != null ? t : cls === 'na' ? '' : '·');
          td.dataset.c = `${cl.i}|${j}`;
          return td;
        });
      }
      const n = Math.max(keep.length, 1);
      const on = pick !== null && clauseUnit[cl.i] === pick;
      if (sg.state === 'present' && sg.tokens && sg.tokens.length)
        return [el('td', { class: 'same sep' + (on ? ' pk' : ''), style: hu, colspan: n }, sg.tokens.join(' '))];
      const why = w.marker != null && sg.state !== 'present' ? 'خلاصه‌شده' : sg.state === 'absent' ? 'نیامده' : 'نامعلوم';
      return [el('td', { class: 'miss sep' + (on ? ' pk' : ''), style: hu, colspan: n, title: { 'نیامده': 'این بخش در متن این شاهد نیست', 'خلاصه‌شده': 'متن با «وساق الحدیث / بنحوه…» کوتاه شده؛ نبودن بخش نشانه‌ی افتادگی نیست', 'نامعلوم': 'فقط تکه‌ای از این بخش پیدا شد' }[why] }, why)];
    };
    /* columns of a part to show: all, or only those where some witness differs from the seed */
    const keepOf = cl => {
      const g = grid[cl.i];
      if (!g || !g.columns.length) return [];
      const all = g.columns.map((_, j) => j);
      if (!onlyDiff) return all;
      return all.filter(j => colUnits[`${cl.i}|${j}`] || g.witnesses.some(w => { const t = g.columns[j][w], r = refTok(g, j, seed); return t == null ? r != null && inExtent(g, w, j) : r == null || mkey(t) !== mkey(r); }));
    };
    const nameCell = (w, i) => el('td', { class: 'nm', title: [w.title, w.author, w.death ? 'ف ' + fa(w.death) : '', w.tradition === 'sunni' ? 'اهل سنت' : 'شیعه', w.wid].filter(Boolean).join(' · '),
      onclick: () => { const h = hadith(w.wid); if (h) modal(hadithBlock(h, true)); } },
      el('span', { class: 'dot ' + w.cls }), `${fa(i)}. ${w.title || w.wid}`, el('span', { class: 'small muted' }, w.death ? ' ف ' + fa(w.death) : ''));
    const table = (cls, rows) => {
      const keeps = cls.map(keepOf);
      const head1 = el('tr', { class: 'b' }, el('th', { class: 'nm', rowspan: 2 }, 'شاهد ↓ / بخش ←'),
        cls.map((cl, n) => el('th', { class: 'band' + (pick !== null && clauseUnit[cl.i] === pick ? ' pk' : ''), style: `--h:${hue(cl.i)}`, colspan: Math.max(keeps[n].length, 1), title: cname(cl) },
          `${fa(cl.i + 1)}${cl.kind === 'extra' ? ' (افزوده)' : ''} `, el('span', { class: 'ar' }, short(cname(cl), Math.max(8, 4 * keeps[n].length))),
          clauseUnit[cl.i] !== undefined ? ubadge(clauseUnit[cl.i]) : null)));
      const head2 = el('tr', { class: 'u' }, cls.flatMap((cl, n) => keeps[n].length
        ? keeps[n].map(j => el('th', { class: j === keeps[n][0] ? 'sep' : '', style: `--h:${hue(cl.i)}` }, (colUnits[`${cl.i}|${j}`] || []).map(ubadge)))
        : el('th', { class: 'sep', style: `--h:${hue(cl.i)}` }, onlyDiff && grid[cl.i] && grid[cl.i].columns.length ? el('span', { class: 'small muted' }, 'یکسان') : '')));
      return el('div', { class: 'grid-wrap', style: 'max-height:76vh' }, el('table', { class: 'altab' }, el('thead', {}, head1, head2),
        el('tbody', {}, rows.map(([w, i]) => el('tr', { class: w.wid === seed ? 'seed' : '' }, nameCell(w, i),
          cls.flatMap((cl, n) => keeps[n].length || !(grid[cl.i] && grid[cl.i].columns.length) ? cellsOf(cl, w, keeps[n])
            : [el('td', { class: 'same sep', style: `--h:${hue(cl.i)}` }, grid[cl.i].witnesses.includes(w.wid) ? '=' : '')]))))));
    };
    const ubadge = k => el('button', { class: 'ub' + (pick === k ? ' on' : ''), title: unitTitle(units[k]), onclick: ev => { ev.stopPropagation(); setPick(pick === k ? null : k); } }, fa(k + 1));
    const unitTitle = u => `واحد ${fa(u.k + 1)}: ${UKIND(u)} — ` + u.labels.map((l, i) => `${ulab(l)} ${fa(Object.values(u.states).filter(v => v === i).length)}`).join('، ');

    const pickBox = el('div', { class: 'pickbox' }), body = el('div');
    const setPick = k => {
      pick = k; drawBody(); drawPick(); drawUnits();
      if (k !== null) {     // scroll the grid (not the page) to the unit's column, then show the unit's box
        const u = units[k], t = body.querySelector(u.cols ? `td[data-c="${u.clause}|${u.cols[0]}"]` : 'th.band.pk');
        const y = window.scrollY;
        if (t) { t.scrollIntoView({ block: 'nearest', inline: 'center' }); window.scrollTo(0, y); }
        pickBox.scrollIntoView({ block: 'nearest' });
      }
    };
    const drawPick = () => {
      if (pick === null) { pickBox.replaceChildren(); return; }
      const u = units[pick], names = Object.fromEntries(W.map((w, i) => [w.wid, `${fa(i)}. ${w.title || w.wid}`]));
      const groups = u.labels.map((l, i) => [ulab(l), Object.keys(u.states).filter(w => u.states[w] === i)]);
      const na = Object.keys(u.states).filter(w => u.states[w] < 0);
      pickBox.replaceChildren(el('div', { class: 'help', style: `--h:${hue(u.clause)}` },
        el('div', { class: 'toolbar', style: 'margin:0' }, swatch(u.clause), el('b', {}, `واحد ${fa(u.k + 1)}: ${UKIND(u)}`), el('span', { class: 'ar' }, uwhere(u, A)), el('span', { class: 'grow' }), el('button', { class: 'small', onclick: () => setPick(null) }, 'بستن')),
        groups.map(([l, ws]) => el('div', { class: 'small' }, el('b', { class: 'ar' }, l), ` (${fa(ws.length)}): `, ws.map(w => names[w]).join('، '))),
        na.length ? el('div', { class: 'small muted' }, `نامعلوم (${fa(na.length)}): `, na.map(w => names[w]).join('، ')) : null));
    };
    const drawBody = () => {
      const rows = W.map((w, i) => [w, i]);
      if (view === 'one') { body.replaceChildren(table(A.clauses, rows)); return; }
      body.replaceChildren(...A.clauses.map(cl => {
        const g = grid[cl.i], holds = rows.filter(([w]) => { const sg = A.segments[`${cl.i}|${w.wid}`] || {}; return g && g.columns.length ? g.witnesses.includes(w.wid) : sg.state === 'present'; });
        const rest = rows.filter(r => !holds.includes(r)), by = {};
        rest.forEach(([w, i]) => { const sg = A.segments[`${cl.i}|${w.wid}`] || {}; const k = w.marker != null ? 'خلاصه‌شده' : sg.state === 'absent' ? 'نیامده' : 'نامعلوم'; (by[k] ||= []).push(`${fa(i)}. ${w.title || w.wid}`); });
        return el('div', { class: 'cardbox', style: `--h:${hue(cl.i)};border-inline-start:5px solid hsl(var(--h) var(--k-s) var(--k-l))` },
          el('div', { class: 'toolbar', style: 'margin:0 0 6px' }, swatch(cl.i), el('b', { class: 'ar' }, cname(cl)), cl.kind === 'extra' ? el('span', { class: 'chip partial' }, `بخش افزوده، در ${fa(cl.support)} شاهد`) : null,
            clauseUnit[cl.i] !== undefined ? ubadge(clauseUnit[cl.i]) : null, el('span', { class: 'small muted' }, `در ${fa(holds.length)} شاهد`)),
          holds.length ? table([cl], holds) : null,
          Object.entries(by).map(([k, ns]) => el('div', { class: 'small muted' }, `${k} در ${fa(ns.length)} شاهد: `, ns.join('، '))));
      }));
    };
    const unitsBox = el('div');
    const drawUnits = () => unitsBox.replaceChildren(el('table', { class: 'units' },
      el('tr', {}, ...['واحد', 'بخش', 'گونه', 'جا در متن', 'حالت‌ها (شمار شاهدها)'].map(t => el('th', {}, t))),
      units.map(u => { const nNA = Object.values(u.states).filter(v => v < 0).length;
        return el('tr', { class: pick === u.k ? 'on' : '', onclick: () => setPick(pick === u.k ? null : u.k), title: 'نمایش در جدول هم‌ترازی' },
          el('td', {}, ubadge(u.k)), el('td', {}, swatch(u.clause)), el('td', { class: 'small' }, UKIND(u)), el('td', { class: 'ar' }, uwhere(u, A)),
          el('td', {}, u.labels.map((l, i) => el('span', { class: 'st', style: `--h:${hue(u.clause)}` }, el('span', { class: 'ar' }, ulab(l)), ' ', fa(Object.values(u.states).filter(v => v === i).length))),
            nNA ? el('span', { class: 'small muted' }, ` نامعلوم ${fa(nNA)}`) : null)); })));

    const seg = (opts, cur, set) => el('span', { class: 'seg' }, opts.map(([k, t]) => el('button', { class: k === cur ? 'on' : '', onclick: () => set(k) }, t)));
    const tools = el('div', { class: 'toolbar' });
    const drawTools = () => tools.replaceChildren(
      seg([['one', 'همه‌ی بخش‌ها در یک جدول'], ['parts', 'هر بخش جدا']], view, k => { view = k; setPref('icm.alview', k); drawTools(); drawBody(); }),
      el('label', { class: 'small' }, el('input', { type: 'checkbox', checked: onlyDiff, onchange: ev => { onlyDiff = ev.target.checked; setPref('icm.aldiff', onlyDiff ? '1' : '0'); drawBody(); } }), ' فقط ستون‌هایی که اختلاف دارند'),
      el('span', { class: 'small muted' }, 'راهنما: '), el('span', { class: 'lg same', style: `--h:${hue(0)}` }, 'مثل حدیث مبدأ'), el('span', { class: 'lg diff', style: `--h:${hue(0)}` }, 'واژه‌ی دیگر'),
      el('span', { class: 'lg ins', style: `--h:${hue(0)}` }, 'افزوده'), el('span', { class: 'lg om' }, '· افتاده'), el('span', { class: 'lg miss' }, 'بخش نیامده'), ubadgeDemo());
    drawTools(); drawBody(); drawUnits();

    const nPrivate = A.private_variants.length;
    main.replaceChildren(
      card('هم‌ترازی متن شاهدها، بخش‌به‌بخش',
        el('p', { class: 'muted small' }, `${fa(W.length)} شاهد (حدیث مبدأ و شاهدهایی که در مرحله‌ی ۲ انتخاب شدند)، ${fa(A.clauses.length)} بخش. هر ردیف یک شاهد است و از راست به چپ مثل متن خوانده می‌شود؛ هر ستون یک جایگاه واژه. رنگ هر گروه ستون همان رنگ بخش در مرحله‌ی ۲ است. نام کتاب را بزنید تا متن کامل آن را ببینید.`),
        el('details', { class: 'help' }, el('summary', {}, 'هم‌ترازی چگونه ساخته شد؟'),
          el('ol', {},
            el('li', {}, 'جای هر بخش حدیث مبدأ در متن هر شاهد جداگانه پیدا شد، بی‌توجه به ترتیب؛ پس شاهدی که بندها را جابه‌جا آورده هم درست هم‌تراز می‌شود. عبارت‌هایی مثل «قال رسول الله صلی الله علیه وآله» جزو بخش شمرده نمی‌شوند.'),
            el('li', {}, 'تکه‌ای که در حدیث مبدأ نیست ولی دست‌کم دو شاهد آن را دارند، «بخش افزوده» شد. تکه‌هایی که فقط یک شاهد دارد پایین‌تر فهرست شده‌اند.'),
            el('li', {}, 'واژه‌های هر بخش در همه‌ی شاهدهای دارای آن بخش با هم هم‌تراز شد (هم‌ترازی چندگانه با یک دور بازبینی برای هر ردیف). واژه‌هایی که فقط در «و/ف/ال» فرق دارند یکسان شمرده می‌شوند، «و» جدانوشته به واژه‌ی بعدش چسبانده می‌شود، و عددها (ستة، سبعة، خمسة…) در یک ستون می‌آیند.'),
            el('li', {}, 'خانه‌ی خالی پیش از نخستین یا پس از واپسین واژه‌ی یک شاهد یعنی متن آن شاهد آنجا کوتاه‌تر است؛ این «افتادگی» شمرده نمی‌شود. شاهدی که با «وساق الحدیث / بنحوه…» خلاصه شده، برای بخش‌هایی که نیاورده «نامعلوم» است.'),
            el('li', {}, 'شماره‌های کوچک بالای ستون‌ها واحدهای تغییرند (جدول پایین)؛ با زدن هر کدام، ستون‌ها و شاهدهای هر حالت نشان داده می‌شوند.'))),
        old && !c.readonly ? el('div', { class: 'help', style: 'border-color:var(--warn)' }, 'این هم‌ترازی با نسخه‌ی پیشینِ برنامه ساخته شده است (پیش از درست شدن جابه‌جایی برخی ردیف‌ها و هم‌ستون شدن عددها). ',
          c.can ? el('button', { class: 'small', onclick: async () => { await post(`/project/${c.pid}/rerun`, {}); reload(c.pid); } }, 'اجرای دوباره با برنامه‌ی تازه') : 'سازنده‌ی پروژه می‌تواند آن را دوباره اجرا کند.',
          el('span', { class: 'small muted' }, ' مرحله‌های بعد هم دوباره محاسبه می‌شوند؛ اجرای کنونی پاک نمی‌شود.')) : null,
        tools, pickBox, body),
      card('واحدهای تغییر: ورودی مدل شجره',
        el('p', { class: 'small' }, `واحد تغییر جایی از متن است که شاهدها در آن با هم فرق دارند؛ هر شاهد در هر واحد یک «حالت» دارد. ${fa(A.n_units)} واحد از جدول بالا بیرون کشیده شد و به مرحله‌ی ۵ (شجره) می‌رود.`),
        el('details', { class: 'help' }, el('summary', {}, 'این واحدها چه کاری می‌کنند؟'),
          el('p', {}, 'مدل شجره (مرحله‌ی ۵) هم سندها را می‌بیند و هم این واحدها را. شاهدهایی که در واحدهای بیشتری حالتِ کم‌یاب مشترک دارند (مثلاً «سبعة» به جای «ستة» و پرسشِ «ومن هم یا رسول الله») به احتمال بیشتر از یک منبع یا یک راوی مشترک گرفته‌اند. پس این جدول همان چیزی است که متن‌ها در برآورد «قدیمی‌ترین منبع» می‌گویند.'),
          el('ul', {},
            el('li', {}, el('b', {}, 'حضور بخش: '), 'آیا شاهد این بخش را دارد؟ (دارد / ندارد)'),
            el('li', {}, el('b', {}, 'حضور واژه: '), 'چند واژه‌ی کنار هم که برخی شاهدها دارند و برخی نه.'),
            el('li', {}, el('b', {}, 'واژه‌ی جایگزین: '), 'در یک جایگاه، شاهدها واژه‌های مختلف دارند؛ هر واژه یک حالت.'),
            el('li', {}, el('b', {}, 'عدد: '), 'عددی که حدیث می‌گوید (۴، ۵، ۶، ۷…).')),
          el('p', {}, `«نامعلوم» یعنی آن جا در متن شاهد نیامده چون کوتاه یا خلاصه شده است؛ مدل آن را حدس نمی‌زند. واحدی که فقط یک شاهد با بقیه فرق دارد درباره‌ی گروه‌بندی چیزی نمی‌گوید و کنار گذاشته شد (${fa(nPrivate)} واحد)؛ ${A.dropped_orthographic ? fa(A.dropped_orthographic) + ' ستون هم فقط اختلاف املایی داشت. ' : ''}حداکثر ۴۰ واحد نگه داشته می‌شود، آگاهی‌بخش‌ترها نخست.`)),
        unitsBox,
        nPrivate ? el('details', {}, el('summary', { class: 'small muted' }, `${fa(nPrivate)} واحد کنارگذاشته (فقط یک شاهد متفاوت)`),
          el('div', { class: 'small ar' }, A.private_variants.map(u => u.name.split(':').slice(1).join(':')).join('  ·  '))) : null,
        A.private_texts.length ? el('details', {}, el('summary', { class: 'small muted' }, `${fa(A.private_texts.length)} تکه‌ی افزوده که فقط در یک شاهد آمده`),
          A.private_texts.map(t => el('div', { class: 'ar small' }, `${fa(W.findIndex(w => w.wid === t.wid))}. ${(W.find(w => w.wid === t.wid) || {}).title || t.wid}: ${t.text}`))) : null));
  },

  /* ----- 4: identities ----- */
  /* three parts: (1) the hadiths and their state, (2) the chain of the chosen hadith, (3) the chosen name: its places,
     then its options and a search of both narrator databases */
  async identities(main, c) {
    const [I, A, S, dec, phd] = await Promise.all([stage(c.pid, c.runId, 'identify'), stage(c.pid, c.runId, 'align'), stage(c.pid, c.runId, 'search'),
      api(`/project/${c.pid}/decision/identities`), api(`/project/${c.pid}/decision/phylogeny`)]);
    /* dates entered on step 5 before they moved here are carried over (those keyed by a person id) */
    const oldDates = Object.fromEntries(Object.entries(phd.dates || {}).filter(([k]) => /^(noor|kg):\d+$/.test(k)));
    const saved = { choices: dec.choices || {}, overrides: dec.overrides || {}, dates: { ...oldDates, ...(dec.dates || {}) } };
    const choices = { ...saved.choices }, overrides = { ...saved.overrides }, dates = { ...saved.dates };
    const cards = I.cards, W = A.witnesses;
    const num = Object.fromEntries(W.map((w, i) => [w.wid, i]));
    const byWid = {}; I.chains.forEach(ch => (byWid[ch.wid] ||= []).push(ch));
    const order = [...W.map(w => w.wid).filter(w => byWid[w]), ...Object.keys(byWid).filter(w => !(w in num))];
    const chainOf = Object.fromEntries(I.chains.map(ch => [ch.aid, ch]));
    const wtitle = wid => { const w = W.find(x => x.wid === wid); return `${wid in num ? fa(num[wid]) + '. ' : ''}${(w && w.title) || wid}`; };
    const hadith = wid => wid === S.seed.wid ? S.seed : S.candidates.find(x => x.wid === wid || (x.copies || []).includes(wid));
    const seq = []; order.forEach(wid => byWid[wid].forEach(ch => ch.elems.forEach(e => { if (e.card && !seq.includes(e.card)) seq.push(e.card); })));
    const relOnly = cd => cd.kind === 'relative';         // «عن أبيه» means a different person in every chain: decided per place
    const prevName = cd => { const [aid, i] = cd.occurrences[0]; const p = chainOf[aid].elems[i - 1]; return p ? p.raw || p.name : ''; };
    const cname = cd => relOnly(cd) && cd.occurrences.length === 1 && prevName(cd) ? `${cd.name} — پس از ${prevName(cd)}` : cd.name;
    const keyOf = e => e.card || (e.exact ? 'id:' + e.exact : null);   // what clicking a name opens: its card, or the person the source fixed
    const splitOcc = occ => { const k = occ.lastIndexOf('|'); const ch = chainOf[occ.slice(0, k)]; return [ch, ch.elems[+occ.slice(k + 1)]]; };
    const extraLabel = {};                                // labels of people chosen from a search
    const qs = new URLSearchParams(location.search);
    let cur = byWid[qs.get('h')] ? qs.get('h') : null, pick = null;
    if (cur && qs.get('n')) { try { const [ch, e] = splitOcc(qs.get('n')); if (ch.wid === cur && keyOf(e)) pick = { key: keyOf(e), occ: qs.get('n') }; } catch (_) {} }
    let listView = pref('icm.idlist', 'hadith'), onlyUnd = false, onlyUndated = false, q = '', scopeHere = false, sq = '', sres = null, sbusy = false;

    /* what a place in a chain resolves to: {st, id}; st = exact | anon | user | auto | und (id 'none' = name only) */
    const stateOf = (ch, e) => {
      const occ = `${ch.aid}|${e.i}`;
      if (e.exact) return { st: 'exact', id: e.exact };
      if (e.kind === 'anon') return { st: 'anon' };
      if (occ in overrides) return { st: 'user', id: overrides[occ], here: true };
      const cd = cards[e.card];
      if (cd && cd.key in choices) return { st: 'user', id: choices[cd.key] };
      if (e.kind === 'relative' && e.cands[0] && e.cands[0].derived) return { st: 'auto', id: e.cands[0].id };
      if (cd && cd.default && !(relOnly(cd) && cd.occurrences.length > 1)) return { st: 'auto', id: cd.default };   // older runs shared one card for every «أبيه»
      return { st: 'und' };
    };
    const stOf = s => s.id === 'none' ? 'none' : s.st;
    const labelOf = (id, e, cd) => { const k = [...(e ? e.cands : []), ...(cd ? cd.candidates : [])].find(x => x.id === id); return k ? k.label : extraLabel[id] || id; };
    const isGap = e => e.exact && /^(موضع |اسم مبهم)/.test(e.name);      // the KG's placeholders: a gap, a cut, an unnamed man
    const cardState = cd => {
      const sts = cd.occurrences.map(o => stateOf(...splitOcc(o.join('|'))).st);
      return sts.includes('und') ? 'und' : sts.includes('user') ? 'user' : 'auto';
    };
    const counts = () => { const n = { auto: 0, user: 0, und: 0 }; Object.values(cards).forEach(cd => n[cardState(cd)]++); return n; };
    const dirty = () => JSON.stringify([choices, overrides, dates]) !== JSON.stringify([saved.choices, saved.overrides, saved.dates]);
    /* death dates: the user's, else the databases' (fetched per id), else the work's author for a collector */
    const known = {}, asked = new Set();
    const fetchDeaths = async ids => {
      ids = [...new Set(ids)].filter(i => /^(noor|kg):\d+$/.test(i) && !asked.has(i)); if (!ids.length) return false;
      ids.forEach(i => asked.add(i));
      for (let k = 0; k < ids.length; k += 150) Object.assign(known, await api('/narrators/deaths?ids=' + ids.slice(k, k + 150).join(',')).catch(() => ({})));
      return true;
    };
    const dateKey = (ch, e, s) => s.id && /^(noor|kg):\d+$/.test(s.id) ? s.id : `occ:${ch.aid}|${e.i}`;
    const dateOf = (ch, e) => {
      const s = stateOf(ch, e), k = dateKey(ch, e, s);
      if (dates[k]) return { death: dates[k], src: 'user', key: k };
      if (s.id && known[s.id]) return { death: known[s.id].death, src: known[s.id].source, key: k };
      const w = W.find(x => x.wid === ch.wid);
      if (e.i === 0 && w && w.death) return { death: [w.death, w.death], src: 'author', key: k };
      return { death: null, key: k };
    };
    const yr = d => d[0] === d[1] ? fa(Math.round(d[0])) : `${fa(Math.round(d[0]))}–${fa(Math.round(d[1]))}`;
    const SRCD = { user: 'واردشده‌ی کاربر', author: 'تاریخ درگذشت مؤلف کتاب', 'hadith-kg': 'گراف دانش' };
    /* the state of a hadith: the states of all the names in its chains */
    const hstate = wid => {
      const els = byWid[wid].flatMap(ch => ch.elems.filter(e => !isGap(e)).map(e => [ch, e]));
      const sts = els.map(([ch, e]) => stOf(stateOf(ch, e)));
      const nund = els.filter(([ch, e]) => e.kind !== 'anon' && !dateOf(ch, e).death).length;
      const n = k => sts.filter(s => s === k).length;
      const [st, txt] = n('und') ? ['und', `${fa(n('und'))} بی‌تصمیم`] : sts.every(s => s === 'exact') ? ['exact', 'همه قطعی']
        : n('none') + n('anon') ? ['none', 'همه تصمیم‌گرفته'] : ['auto', 'همه شناخته'];
      return { sts, st, txt, nund, tip: ['exact', 'auto', 'user', 'und', 'none', 'anon'].filter(k => n(k)).map(k => `${fa(n(k))} ${ST[k]}`).join('، ') + (nund ? `، ${fa(nund)} بی‌تاریخ` : '') };
    };

    const ST = { exact: 'قطعی (از منبع)', auto: 'خودکار', user: 'انتخاب کاربر', und: 'بی‌تصمیم', none: 'فقط نام', anon: 'نامعلوم' };
    const chip = (ch, e) => {
      if (isGap(e)) return el('span', { class: 'gapm', title: 'جایی که منبع راوی‌ای را نیاورده است' }, '‹' + e.name.replace('موضع ', '').replace('اسم مبهم', 'بی‌نام') + '›');
      const s = stateOf(ch, e), cd = cards[e.card], st = stOf(s), occ = `${ch.aid}|${e.i}`;
      const n = cd && !relOnly(cd) ? cd.occurrences.length : 1;
      const sub = st === 'exact' ? '✓' : st === 'und' ? 'بی‌تصمیم' : st === 'none' ? 'فقط نام' : st === 'anon' ? 'نامعلوم' : labelOf(s.id, e, cd);
      const hit = pick && (pick.key === keyOf(e) || pick.key.startsWith('id:') && s.id === pick.key.slice(3));
      return el('button', { class: `nc ${st}` + (hit ? (pick.occ === occ ? ' pk pk2' : ' pk') : ''),
        title: `${ST[st]}${s.here ? ' — فقط همین جا' : ''}${s.id && s.id !== 'none' ? ' — ' + s.id : ''}`,
        onclick: () => { if (keyOf(e)) setPick(keyOf(e), occ); } },
        el('span', { class: 'n ar', title: e.raw || e.name }, st === 'exact' ? short(e.raw || e.name, 34) : e.raw || e.name, n > 1 ? el('sup', {}, ' ×' + fa(n)) : null),
        el('span', { class: 'id' + (st === 'exact' ? '' : ' ar') }, s.here ? '◆ ' : '', sub),
        st === 'anon' ? null : (d => el('span', { class: 'dt' + (d.death ? (d.src === 'user' ? ' u' : '') : ' no') }, d.death ? 'ف ' + yr(d.death) : 'بی‌تاریخ'))(dateOf(ch, e)));
    };
    const SRC = { noor_slots: 'سند از پیوندهای نور', kg: 'سند از گراف دانش', text: 'سند خوانده‌شده از متن' };
    const strip2 = sts => el('span', { class: 'ststrip' }, sts.map(s => el('i', { class: s, title: ST[s] })));

    /* navigation; the hadith and the name are kept in the address */
    const keepUrl = () => { const u = new URLSearchParams(); if (cur) u.set('h', cur); if (pick) u.set('n', pick.occ); history.replaceState(history.state, '', location.pathname + (cur ? '?' + u : '')); };
    const setCur = wid => { cur = wid; if (pick && splitOcc(pick.occ)[0].wid !== wid) pick = null; keepUrl(); draw(); part2.scrollIntoView({ block: 'start', behavior: 'smooth' }); };
    const setPick = (key, occ) => {
      if (!pick || pick.key !== key) { scopeHere = false; sq = cards[key] ? cards[key].name : ''; sres = null; }
      pick = { key, occ }; cur = splitOcc(occ)[0].wid; keepUrl(); draw();
      part3.scrollIntoView({ block: 'start', behavior: 'smooth' });
    };
    const setVal = (cd, occ, here, v) => {
      if (here) { if (v === undefined) delete overrides[occ]; else overrides[occ] = v; }
      else { if (v === undefined) delete choices[cd.key]; else choices[cd.key] = v; delete overrides[occ]; }
      draw();
    };

    /* part 1: the hadiths (or the names) */
    const listBox = el('div', { class: 'hlist' });
    const drawList = () => {
      const top = listBox.scrollTop;
      if (listView === 'hadith') {
        const rows = order.map(wid => [wid, hstate(wid)]).filter(([, h]) => (!onlyUnd || h.st === 'und') && (!onlyUndated || h.nund))
          .filter(([wid]) => !q || byWid[wid].some(ch => ch.elems.some(e => (e.raw || e.name).includes(q))) || wtitle(wid).includes(q));
        fill(listBox, rows.length ? el('table', { class: 'hl' }, el('tbody', {}, rows.map(([wid, h]) => {
          const w = W.find(x => x.wid === wid) || hadith(wid) || {};
          return el('tr', { class: wid === cur ? 'on' : '', onclick: () => setCur(wid) },
            el('td', {}, el('span', { class: 'dot ' + (w.cls || '') }), el('b', {}, wtitle(wid))),
            el('td', { class: 'small muted hide-sm' }, [w.author, w.death ? 'ف ' + fa(w.death) : '', w.tradition === 'sunni' ? 'اهل سنت' : 'شیعه'].filter(Boolean).join(' · ')),
            el('td', { title: h.tip }, strip2(h.sts), h.nund ? el('div', { class: 'small muted' }, `${fa(h.nund)} بی‌تاریخ`) : null), el('td', {}, el('span', { class: 'chip ' + h.st }, h.txt)));
        }))) : el('p', { class: 'muted' }, 'حدیثی نیست.'));
      } else {
        const ks = seq.filter(k => (!onlyUnd || cardState(cards[k]) === 'und') && (!q || cards[k].name.includes(q)));
        fill(listBox, ks.length ? el('table', { class: 'hl' }, el('tbody', {}, ks.map(k => {
          const cd = cards[k], st = cardState(cd), o = cd.occurrences[0].join('|');
          return el('tr', { class: pick && pick.key === k ? 'on' : '', onclick: () => setPick(k, pick && pick.key === k ? pick.occ : o) },
            el('td', {}, el('b', { class: 'ar' }, cname(cd)), cd.kind === 'collector' ? el('span', { class: 'small muted' }, ' (گردآورنده)') : null),
            el('td', { class: 'small muted' }, `${fa(cd.occurrences.length)} جا · بهترین نامزد: ${TIER[cd.tier] || cd.tier}`),
            el('td', { class: 'small muted hide-sm' }, 'نخست در ' + wtitle(chainOf[cd.occurrences[0][0]].wid)),
            el('td', {}, el('span', { class: 'chip ' + st }, ST[st])));
        }))) : el('p', { class: 'muted' }, 'نامی نیست.'));
      }
      listBox.scrollTop = top;
      const hs = order.map(hstate), n = k => hs.filter(h => h.st === k).length;
      listSum.textContent = `${fa(order.length)} حدیث: ${fa(n('exact'))} همه قطعی، ${fa(n('auto'))} همه شناخته، ${fa(n('none'))} همه تصمیم‌گرفته (برخی فقط نام)، ${fa(n('und'))} با نام بی‌تصمیم.`;
      seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === listView));
    };

    /* part 2: the chain(s) of the chosen hadith */
    const part2 = el('div', { class: 'card' });
    const drawChain = () => {
      if (!cur) { fill(part2, el('h2', {}, '۲. راویان حدیث'), el('p', { class: 'muted' }, 'از فهرست بالا یک حدیث را برگزینید.')); return; }
      const h = hadith(cur), w = W.find(x => x.wid === cur) || h || {}, chs = byWid[cur], k = order.indexOf(cur), hs = hstate(cur);
      fill(part2, 
        el('div', { class: 'toolbar', style: 'margin-top:0' }, el('h2', { style: 'margin:0' }, '۲. راویان ', el('button', { class: 'lnk', style: 'font-size:inherit;font-weight:700', title: 'متن کامل', onclick: () => h && modal(hadithBlock(h, true)) }, wtitle(cur))),
          el('span', { class: 'chip ' + hs.st, title: hs.tip }, hs.txt), el('span', { class: 'grow' }),
          el('button', { class: 'small', disabled: k <= 0, onclick: () => setCur(order[k - 1]) }, '→ قبلی'), el('button', { class: 'small', disabled: k >= order.length - 1, onclick: () => setCur(order[k + 1]) }, 'بعدی ←')),
        el('div', { class: 'small muted' }, [w.author, w.death ? 'ف ' + fa(w.death) : '', w.tradition === 'sunni' ? 'اهل سنت' : 'شیعه', SRC[chs[0].source], hs.tip].filter(Boolean).join(' · ')),
        chs.some(ch => ch.flags.length) ? el('div', { class: 'small', style: 'color:var(--warn)', title: chs.flatMap(ch => ch.flags).join(' ') }, '⚠ سند ناقص یا نامعمول خوانده شد') : null,
        chs.map((ch, j) => el('div', { class: 'chain' }, chs.length > 1 ? el('span', { class: 'small muted' }, `سند ${fa(j + 1)}:`) : null,
          ch.elems.flatMap((e, i) => [i ? el('span', { class: 'tm' }, e.term || '←') : null, chip(ch, e)]),
          ch.speaker ? [el('span', { class: 'tm' }, ch.speaker.term || '←'), el('span', { class: 'nc spk' }, el('span', { class: 'n ar' }, ch.speaker.kind === 'prophet' ? 'پیامبر (ص)' : (ch.speaker.raw || 'امام')), el('span', { class: 'id' }, 'گوینده'))] : null)),
        el('p', { class: 'small muted' }, pick ? 'نام برگزیده زرد است؛ جاهای دیگرش و گزینه‌هایش در بخش ۳.' : 'یک نام را بزنید تا جاهای دیگرش و گزینه‌هایش را ببینید. نام‌های «قطعی» را منبع تعیین کرده است (با زدن، کارت راوی باز می‌شود).'),
        h && h.sanad ? el('details', { class: 'small' }, el('summary', { class: 'muted' }, 'متن سند'), el('div', { class: 'ar' }, h.sanad)) : null);
    };

    /* part 3: the chosen name: all its places first, then the choice */
    const part3 = el('div', { class: 'card edcard' });
    const meta = k => [k.db === 'kg' || k.id.startsWith('kg:') ? 'گراف دانش' : 'نور', k.death ? 'ف ' + fa(k.death) : '', k.mazhab, k.grade || k.rank, k.tabaka ? 'طبقه ' + fa(k.tabaka) : ''].filter(Boolean).join(' · ');
    const snippet = o => { const [c2, e2] = splitOcc(o), x = c2.elems, i = e2.i;
      return el('span', { class: 'chain mini' }, i > 1 ? el('span', { class: 'tm' }, '…') : null, x[i - 1] ? [chip(c2, x[i - 1]), el('span', { class: 'tm' }, e2.term || '←')] : null, chip(c2, e2),
        x[i + 1] ? [el('span', { class: 'tm' }, x[i + 1].term || '←'), chip(c2, x[i + 1])] : null, i + 2 < x.length ? el('span', { class: 'tm' }, '…') : null); };
    const occRow = (o, cur0, label) => { const [c2, e2] = splitOcc(o), s2 = stateOf(c2, e2);
      return el('div', { class: 'occ' + (o === cur0 ? ' on' : '') },
        el('button', { class: 'lnk small', onclick: () => setPick(pick.key, o) }, o === cur0 ? '● ' : '', wtitle(c2.wid), byWid[c2.wid].length > 1 ? ` (سند ${fa(byWid[c2.wid].indexOf(c2) + 1)})` : ''),
        snippet(o), el('span', { class: 'small ' + stOf(s2) + 'c' }, ST[stOf(s2)], label && s2.id && s2.id !== 'none' && s2.st !== 'exact' ? ': ' + labelOf(s2.id, e2, cards[e2.card]) : '', s2.here ? ' ◆' : ''));
    };
    /* a name the source fixed: the person's places in all chains and his date */
    const drawPerson = () => {
      const id = pick.key.slice(3), occ = pick.occ, [ch, e] = splitOcc(occ), s = stateOf(ch, e);
      const places = I.chains.flatMap(c2 => c2.elems.filter(e2 => stateOf(c2, e2).id === id).map(e2 => `${c2.aid}|${e2.i}`));
      fill(part3,
        el('div', { class: 'toolbar', style: 'margin-top:0' }, el('h2', { style: 'margin:0' }, '۳. ', el('span', { class: 'ar' }, e.raw || e.name)),
          el('span', { class: 'chip exact' }, ST.exact), el('span', { class: 'small muted', dir: 'ltr' }, id), el('span', { class: 'grow' }),
          el('button', { class: 'small', onclick: () => narratorCard(id) }, 'کارت راوی'), el('button', { class: 'small', onclick: () => { pick = null; keepUrl(); draw(); } }, 'بستن')),
        el('p', { class: 'small muted' }, `${id.startsWith('kg:') ? 'گراف دانش' : 'نور'} خودش این نام را به این راوی پیوند داده است؛ تصمیمی لازم نیست.`),
        el('h3', {}, places.length > 1 ? `این شخص در ${fa(places.length)} جا آمده است` : 'این شخص فقط همین جا آمده است'),
        el('div', { class: 'occs' }, places.map(o => occRow(o, occ, false))),
        dateBox(ch, e, s));
      fetchDeaths([id]).then(more => { if (more) draw(); });
    };
    const drawEd = () => {
      if (!pick) { part3.hidden = true; return; }
      part3.hidden = false;
      if (pick.key.startsWith('id:')) return drawPerson();
      const cd = cards[pick.key], occ = pick.occ, [ch, e] = splitOcc(occ), s = stateOf(ch, e), n = cd.occurrences.length;
      const here = relOnly(cd) || n === 1 ? true : scopeHere || !!s.here;
      const val = here ? overrides[occ] : choices[cd.key], curId = s.id;
      const radio = (id, kids) => el('label', { class: 'cand' + (curId === id ? ' sel' : '') },
        el('input', { type: 'radio', name: 'ed', checked: curId === id, disabled: !c.can, onchange: () => setVal(cd, occ, here, id) }), ...kids);
      const candLine = k => radio(k.id, [el('span', { class: 'ar', style: 'font-size:15px' }, k.label),
        k.tier || k.derived ? el('span', { class: 'chip ' + (k.derived ? 'derived' : k.tier) }, TIER[k.derived ? 'derived' : k.tier]) : null,
        el('span', { class: 'small muted' }, `هم‌خوانی نام ${pct(k.cov || 0)}${k.ctx != null ? ' · هم‌نشین ' + fa(k.ctx) : ''} · ${fa(k.n || 0)} روایت`), el('span', { class: 'small muted' }, meta(k)),
        k.reason ? el('span', { class: 'small muted' }, k.reason) : null, cd.default === k.id && !relOnly(cd) ? el('span', { class: 'small', style: 'color:var(--good)' }, 'انتخاب خودکار') : null,
        el('button', { class: 'small', onclick: ev => { ev.preventDefault(); narratorCard(k.id); } }, 'کارت راوی')]);
      const cands = relOnly(cd) ? [...e.cands] : [...cd.candidates]; e.cands.forEach(k => { if (!cands.some(x => x.id === k.id)) cands.push(k); });
      const go3 = (q3) => { sq = q3; sbusy = true; sres = null; drawEd();
        api('/narrators?q=' + encodeURIComponent(q3)).then(r => { sres = r; }, err => { sres = { error: err.message }; }).finally(() => { sbusy = false; drawEd(); }); };
      const sIn = el('input', { type: 'text', value: sq, placeholder: 'نام راوی', class: 'ar', oninput: ev => { sq = ev.target.value; }, onkeydown: ev => { if (ev.key === 'Enter') go3(sq.trim()); } });
      fill(part3, 
        el('div', { class: 'toolbar', style: 'margin-top:0' }, el('h2', { style: 'margin:0' }, '۳. ', el('span', { class: 'ar' }, relOnly(cd) ? cname({ ...cd, name: e.raw || e.name }) : e.raw || e.name)),
          el('span', { class: 'chip ' + stOf(s) }, ST[stOf(s)] + (s.here ? ' — فقط همین جا' : '')),
          el('span', { class: 'small muted' }, `در ${wtitle(ch.wid)} · بهترین نامزد: ${TIER[cd.tier] || cd.tier}`), el('span', { class: 'grow' }),
          el('button', { class: 'small', onclick: () => { pick = null; keepUrl(); draw(); } }, 'بستن')),
        el('h3', {}, relOnly(cd) ? 'نسبت، نه نام' : n > 1 ? `این نام در ${fa(n)} جا آمده است` : 'این نام فقط همین جا آمده است'),
        relOnly(cd) ? el('p', { class: 'small muted' }, `«${e.raw || e.name}» نام نیست، نسبت است (پدر، جد، برادر… راوی پیشین)، پس در هر سند کس دیگری است و جداگانه تعیین می‌شود. پیشنهادها از سندهای نور آمده‌اند که برای همان راوی پیشین همین نسبت را به نام آورده‌اند (پدر و جد امامان از جدول امامان). «عن أبيه عن جده» یعنی پدرِ همان پدر.`) : null,
        el('div', { class: 'occs' }, cd.occurrences.map(o => occRow(o.join('|'), occ, true))),
        el('h3', {}, 'گزینش شخص'),
        c.can && n > 1 && !relOnly(cd) ? el('div', { class: 'toolbar small' }, 'انتخاب برای: ',
          el('label', {}, el('input', { type: 'radio', name: 'scope', checked: !here, onchange: () => { scopeHere = false; delete overrides[occ]; draw(); } }), ` همه‌ی ${fa(n)} جا`),
          el('label', {}, el('input', { type: 'radio', name: 'scope', checked: here, onchange: () => { scopeHere = true; draw(); } }), ' فقط همین جا (' + wtitle(ch.wid) + ')')) : null,
        el('div', { class: 'small muted' }, 'نامزدهای پیشنهادی:'),
        cands.length ? cands.map(candLine) : el('p', { class: 'small muted' }, 'برای این نام نامزدی در پایگاه‌ها پیدا نشد؛ جستجو کنید.'),
        radio('none', ['هیچ‌کدام (فقط نام)']),
        el('div', { class: 'toolbar' }, el('span', { class: 'small muted' }, 'جستجو در راویان نور و گراف دانش:'), sIn,
          el('button', { class: 'small', disabled: sbusy, onclick: () => go3(sq.trim()) }, sbusy ? 'در حال جستجو…' : 'جستجو')),
        sres && sres.error ? el('p', { class: 'small', style: 'color:var(--bad)' }, 'خطا: ' + sres.error) : null,
        Array.isArray(sres) ? (sres.length ? sres.filter(k => !cands.some(x => x.id === k.id)).map(k => { extraLabel[k.id] = k.label; return candLine(k); })
          .concat(sres.some(k => cands.some(x => x.id === k.id)) ? [el('div', { class: 'small muted' }, 'نتیجه‌هایی که در نامزدهای بالا هست تکرار نشد.')] : [])
          : el('p', { class: 'small muted' }, 'راوی‌ای با این نام پیدا نشد. بخشی از نام را بنویسید (مثلاً «زيد بن علي»).')) : null,
        c.can ? el('div', { class: 'toolbar' },
          el('input', { type: 'text', placeholder: 'یا شناسه: noor:2107 یا kg:3795', dir: 'ltr', onchange: ev => { const v = ev.target.value.trim(); if (/^(noor|kg):\d+$/.test(v)) setTimeout(() => setVal(cd, occ, here, v)); else if (v) alert('قالب شناسه نادرست است'); } }),
          val !== undefined ? el('button', { class: 'small', onclick: () => setVal(cd, occ, here, undefined) }, cd.default && !relOnly(cd) || (e.cands[0] || {}).derived ? 'بازگشت به انتخاب خودکار' : 'پاک کردن انتخاب') : null) : null,
        dateBox(ch, e, s));
      if (s.id || cands.length) fetchDeaths([s.id, ...cands.map(k => k.id)].filter(Boolean)).then(more => { if (more) draw(); });
    };
    /* the death date of the person (or the name) at the chosen place */
    const dateBox = (ch, e, s) => {
      const d = dateOf(ch, e), k = d.key, isPerson = !k.startsWith('occ:'), mine = dates[k];
      const kn = s.id && known[s.id] ? known[s.id] : null;
      const set = (lo, hi) => { if (lo == null && hi == null) delete dates[k]; else { lo = lo ?? hi; hi = hi ?? lo; dates[k] = [Math.min(lo, hi), Math.max(lo, hi)]; } setTimeout(draw); };   // after the input's own blur/change
      const num = v => v === '' || isNaN(+v) ? null : +v;
      const lo = el('input', { type: 'number', placeholder: 'از', style: 'width:90px', value: mine ? mine[0] : '', disabled: !c.can });
      const hi = el('input', { type: 'number', placeholder: 'تا', style: 'width:90px', value: mine ? mine[1] : '', disabled: !c.can });
      lo.onchange = hi.onchange = () => set(num(lo.value), num(hi.value));
      return [el('h3', {}, 'تاریخ وفات (اختیاری)'),
        el('p', { class: 'small' }, d.death && d.src !== 'user' ? `ف ${yr(d.death)} — ${SRCD[d.src] || d.src || 'پایگاه راویان'}. ` : '',
          kn && mine ? `پایگاه: ف ${yr(kn.death)}. ` : '',
          !d.death ? el('span', { class: 'undc' }, 'تاریخی در پایگاه‌ها نیست؛ در شجره از روی جایش در سندها (میان استاد و شاگرد) برآورد می‌شود. ') : null,
          el('span', { class: 'muted' }, isPerson ? `تاریخی که بنویسید برای «${labelOf(s.id, e, cards[e.card])}» در همه‌ی سندها به کار می‌رود و بر تاریخ پایگاه مقدم است.`
            : 'این نام به شخصی پیوند نخورده است؛ تاریخ برای همین نام (گره «فقط نام») در شجره به کار می‌رود.')),
        el('div', { class: 'toolbar' }, 'سال هجری: ', lo, hi, mine && c.can ? el('button', { class: 'small', onclick: () => set(null, null) }, 'پاک کردن') : null,
          el('span', { class: 'small muted' }, 'یک سال یا یک بازه؛ با «تأیید راویان» ذخیره می‌شود.'))];
    };

    const status = el('div', { class: 'small' }), save = el('div', { class: 'toolbar' }), listSum = el('div', { class: 'small muted' });
    const drawStatus = () => {
      const n = counts();
      fill(status, `${fa(Object.keys(cards).length)} نام برای تصمیم: ${fa(n.auto)} خودکار، ${fa(n.user)} انتخاب کاربر، ${fa(n.und)} بی‌تصمیم. `,
        el('span', { class: 'muted' }, `${fa(I.stats.exact)} از ${fa(I.stats.elements)} جای سند را خود منبع به راوی‌ای پیوند داده است (قطعی). `),
        dec.version ? el('span', { class: 'muted' }, `آخرین تصمیم ذخیره‌شده: نسخه‌ی ${fa(dec.version)}، ${dec.user}${dec.note === 'auto' ? ' (پذیرش خودکار)' : ''}.`) : null);
      fill(save, [c.can ? el('button', { class: 'primary', onclick: async () => {
        const nu = counts().und;
        if (nu && !confirm(`${fa(nu)} نام بی‌تصمیم است و بدون هویت (فقط نام) به شجره می‌رود. ادامه؟`)) return;
        await post(`/project/${c.pid}/decide/identities`, { payload: { choices, overrides, dates } }); reload(c.pid);
      } }, 'تأیید راویان و ساخت شجره') : el('span', { class: 'muted small' }, 'فقط سازنده‌ی پروژه یا مدیر می‌تواند تصمیم بگیرد.'),
        dirty() ? el('span', { class: 'small', style: 'color:var(--warn)' }, 'تغییرهای ذخیره‌نشده') : null]);
    };
    const draw = () => { drawStatus(); drawList(); drawChain(); drawEd(); };

    const seg = el('span', { class: 'seg' }, [['hadith', 'حدیث‌ها'], ['names', 'نام‌ها']].map(([v, t]) => {
      const b = el('button', { class: 'small', onclick: () => { listView = v; setPref('icm.idlist', v); drawList(); } }, t); b.dataset.v = v; return b; }));
    const legend = el('div', { class: 'small' }, 'راهنما: ', ['exact', 'auto', 'user', 'und', 'none'].map(s => el('span', { class: 'nc demo ' + s }, ST[s])), el('span', { class: 'muted' }, ' ◆ = فقط همین جا'));
    const oldRun = (I.stage_version || 1) < 3;
    main.replaceChildren(
      card('تعیین هویت راویان',
        el('p', { class: 'muted small' }, 'یک حدیث را در بخش ۱ برگزینید؛ سندش در بخش ۲ می‌آید. یک نام را بزنید؛ در بخش ۳ نخست همه‌ی جاهای دیگر آن نام در سندهای دیگر می‌آید و سپس گزینه‌ها و جستجو برای انتخاب شخص.'),
        el('details', { class: 'help' }, el('summary', {}, 'این گزینه‌ها از کجا می‌آیند؟'),
          el('ul', {},
            el('li', {}, el('b', {}, 'قطعی: '), 'منبع (نور یا گراف دانش) خودش این نام را به یک راوی پیوند داده است؛ تصمیمی لازم نیست.'),
            el('li', {}, el('b', {}, 'نامزدها: '), 'برای نام‌های دیگر، راویانی که نامشان می‌خواند از دو پایگاه آمده‌اند: فهرست راویان نور (شیعه) و رجال گراف دانش (اهل سنت). پس یک شخص ممکن است دو بار بیاید (مثلاً «زيد بن علي» یک بار از نور و یک بار از گراف دانش)؛ اگر پایگاه راویان آن دو را یکی دانسته باشد، در شجره یکی می‌شوند. اگر شخص درست در نامزدها نیست، جستجو کنید.'),
            el('li', {}, el('b', {}, 'نسبت‌ها: '), '«عن أبيه»، «حدثني أبي»، «عن جده»، «عن أخيه»… نام نیستند؛ هر کدام جداگانه در سند خودش تعیین می‌شود: پدر و جد امامان از جدول امامان، دیگران از سندهای نور که همان نسبت را برای همان راوی به نام آورده‌اند. «عن أبيه عن جده» یعنی پدرِ همان پدر.'),
            el('li', {}, el('b', {}, 'رتبه‌بندی: '), '«هم‌خوانی نام» = چه اندازه نام سند با نام راوی می‌خواند؛ «هم‌نشین» = چند همسایه‌ی این نام در همین سند، در سندهای دیگر هم استاد یا شاگرد این راوی‌اند؛ «روایت» = شمار روایت‌های او.'),
            el('li', {}, el('b', {}, 'بالا / متوسط / پایین: '), 'اطمینان به نامزد نخست. «بالا» = نام دست‌کم ۸۸٪ می‌خواند، دست‌کم یک همسایه او را تأیید می‌کند و از نامزد بعدی آشکارا جلوتر است؛ چنین نامزدی خودکار انتخاب می‌شود. «متوسط» و «پایین» خودکار انتخاب نمی‌شوند.'),
            el('li', {}, el('b', {}, 'وضع حدیث: '), '«همه قطعی» = منبع همه‌ی راویان را تعیین کرده؛ «همه شناخته» = هر نام یا قطعی است یا (خودکار یا با انتخاب کاربر) به شخصی پیوند دارد؛ «همه تصمیم‌گرفته» = برخی نام‌ها عمداً «فقط نام» یا نامعلوم‌اند؛ وگرنه شمار نام‌های بی‌تصمیم.'),
            el('li', {}, el('b', {}, 'تاریخ: '), 'زیر هر نام سال وفات او آمده است (از پایگاه راویان، گراف دانش یا تاریخ مؤلف برای گردآورنده). «بی‌تاریخ» در شجره از روی جایش در سندها برآورد می‌شود؛ اگر تاریخ را می‌دانید، در بخش ۳ بنویسید.'),
            el('li', {}, el('b', {}, 'خودکار / انتخاب کاربر / بی‌تصمیم: '), 'نامی که کسی برایش انتخاب نکرده، اگر نامزد «بالا» داشته باشد خودکار همان را می‌گیرد؛ وگرنه بی‌تصمیم است و فقط با نامش به شجره می‌رود. انتخاب شما برای همه‌ی جاهای همان نام است، مگر «فقط همین جا» را بزنید.'))),
        oldRun && !c.readonly ? el('div', { class: 'help', style: 'border-color:var(--warn)' }, 'نامزدهای این اجرا با نسخه‌ی پیشینِ برنامه ساخته شده‌اند: «عن أبيه / عن جده» یک نام شمرده می‌شد (و در همه‌ی سندها امام باقر گرفته می‌شد)، و برخی نام‌ها به راوی نادرست می‌خوردند (مثلاً «علی بن ابراهیم» به محمد بن یحیی). برنامه‌ی تازه هر نسبت را در سند خودش و از روی سندهای نور تعیین می‌کند. ',
          c.can ? el('button', { class: 'small', onclick: async () => { await post(`/project/${c.pid}/rerun`, {}); reload(c.pid); } }, 'اجرای دوباره با برنامه‌ی تازه') : 'سازنده‌ی پروژه می‌تواند آن را دوباره اجرا کند.',
          el('span', { class: 'small muted' }, ' انتخاب‌های ذخیره‌شده می‌مانند؛ شجره دوباره ساخته می‌شود و باید دوباره تأیید شود.')) : null,
        status, legend, save,
        I.flags.map(f => el('div', { class: 'small', style: 'color:var(--warn)' }, `${wtitle(f.ref)}: ${flagText(f)}`))),
      el('div', { class: 'card' }, el('div', { class: 'toolbar', style: 'margin-top:0' }, el('h2', { style: 'margin:0' }, '۱. حدیث‌ها'), seg,
          el('label', { class: 'small' }, el('input', { type: 'checkbox', onchange: ev => { onlyUnd = ev.target.checked; drawList(); } }), ' فقط با نام بی‌تصمیم'),
          el('label', { class: 'small' }, el('input', { type: 'checkbox', onchange: ev => { onlyUndated = ev.target.checked; drawList(); } }), ' فقط با راوی بی‌تاریخ'),
          el('input', { type: 'text', placeholder: 'پالایش با نام راوی یا کتاب', oninput: ev => { q = ev.target.value.trim(); drawList(); } })),
        listSum, listBox),
      part2, part3);
    draw();
    if (cur) (pick ? part3 : part2).scrollIntoView({ block: 'start' });
    if (await fetchDeaths(I.chains.flatMap(ch => ch.elems.map(e => stateOf(ch, e).id)).filter(Boolean))) draw();
  },

  /* ----- 5: phylogeny ----- */
  async phylogeny(main, c) {
    const G = await stage(c.pid, c.runId, 'graph');
    const dec = await api(`/project/${c.pid}/decision/phylogeny`);
    const preset = el('select', {}, ...['', 'quick', 'standard', 'thorough'].map(p => el('option', { value: p, selected: (dec.preset || '') === p }, p ? { quick: 'سریع', standard: 'معمول', thorough: 'دقیق' }[p] : 'خودکار (بر پایه‌ی اندازه)')));
    const computeSel = el('select', {}, ...[['browser', 'در مرورگر من (سریع، موازی)'], ['helper', 'رایانه‌ی کمکی مدیر'], ['server', 'روی سرور (کند، نزدیک یک ساعت)']].map(([v, t]) => el('option', { value: v, selected: (dec.compute || 'browser') === v }, t)));
    const undated = Object.entries(G.nodes).filter(([n, i]) => (!i.date_source || i.date_source === 'interpolated') && !/^anon:/.test(n) && !/^(موضع |اسم مبهم|من ذكره)/.test(i.name || ''));
    main.replaceChildren(...[
      card('شجره‌ی انتقال ثبت‌شده', el('p', { class: 'muted small' }, `${fa(G.stats.narrators)} راوی، ${fa(G.stats.attestations)} سند، ${fa(G.stats.witnesses)} شاهد؛ ${fa(G.stats.dated)} راوی تاریخ دارند، ${fa(G.stats.name_only)} فقط نام‌اند. محور افقی زمان است (راست = قدیم‌تر).`),
        graphSvg(G)),
      G.flags.length ? card('هشدارها', el('ul', { class: 'flags' }, G.flags.slice(0, 60).map(f => el('li', {}, flagText(f))))) : null,
      undated.length ? card('راویان بی‌تاریخ', el('p', { class: 'small' }, `${fa(undated.length)} راوی تاریخ وفات ندارند و تاریخشان از روی جایشان در سندها برآورد شده است. `,
        'تاریخ را در ', el('a', { href: BASE + stepUrl(c.pid, null, 'identities') }, 'مرحله‌ی ۴ (راویان)'), ' وارد کنید: حدیث را برگزینید، نام را بزنید، و در بخش ۳ «تاریخ وفات» را بنویسید.'),
        el('div', { class: 'small muted ar' }, undated.slice(0, 80).map(([, i]) => i.name).join('  ·  ') + (undated.length > 80 ? ' …' : ''))) : null,
      card('تأیید', c.can ? el('div', { class: 'toolbar' }, 'دقت اجرا: ', preset, ' محاسبه: ', computeSel,
        el('button', { class: 'primary', onclick: async () => {
          await post(`/project/${c.pid}/decide/phylogeny`, { payload: { confirmed: P.project.graph_hash, dates: dec.dates || {}, preset: preset.value || null, compute: computeSel.value } }); if (computeSel.value === 'browser') sessionStorage.setItem('icm_auto', c.pid); reload(c.pid);
        } }, 'تأیید شجره و اجرای تحلیل')) : el('span', { class: 'muted' }, 'فقط سازنده‌ی پروژه یا مدیر می‌تواند تأیید کند.'),
        P.project.step === 'done' ? el('p', { class: 'small muted' }, 'تحلیل این شجره انجام شده است.') : null)].filter(Boolean));
  },

  /* ----- 6: analysis ----- */
  async analysis(main, c) {
    if (c.p.status === 'awaiting' && !c.readonly) return awaitingPanel(main, c);
    const R = await stage(c.pid, c.runId, 'results');
    const rows = Object.entries(R.posterior).sort((a, b) => b[1] - a[1]).slice(0, 12);
    main.replaceChildren(
      card('نتیجه', el('p', {}, 'بهترین‌پشتیبانی‌شده: ', el('b', { class: 'ar' }, R.map_source), ' — مجموعه‌ی اطمینان ۹۵٪: ', R.credible_set_95.join('، ')),
        R.bayes_factor_top_vs_next ? el('p', { class: 'small muted' }, `لگاریتم عامل بیز نسبت به نامزد بعدی (${R.bayes_factor_top_vs_next.pair[1]}): ${fa(R.bayes_factor_top_vs_next.log_BF.toFixed(1))}`) : null,
        el('table', {}, el('tr', {}, ...['نامزد منبع', 'احتمال پسین', 'log Z'].map(t => el('th', {}, t))), rows.map(([n, v]) => el('tr', {}, el('td', { class: 'ar' }, n), el('td', {}, el('div', { class: 'bar' }, el('i', { style: `width:${Math.round(v * 100)}%` })), pct(v)), el('td', {}, R.log_Z[n] == null ? '−∞' : fa(R.log_Z[n].toFixed(1)))))),
        el('p', { class: 'small muted' }, 'محاسبه: ' + ({ browser: 'در مرورگر یک کاربر', helper: 'روی رایانه‌ی کمکی', server: 'روی سرور' }[(R.provenance || { kind: 'server' }).kind] || '—') + (R.provenance && R.provenance.workers ? ` (${fa(R.provenance.workers)} پردازه)` : '') + '؛ نتیجه‌ی رایانه‌های کاربران بازتولید مستقل نشده است و اعتبارش همان ورود کاربر است.'),
        el('p', { class: 'small muted' }, `اجرا: ${R.preset} — ${fa(R.n_particles)} ذره، ${fa(R.n_steps)} دما، ${fa(R.seconds)} ثانیه. این نتیجه به شجره‌ی تأییدشده و پارامترهای ثابت مدل وابسته است؛ بخش «روش و محدودیت‌ها» در نمایشگر را بخوانید.`)),
      el('iframe', { class: 'viewer', src: `${API}/project/${c.pid}/runs/${c.runId}/viewer` }));
  },
};

async function awaitingPanel(main, c) {
  const aw = c.p.awaiting || {};
  const bar = el('div', { class: 'bar' }, el('i', { style: 'width:0%' })), msg = el('div', { class: 'small muted' }, '');
  const ui = (t, f) => { msg.textContent = t; bar.firstChild.style.width = Math.round(100 * f) + '%'; };
  let running = false;
  const start = async () => {
    if (running) return; running = true; btn.disabled = true;
    try { await computeInBrowser(c.pid, ui); ui('انجام شد؛ در حال ثبت…', 1); setTimeout(() => { reload(c.pid); }, 2500); }
    catch (e) { running = false; btn.disabled = false; msg.textContent = 'خطا: ' + e.message; }
  };
  const btn = el('button', { class: 'primary', disabled: !c.can, onclick: start }, 'شروع محاسبه در مرورگر من');
  main.replaceChildren(card('محاسبه‌ی مدل', el('p', {}, aw.compute === 'helper' ? 'این پروژه منتظر رایانه‌ی کمکی مدیر است. اگر نمی‌خواهید صبر کنید، می‌توانید همین‌جا در مرورگر خودتان اجرا کنید.' : 'تحلیل در مرورگر شما اجرا می‌شود و نتیجه به سرور فرستاده می‌شود. صفحه را نبندید.'),
    el('p', { class: 'small muted' }, `دقت: ${aw.preset}. نامزدهای منبع جداگانه و موازی محاسبه می‌شوند؛ بار اول حدود ۱۵ مگابایت (مفسر پایتون و numpy) بارگیری می‌شود.`),
    el('div', { class: 'toolbar' }, btn, c.can ? el('button', { onclick: async () => {
      if (!confirm('محاسبه روی سرور نزدیک یک ساعت طول می‌کشد. ادامه؟')) return;
      const d = await api(`/project/${c.pid}/decision/phylogeny`);
      await post(`/project/${c.pid}/decide/phylogeny`, { payload: { confirmed: P.project.graph_hash, dates: d.dates || {}, preset: d.preset || null, compute: 'server' } }); reload(c.pid);
    } }, 'اجرا روی سرور (کند)') : null), bar, msg));
  if (c.can && sessionStorage.getItem('icm_auto') === c.pid) { sessionStorage.removeItem('icm_auto'); start(); }
}

/* ---------- pieces ---------- */
const fill = (node, ...kids) => node.replaceChildren(...kids.flat(2).filter(x => x != null && x !== false));
function card(title, ...kids) { return el('div', { class: 'card' }, el('h2', {}, title), ...kids); }
function hadithBlock(h, colored) {
  return el('div', {}, el('div', { class: 'small muted' }, [h.title, h.author, h.death ? 'ف ' + fa(h.death) : '', h.tradition === 'sunni' ? 'اهل سنت' : 'شیعه', h.wid].filter(Boolean).join(' · ')),
    h.sanad ? el('div', { class: 'ar small matn' }, h.sanad) : null, colored && h.marks ? marked(h.matn, h.marks, 'ar seedtext') : el('div', { class: 'ar' }, h.matn));
}
/* clause colours: one hue per part of the seed, the same everywhere (seed, strips, candidate texts) */
const HUES = [210, 28, 140, 330, 48, 268, 178, 0, 95, 300, 235, 15];
const hue = i => HUES[i % HUES.length];
const swatch = i => el('span', { class: 'sw', style: `--h:${hue(i)}`, title: 'بخش ' + fa(i + 1) }, fa(i + 1));
function marked(text, marks, cls, S) {
  const cp = Array.from(text), out = el('div', { class: cls });
  let at = 0;
  for (const [a, b, k] of marks) {
    if (a < at) continue;
    if (a > at) out.append(cp.slice(at, a).join(''));
    out.append(el('span', { class: 'hl', style: `--h:${hue(k)}`, title: 'بخش ' + fa(k + 1) + (S ? ': ' + S.clauses[k].text : '') }, cp.slice(a, b).join('')));
    at = b;
  }
  if (at < cp.length) out.append(cp.slice(at).join(''));
  return out;
}
function clauseRaw(S, i) {   // the seed's own spelling of part i (falls back to the normalised text)
  const m = (S.seed.marks || []).filter(x => x[2] === i), cp = Array.from(S.seed.matn);
  return m.length ? m.map(([a, b]) => cp.slice(a, b).join('')).join(' … ') : S.clauses[i].text;
}
const STATE_FA = { present: 'کامل', partial: 'بخشی', changed: 'دگرگون', absent: 'نیامده' };
const strip = (parts, S) => el('span', { class: 'strip' }, parts.map(p => el('i', { class: p.state, style: `--h:${hue(p.clause)}`, title: `بخش ${fa(p.clause + 1)}: ${S.clauses[p.clause].text} — ${STATE_FA[p.state]} ${pct(p.cov)}` })));
function partsHelp() {
  return el('details', { class: 'help' }, el('summary', {}, 'بخش‌ها چگونه ساخته شدند؟'),
    el('p', {}, 'متن حدیث مبدأ خودکار و فقط با قاعده‌های نگارشی به بخش‌ها بریده شده است (بی‌آنکه معنا فهمیده شود): در نشانه‌های نگارشی (، ؛ . : ؟)، پیش از «و/ف» که اسمی با «ال» را آغاز می‌کند (آغاز هر بند در فهرست‌هایی مثل «و المکذب… و التارک…»)، بخش‌های بلندتر از ۱۲ واژه در نزدیک‌ترین حرف ربط (و، ف، ثم، الذی، من…) به میانه، و بخش یک‌واژه‌ای به همسایه‌اش چسبانده می‌شود. پیش از بریدن، اعراب حذف و املا یکسان می‌شود (ي→ی، ك→ک، ة→ه، أ/إ/آ→ا).'),
    el('p', {}, 'چرا بخش‌به‌بخش؟ روایت‌های یک حدیث بندها را جابه‌جا، کوتاه یا بلند می‌کنند. هر بخش جداگانه جستجو و جداگانه مقایسه می‌شود، پس روایتی که فقط یک بند را دارد یا ترتیب دیگری دارد هم پیدا و درست سنجیده می‌شود. چون بریدن خودکار است، گاهی دو بند در یک بخش می‌مانند یا یک بند دو تکه می‌شود؛ این فقط دقت جستجو را کمی کم می‌کند.'));
}
/* ---------- alignment helpers ---------- */
const mkey = t => { if (t.length > 3 && 'وف'.includes(t[0])) t = t.slice(1); if (t.length > 3 && t.startsWith('ال')) t = t.slice(2); return t; };   // similarity.match_key
function refTok(g, j, seed) {   // what a cell is compared with: the seed's word, or the column's commonest word in a part the seed lacks
  if (g.kind === 'seed') return g.columns[j][seed];
  const n = {}; Object.values(g.columns[j]).forEach(t => n[t] = (n[t] || 0) + 1);
  return Object.keys(n).sort((a, b) => n[b] - n[a])[0];
}
function inExtent(g, w, j) {
  let lo = -1, hi = -1; g.columns.forEach((col, k) => { if (col[w] != null) { if (lo < 0) lo = k; hi = k; } });
  return j > lo && j < hi;
}
function unitCols(u) {          // stage files written before units carried their columns
  const m = /^c\d+([ws])(\d+)/.exec(u.name);
  return m ? [+m[2], +m[2]] : null;
}
const UKIND = u => u.unit_kind === 'clause' ? 'حضور بخش' : u.kind === 'presence' ? 'حضور واژه' : u.kind === 'numeric' ? 'عدد' : 'واژه‌ی جایگزین';
const ulab = l => ({ absent: 'ندارد', present: 'دارد', '*other*': 'دیگر' }[l] || (/^\d+$/.test(l) ? fa(l) : l));
function uwhere(u, A) {
  if (u.unit_kind === 'clause') return short(A.clauses[u.clause].text, 40);
  if (u.kind === 'presence') return '«' + u.name.split(':').slice(1).join(':') + '»';
  return u.labels.map(ulab).join(' / ');
}
const short = (t, n) => t.length > n ? t.slice(0, n) + '…' : t;
const ubadgeDemo = () => el('span', { class: 'small muted' }, el('span', { class: 'ub' }, '۱'), ' واحد تغییر');
function pref(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } }
function setPref(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }
function candRow(x, S, sel, can, upd) {
  const SIG = { 'rel:noor_group': 'گروه نور', 'rel:noor_cluster': 'خوشه‌ی نور', 'rel:noor_related': 'مرتبط در نور', 'rel:consensus': 'هم‌خوانی v02', 'rel:kg_group': 'گروه گراف دانش', 'q:pair': 'دو واژه‌ی کمیاب', 'q:phrase': 'عبارت کمیاب', user: 'افزوده‌ی کاربر' };
  return el('tr', { class: sel.has(x.wid) ? 'on' : '' },
    el('td', {}, el('input', { type: 'checkbox', checked: sel.has(x.wid), disabled: !can, onchange: ev => { ev.target.checked ? sel.add(x.wid) : sel.delete(x.wid); ev.target.closest('tr').classList.toggle('on', ev.target.checked); upd(); } })),
    el('td', {}, el('span', { class: 'chip ' + x.suggested }, CLS[x.suggested]), x.score.reordered ? el('div', { class: 'small muted' }, 'ترتیب بخش‌ها فرق دارد') : null),
    el('td', {}, el('b', {}, x.title || x.book), el('div', { class: 'small muted' }, [x.author, x.tradition === 'sunni' ? 'سنی' : 'شیعه', x.vol ? 'ج ' + fa(x.vol) : '', x.page ? 'ص ' + fa(x.page) : ''].filter(Boolean).join(' · ')),
      x.copies && x.copies.length ? el('div', { class: 'small muted', title: x.copies.join(' ') }, `+${fa(x.copies.length)} نسخه‌ی یکسان`) : null, el('div', { class: 'small muted', dir: 'ltr' }, x.wid),
      el('div', { class: 'small muted', title: x.signals.join(' ') }, 'یافته با: ', x.signals.map(g => SIG[g] || g).join('، '), x.round > 1 ? ` (دور ${fa(x.round)}، از متن ${x.via})` : '')),
    el('td', {}, el('div', { class: 'small' }, 'از حدیث مبدأ ', pct(x.score.cov_seed)), el('div', { class: 'bar' }, el('i', { style: `width:${100 * x.score.cov_seed}%` })),
      el('div', { class: 'small' }, 'از این متن ', pct(x.score.cov_cand)), el('div', { class: 'bar' }, el('i', { style: `width:${100 * x.score.cov_cand}%` }))),
    el('td', {}, strip(x.parts, S)),
    el('td', { class: 'txt' }, x.marks ? marked(x.matn, x.marks, 'ar matn', S) : el('div', { class: 'ar matn' }, x.matn),
      x.matn_len > x.matn.length ? el('div', { class: 'small muted' }, `… (${fa(x.matn_len)} نویسه، ${fa(x.matn.length)} نخست نمایش داده شد)`) : null));
}
async function narratorCard(id) {
  const n = await api('/narrator/' + encodeURIComponent(id));
  const kv = (k, v) => v != null && v !== '' ? [el('dt', {}, k), el('dd', {}, typeof v === 'object' ? JSON.stringify(v) : String(v))] : [];
  const box = el('div', {}, el('h2', { class: 'ar' }, n.name || id), el('div', { class: 'small muted', dir: 'ltr' }, id));
  if (n.kind === 'name-only') { box.append(el('p', {}, n.note)); return modal(box); }
  box.append(el('dl', { class: 'kv' }, ...(n.kind === 'noor'
    ? [...kv('مذهب', n.mazhab), ...kv('درجه', n.grade), ...kv('کنیه', n.kunya), ...kv('لقب', Array.isArray(n.laqab) ? n.laqab.map(l => l && l.text || l).join('، ') : n.laqab), ...kv('طبقه', n.tabaqa && n.tabaqa.map(t => t.text || t).join('، ')), ...kv('جمع‌بندی', n.eval_summary), ...kv('تعداد روایت', n.n_slots)]
    : [...kv('نام مشهور', n.nickname), ...kv('رتبه', n.rank), ...kv('طبقه', n.tabaka), ...kv('تولد', n.birth), ...kv('وفات', n.death), ...kv('اقامت', n.iqama), ...kv('محل وفات', n.death_place), ...kv('تدلیس', n.has_tadlis ? 'دارد' : ''), ...kv('اختلاط', n.has_ikhtilat ? 'دارد' : ''), ...kv('تعداد روایت', n.narrations)])));
  if (n.facts && n.facts.length) box.append(el('h3', {}, 'داده‌های استخراج‌شده'), el('table', {}, n.facts.map(f => el('tr', {}, el('td', {}, f.kind), el('td', {}, f.value || (f.lo != null ? `${f.lo}–${f.hi}` : '')), el('td', { class: 'small muted' }, f.source), el('td', { class: 'ar small' }, f.evidence || '')))));
  if (n.entries && n.entries.length) box.append(el('h3', {}, 'مدخل‌های رجالی'), n.entries.map(e => el('div', { class: 'card' }, el('b', {}, `${e.book} — ${e.heading}`), el('span', { class: 'chip ' + e.confidence }, e.confidence), el('div', { class: 'ar small matn', style: 'max-height:200px' }, e.body))));
  if (n.aqwal && n.aqwal.length) box.append(el('h3', {}, 'اقوال علمای رجال'), n.aqwal.map(a => el('div', { class: 'small' }, `${a.alem}${a.death ? ' (' + fa(a.death) + ')' : ''}: ${a.qawl}`)));
  const nb = (t, l) => l && l.length ? [el('h3', {}, t), l.map(x => el('div', { class: 'small' }, `${x.name} — ${fa(x.n)}`))] : [];
  box.append(...nb('استادان پرتکرار', n.teachers).flat(), ...nb('شاگردان پرتکرار', n.students).flat());
  modal(box);
}

function graphSvg(G) {
  const nodes = G.narrators, atts = G.attestations;
  const year = n => { const d = nodes[n].death; return d ? (d[0] + d[1]) / 2 : null; };
  const ys = Object.keys(nodes).map(year).filter(v => v != null);
  const lo = Math.min(...ys, 0), hi = Math.max(...ys, 100), W = 2600, L = 70, R = W - 40;
  const X = n => L + (R - L) * (1 - (((year(n) ?? lo) - lo) / (hi - lo || 1)));   // older (small year) on the right
  const lane = {}; let next = 0;
  atts.forEach(a => a.sanad.forEach(n => { if (!(n in lane)) lane[n] = next++; }));
  const H = Math.max(120, next * 16 + 50);
  const tierColor = n => { const t = (G.nodes[n].tiers || []); return t.includes('exact') ? 'var(--good)' : t.includes('chosen') || t.includes('derived') ? 'var(--accent)' : t.includes('default') ? 'var(--warn)' : 'var(--muted)'; };
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('width', W); svg.setAttribute('height', H);
  const wrap = el('div', { class: 'grid-wrap', style: 'max-height:640px' }, svg);
  const mk = (t, a, txt) => { const e = document.createElementNS(ns, t); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); if (txt) e.textContent = txt; return e; };
  const drawn = new Set();
  atts.forEach(a => a.sanad.slice(1).forEach((v, i) => { const u = a.sanad[i], k = u + '>' + v; if (drawn.has(k)) return; drawn.add(k);
    svg.append(mk('line', { x1: X(u), y1: 20 + lane[u] * 16, x2: X(v), y2: 20 + lane[v] * 16, stroke: 'var(--line)', 'stroke-width': 1.2 })); }));
  Object.keys(lane).forEach(n => { const g = mk('g', {}); const c = mk('circle', { style: 'cursor:pointer', cx: X(n), cy: 20 + lane[n] * 16, r: 4.5, fill: tierColor(n), stroke: (G.nodes[n].date_source === 'interpolated' || !G.nodes[n].date_source) ? 'var(--fg)' : 'none', 'stroke-dasharray': '2 2' });
    const t = mk('title', {}, `${nodes[n].name}\n${G.nodes[n].date_source || 'بدون تاریخ'} ${nodes[n].death ? Math.round(nodes[n].death[0]) : ''}`); c.append(t); c.addEventListener('click', () => narratorCard(n)); g.append(c);
    g.append(mk('text', { x: X(n) - 8, y: 24 + lane[n] * 16, 'font-size': 11, 'text-anchor': 'start', fill: 'var(--fg)', 'direction': 'rtl' }, nodes[n].name.slice(0, 26))); svg.append(g); });
  for (let yv = Math.ceil(lo / 100) * 100; yv <= hi; yv += 100) [yv].forEach(yv => { const xx = L + (R - L) * (1 - (yv - lo) / (hi - lo || 1)); svg.append(mk('line', { x1: xx, y1: 0, x2: xx, y2: H - 18, stroke: 'var(--line)', 'stroke-dasharray': '3 4' })); svg.append(mk('text', { x: xx, y: H - 4, 'font-size': 11, fill: 'var(--muted)', 'text-anchor': 'middle' }, fa(yv) + ' ق')); });
  return wrap;
}

render();

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
    else if (k === 'dir' || k === 'title' || k === 'href' || k === 'type' || k === 'value' || k === 'placeholder' || k === 'name' || k === 'src' || k === 'target' || k === 'style' || k === 'colspan') e.setAttribute(k, v);
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
    const A = await stage(c.pid, c.runId, 'align');
    const names = Object.fromEntries(A.witnesses.map((w, i) => [w.wid, `${i}. ${(w.title || '').slice(0, 14)}`]));
    const parts = [card('هم‌ترازی بخش‌به‌بخش', el('p', { class: 'muted small' }, `${fa(A.witnesses.length)} شاهد، ${fa(A.clauses.length)} بخش، ${fa(A.n_units)} واحد تغییر برای مدل (${fa(A.private_variants.length)} واحد فقط در یک شاهد دیده شد و کنار گذاشته شد).`),
      A.private_texts.length ? el('details', {}, el('summary', {}, `${fa(A.private_texts.length)} عبارت اضافه‌ی منفرد`), A.private_texts.map(t => el('div', { class: 'ar' }, `${names[t.wid] || t.wid}: ${t.text}`))) : null)];
    parts.push(card('واحدهای تغییر', el('table', {}, el('tr', {}, ...['نام', 'نوع', 'حالت‌ها'].map(t => el('th', {}, t))),
      A.units.map(u => el('tr', {}, el('td', { class: 'ar' }, u.name), el('td', {}, { presence: 'حضور/غیاب', numeric: 'عدد', lexical: 'واژه' }[u.kind] || u.kind),
        el('td', {}, u.labels.map((l, i) => `${l} (${fa(Object.values(u.states).filter(v => v === i).length)})`).join('  |  ')))))));
    for (const g of A.grids) {
      if (!g.columns.length) continue;
      const ws = g.witnesses;
      parts.push(card(`بخش ${fa(g.clause + 1)}: ${g.text}`, el('div', { class: 'grid-wrap' }, el('table', { class: 'grid' },
        el('tr', {}, ws.map(w => el('th', { title: w }, names[w] || w))),
        g.columns.map(col => { const vals = ws.map(w => col[w]); const cnt = {}; vals.forEach(v => { if (v) cnt[v] = (cnt[v] || 0) + 1; }); const top = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a])[0];
          return el('tr', {}, vals.map(v => el('td', { class: v ? (v === top ? 'ar' : 'ar diff') : 'gap' }, v || '—'))); })))));
    }
    main.replaceChildren(...parts);
  },

  /* ----- 4: identities ----- */
  async identities(main, c) {
    const I = await stage(c.pid, c.runId, 'identify');
    const dec = await api(`/project/${c.pid}/decision/identities`);
    const choices = Object.assign({}, dec.choices || {});
    const cards = Object.values(I.cards);
    const chainOf = {}; I.chains.forEach(ch => chainOf[ch.aid] = ch);
    const value = cd => (cd.key in choices ? choices[cd.key] : (cd.default || undefined));
    const status = el('span', { class: 'muted' });
    const list = el('div');
    let onlyUnd = false, q = '';
    const undecided = () => cards.filter(cd => value(cd) === undefined).length;
    const draw = () => {
      status.textContent = `${fa(cards.length)} نام، ${fa(undecided())} بی‌تصمیم؛ ${fa(I.stats.exact)} از ${fa(I.stats.elements)} راوی را خود منبع قطعی کرده است.`;
      list.replaceChildren(...cards.filter(cd => (!onlyUnd || value(cd) === undefined) && (!q || cd.name.includes(q)))
        .sort((a, b) => (value(a) === undefined ? 0 : 1) - (value(b) === undefined ? 0 : 1) || b.occurrences.length - a.occurrences.length)
        .map(cd => cardBox(cd, value(cd), c.can, v => { choices[cd.key] = v; draw(); }, chainOf)));
    };
    main.replaceChildren(
      card('تعیین هویت راویان', el('p', { class: 'muted small' }, 'برای هر نام، شخصِ درست را برگزینید. نامزدها بر پایه‌ی هم‌خوانی نام، هم‌نشینی در سندهای دیگر و پرکاربردی رتبه‌بندی شده‌اند. گزینه‌ی «هیچ‌کدام» نام را بدون هویت نگه می‌دارد.'),
        status, el('div', { class: 'toolbar' },
          el('label', {}, el('input', { type: 'checkbox', onchange: ev => { onlyUnd = ev.target.checked; draw(); } }), ' فقط بی‌تصمیم‌ها'),
          el('input', { type: 'text', placeholder: 'پالایش نام', oninput: ev => { q = ev.target.value; draw(); } }),
          c.can ? el('button', { onclick: () => { cards.forEach(cd => { if (cd.default && !(cd.key in choices)) choices[cd.key] = cd.default; }); draw(); } }, 'پذیرش همه‌ی «بالا»') : null,
          c.can ? el('button', { class: 'primary', onclick: async () => {
            const und = undecided();
            if (und && !confirm(`${und} نام بی‌تصمیم است و بدون هویت (فقط نام) به شجره می‌رود. ادامه؟`)) return;
            cards.forEach(cd => { if (!(cd.key in choices)) choices[cd.key] = cd.default || 'none'; });
            await post(`/project/${c.pid}/decide/identities`, { payload: { choices } }); reload(c.pid);
          } }, 'تأیید راویان و ساخت شجره') : null),
        I.flags.map(f => el('div', { class: 'small', style: 'color:var(--warn)' }, flagText(f)))),
      list);
    draw();
  },

  /* ----- 5: phylogeny ----- */
  async phylogeny(main, c) {
    const G = await stage(c.pid, c.runId, 'graph');
    const dec = await api(`/project/${c.pid}/decision/phylogeny`);
    const dates = Object.assign({}, dec.dates || {});
    const preset = el('select', {}, ...['', 'quick', 'standard', 'thorough'].map(p => el('option', { value: p, selected: (dec.preset || '') === p }, p ? { quick: 'سریع', standard: 'معمول', thorough: 'دقیق' }[p] : 'خودکار (بر پایه‌ی اندازه)')));
    const computeSel = el('select', {}, ...[['browser', 'در مرورگر من (سریع، موازی)'], ['helper', 'رایانه‌ی کمکی مدیر'], ['server', 'روی سرور (کند، نزدیک یک ساعت)']].map(([v, t]) => el('option', { value: v, selected: (dec.compute || 'browser') === v }, t)));
    const undated = Object.entries(G.nodes).filter(([n, i]) => !i.date_source || i.date_source === 'interpolated').slice(0, 60);
    main.replaceChildren(...[
      card('شجره‌ی انتقال ثبت‌شده', el('p', { class: 'muted small' }, `${fa(G.stats.narrators)} راوی، ${fa(G.stats.attestations)} سند، ${fa(G.stats.witnesses)} شاهد؛ ${fa(G.stats.dated)} راوی تاریخ دارند، ${fa(G.stats.name_only)} فقط نام‌اند. محور افقی زمان است (راست = قدیم‌تر).`),
        graphSvg(G)),
      G.flags.length ? card('هشدارها', el('ul', { class: 'flags' }, G.flags.slice(0, 60).map(f => el('li', {}, flagText(f))))) : null,
      c.can && undated.length ? card('تاریخ راویان بی‌تاریخ (اختیاری)', el('p', { class: 'muted small' }, 'اگر تاریخ وفات را می‌دانید، بازه‌ی سال هجری را بنویسید؛ در غیر این صورت برآورد از زنجیره‌ها می‌آید.'),
        undated.map(([n, i]) => el('div', { class: 'toolbar' }, el('span', { style: 'min-width:260px' }, i.name), el('input', { type: 'number', placeholder: 'از', style: 'width:90px', value: (dates[n] || [])[0] ?? '', oninput: ev => setDate(dates, n, 0, ev.target.value) }),
          el('input', { type: 'number', placeholder: 'تا', style: 'width:90px', value: (dates[n] || [])[1] ?? '', oninput: ev => setDate(dates, n, 1, ev.target.value) })))) : null,
      card('تأیید', c.can ? el('div', { class: 'toolbar' }, 'دقت اجرا: ', preset, ' محاسبه: ', computeSel,
        el('button', { class: 'primary', onclick: async () => {
          await post(`/project/${c.pid}/decide/phylogeny`, { payload: { confirmed: P.project.graph_hash, dates: cleanDates(dates), preset: preset.value || null, compute: computeSel.value } }); if (computeSel.value === 'browser') sessionStorage.setItem('icm_auto', c.pid); reload(c.pid);
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
function cardBox(cd, val, can, set, chainOf) {
  const und = val === undefined;
  const ctx = a => { const [aid, i] = a; const ch = chainOf[aid]; if (!ch) return ''; const e = ch.elems; return `${e[i + 1] ? e[i + 1].name + ' ← ' : ''}«${e[i].name}»${e[i - 1] ? ' ← ' + e[i - 1].name : ''}`; };
  return el('div', { class: 'cardbox' + (und ? ' und' : '') },
    el('div', { class: 'toolbar' }, el('b', { class: 'ar' }, cd.name), el('span', { class: 'chip ' + cd.tier }, TIER[cd.tier] || cd.tier), el('span', { class: 'small muted' }, `${fa(cd.occurrences.length)} بار — ${cd.tradition === 'sunni' ? 'اهل سنت' : 'شیعه'}`),
      cd.kind === 'collector' ? el('span', { class: 'chip partial' }, 'گردآورنده') : null, cd.kind === 'relative' ? el('span', { class: 'chip partial' }, 'نسبت') : null),
    el('details', {}, el('summary', { class: 'small muted' }, 'جای‌های آمدن'), cd.occurrences.slice(0, 6).map(o => el('div', { class: 'small' }, ctx(o)))),
    cd.candidates.map(k => el('label', { class: 'cand' + (val === k.id ? ' sel' : '') },
      el('input', { type: 'radio', name: cd.key, checked: val === k.id, disabled: !can, onchange: () => set(k.id) }),
      el('span', { class: 'ar', style: 'font-size:15px' }, k.label), el('span', { class: 'chip ' + (k.tier || 'none') }, TIER[k.derived ? 'derived' : k.tier] || ''),
      el('span', { class: 'small muted' }, `هم‌خوانی ${pct(k.cov || 0)} · هم‌نشین ${fa(k.ctx || 0)} · ${fa(k.n || 0)} روایت`), el('span', { class: 'small muted', dir: 'ltr' }, k.id),
      k.reason ? el('span', { class: 'small muted' }, k.reason) : null,
      el('button', { class: 'small', onclick: ev => { ev.preventDefault(); narratorCard(k.id); } }, 'کارت راوی'))),
    el('label', { class: 'cand' + (val === 'none' ? ' sel' : '') }, el('input', { type: 'radio', name: cd.key, checked: val === 'none', disabled: !can, onchange: () => set('none') }), 'هیچ‌کدام (فقط نام)'),
    can ? el('div', { class: 'toolbar' }, el('input', { type: 'text', placeholder: 'شناسه‌ی دیگر، مثلاً noor:2107 یا kg:3795', dir: 'ltr', onchange: ev => { const v = ev.target.value.trim(); if (/^(noor|kg):\d+$/.test(v)) set(v); else if (v) alert('قالب شناسه نادرست است'); } })) : null);
}
async function narratorCard(id) {
  const n = await api('/narrator/' + encodeURIComponent(id));
  const kv = (k, v) => v != null && v !== '' ? [el('dt', {}, k), el('dd', {}, typeof v === 'object' ? JSON.stringify(v) : String(v))] : [];
  const box = el('div', {}, el('h2', { class: 'ar' }, n.name || id), el('div', { class: 'small muted', dir: 'ltr' }, id));
  if (n.kind === 'name-only') { box.append(el('p', {}, n.note)); return modal(box); }
  box.append(el('dl', { class: 'kv' }, ...(n.kind === 'noor'
    ? [...kv('مذهب', n.mazhab), ...kv('درجه', n.grade), ...kv('کنیه', n.kunya), ...kv('لقب', n.laqab), ...kv('طبقه', n.tabaqa && n.tabaqa.map(t => t.text || t).join('، ')), ...kv('جمع‌بندی', n.eval_summary), ...kv('تعداد روایت', n.n_slots)]
    : [...kv('نام مشهور', n.nickname), ...kv('رتبه', n.rank), ...kv('طبقه', n.tabaka), ...kv('تولد', n.birth), ...kv('وفات', n.death), ...kv('اقامت', n.iqama), ...kv('محل وفات', n.death_place), ...kv('تدلیس', n.has_tadlis ? 'دارد' : ''), ...kv('اختلاط', n.has_ikhtilat ? 'دارد' : ''), ...kv('تعداد روایت', n.narrations)])));
  if (n.facts && n.facts.length) box.append(el('h3', {}, 'داده‌های استخراج‌شده'), el('table', {}, n.facts.map(f => el('tr', {}, el('td', {}, f.kind), el('td', {}, f.value || (f.lo != null ? `${f.lo}–${f.hi}` : '')), el('td', { class: 'small muted' }, f.source), el('td', { class: 'ar small' }, f.evidence || '')))));
  if (n.entries && n.entries.length) box.append(el('h3', {}, 'مدخل‌های رجالی'), n.entries.map(e => el('div', { class: 'card' }, el('b', {}, `${e.book} — ${e.heading}`), el('span', { class: 'chip ' + e.confidence }, e.confidence), el('div', { class: 'ar small matn', style: 'max-height:200px' }, e.body))));
  if (n.aqwal && n.aqwal.length) box.append(el('h3', {}, 'اقوال علمای رجال'), n.aqwal.map(a => el('div', { class: 'small' }, `${a.alem}${a.death ? ' (' + fa(a.death) + ')' : ''}: ${a.qawl}`)));
  const nb = (t, l) => l && l.length ? [el('h3', {}, t), l.map(x => el('div', { class: 'small' }, `${x.name} — ${fa(x.n)}`))] : [];
  box.append(...nb('استادان پرتکرار', n.teachers), ...nb('شاگردان پرتکرار', n.students));
  modal(box);
}
function setDate(d, n, i, v) { d[n] = d[n] || [null, null]; d[n][i] = v === '' ? null : +v; }
function cleanDates(d) { const o = {}; for (const [n, [a, b]] of Object.entries(d)) { if (a != null || b != null) o[n] = [a ?? b, b ?? a]; } return o; }

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

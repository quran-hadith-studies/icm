'use strict';
/* an exported page (icm/export.py) carries its data in window.ICM_STATIC: nothing is fetched, nothing can change */
const STATIC = window.ICM_STATIC || null;
const BASE = document.body.dataset.base;
const API = BASE + '/api';
const SITE = STATIC ? STATIC.site : '';
const VER = (() => { try { return new URL(document.currentScript.src).searchParams.get('v') || ''; } catch (_) { return ''; } })();
/* the address: the browser's, or a virtual one on an exported page (a file:// page cannot change its URL) */
const VLOC = STATIC ? { path: `/p/${STATIC.route.pid}/r/${STATIC.route.run}/${STATIC.route.step}`, search: '' } : null;
const here = () => (VLOC ? VLOC.path : location.pathname);
const query = () => (VLOC ? VLOC.search : location.search);
function setUrl(url, push) {
  if (VLOC) { const u = new URL(url, 'http://x'); VLOC.path = u.pathname; VLOC.search = u.search; return; }
  history[push ? 'pushState' : 'replaceState'](history.state || {}, '', url);
}
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
  const workers = Array.from({ length: nW }, () => new Worker(BASE + '/static/model_worker.js' + (VER ? '?v=' + VER : '')));
  try {
    ui(`بارگذاری مفسر پایتون در ${fa(nW)} پردازه…`, 0.02);
    await Promise.all(workers.map(w => rpc(w, { cmd: 'boot', base: BASE, v: VER })));
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
    else if (/^(aria-|data-)/.test(k) || k === 'role' || k === 'tabindex' || k === 'dir' || k === 'title' || k === 'href' || k === 'type' || k === 'value' || k === 'placeholder' || k === 'name' || k === 'src' || k === 'target' || k === 'style' || k === 'colspan' || k === 'rowspan') e.setAttribute(k, v);
    else if (v === true) e.setAttribute(k, '');
    else e[k] = v;
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
/* the server's messages are English; the page shows Persian */
const ERR_FA = [[/^no such project/i, 'چنین پروژه‌ای نیست.'], [/^login required/i, 'برای این کار وارد شوید.'], [/^missing X-ICM/i, 'درخواست نامعتبر است.'],
  [/^only the owner or an admin/i, 'فقط سازنده‌ی پروژه یا مدیر می‌تواند آن را تغییر دهد.'], [/not waiting for a computation|no longer waits/i, 'این پروژه اکنون منتظر محاسبه نیست.'],
  [/^stage files are missing/i, 'فایل‌های این مرحله پیدا نشد.'], [/^no analysis in this run/i, 'در این اجرا تحلیلی نیست.'], [/^no such run/i, 'چنین اجرایی نیست.'],
  [/^stage not available/i, 'این مرحله در این اجرا نیست.'], [/^no such text/i, 'چنین متنی در این اجرا نیست.'], [/^unknown seed hadith/i, 'حدیث مبدأ شناخته نشد.'],
  [/^you have (\d+) unfinished/i, (m) => `شما ${fa(m[1])} پروژه‌ی ناتمام دارید؛ نخست یکی را به پایان ببرید.`], [/^unknown narrator database version/i, 'پایگاه راویان ناشناخته است.'],
  [/^the graph changed/i, 'شجره تغییر کرده است؛ شجره‌ی روی صفحه را دوباره تأیید کنید.'], [/^the search has not run yet/i, 'جستجو هنوز اجرا نشده است.'],
  [/^unknown preset|^unknown compute mode/i, 'گزینه‌ی نامعتبر.'], [/^not found$/i, 'پیدا نشد.'], [/^bad id/i, 'شناسه‌ی نامعتبر.'],
  [/^the result is not consistent/i, 'نتیجه با شجره‌ی این پروژه نمی‌خواند.'], [/^chain must map/i, 'ویرایش سند نامعتبر است (مرزها باید درون متن و به ترتیب باشند).']];
function faErr(m) {
  m = String(m || '');
  if (/[\u0600-\u06FF]/.test(m)) return m;
  for (const [re, t] of ERR_FA) { const x = re.exec(m); if (x) return typeof t === 'function' ? t(x) : t; }
  return 'کارگزار پذیرفت نکرد: ' + m;
}
async function api(path, opts) {
  if (STATIC) return staticApi(path);
  const r = await fetch(API + path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} const err = new Error(faErr(typeof m === 'string' ? m : JSON.stringify(m))); err.status = r.status; throw err; }
  return r.json();
}
/* the API of an exported page: the answers are in the page */
async function staticApi(path) {
  const [p, q] = path.split('?'), qs = new URLSearchParams(q || '');
  const nf = () => { const e = new Error('در این فایل نیامده است'); e.status = 404; throw e; };
  let m;
  if (p === '/me') return { user: null, admin: false, presets: [] };
  if (p === '/index') return { projects: [] };
  if (/^\/project\/[^/]+$/.test(p)) return STATIC.project;
  if ((m = /^\/project\/[^/]+\/runs\/[^/]+\/([a-z]+)$/.exec(p))) return STATIC.stages[m[1]] || nf();
  if ((m = /^\/project\/[^/]+\/decision\/([a-z]+)$/.exec(p))) return STATIC.decisions[m[1]] || {};
  if (p === '/narrators/deaths') { const o = {}; (qs.get('ids') || '').split(',').forEach(i => { if (STATIC.deaths[i]) o[i] = STATIC.deaths[i]; }); return o; }
  if ((m = /^\/narrator\/(.+)$/.exec(p))) { const id = decodeURIComponent(m[1]); return STATIC.narrators[id] || { id, kind: 'name-only', note: 'کارت این راوی در این فایل نیامده است.' }; }
  if ((m = /^\/hadith\/(.+)$/.exec(p))) return STATIC.hadith[decodeURIComponent(m[1])] || nf();
  if (p === '/narrators') return [];
  return nf();
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ICM': '1' }, body: JSON.stringify(body) });
const cache = new Map();
async function stage(pid, run, name) {
  const k = `${pid}|${run}|${name}`;
  if (!cache.has(k)) cache.set(k, api(`/project/${pid}/runs/${run}/${name}`));
  try { return await cache.get(k); } catch (e) { cache.delete(k); throw e; }
}
let modalGuard = null;      // an editor with unsaved work sets it: () => true when the window may close, else it asks the user what to do
function modal(content) {
  const m = document.getElementById('modal'); const b = document.getElementById('modal-body');
  modalGuard = null; b.replaceChildren(content); m.hidden = false;
}
function tryCloseModal() { if (!modalGuard || modalGuard()) { modalGuard = null; document.getElementById('modal').hidden = true; } }
window.ICMCloseModal = tryCloseModal;     // base.js calls it on Escape
document.getElementById('modal-x').onclick = tryCloseModal;
document.getElementById('modal').addEventListener('click', e => { if (e.target.id === 'modal') tryCloseModal(); });
const STATUS = { draft: 'منتظر تصمیم شما', running: 'در حال اجرا', finalized: 'نهایی', failed: 'خطا', awaiting: 'منتظر محاسبه' };
const CLS = { witness: 'همان متن', variant: 'نسخه‌ی دیگر', extended: 'در متن بلندتر', abridged: 'خلاصه', partial: 'بخشی', different: 'متفاوت' };
const TIER = { exact: 'قطعی', high: 'بالا', medium: 'متوسط', low: 'پایین', derived: 'استنباطی', none: 'بی‌نامزد' };
const STEPS = [['seed', '۱ حدیث مبدأ'], ['selection', '۲ جستجو و انتخاب'], ['alignment', '۳ هم‌ترازی'], ['identities', '۴ راویان'], ['phylogeny', '۵ شجره'], ['analysis', '۶ تحلیل']];
const FLAG_FA = {
  pool_capped: 'تعداد نامزدها بسیار زیاد بود؛ فقط آن‌هایی که نشانه‌ی بیشتری داشتند سنجیده شدند.',
  seed_short: 'متن حدیث مبدأ کوتاه است و جستجو کم‌اعتماد است.', family_large: 'خانواده بسیار بزرگ است.',
  heads_capped: 'تعداد سرزنجیره‌ها از حد گذشت؛ فقط پرشاهدترها نامزد منبع شدند.', units_capped: 'فقط آگاهی‌بخش‌ترین واحدهای تغییر نگه داشته شد.',
  chronology: 'ناسازگاری زمانی: استاد پس از شاگرد درگذشته است (هویت یکی از آن‌ها را بررسی کنید).', repeated_node: 'یک راوی دو بار در یک سند آمده بود.',
  chain_too_short: 'سندی با کمتر از دو راوی.', witness_without_chain: 'شاهدی بدون سند خوانا؛ فقط در گردآورنده‌اش لنگر شد (پیشنهاد کنار گذاشتنش در مرحله‌ی ۴ آمده است).',
  unknown_witness: 'شاهد انتخاب‌شده در نتایج جستجو نیست.', part_unused: 'یکی از اصلاح‌های فرازبندی به بخش یا شاهدی برمی‌گردد که در این هم‌ترازی نیست؛ به کار نرفت.', no_chain: 'برای این شاهد سندی خوانده نشد.',
  unit_unused: 'یکی از اصلاح‌های واحدها به واحد، واژه یا شاهدی برمی‌گردد که در این هم‌ترازی نیست؛ به کار نرفت.',
  unit_constant: 'واحدی که نگه داشته یا ساخته‌اید در همه‌ی شاهدها یک حالت دارد؛ به مدل نمی‌رود.' };
const flagText = f => FLAG_FA[f.kind] ? (f.kind === 'chronology' ? FLAG_FA[f.kind] + ' — ' + f.message : FLAG_FA[f.kind]) : f.message;
let ME = { user: null, admin: false }, timer = null;
const SITE_TITLE = 'تحلیل منبع حدیث';
const setTitle = (...parts) => { document.title = [...parts.filter(Boolean), SITE_TITLE].join(' — '); };
const PSTEP = { search: 'جستجو', selection: 'انتخاب شاهدها', identities: 'راویان', phylogeny: 'شجره', analysis: 'تحلیل', done: 'پایان' };

function route() {
  const m = here().slice(BASE.length).split('/').filter(Boolean);
  if (m[0] === 'new' && !STATIC) return { new: true };
  if (m[0] !== 'p' || !m[1]) return STATIC ? { pid: STATIC.route.pid, run: STATIC.route.run, step: STATIC.route.step } : {};
  const r = m[2] === 'r' ? { pid: m[1], run: m[3] || null, step: m[4] || null } : { pid: m[1], run: null, step: m[2] || null };
  if (STATIC) r.run = STATIC.route.run;              // an exported page shows the one run it was made from
  return r;
}
const stepUrl = (pid, run, step) => `/p/${pid}${run ? '/r/' + run : ''}${step ? '/' + step : ''}`;
function go(path) { setUrl(BASE + path, true); render(); }
/* after a decision: back to the project's own address, which opens the step that now needs the user */
function reload(pid) { cache.clear(); setUrl(BASE + stepUrl(pid), true); render(); }
window.addEventListener('popstate', render);

/* ---------- top level ---------- */
async function render() {
  clearTimeout(timer);
  setTitle();
  ME = await api('/me').catch(() => ME);
  const who = document.getElementById('who');
  who.replaceChildren(STATIC ? el('span', { class: 'small' }, `نسخه‌ی ذخیره‌شده (${fa(STATIC.generated)})، فقط خواندنی${STATIC.narrator_version ? ' · پایگاه راویان: ' + (STATIC.narrator_version.label_fa || (STATIC.narrator_version.name === 'raw' ? 'خام' : STATIC.narrator_version.name)) : ''}`)
    : ME.user ? `${ME.user}${ME.admin ? ' (مدیر)' : ''}` : el('a', { href: '/account/login?next=' + encodeURIComponent(location.pathname) }, 'ورود'));
  const r = route();
  document.querySelectorAll('.navs a.nav').forEach(a => a.classList.toggle('on', !r.pid && a.getAttribute('href') === (r.new ? BASE + '/new' : BASE)));
  try { r.pid ? await project(r.pid, r.run) : r.new ? await newProject() : await home(); }
  catch (e) { setTitle(e.status === 404 ? 'پیدا نشد' : 'خطا'); $app.replaceChildren(el('div', { class: 'card err', role: 'alert' }, 'خطا: ' + e.message, ' ', el('a', { href: BASE || '/' , onclick: ev => { ev.preventDefault(); go('/'); } }, 'بازگشت به فهرست پروژه‌ها'))); }
}

/* ---------- home: the projects ---------- */
async function home() {
  const pre = new URLSearchParams(query()).get('seed');
  if (pre) { setUrl(BASE + '/new?seed=' + encodeURIComponent(pre), false); return newProject(); }     // the old link «/icm?seed=…»
  setTitle('پروژه‌ها');
  const idx = await api('/index');
  $app.replaceChildren(
    el('div', { class: 'card' }, el('h2', {}, 'پروژه‌ها'), help('home'),
      el('div', { class: 'toolbar' }, el('button', { class: 'primary', disabled: !ME.user, title: ME.user ? '' : 'برای ساخت پروژه وارد شوید', onclick: () => go('/new') }, 'پروژه‌ی تازه')),
      idx.projects.length ? el('div', { class: 'tscroll' }, el('table', {}, el('thead', {}, el('tr', {}, ...['عنوان', 'حدیث مبدأ', 'سازنده', 'وضعیت', 'مرحله', 'به‌روزرسانی'].map(t => el('th', {}, t)))), el('tbody', {},
        idx.projects.map(p => el('tr', {}, el('td', {}, el('a', { href: `${BASE}/p/${p.pid}`, onclick: ev => { ev.preventDefault(); go('/p/' + p.pid); } }, p.title)),
          el('td', { dir: 'ltr', class: 'small' }, p.seed), el('td', {}, p.owner), el('td', {}, el('span', { class: 'chip ' + p.status }, STATUS[p.status] || p.status)),
          el('td', {}, PSTEP[p.step] || (STEPS.find(s => s[0] === p.step) || [0, p.step])[1]), el('td', { class: 'small muted', dir: 'ltr' }, p.updated.replace('T', ' ').slice(0, 16)))))))
        : el('p', { class: 'empty-state' }, 'هنوز پروژه‌ای نیست؛ با «پروژه‌ی تازه» یک حدیث را جستجو کنید.')));
}

/* ---------- new project: search a hadith to start from ---------- */
async function newProject() {
  setTitle('پروژه‌ی تازه');
  const q = el('input', { type: 'text', placeholder: 'شناسه‌ی حدیث، نشانی /h/i/… یا چند کلمه از متن', id: 'q', 'aria-label': 'جستجوی حدیث مبدأ' });
  const out = el('div');
  const go_ = async () => {
    out.replaceChildren(el('p', { class: 'muted' }, 'در حال جستجو…'));
    const rs = await api('/search?limit=60&q=' + encodeURIComponent(q.value));
    out.replaceChildren(rs.length ? el('div', { class: 'tscroll' }, el('table', {}, el('tbody', {}, rs.map(h => hadithRow(h))))) : el('p', { class: 'muted' }, 'چیزی پیدا نشد.'));
  };
  q.addEventListener('keydown', e => { if (e.key === 'Enter') go_(); });
  const pre = new URLSearchParams(query()).get('seed');
  if (pre) { q.value = pre; setTimeout(go_, 0); }
  $app.replaceChildren(
    el('p', { class: 'small' }, el('a', { href: BASE || '/', onclick: ev => { ev.preventDefault(); go('/'); } }, '← فهرست پروژه‌ها')),
    el('div', { class: 'card' }, el('h2', {}, 'پروژه‌ی تازه: شروع از یک حدیث'), help('new'),
      el('p', { class: 'muted' }, 'یک حدیث را انتخاب کنید؛ سامانه خانواده‌ی آن را می‌یابد، شما شاهدها و راویان را تأیید می‌کنید و در پایان قدیمی‌ترین منبع قابل‌بازیابی برآورد می‌شود.'),
      el('div', { class: 'toolbar' }, q, el('button', { class: 'primary', onclick: go_ }, 'جستجو')), out));
  if (!pre) q.focus();
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
  NV = null;
  P = await api('/project/' + pid);
  const p = P.project, readonly = !!run, runId = run || p.current_run || 'current';
  const pname = p.label || p.title;
  const can = ME.user && (ME.user === p.owner || ME.admin) && !readonly;
  const cur = run ? 'analysis' : ({ search: 'selection', selection: 'selection', identities: 'identities', phylogeny: 'phylogeny', analysis: 'analysis', done: 'analysis' }[p.step] || 'selection');
  const after = ['identities', 'phylogeny', 'analysis', 'done'];
  const reached = s => run ? true : ({ seed: true, selection: true, alignment: after.includes(p.step), identities: after.includes(p.step),
    phylogeny: after.slice(1).includes(p.step), analysis: after.slice(2).includes(p.step) })[s];
  const want = route().step;
  const SHOWN = STATIC ? STEPS.filter(([k]) => STATIC.steps.includes(k)) : STEPS;
  let step = SHOWN.some(x => x[0] === want) && reached(want) ? want : cur;
  if (!SHOWN.some(x => x[0] === step)) step = SHOWN[0][0];
  const tabs = SHOWN.map(([k, t]) => reached(k)
    ? el('a', { class: 'step' + (step === k ? ' cur' : '') + (k === cur && !run ? ' now' : ''), href: BASE + stepUrl(pid, run, k), title: k === cur && !run ? 'مرحله‌ای که منتظر شماست' : '',
        onclick: ev => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return; ev.preventDefault(); if (step === k) return;
          step = k; setUrl(BASE + stepUrl(pid, run, k), true); tabs.forEach(e => e.classList.toggle('cur', e.dataset.k === k)); draw(); } }, t)
    : el('div', { class: 'step off', title: 'هنوز به این مرحله نرسیده‌اید' }, t));
  tabs.forEach((e, i) => e.dataset.k = SHOWN[i][0]);
  const runSel = STATIC ? el('span', { class: 'small', dir: 'ltr' }, run) : el('select', { 'aria-label': 'نسخه‌ی اجرا', onchange: ev => go(stepUrl(pid, ev.target.value === 'current' ? null : ev.target.value, step)) },
    el('option', { value: 'current' }, 'نسخه‌ی کنونی'), P.runs.slice().reverse().map(r => el('option', { value: r.run, selected: r.run === run }, `${r.run} — ${r.created.replace('T', ' ').slice(0, 16)}`)));
  /* one self-contained HTML file of this step or of the whole project (export.py) */
  /* one self-contained HTML file of this step or of the whole project (export.py) */
  const dlStep = el('a', { class: 'btn small', download: '', title: 'یک فایل HTML یک‌تکه، بی‌نیاز از اینترنت' }, 'دانلود HTML این مرحله');
  const dlAll = el('a', { class: 'btn small', download: '', title: 'یک فایل HTML یک‌تکه، بی‌نیاز از اینترنت' }, 'دانلود کل پروژه');
  const setDl = () => { dlStep.href = `${API}/project/${pid}/runs/${runId}/export/${step}.html`; dlAll.href = `${API}/project/${pid}/runs/${runId}/export/all.html`; };
  const exportSel = STATIC ? null : el('span', { class: 'toolbar', style: 'margin:0' }, dlStep, dlAll);
  setDl();
  const status = el('div'), main = el('div');
  $app.replaceChildren(
    el('div', { class: 'card' }, el('div', { class: 'toolbar', style: 'margin:0' },
      el('h1', { style: 'margin:0;font-size:18px' }, pname), el('span', { class: 'chip ' + p.status }, STATUS[p.status] || p.status),
      el('span', { class: 'muted small' }, 'سازنده: ' + p.owner), el('span', { class: 'grow' }), 'نسخه: ', runSel,
      readonly ? el('span', { class: 'chip partial' }, 'فقط‌خواندنی') : null, exportSel, exportSel ? info(() => help('export', true)) : null), status),
    el('nav', { class: 'steps' }, tabs), main);
  statusBox(status, p, pid);
  const draw = async () => {
    setTitle(pname, (STEPS.find(x => x[0] === step) || [0, ''])[1].replace(/^[۰-۹]+\s*/, ''));
    if (typeof setDl === 'function') setDl();
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
      ...Object.entries(P.decisions).map(([s, list]) => el('div', {}, { search: 'جستجو', selection: 'انتخاب شاهدها', identities: 'راویان', phylogeny: 'شجره', parts: 'فرازبندی', narrators: 'پایگاه راویان' }[s] + ': ', list.length ? list.map(d => `v${fa(d.version)} (${d.user})`).join('، ') : '—')),
      el('div', { class: 'log' }, P.log.slice().reverse().map(l => el('div', {}, `${l.ts.slice(11, 19)} ${l.stage}: ${l.msg}`))))].filter(Boolean));
}

/* the narrator database versions (step 2); an exported page has no such endpoint */
const narratorVersions = () => api('/narrator-versions').catch(() => []);
const verLine = v => {
  const e = v.eval || {}, d = e.noor_kg_death, parts = [];
  if (v.built) parts.push('ساخته‌شده ' + v.built.slice(0, 10));
  if (v.name !== 'raw') parts.push(`${fa(v.n_groups)} شخص چندشناسه`);
  if (d && d.n_checked) parts.push(`وفات جفت‌های نور و گراف دانش: ${fa(d.n_agree_3)} از ${fa(d.n_checked)} در ۳ سال هم‌خوان`);
  if (e.conflicts && e.conflicts.one_to_one != null) parts.push(`${fa(e.conflicts.one_to_one)} تعارض یک‌به‌یک`);
  return parts.join(' · ');
};
const verLabel = v => v.label_fa || v.name;
const runVer = I => (I && I.narrator_version && I.narrator_version.name) || null;
const verQ = I => { const n = runVer(I); return n ? '&version=' + encodeURIComponent(n) : ''; };
let NV = null;              // the narrator version of the run on screen (step 4); the narrator card asks for it

const panels = {
  async seed(main, c) {
    const [S, pd] = await Promise.all([stage(c.pid, c.runId, 'search'), api(`/project/${c.pid}/decision/parts`)]);
    main.replaceChildren(card(['حدیث مبدأ', partsHelp()], help('step1.seed'), hadithBlock(S.seed, true),
      el('table', {}, el('tr', {}, ...['بخش', 'متن بخش', 'شکل یکسان‌شده (برای جستجو)', S.clause_owners ? 'گوینده (hadith.ai)' : null].filter(Boolean).map(t => el('th', {}, t))),
        S.clauses.map(cl => el('tr', {}, el('td', {}, swatch(cl.i)), el('td', { class: 'ar' }, clauseRaw(S, cl.i)), el('td', { class: 'ar small muted' }, cl.text),
          S.clause_owners ? el('td', { class: 'small' }, S.clause_owners[cl.i] || '') : null))),
      seedPartsBox(S, c, pd, 'seed')));
  },

  /* ----- 2: search and selection ----- */
  async selection(main, c) {
    const S = await stage(c.pid, c.runId, 'search');
    const dec = await api(`/project/${c.pid}/decision/selection`);
    const sdec = await api(`/project/${c.pid}/decision/search`);
    const pd = await api(`/project/${c.pid}/decision/parts`);
    const pre = new Set(['witness', 'variant', 'extended', 'abridged']);
    const presel = x => pre.has(x.suggested) && !x.reference;      // a Dorar quotation without isnad is not pre-selected
    const sel = new Set(dec.selected || S.candidates.filter(presel).map(x => x.wid));
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
    const searchHelp = helpOf('جستجو چگونه انجام شد و دسته‌ها و ستون‌ها چه معنایی دارند؟',

          el('ol', {},
            el('li', {}, `دور نخست: برای هر بخش، دو واژه‌ی کمیاب آن با هم و یکی دو عبارت سه‌واژه‌ای کمیاب در دو پیکره (v02 و گراف دانش اهل سنت) پرسیده شد (${fa(S.queries.filter(q => q.round === 1).length)} پرسش)، به‌علاوه‌ی گروه‌بندی‌های موجود: گروه/خوشه/مرتبط‌های نور (${fa(nSig('rel:noor_group') + nSig('rel:noor_cluster') + nSig('rel:noor_related'))} نامزد)، مجموعه‌های هم‌خوانی v02 (${fa(nSig('rel:consensus'))}) و گروه‌های گراف دانش (${fa(nSig('rel:kg_group'))}). ${fa(r1.n_candidates || 0)} نامزد پیدا شد.`),
            r2 ? el('li', {}, `دور دوم: از متن ${fa(Math.min(S.params.expand_max, S.candidates.filter(presel).length))} شاهدِ با عبارت‌بندی متفاوت دوباره جستجو شد تا روایت‌هایی که با حدیث مبدأ واژه‌ی مشترک کمتری دارند هم پیدا شوند (${fa(r2.n_candidates)} نامزد خام، ${fa(r2.n_scored)} ماند).`) : null,
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
          el('p', {}, el('b', {}, 'متن: '), 'جای هر بخش در متن نامزد به رنگ همان بخش است؛ متن بی‌رنگ در حدیث مبدأ نیست (افزوده‌ی این روایت).'));
    const VERS = await narratorVersions();
    main.replaceChildren(
      card(['حدیث مبدأ و بخش‌هایش', partsHelp()], help('step2.search'), el('details', { class: 'fullt' }, el('summary', { class: 'small' }, 'متن حدیث مبدأ'), hadithBlock(S.seed, true)),
        el('table', {}, el('tr', {}, el('th', {}, 'بخش'), el('th', { title: 'بخش‌های تیک‌خورده در دور نخست جستجو پرسیده می‌شوند' }, 'در جستجو', helpOf('تیک «در جستجو» برای چیست؟', el('p', {}, 'فقط برای یافتن نامزدها. هر بخش تیک‌خورده جداگانه در پیکره پرسیده می‌شود. اگر بخشی بسیار رایج باشد (مثل «قال رسول الله») و نامزدهای بی‌ربط بیاورد، تیکش را بردارید، یا اگر فقط حدیث‌هایی را می‌خواهید که بخش خاصی را دارند، فقط آن را نگه دارید؛ سپس «جستجوی دوباره» را بزنید.'),
          el('p', {}, 'این تیک روی مرحله‌ی بعد اثری ندارد: مقایسه‌ی هر نامزد با حدیث مبدأ و هم‌ترازی همیشه با همه‌ی بخش‌ها انجام می‌شود. آنچه به مرحله‌ی بعد می‌رود، شاهدهایی است که پایین‌تر تیک می‌زنید.'))), el('th', {}, 'متن بخش'), el('th', {}, 'در شاهدهای انتخاب‌شده'), el('th', {}, 'پرسش‌های جستجو')),
          S.clauses.map(cl => { const qs = queriesOf(cl.i);
            return el('tr', {}, el('td', {}, swatch(cl.i)),
              el('td', {}, el('input', { type: 'checkbox', checked: clausesSel.has(cl.i), disabled: !c.can, title: 'این بخش در جستجو پرسیده شود', onchange: ev => { ev.target.checked ? clausesSel.add(cl.i) : clausesSel.delete(cl.i); } })),
              el('td', { class: 'ar' }, clauseRaw(S, cl.i)), covCells[cl.i],
              el('td', {}, qs.length ? el('details', {}, el('summary', { class: 'small' }, `${fa(qs.length)} پرسش، ${fa(qs.reduce((t, q) => t + q.hits, 0))} یافته`),
                qs.map(q => el('div', { class: 'small', dir: 'ltr', style: 'text-align:left' }, `${q.corpus} · ${q.expr} → ${q.hits}${q.capped ? ' (capped)' : ''}`)))
                : el('span', { class: 'small muted' }, clausesSel.has(cl.i) ? 'بی‌واژه‌ی کمیاب' : 'پرسیده نشد')));
          })),
        seedPartsBox(S, c, pd, 'selection'),
        c.can ? el('div', { class: 'toolbar' }, extra, el('button', { onclick: async () => {
          const all = clausesSel.size === S.clauses.length;
          await post(`/project/${c.pid}/decide/search`, { payload: { part_query: all ? null : [...clausesSel], extra_ids: [...(sdec.extra_ids || []), ...(extra.value.trim() ? [extra.value.trim()] : [])] } });
          reload(c.pid);
        } }, 'جستجوی دوباره با بخش‌های تیک‌خورده')) : null,
        S.flags.map(f => el('div', { class: 'small', style: 'color:var(--warn)' }, flagText(f)))),
      narratorsBox(c, VERS),
      card(['نامزدها: حدیث‌هایی که بخشی از حدیث مبدأ را دارند', searchHelp], help('step2.candidates'),
        el('p', { class: 'muted small' }, 'هر نامزد بخش‌به‌بخش با حدیث مبدأ سنجیده شد (بی‌توجه به ترتیب بخش‌ها) و سامانه دسته‌ای پیشنهاد کرد. شاهدهایی که تیک می‌زنید ورودی مرحله‌های بعدند: هم‌ترازی متن‌ها، خواندن سندها و ساخت شجره. چهار دسته‌ی نخست از پیش تیک خورده‌اند؛ تصمیم با شماست.'),
        el('div', { class: 'toolbar' }, ...chips, el('input', { type: 'text', placeholder: 'پالایش با نام کتاب یا کلمه', oninput: ev => { filter = ev.target.value; draw(); } }), count,
          c.can ? el('button', { class: 'small', onclick: () => { shown().forEach(x => sel.add(x.wid)); draw(); } }, 'تیک همه‌ی نمایش‌داده‌ها') : null,
          c.can ? el('button', { class: 'small', onclick: () => { shown().forEach(x => sel.delete(x.wid)); draw(); } }, 'برداشتن تیک نمایش‌داده‌ها') : null),
        list,
        el('div', { class: 'toolbar actionbar' }, c.can ? el('button', { class: 'primary', disabled: !sel.size, onclick: async () => {
          await post(`/project/${c.pid}/decide/selection`, { payload: { selected: [...sel] } }); reload(c.pid);
        } }, 'تأیید انتخاب و ادامه') : el('span', { class: 'muted' }, ME.user ? 'فقط سازنده‌ی پروژه یا مدیر می‌تواند تصمیم بگیرد.' : 'برای تصمیم‌گیری وارد شوید.'),
          el('span', { class: 'small muted' }, `اجرای جستجو ${fa(S.seconds)} ثانیه؛ دورهای گسترش: ${fa(S.rounds.length)}`))));
    draw();
  },

  /* ----- 3: alignment ----- */
  async alignment(main, c) {
    const [A, S, pd] = await Promise.all([stage(c.pid, c.runId, 'align'), stage(c.pid, c.runId, 'search'), api(`/project/${c.pid}/decision/parts`)]);
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
    let umode = false, uanc = null;        // defining a unit of one's own: picking its first and last column in the grid
    const canEdit = c.can && !STATIC;

    /* the cells of one witness in one part: per grid column, or one cell when the part is not in its text */
    const cellsOf = (cl, w, keep) => {
      const g = grid[cl.i], sg = A.segments[`${cl.i}|${w.wid}`] || {};
      const hu = `--h:${hue(cl.i)}`;
      if (g && g.columns.length && g.witnesses.includes(w.wid)) {
        const idx = g.columns.map((col, j) => col[w.wid] != null ? j : -1).filter(j => j >= 0);
        const lo = idx[0], hi = idx[idx.length - 1];
        const mine = new Set((g.user_cells || []).filter(([, x]) => x === w.wid).map(([j]) => j));     // words the user placed (parts.words)
        const editable = c.can && !STATIC && !!cl.key;
        return keep.map(j => {
          const col = g.columns[j], t = col[w.wid], r = refTok(g, j, seed);
          const cls = j < lo || j > hi ? 'na' : t == null ? (r == null ? 'gap' : 'om') : r == null ? 'ins' : mkey(t) === mkey(r) ? 'same' : 'diff';
          const on = pick !== null && (colUnits[`${cl.i}|${j}`] || []).includes(pick);
          const td = el('td', { class: cls + (on ? ' pk' : '') + (j === keep[0] ? ' sep' : '') + (sg.user ? ' usr' : '') + (mine.has(j) ? ' wusr' : '') + (umode ? ' clk' : editable && t != null ? ' clk' : '') + (uanc && uanc.ci === cl.i && uanc.j === j ? ' anc' : ''), style: hu,
            title: (cls === 'om' ? `«${r}» در این شاهد نیامده` : cls === 'ins' ? 'در حدیث مبدأ نیست' : cls === 'diff' ? `حدیث مبدأ: ${r}` : '') + (mine.has(j) ? ' (تصمیم دستی شما)' : '')
              + (editable && t != null ? ' — برای اصلاح بزنید: افزوده یا متفاوت؟' : '') }, t != null ? t : cls === 'na' ? '' : '·');
          td.dataset.c = `${cl.i}|${j}`;
          if (umode && cl.key) td.onclick = () => pickCol(cl, j);
          else if (editable && t != null) td.onclick = () => wordModal(cl, w, j, g, A, c, pd);
          return td;
        });
      }
      const n = Math.max(keep.length, 1);
      const on = pick !== null && clauseUnit[cl.i] === pick;
      if (sg.state === 'present' && sg.tokens && sg.tokens.length)
        return [el('td', { class: 'same sep' + (on ? ' pk' : '') + (sg.user ? ' usr' : ''), style: hu, colspan: n }, sg.tokens.join(' '))];
      const why = sg.user ? (sg.state === 'absent' ? 'ندارد (دستی)' : 'نامعلوم (دستی)') : w.marker != null && sg.state !== 'present' ? 'خلاصه‌شده' : sg.state === 'absent' ? 'نیامده' : 'نامعلوم';
      return [el('td', { class: 'miss sep' + (on ? ' pk' : '') + (sg.user ? ' usr' : ''), style: hu, colspan: n, title: { 'نیامده': 'این بخش در متن این شاهد نیست', 'خلاصه‌شده': 'متن با «وساق الحدیث / بنحوه…» کوتاه شده؛ نبودن بخش نشانه‌ی افتادگی نیست', 'نامعلوم': 'فقط تکه‌ای از این بخش پیدا شد' }[why] || 'تصمیم شما در فرازبندی (بالای صفحه)' }, why)];
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
      onclick: () => hadithModal(w.wid) },
      el('span', { class: 'dot ' + w.cls }), `${fa(i)}. ${w.title || w.wid}`, el('span', { class: 'small muted' }, w.death ? ' ف ' + fa(w.death) : ''));
    const table = (cls, rows) => {
      const keeps = cls.map(keepOf);
      const head1 = el('tr', { class: 'b' }, el('th', { class: 'nm', rowspan: 2 }, 'شاهد ↓ / بخش ←'),
        cls.map((cl, n) => el('th', { class: 'band' + (pick !== null && clauseUnit[cl.i] === pick ? ' pk' : ''), style: `--h:${hue(cl.i)}`, colspan: Math.max(keeps[n].length, 1), title: cname(cl) },
          `${fa(cl.i + 1)}${cl.kind === 'extra' ? ' (افزوده)' : ''} `, el('span', { class: 'ar' }, short(cname(cl), Math.max(8, 4 * keeps[n].length))),
          clauseUnit[cl.i] !== undefined ? ubadge(clauseUnit[cl.i]) : null,
          cl.key && !STATIC ? el('button', { class: 'ub', title: 'بازبینی و اصلاح جای این بخش در شاهدها', onclick: ev => { ev.stopPropagation(); partModal(cl, A, S, c, pd, cname); } }, '✎') : null)));
      const head2 = el('tr', { class: 'u' }, cls.flatMap((cl, n) => keeps[n].length
        ? keeps[n].map(j => el('th', { class: j === keeps[n][0] ? 'sep' : '', style: `--h:${hue(cl.i)}`, onclick: umode && cl.key ? () => pickCol(cl, j) : null }, (colUnits[`${cl.i}|${j}`] || []).map(ubadge)))
        : el('th', { class: 'sep', style: `--h:${hue(cl.i)}` }, onlyDiff && grid[cl.i] && grid[cl.i].columns.length ? el('span', { class: 'small muted' }, 'یکسان') : '')));
      return el('div', { class: 'grid-wrap', style: 'max-height:76vh' }, el('table', { class: 'altab' + (umode ? ' pickc' : '') }, el('thead', {}, head1, head2),
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
            clauseUnit[cl.i] !== undefined ? ubadge(clauseUnit[cl.i]) : null, el('span', { class: 'small muted' }, `در ${fa(holds.length)} شاهد`),
            cl.key ? el('span', { class: 'chip ' + partStatus(cl, pd, W).k }, partStatus(cl, pd, W).t) : null,
            cl.key && !STATIC ? el('button', { class: 'small', onclick: () => partModal(cl, A, S, c, pd, cname) }, c.can ? 'بازبینی و اصلاح' : 'دیدن') : null),
          holds.length ? table([cl], holds) : null,
          Object.entries(by).map(([k, ns]) => el('div', { class: 'small muted' }, `${k} در ${fa(ns.length)} شاهد: `, ns.join('، '))));
      }));
    };
    const unitsBox = el('div');
    /* a presence unit is either an addition (gaining the words is likely, losing them unlikely) or an omission: the model's rate class */
    const clsCell = u => {
      if (u.kind !== 'presence') return el('td', {});
      const key = (A.clauses.find(x => x.i === u.clause) || {}).key, set = ((pd.unit_cls || {})[key] || {})[u.ukey];
      const flip = async x => {
        await saveParts(c.pid, d => { const m = JSON.parse(JSON.stringify(d.unit_cls || {})); (m[key] ||= {});
          if (x == null) delete m[key][u.ukey]; else m[key][u.ukey] = x; if (!Object.keys(m[key]).length) delete m[key]; return { unit_cls: m }; }, `unit ${u.ukey}`);
        reloadAt(c.pid, 'alignment');
      };
      const can = c.can && !STATIC && key && u.ukey !== undefined;
      return el('td', { class: 'small', onclick: ev => ev.stopPropagation() },
        el('span', { class: 'chip ' + (u.cls === 'addition' ? 'partial' : 'medium'), title: 'افزوده: دریافت این واژه‌ها محتمل و از دست دادنشان نامحتمل است؛ افتاده: برعکس.' }, u.cls === 'addition' ? 'افزوده' : 'افتاده'),
        u.cls_user ? el('span', { class: 'userc' }, ' (دستی)') : null,
        can ? el('button', { class: 'small', onclick: () => flip(u.cls === 'addition' ? 'omission' : 'addition') }, u.cls === 'addition' ? 'افتاده است' : 'افزوده است') : null,
        can && set ? el('button', { class: 'small', title: 'برگشت به حدس خودکار', onclick: () => flip(null) }, 'خودکار') : null);
    };
    /* every candidate unit in one table, with a tick «در مدل»: the units the next step uses. A tick that differs from the unit's
       present state is a pending change; «ذخیره‌ی انتخاب» saves them all at once (parts.units: keep / drop). */
    const PV = A.private_variants || [], DU = A.dropped_units || [], nPrivate = PV.filter(u => !u.why || u.why === 'private').length;
    const WHY = { private: 'کنارگذاشته‌ی خودکار: فقط یک شاهد متفاوت', capped: 'کنارگذاشته‌ی خودکار: بیش از ۴۰ واحد', orthographic: 'کنارگذاشته‌ی خودکار: فقط اختلاف املایی', constant: 'همه‌ی شاهدها یک حالت دارند' };
    const stale = A.units.some(u => u.ukey === undefined) || PV.some(u => !u.labels);     // built before the units could be edited: run it again
    const cand = [...units.map(u => ({ u, st: 'in' })), ...(PV.every(x => x.labels) ? PV : []).map(u => ({ u, st: u.why || 'private' })), ...DU.map(u => ({ u, st: 'dropped' }))];
    const want = {}, cid = x => `${x.u.clause}|${x.u.ukey}`;
    const usable = x => canEdit && x.u.ukey !== undefined && !!clauseKeyOf(x.u) && x.st !== 'constant';
    const isOn = x => cid(x) in want ? want[cid(x)] : x.st === 'in';
    const setWant = (x, on) => { if (on === (x.st === 'in')) delete want[cid(x)]; else want[cid(x)] = on; };
    const statOf = x => x.st === 'in' ? (x.u.by === 'user' ? ['دستی', 'partial'] : x.u.user_keep ? ['نگه‌داشته', 'partial'] : ['خودکار', 'auto']) : x.st === 'dropped' ? ['کنارگذاشته‌ی شما', 'medium'] : [WHY[x.st], 'medium'];
    const saveSelection = () => saveUnits(cand.filter(x => cid(x) in want).map(x => [clauseKeyOf(x.u), x.u.ukey, want[cid(x)] ? e => { e.keep = true; delete e.drop; } : e => { e.drop = true; delete e.keep; }]), 'unit selection');
    const decided = () => Object.entries(pd.units || {}).flatMap(([k, m]) => Object.entries(m).filter(([, e]) => e.drop || e.keep).map(([uk]) => [k, uk, e => { delete e.drop; delete e.keep; }]));
    const drawUnits = () => {
      const vis = cand;                                // every unit, the ones set aside included
      const pending = Object.keys(want).length, nOn = cand.filter(isOn).length, mine = decided(), all = (on) => () => { vis.filter(usable).forEach(x => setWant(x, on)); drawUnits(); };
      unitsBox.replaceChildren(
        canEdit && cand.some(usable) ? el('div', { class: 'toolbar' + (pending ? ' actionbar' : '') },
          el('b', {}, `${fa(nOn)} از ${fa(cand.length)} واحد برای مدل (مرحله‌ی ۵) انتخاب شده`),
          el('button', { class: 'small', onclick: all(true) }, 'انتخاب همه'), el('button', { class: 'small', onclick: all(false) }, 'برداشتن همه'),
          el('button', { class: 'small', disabled: !mine.length || pending > 0, title: 'همه‌ی «نگه داشتن» و «کنار گذاشتن»های شما برداشته می‌شود و انتخاب خودکار برمی‌گردد', onclick: () => saveUnits(mine, 'unit selection reset') }, 'بازگشت به انتخاب خودکار'),
          pending ? [el('button', { class: 'primary', onclick: saveSelection }, `ذخیره‌ی انتخاب (${fa(pending)} تغییر)`), el('button', { class: 'small', onclick: () => { Object.keys(want).forEach(k => delete want[k]); drawUnits(); } }, 'لغو تغییرها'),
            el('span', { class: 'small muted' }, 'پس از ذخیره هم‌ترازی دوباره ساخته می‌شود و مرحله‌ی ۵ دوباره تأیید می‌خواهد.')] : null) : null,
        el('table', { class: 'units' },
          el('tr', {}, ...['در مدل', 'واحد', 'بخش', 'گونه', 'جا در متن', 'حالت‌ها (شمار شاهدها)', 'وضع', 'افزوده یا افتاده؟', ''].map(t => el('th', {}, t))),
          vis.map(x => { const u = x.u, on = isOn(x), nNA = Object.values(u.states).filter(v => v < 0).length, [stt, stc] = statOf(x), inm = x.st === 'in';
            return el('tr', { class: (inm && pick === u.k ? 'on ' : '') + (on ? '' : 'off'), onclick: inm ? () => setPick(pick === u.k ? null : u.k) : null, title: inm ? 'نمایش در جدول هم‌ترازی' : '' },
              el('td', { onclick: ev => ev.stopPropagation() }, el('input', { type: 'checkbox', checked: on, disabled: !usable(x), 'aria-label': 'این واحد به مدل برود', title: x.st === 'constant' ? 'همه‌ی شاهدها در این واحد یک حالت دارند؛ به مدل نمی‌رود' : '',
                onchange: ev => { setWant(x, ev.target.checked); drawUnits(); } })),
              el('td', {}, inm ? ubadge(u.k) : ''), el('td', {}, swatch(u.clause)), el('td', { class: 'small' }, UKIND(u), unitChips(u)), el('td', { class: 'ar' }, uwhere(u, A)),
              el('td', {}, u.labels.map((l, i) => el('span', { class: 'st', style: `--h:${hue(u.clause)}` }, el('span', { class: 'ar' }, ulab(l)), ' ', fa(Object.values(u.states).filter(v => v === i).length))),
                nNA ? el('span', { class: 'small muted' }, ` نامعلوم ${fa(nNA)}`) : null),
              el('td', {}, el('span', { class: 'chip ' + stc }, stt), cid(x) in want ? el('span', { class: 'small', style: 'color:var(--accent)' }, on ? ' ← می‌رود' : ' ← نمی‌رود') : null),
              clsCell(u), unitEdit(u)); })));
    };

    /* ----- the user's decisions about units ----- */
    const clauseKeyOf = u => (A.clauses.find(x => x.i === u.clause) || {}).key;
    const names = Object.fromEntries(W.map((w, i) => [w.wid, `${fa(i)}. ${w.title || w.wid}`]));
    const saveUnits = async (edits, note) => { await saveParts(c.pid, d => foldUnits(d, edits), note); reloadAt(c.pid, 'alignment'); };
    const pickCol = (cl, j) => {
      if (!uanc || uanc.ci !== cl.i) { uanc = { ci: cl.i, j }; drawTools(); drawBody(); return; }
      const j0 = Math.min(uanc.j, j), j1 = Math.max(uanc.j, j);
      uanc = null; umode = false; drawTools(); drawBody(); newUnitModal(cl, j0, j1);
    };
    /* a unit over the columns j0..j1 of a part: what each witness has there decides its state */
    const newUnitModal = (cl, j0, j1) => {
      const g = grid[cl.i], seqs = rangeSeqs(g, j0, j1), forms = new Set(Object.values(seqs).filter(x => x && x.length).map(x => x.map(mkey).join(' ')));
      let kind = forms.size <= 1 ? 'presence' : 'lexical', dropAuto = true;
      const first = Object.values(seqs).find(x => x && x.length), txt = el('input', { type: 'text', size: 32, dir: 'rtl', class: 'ar', value: (seqs[A.seed] || []).join(' ') || (first || []).join(' ') });
      const inside = units.filter(u => u.clause === cl.i && u.cols && u.unit_kind !== 'clause' && u.ukey !== undefined && u.by !== 'user' && u.cols[0] >= j0 && u.cols[1] <= j1);
      const anchors = {};
      [A.seed, ...g.witnesses.filter(w => w !== A.seed)].filter(w => seqs[w] && seqs[w].length).slice(0, 50).forEach(w => {
        const ks = rowKeys(g.columns, w).filter(([j]) => j >= j0 && j <= j1).map(([, k]) => k); anchors[w] = [ks[0], ks[ks.length - 1]]; });
      const body = el('div'), ok = el('button', { class: 'primary' }, 'ساختن واحد');
      const draw = () => {
        const pv = unitPreview(kind, seqs);
        body.replaceChildren(
          el('div', { class: 'toolbar' }, el('b', {}, 'گونه‌ی واحد: '), seg([['presence', 'حضور واژه (دارد / ندارد)'], ['lexical', 'واژه‌ی جایگزین (هر واژه یک حالت)']], kind, k => { kind = k; draw(); })),
          el('table', { class: 'small' }, el('tr', {}, ...['شاهد', 'واژه‌ها در این ستون‌ها', 'حالت'].map(t => el('th', {}, t))),
            g.witnesses.map(w => el('tr', {}, el('td', {}, names[w] || w), el('td', { class: 'ar' }, seqs[w] == null ? el('span', { class: 'muted' }, 'بیرون از متن این شاهد') : seqs[w].length ? seqs[w].join(' ') : '·'),
              el('td', {}, pv.st[w] < 0 ? el('span', { class: 'muted' }, 'نامعلوم') : ulab(pv.labels[pv.st[w]]))))),
          kind === 'lexical' && pv.labels.length < 2 || kind === 'presence' && new Set(Object.values(pv.st).filter(v => v >= 0)).size < 2
            ? el('p', { class: 'small', style: 'color:var(--warn)' }, 'همه‌ی شاهدها در این ستون‌ها یک حالت دارند؛ چنین واحدی به مدل نمی‌رود.') : null);
        ok.disabled = !Object.keys(anchors).length;
      };
      ok.onclick = async () => {
        await saveParts(c.pid, d => foldUnits(d, [...(dropAuto ? inside.map(u => [cl.key, u.ukey, e => { e.drop = true; delete e.keep; }]) : []),
          [cl.key, 'm:' + nextUnitId(d), e => { e.def = { kind, anchors, text: txt.value.trim() || (first || []).join(' ') }; }]]), `unit over columns ${j0}-${j1} of ${cl.key}`);
        reloadAt(c.pid, 'alignment');
      };
      draw();
      modal(el('div', {}, el('h3', { style: 'margin-top:0' }, 'واحد تازه روی ستون‌های ', fa(j0 + 1), ' تا ', fa(j1 + 1), ' از بخش ', fa(cl.i + 1)), help('step3.unitedit'),
        el('p', { class: 'small muted' }, 'واحد به واژه‌های همین شاهدها بسته است، نه به شماره‌ی ستون؛ اگر هم‌ترازی عوض شود، واحد همراه واژه‌ها می‌رود.'),
        el('div', { class: 'toolbar' }, el('label', { class: 'small' }, 'نام واحد: '), txt), body,
        inside.length ? el('label', { class: 'small' }, el('input', { type: 'checkbox', checked: true, onchange: ev => { dropAuto = ev.target.checked; } }),
          ` واحدهای خودکارِ درون همین ستون‌ها کنار گذاشته شوند (${fa(inside.length)} واحد: جایگزین شدن یا ادغام)`) : null,
        el('div', { class: 'toolbar' }, ok, el('span', { class: 'small muted' }, 'پس از ذخیره هم‌ترازی دوباره ساخته می‌شود و مرحله‌ی ۵ دوباره تأیید می‌خواهد.'))));
    };
    /* one unit: leave it out or keep it, fix the state of a witness, delete a unit of one's own */
    const unitModal = u => {
      const key = clauseKeyOf(u), ukey = u.ukey, saved = uentry(pd, key, ukey), manual = u.by === 'user', g = grid[u.clause];
      const pend = { states: JSON.parse(JSON.stringify(saved.states || {})) };
      const sj = o => JSON.stringify(Object.entries(o).sort()), dirty = () => sj(pend.states) !== sj(saved.states || {});
      const seqs = u.cols && g && g.columns.length ? rangeSeqs(g, u.cols[0], u.cols[1]) : null;
      const wids = [...new Set([...Object.keys(u.states), ...Object.keys(pend.states)])].filter(w => names[w]);
      const NA = '\u0001na', NEW = '\u0001new', body = el('div'), ok = el('button', { class: 'primary' }, 'ذخیره');
      const row = w => {
        const sg = A.segments[`${u.clause}|${w}`] || {}, st = u.states[w], cur = pend.states[w];
        const sel = el('select', { 'aria-label': 'حالت ' + names[w] }, el('option', { value: '' }, 'خودکار'), el('option', { value: NA }, 'نامعلوم'),
          u.labels.filter(l => l !== '*other*').map(l => el('option', { value: 'L:' + l }, ulab(l))), u.kind === 'presence' ? null : el('option', { value: NEW }, 'حالت تازه…'));
        const inp = el('input', { type: 'text', size: 12, dir: 'rtl', class: 'ar', placeholder: 'حالت تازه' });
        const isNew = typeof cur === 'string' && !u.labels.includes(cur);
        sel.value = !(w in pend.states) ? '' : cur === null ? NA : isNew ? NEW : 'L:' + cur;
        if (isNew) inp.value = cur;
        inp.hidden = sel.value !== NEW;
        sel.onchange = () => {
          if (sel.value === '') delete pend.states[w]; else if (sel.value === NA) pend.states[w] = null;
          else if (sel.value === NEW) { inp.hidden = false; inp.focus(); if (inp.value.trim()) pend.states[w] = inp.value.trim(); else delete pend.states[w]; }
          else pend.states[w] = sel.value.slice(2);
          if (sel.value !== NEW) inp.hidden = true;
          ok.disabled = !dirty();
        };
        inp.oninput = () => { if (inp.value.trim()) pend.states[w] = inp.value.trim(); else delete pend.states[w]; ok.disabled = !dirty(); };
        const words = seqs ? (seqs[w] == null ? '—' : seqs[w].length ? seqs[w].join(' ') : '·') : sg.tokens && sg.tokens.length ? short(sg.tokens.join(' '), 60) : '—';
        return el('tr', {}, el('td', {}, names[w]), el('td', { class: 'ar' }, words), el('td', { class: 'small' }, st == null || st < 0 ? 'نامعلوم' : ulab(u.labels[st])), el('td', {}, sel, ' ', inp));
      };
      const draw = () => {
        body.replaceChildren(
          el('p', { class: 'small muted' }, 'اینجا فقط حالت هر شاهد در این واحد را درست می‌کنید. اینکه واحد به مدل برود یا نه را با تیک «در مدل» در جدول واحدها تعیین کنید.'),
          manual ? el('div', { class: 'toolbar' }, el('button', { class: 'small', onclick: () => saveUnits([[key, ukey, e => Object.keys(e).forEach(k => delete e[k])]], `delete unit ${ukey}`) }, 'حذف این واحدِ دستی')) : null,
          el('table', { class: 'small' }, el('tr', {}, ...['شاهد', u.cols ? 'واژه‌ها در ستون‌های واحد' : 'متن', 'حالت کنونی', 'حالت دستی'].map(t => el('th', {}, t))), wids.map(row)));
      };
      ok.disabled = true;
      ok.onclick = () => saveUnits([[key, ukey, e => { delete e.states; if (Object.keys(pend.states).length) e.states = pend.states; }]], `unit ${ukey}`);
      draw();
      modal(el('div', {}, el('h3', { style: 'margin-top:0' }, `واحد ${u.k != null ? fa(u.k + 1) + ': ' : ''}${UKIND(u)} — `, el('span', { class: 'ar' }, uwhere(u, A))), help('step3.unitedit'),
        body, el('div', { class: 'toolbar' }, ok, el('span', { class: 'small muted' }, 'پس از ذخیره هم‌ترازی دوباره ساخته می‌شود و مرحله‌ی ۵ دوباره تأیید می‌خواهد.'))));
    };
    const unitChips = u => [
      (u.states_user || []).length ? el('span', { class: 'chip medium', style: 'margin-inline-start:4px' }, `${fa(u.states_user.length)} حالت دستی`) : null];
    const unitEdit = u => el('td', { onclick: ev => ev.stopPropagation() }, canEdit && u.ukey !== undefined && clauseKeyOf(u) ? el('button', { class: 'small', title: 'ویرایش این واحد', onclick: () => unitModal(u) }, '✎') : null);
    const seg = (opts, cur, set) => el('span', { class: 'seg' }, opts.map(([k, t]) => el('button', { class: k === cur ? 'on' : '', onclick: () => set(k) }, t)));
    const tools = el('div', { class: 'toolbar' });
    const drawTools = () => tools.replaceChildren(
      seg([['one', 'همه‌ی بخش‌ها در یک جدول'], ['parts', 'هر بخش جدا']], view, k => { view = k; setPref('icm.alview', k); drawTools(); drawBody(); }),
      el('label', { class: 'small' }, el('input', { type: 'checkbox', checked: onlyDiff, onchange: ev => { onlyDiff = ev.target.checked; setPref('icm.aldiff', onlyDiff ? '1' : '0'); drawBody(); } }), ' فقط ستون‌هایی که اختلاف دارند'),
      el('span', { class: 'small muted' }, 'راهنما: '), el('span', { class: 'lg same', style: `--h:${hue(0)}` }, 'مثل حدیث مبدأ'), el('span', { class: 'lg diff', style: `--h:${hue(0)}` }, 'واژه‌ی دیگر'),
      el('span', { class: 'lg ins', style: `--h:${hue(0)}` }, 'افزوده'), el('span', { class: 'lg om' }, '· افتاده'), el('span', { class: 'lg miss' }, 'بخش نیامده'), ubadgeDemo(),
      ...[canEdit && A.clauses.length && A.clauses[0].key ? el('button', { class: 'small', title: 'یک واحد تغییر را خودتان روی چند ستون از جدول تعریف کنید (ستون نخست و واپسین را می‌زنید)',
        onclick: () => { umode = !umode; uanc = null; drawTools(); drawBody(); } }, umode ? 'لغو تعریف واحد' : '+ واحد تازه') : null,
      umode ? el('span', { class: 'small', style: 'color:var(--accent)' }, uanc ? 'اکنون ستون واپسین واحد را بزنید (در همان بخش).' : 'ستون نخست واحد را در جدول بزنید.') : null].filter(Boolean));
    drawTools(); drawBody(); drawUnits();

    main.replaceChildren(
      A.clauses.length && A.clauses[0].key ? partsReviewCard(A, S, c, pd, cname) : null,
      card(['هم‌ترازی متن شاهدها، بخش‌به‌بخش', helpOf('هم‌ترازی چگونه ساخته شد؟',
          el('ol', {},
            el('li', {}, 'جای هر بخش حدیث مبدأ در متن هر شاهد جداگانه پیدا شد، بی‌توجه به ترتیب؛ پس شاهدی که بندها را جابه‌جا آورده هم درست هم‌تراز می‌شود. عبارت‌هایی مثل «قال رسول الله صلی الله علیه وآله» جزو بخش شمرده نمی‌شوند.'),
            el('li', {}, 'تکه‌ای که در حدیث مبدأ نیست ولی دست‌کم دو شاهد آن را دارند، «بخش افزوده» شد. تکه‌هایی که فقط یک شاهد دارد پایین‌تر فهرست شده‌اند.'),
            el('li', {}, 'واژه‌های هر بخش در همه‌ی شاهدهای دارای آن بخش با هم هم‌تراز شد (هم‌ترازی چندگانه با یک دور بازبینی برای هر ردیف). واژه‌هایی که فقط در «و/ف/ال» فرق دارند یکسان شمرده می‌شوند، «و» جدانوشته به واژه‌ی بعدش چسبانده می‌شود، و عددها (ستة، سبعة، خمسة…) در یک ستون می‌آیند.'),
            el('li', {}, 'خانه‌ی خالی پیش از نخستین یا پس از واپسین واژه‌ی یک شاهد یعنی متن آن شاهد آنجا کوتاه‌تر است؛ این «افتادگی» شمرده نمی‌شود. شاهدی که با «وساق الحدیث / بنحوه…» خلاصه شده، برای بخش‌هایی که نیاورده «نامعلوم» است.'),
            el('li', {}, 'شماره‌های کوچک بالای ستون‌ها واحدهای تغییرند (جدول پایین)؛ با زدن هر کدام، ستون‌ها و شاهدهای هر حالت نشان داده می‌شوند.')))], help('step3.alignment'),
        el('p', { class: 'muted small' }, `${fa(W.length)} شاهد (حدیث مبدأ و شاهدهایی که در مرحله‌ی ۲ انتخاب شدند)، ${fa(A.clauses.length)} بخش. هر ردیف یک شاهد است و از راست به چپ مثل متن خوانده می‌شود؛ هر ستون یک جایگاه واژه. رنگ هر گروه ستون همان رنگ بخش در مرحله‌ی ۲ است. نام کتاب را بزنید تا متن کامل آن را ببینید.${c.can && !STATIC ? ' اگر هم‌ترازی خودکار یک واژه را درست نشاند (مثلاً واژه‌ای افزوده را به‌جای واژه‌ی دیگری گذاشته)، همان واژه را در جدول بزنید و بگویید «افزوده» است یا «متفاوت».' : ''}`),
        old && !c.readonly ? el('div', { class: 'help warnbox' }, 'این هم‌ترازی با نسخه‌ی پیشینِ برنامه ساخته شده است (پیش از درست شدن جابه‌جایی برخی ردیف‌ها و هم‌ستون شدن عددها). ',
          c.can ? el('button', { class: 'small', onclick: async () => { await post(`/project/${c.pid}/rerun`, {}); reload(c.pid); } }, 'اجرای دوباره با برنامه‌ی تازه') : 'سازنده‌ی پروژه می‌تواند آن را دوباره اجرا کند.',
          el('span', { class: 'small muted' }, ' مرحله‌های بعد هم دوباره محاسبه می‌شوند؛ اجرای کنونی پاک نمی‌شود.')) : null,
        tools, pickBox, body),
      card(['واحدهای تغییر: ورودی مدل شجره', helpOf('این واحدها چه کاری می‌کنند؟',
          el('p', {}, 'مدل شجره (مرحله‌ی ۵) هم سندها را می‌بیند و هم این واحدها را. شاهدهایی که در واحدهای بیشتری حالتِ کم‌یاب مشترک دارند (مثلاً «سبعة» به جای «ستة» و پرسشِ «ومن هم یا رسول الله») به احتمال بیشتر از یک منبع یا یک راوی مشترک گرفته‌اند. پس این جدول همان چیزی است که متن‌ها در برآورد «قدیمی‌ترین منبع» می‌گویند.'),
          el('ul', {},
            el('li', {}, el('b', {}, 'حضور بخش: '), 'آیا شاهد این بخش را دارد؟ (دارد / ندارد)'),
            el('li', {}, el('b', {}, 'حضور واژه: '), 'چند واژه‌ی کنار هم که برخی شاهدها دارند و برخی نه.'),
            el('li', {}, el('b', {}, 'واژه‌ی جایگزین: '), 'در یک جایگاه، شاهدها واژه‌های مختلف دارند؛ هر واژه یک حالت.'),
            el('li', {}, el('b', {}, 'عدد: '), 'عددی که حدیث می‌گوید (۴، ۵، ۶، ۷…).')),
          el('p', {}, `«نامعلوم» یعنی آن جا در متن شاهد نیامده چون کوتاه یا خلاصه شده است؛ مدل آن را حدس نمی‌زند. واحدی که فقط یک شاهد با بقیه فرق دارد درباره‌ی گروه‌بندی چیزی نمی‌گوید و کنار گذاشته شد (${fa(nPrivate)} واحد)؛ ${A.dropped_orthographic ? fa(A.dropped_orthographic) + ' ستون هم فقط اختلاف املایی داشت. ' : ''}حداکثر ۴۰ واحد خودکار نگه داشته می‌شود، آگاهی‌بخش‌ترها نخست؛ واحدهایی که خودتان نگه می‌دارید یا می‌سازید جدا از این سقف‌اند.`))], help('step3.units'),
        el('p', { class: 'small' }, `واحد تغییر جایی از متن است که شاهدها در آن با هم فرق دارند؛ هر شاهد در هر واحد یک «حالت» دارد. ${fa(A.n_units)} واحد از جدول بالا بیرون کشیده شد و به مرحله‌ی ۵ (شجره) می‌رود.${DU.length ? ` ${fa(DU.length)} واحد را خودتان کنار گذاشته‌اید.` : ''}${canEdit ? ' ستون «در مدل» را تیک بزنید یا بردارید تا تعیین کنید کدام واحدها به مرحله‌ی بعد بروند (واحدهای کنارگذاشته هم در همین جدول‌اند)؛ با «+ واحد تازه» بالای جدول هم‌ترازی روی چند ستون واحدی بسازید.' : ''}`),
        stale ? el('div', { class: 'help warnbox' }, 'این هم‌ترازی با نسخه‌ی پیشینِ برنامه ساخته شده است: واحدهای کنارگذاشته (مانند واژه‌ای که فقط یک شاهد دارد، یا ستون‌هایی که فقط در املا فرق دارند) در این جدول نیستند و برخی واحدها را نمی‌توان برداشت. با «اجرای دوباره» همه‌ی آن‌ها در جدول می‌آیند و می‌توانید تیکشان بزنید. ',
          c.can && !STATIC ? el('button', { class: 'small', onclick: async () => { await post(`/project/${c.pid}/rerun`, {}); reloadAt(c.pid, 'alignment'); } }, 'اجرای دوباره با برنامه‌ی تازه') : 'سازنده‌ی پروژه می‌تواند آن را دوباره اجرا کند.',
          el('span', { class: 'small muted' }, ' مرحله‌های بعد هم دوباره محاسبه می‌شوند و مرحله‌ی ۵ دوباره تأیید می‌خواهد.')) : null,
        unitsBox,
        A.private_texts.length ? el('details', {}, el('summary', { class: 'small muted' }, `${fa(A.private_texts.length)} تکه‌ی افزوده که فقط در یک شاهد آمده`),
          A.private_texts.map(t => el('div', { class: 'ar small' }, `${fa(W.findIndex(w => w.wid === t.wid))}. ${(W.find(w => w.wid === t.wid) || {}).title || t.wid}: ${t.text}`))) : null));
  },

  /* ----- 4: identities ----- */
  /* three parts: (1) the hadiths and their state, (2) the chain of the chosen hadith, (3) the chosen name: its places,
     then its options and a search of both narrator databases */
  async identities(main, c) {
    const [I, A, S, dec, phd, pdec] = await Promise.all([stage(c.pid, c.runId, 'identify'), stage(c.pid, c.runId, 'align'), stage(c.pid, c.runId, 'search'),
      api(`/project/${c.pid}/decision/identities`), api(`/project/${c.pid}/decision/phylogeny`), api(`/project/${c.pid}/decision/parts`)]);
    const nonEmpty = o => (o && Object.keys(o).length ? o : null);
    const renPending = !sameJ(nonEmpty(pdec.renames), nonEmpty(I.renames_in));     // name/term corrections saved but not in these candidates yet
    const chainPending = !sameJ(nonEmpty(pdec.chain), nonEmpty(I.chain_in));       // chain edits (places added/removed/re-bounded) likewise
    NV = runVer(I);
    /* dates entered on step 5 before they moved here are carried over (those keyed by a person id) */
    const oldDates = Object.fromEntries(Object.entries(phd.dates || {}).filter(([k]) => /^(noor|kg):\d+$/.test(k)));
    const saved = { choices: dec.choices || {}, overrides: dec.overrides || {}, dates: { ...oldDates, ...(dec.dates || {}) },
      merges: dec.merges || [], drops: dec.drops || [], dismissed: dec.dismissed || [], splits: dec.splits || {} };
    const choices = { ...saved.choices }, overrides = { ...saved.overrides }, dates = { ...saved.dates };
    const splits = JSON.parse(JSON.stringify(saved.splits));   // people the user split off an identity: split:<n> -> {label, from}
    const merges = saved.merges.map(m => [...m]), drops = new Set(saved.drops), dismissed = new Set(saved.dismissed);
    const cards = I.cards, W = A.witnesses;
    const num = Object.fromEntries(W.map((w, i) => [w.wid, i]));
    const byWid = {}; I.chains.forEach(ch => (byWid[ch.wid] ||= []).push(ch));
    const order = [...W.map(w => w.wid).filter(w => byWid[w]), ...Object.keys(byWid).filter(w => !(w in num))];
    const chainOf = Object.fromEntries(I.chains.map(ch => [ch.aid, ch]));
    const wtitle = wid => { const w = W.find(x => x.wid === wid); return `${wid in num ? fa(num[wid]) + '. ' : ''}${(w && w.title) || wid}`; };
    const hadith = wid => wid === S.seed.wid ? S.seed : S.candidates.find(x => x.wid === wid || (x.copies || []).includes(wid));
    const seq = []; order.forEach(wid => byWid[wid].forEach(ch => ch.elems.forEach(e => { if (e.card && !seq.includes(e.card)) seq.push(e.card); })));
    const relOnly = cd => cd.kind === 'relative';         // «عن أبيه» means a different person in every chain: decided per place
    const elemAt = (ch, i) => ch.elems.find(e => e.i === i);     // a place by its id (an added place is 1000+; ids never shift)
    const prevName = cd => { const [aid, i] = cd.occurrences[0]; const x = chainOf[aid].elems, p = x[x.indexOf(elemAt(chainOf[aid], i)) - 1]; return p ? p.raw || p.name : ''; };
    const cname = cd => relOnly(cd) && cd.occurrences.length === 1 && prevName(cd) ? `${cd.name} — پس از ${prevName(cd)}` : cd.name;
    const keyOf = e => e.card || (e.exact ? 'id:' + e.exact : null);   // what clicking a name opens: its card, or the person the source fixed
    const keyAt = (ch, e) => { const o = overrides[`${ch.aid}|${e.i}`]; return /^split:/.test(o || '') ? 'id:' + o : keyOf(e); };   // a split person opens as a person
    const splitOcc = occ => { const k = occ.lastIndexOf('|'); const ch = chainOf[occ.slice(0, k)]; return [ch, elemAt(ch, +occ.slice(k + 1))]; };
    const extraLabel = {};                                // labels of people chosen from a search
    const qs = new URLSearchParams(query());
    let cur = byWid[qs.get('h')] ? qs.get('h') : null, pick = null;
    if (cur && qs.get('n')) { try { const [ch, e] = splitOcc(qs.get('n')); if (ch.wid === cur && keyAt(ch, e)) pick = { key: keyAt(ch, e), occ: qs.get('n') }; } catch (_) {} }
    const splitSel = new Set(), openFull = new Set();     // places ticked for a split; full texts left open
    let listView = pref('icm.idlist', 'hadith'), onlyUnd = false, onlyUndated = false, q = '', scopeHere = false, sq = '', sres = null, sbusy = false;

    /* what a place in a chain resolves to: {st, id}; st = drop | exact | anon | user | auto | und (id 'none' = name only) */
    const stateOf = (ch, e) => {
      const occ = `${ch.aid}|${e.i}`;
      if (drops.has(occ)) return { st: 'drop' };
      if (occ in overrides) return { st: 'user', id: overrides[occ], here: true, split: /^split:/.test(overrides[occ] || '') };   // as identity.resolve: before the source's own link
      if (e.exact) return { st: 'exact', id: e.exact };
      if (e.kind === 'anon') return { st: 'anon' };
      const cd = cards[e.card];
      if (cd && cd.key in choices) return { st: 'user', id: choices[cd.key] };
      if (e.kind === 'relative' && e.cands[0] && e.cands[0].derived) return { st: 'auto', id: e.cands[0].id };
      if (cd && cd.default && !(relOnly(cd) && cd.occurrences.length > 1)) return { st: 'auto', id: cd.default };   // older runs shared one card for every «أبيه»
      return { st: 'und' };
    };
    const stOf = s => s.id === 'none' ? 'none' : s.st;
    const labelOf = (id, e, cd) => {
      if (/^split:/.test(id || '')) return (splits[id] || {}).label || id;
      if (/^name:/.test(id || '')) return id.slice(5);
      if (/^rel:/.test(id || '')) return 'نسبت در ' + wtitle(id.slice(4).split('#')[0]);
      if (/^anon:/.test(id || '')) return 'نامعلوم';
      const k = [...(e ? e.cands : []), ...(cd ? cd.candidates : [])].find(x => x.id === id); return k ? k.label : extraLabel[id] || id; };
    const isGap = e => e.exact && /^(موضع |اسم مبهم)/.test(e.name);      // the KG's placeholders: a gap, a cut, an unnamed man
    const cardState = cd => {
      const sts = cd.occurrences.map(o => stateOf(...splitOcc(o.join('|'))).st);
      return sts.includes('und') ? 'und' : sts.includes('user') || sts.includes('drop') ? 'user' : 'auto';
    };
    const counts = () => { const n = { auto: 0, user: 0, und: 0 }; Object.values(cards).forEach(cd => n[cardState(cd)]++); return n; };
    const dirty = () => JSON.stringify([choices, overrides, dates, merges, [...drops].sort(), [...dismissed].sort(), splits])
      !== JSON.stringify([saved.choices, saved.overrides, saved.dates, saved.merges, [...saved.drops].sort(), [...saved.dismissed].sort(), saved.splits]);
    /* death dates: the user's, else the databases' (fetched per id), else the work's author for a collector */
    const known = {}, asked = new Set();
    const fetchDeaths = async ids => {
      ids = [...new Set(ids)].filter(i => /^(noor|kg):\d+$/.test(i) && !asked.has(i)); if (!ids.length) return false;
      ids.forEach(i => asked.add(i));
      for (let k = 0; k < ids.length; k += 150) Object.assign(known, await api('/narrators/deaths?ids=' + ids.slice(k, k + 150).join(',') + verQ(I)).catch(() => ({})));
      return true;
    };
    const dateKey = (ch, e, s) => s.id && /^(noor|kg|split):\d+$/.test(s.id) ? s.id : `occ:${ch.aid}|${e.i}`;
    const dateOf = (ch, e) => {
      const s = stateOf(ch, e), k = dateKey(ch, e, s);
      if (dates[k]) return { death: dates[k], src: 'user', key: k };
      if (s.id && known[s.id]) return { death: known[s.id].death, src: known[s.id].source, key: k };
      const w = W.find(x => x.wid === ch.wid);
      if (e.role === 'collector' && w && w.death) return { death: [w.death, w.death], src: 'author', key: k };
      return { death: null, key: k };
    };
    const yr = d => d[0] === d[1] ? fa(Math.round(d[0])) : `${fa(Math.round(d[0]))}–${fa(Math.round(d[1]))}`;
    const SRCD = { user: 'واردشده‌ی کاربر', author: 'تاریخ درگذشت مؤلف کتاب', 'hadith-kg': 'گراف دانش' };
    /* the state of a hadith: the states of all the names in its chains */
    const hstate = wid => {
      const els = byWid[wid].flatMap(ch => ch.elems.filter(e => !isGap(e)).map(e => [ch, e]));
      const sts = els.map(([ch, e]) => stOf(stateOf(ch, e)));
      const nund = els.filter(([ch, e]) => e.kind !== 'anon' && !drops.has(`${ch.aid}|${e.i}`) && !dateOf(ch, e).death).length;
      const n = k => sts.filter(s => s === k).length;
      const [st, txt] = n('und') ? ['und', `${fa(n('und'))} بی‌تصمیم`] : sts.every(s => s === 'exact') ? ['exact', 'همه قطعی']
        : n('none') + n('anon') + n('drop') ? ['none', 'همه تصمیم‌گرفته'] : ['auto', 'همه شناخته'];
      return { sts, st, txt, nund, tip: ['exact', 'auto', 'user', 'und', 'none', 'anon', 'drop'].filter(k => n(k)).map(k => `${fa(n(k))} ${ST[k]}`).join('، ') + (nund ? `، ${fa(nund)} بی‌تاریخ` : '') };
    };

    const ST = { exact: 'قطعی (از منبع)', auto: 'خودکار (بازبینی‌نشده)', user: 'انتخاب کاربر', und: 'بی‌تصمیم', none: 'فقط نام', anon: 'نامعلوم', drop: 'حذف می‌شود (با ذخیره)' };
    const chip = (ch, e) => {
      if (isGap(e)) return el('span', { class: 'gapm', title: 'جایی که منبع راوی‌ای را نیاورده است' }, '‹' + e.name.replace('موضع ', '').replace('اسم مبهم', 'بی‌نام') + '›');
      const s = stateOf(ch, e), cd = cards[e.card], st = stOf(s), occ = `${ch.aid}|${e.i}`;
      const n = cd && !relOnly(cd) ? cd.occurrences.length : 1;
      const sub = st === 'exact' ? '✓' : st === 'und' ? 'بی‌تصمیم' : st === 'none' ? 'فقط نام' : st === 'anon' ? 'نامعلوم' : st === 'drop' ? 'حذف می‌شود' : labelOf(s.id, e, cd);
      const hit = pick && (pick.key === keyAt(ch, e) || pick.key.startsWith('id:') && s.id === pick.key.slice(3));
      return el('button', { class: `nc ${st}` + (hit ? (pick.occ === occ ? ' pk pk2' : ' pk') : ''),
        title: `${ST[st]}${s.split ? ' — شخص جداشده' : s.here ? ' — فقط همین جا' : ''}${s.id && s.id !== 'none' ? ' — ' + s.id : ''}`,
        onclick: () => { if (keyAt(ch, e)) setPick(keyAt(ch, e), occ); } },
        el('span', { class: 'n ar', title: e.raw || e.name }, st === 'exact' ? short(e.raw || e.name, 34) : e.raw || e.name, n > 1 ? el('sup', {}, ' ×' + fa(n)) : null,
          e.renamed ? el('sup', { title: 'نام و لفظ نقل این جا را شما تعیین کرده‌اید' }, ' ✎') : null),
        el('span', { class: 'id' + (st === 'exact' ? '' : ' ar') }, s.split ? '✂ ' : s.here ? '◆ ' : '', sub),
        st === 'anon' || st === 'drop' ? null : (d => el('span', { class: 'dt' + (d.death ? (d.src === 'user' ? ' u' : '') : ' no') }, d.death ? 'ف ' + yr(d.death) : 'بی‌تاریخ'))(dateOf(ch, e)));
    };
    const SRC = { noor_slots: 'سند از پیوندهای نور', kg: 'سند از گراف دانش', hadithai: 'سند از برچسب‌های راوی hadith.ai', text: 'سند خوانده‌شده از متن' };
    const strip2 = sts => el('span', { class: 'ststrip' }, sts.map(s => el('i', { class: s, title: ST[s] })));

    /* navigation; the hadith and the name are kept in the address */
    /* the chosen name as written at its place (marked in the full texts) */
    const pickName = () => { if (!pick) return null; try { const [, e] = splitOcc(pick.occ); return e.raw || e.name; } catch (_) { return null; } };
    const keepUrl = () => { const u = new URLSearchParams(); if (cur) u.set('h', cur); if (pick) u.set('n', pick.occ); setUrl(here() + (cur ? '?' + u : '')); };
    const setCur = wid => { cur = wid; if (pick && splitOcc(pick.occ)[0].wid !== wid) pick = null; keepUrl(); draw(); part2.scrollIntoView({ block: 'start', behavior: 'smooth' }); };
    const setPick = (key, occ) => {
      if (!pick || pick.key !== key) { scopeHere = false; sq = cards[key] ? cards[key].name : ''; sres = null; splitSel.clear(); }
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
        el('div', { class: 'toolbar', style: 'margin-top:0' }, el('h2', { style: 'margin:0' }, '۲. راویان ', el('button', { class: 'lnk', style: 'font-size:inherit;font-weight:700', title: 'متن کامل', onclick: () => hadithModal(cur, pickName(cur)) }, wtitle(cur))),
          el('span', { class: 'chip ' + hs.st, title: hs.tip }, hs.txt), el('span', { class: 'grow' }),
          el('button', { class: 'small', disabled: k <= 0, onclick: () => setCur(order[k - 1]) }, '→ قبلی'), el('button', { class: 'small', disabled: k >= order.length - 1, onclick: () => setCur(order[k + 1]) }, 'بعدی ←')),
        help('step4.chain'),
        el('div', { class: 'small muted' }, [w.author, w.death ? 'ف ' + fa(w.death) : '', w.tradition === 'sunni' ? 'اهل سنت' : 'شیعه', SRC[chs[0].source], hs.tip].filter(Boolean).join(' · ')),
        chs.some(ch => ch.flags.length) ? el('div', { class: 'small', style: 'color:var(--warn)', title: chs.flatMap(ch => ch.flags).join(' ') }, '⚠ سند ناقص یا نامعمول خوانده شد') : null,
        chs.map((ch, j) => el('div', { class: 'chain' }, chs.length > 1 ? el('span', { class: 'small muted' }, `سند ${fa(j + 1)}:`) : null,
          ch.elems.flatMap((e, i) => [i ? el('span', { class: 'tm' }, e.term || '←') : null, chip(ch, e)]),
          ch.speaker ? [el('span', { class: 'tm' }, ch.speaker.term || '←'), el('span', { class: 'nc spk' }, el('span', { class: 'n ar' }, ch.speaker.kind === 'prophet' ? 'پیامبر (ص)' : (ch.speaker.raw || 'امام')), el('span', { class: 'id' }, 'گوینده'))] : null,
          c.can && !STATIC ? el('button', { class: 'small', style: 'margin-inline-start:6px', title: 'افزودن راوی از هر جای متن، حذف راوی، یا جابه‌جایی مرز نام و لفظ نقل', onclick: () => chainEditor(ch) }, '✎ ویرایش سند') : null)),
        chainPending ? el('div', { class: 'small', style: 'color:var(--accent)' }, 'ویرایش سند ثبت شد؛ راویان و نامزدها دوباره ساخته می‌شوند و این صفحه به‌روز می‌شود.') : null,
        el('p', { class: 'small muted' }, pick ? 'نام برگزیده زرد است؛ جاهای دیگرش و گزینه‌هایش در بخش ۳.' : 'یک نام را بزنید تا جاهای دیگرش و گزینه‌هایش را ببینید. نام‌های «قطعی» را منبع تعیین کرده است (با زدن، کارت راوی باز می‌شود).'),
        fullDetails(cur, pickName(cur), 'سند و متن کامل این حدیث' + (pick ? ' (نام برگزیده رنگی است)' : ''), openFull, 'chain:' + cur));
    };

    /* part 3: the chosen name: all its places first, then the choice */
    const part3 = el('div', { class: 'card edcard' });
    const meta = k => [k.db === 'kg' || k.id.startsWith('kg:') ? 'گراف دانش' : 'نور', k.death ? 'ف ' + fa(k.death) : '', k.mazhab, k.grade || k.rank, k.tabaka ? 'طبقه ' + fa(k.tabaka) : ''].filter(Boolean).join(' · ');
    const snippet = o => { const [c2, e2] = splitOcc(o), x = c2.elems, i = x.indexOf(e2);
      return el('span', { class: 'chain mini' }, i > 1 ? el('span', { class: 'tm' }, '…') : null, x[i - 1] ? [chip(c2, x[i - 1]), el('span', { class: 'tm' }, e2.term || '←')] : null, chip(c2, e2),
        x[i + 1] ? [el('span', { class: 'tm' }, x[i + 1].term || '←'), chip(c2, x[i + 1])] : null, i + 2 < x.length ? el('span', { class: 'tm' }, '…') : null); };
    const occRow = (o, cur0, label) => { const [c2, e2] = splitOcc(o), s2 = stateOf(c2, e2);
      return el('div', { class: 'occ' + (o === cur0 ? ' on' : '') },
        el('button', { class: 'lnk small', onclick: () => setPick(pick.key, o) }, o === cur0 ? '● ' : '', wtitle(c2.wid), byWid[c2.wid].length > 1 ? ` (سند ${fa(byWid[c2.wid].indexOf(c2) + 1)})` : ''),
        snippet(o), el('span', { class: 'small ' + stOf(s2) + 'c' }, ST[stOf(s2)], label && s2.id && s2.id !== 'none' && s2.st !== 'exact' ? ': ' + labelOf(s2.id, e2, cards[e2.card]) : '', s2.split ? ' ✂' : s2.here ? ' ◆' : ''),
        fullDetails(c2.wid, e2.raw || e2.name, 'سند و متن کامل', openFull, o));
    };
    /* the identity of a place: a person (noor:/kg:/split:) or, with none, the node the place becomes in the graph */
    const identOf = (ch, e) => { const s = stateOf(ch, e); if (s.st === 'drop' || isGap(e)) return null; return s.id && s.id !== 'none' ? s.id : e.node_name || null; };
    const identsOf = () => {
      const m = new Map();
      I.chains.forEach(ch => ch.elems.forEach(e => {
        const id = identOf(ch, e); if (!id) return;
        if (!m.has(id)) m.set(id, { id, label: /^(noor|kg):/.test(id) ? labelOf(id, e, cards[e.card]) : /^split:/.test(id) ? labelOf(id) : (e.raw || e.name), n: 0, places: [], trad: new Set() });
        const x = m.get(id); x.n++; x.places.push(`${ch.aid}|${e.i}`); if (ch.tradition) x.trad.add(ch.tradition);
      }));
      return m;
    };
    const placesOf = id => (identsOf().get(id) || { places: [] }).places;
    const rankOf = id => ({ noor: 0, kg: 1, split: 2, name: 3, rel: 4, anon: 5 })[id.split(':')[0]] ?? 6;
    const deathOf = id => dates[id] || (known[id] && known[id].death) || null;
    /* «یکی کردن / جدا کردن»: any two identities of this project are one person; some places of one identity are another */
    const mergeSplit = (ch, e) => {
      const me = identOf(ch, e); if (!me) return null;
      const all = identsOf(), mine = all.get(me) || { id: me, label: me, n: 0, places: [], trad: new Set() };
      const others = [...all.values()].filter(x => x.id !== me).sort((a, b) => b.n - a.n);
      const curM = merges.map((m, j) => [m, j]).filter(([m]) => m.includes(me));
      const list = el('div', { class: 'mlist' }), prev = el('div');
      let mq = '';
      const preview = x => {
        const warn = [];
        if (mine.trad.size && x.trad.size && ![...mine.trad].some(t => x.trad.has(t))) warn.push('یکی در سندهای شیعه و دیگری در سندهای اهل سنت آمده است.');
        const da = deathOf(me), db = deathOf(x.id);
        if (da && db && Math.abs((da[0] + da[1]) / 2 - (db[0] + db[1]) / 2) > 30) warn.push(`سال وفات‌ها بیش از ۳۰ سال فاصله دارند (${yr(da)} و ${yr(db)}).`);
        if (I.chains.some(c2 => { const ids = c2.elems.map(e2 => identOf(c2, e2)); return ids.includes(me) && ids.includes(x.id); })) warn.push('این دو در یک سند با هم آمده‌اند؛ یکی کردنشان راوی تکراری در آن سند می‌سازد.');
        const name = [me, x.id].sort((a, b) => rankOf(a) - rankOf(b) || (a < b ? -1 : 1))[0];
        fill(prev, el('div', { class: 'help' }, el('b', {}, 'یکی کردن: '), el('span', { class: 'ar' }, mine.label), ` (${fa(mine.n)} جا) = `, el('span', { class: 'ar' }, x.label), ` (${fa(x.n)} جا)`,
          warn.map(w => el('div', { class: 'small', style: 'color:var(--warn)' }, '⚠ ' + w)),
          el('div', { class: 'small muted' }, 'در شجره هر دو یک گره می‌شوند با شناسه‌ی ', el('span', { dir: 'ltr' }, name), '. با «ذخیره» ثبت می‌شود و «واگرد» دارد.'),
          el('div', { class: 'toolbar' }, el('button', { class: 'primary small', onclick: () => { merges.push([me, x.id]); draw(); } }, 'تأیید یکی کردن'),
            /^(noor|kg):/.test(x.id) ? el('button', { class: 'small', onclick: () => narratorCard(x.id) }, 'کارت راوی') : null,
            el('button', { class: 'small', onclick: () => fill(prev) }, 'انصراف'))));
      };
      const drawM = () => fill(list, others.filter(x => !mq || x.label.includes(mq) || x.id.includes(mq)).slice(0, 25).map(x =>
        el('button', { class: 'small mcand', onclick: () => preview(x) }, el('span', { class: 'ar' }, short(x.label, 40)), el('span', { class: 'muted', dir: 'ltr' }, ' ' + short(x.id, 26)), el('span', { class: 'muted' }, ` · ${fa(x.n)} جا`))));
      drawM();
      const isSplit = /^split:/.test(me);
      const places = mine.places;
      const lab = el('input', { type: 'text', class: 'ar', placeholder: 'برچسب شخص تازه (مثلاً نام کامل‌تر)', style: 'min-width:240px' });
      const splitBox = !c.can ? null : places.length < 2 ? el('p', { class: 'small muted' }, 'این هویت فقط یک جا دارد؛ جدا کردن معنا ندارد.')
        : el('details', { class: 'splitd', open: splitSel.size > 0 }, el('summary', { class: 'small' }, `جدا کردن: کدام جاهای این هویت (${fa(places.length)} جا) شخص دیگری‌اند؟`),
          places.map(o => el('div', { class: 'occ' }, el('input', { type: 'checkbox', checked: splitSel.has(o), onchange: ev => { ev.target.checked ? splitSel.add(o) : splitSel.delete(o); } }), ' ',
            el('span', { class: 'small' }, wtitle(splitOcc(o)[0].wid)), snippet(o))),
          el('div', { class: 'toolbar' }, lab, el('button', { class: 'small primary', onclick: () => {
            const sel = places.filter(o => splitSel.has(o));
            if (!sel.length) return alert('نخست جاهایی را که شخص دیگری‌اند تیک بزنید.');
            if (sel.length === places.length) return alert('همه‌ی جاها تیک خورده است؛ برای این کار شخص دیگری را برای این نام برگزینید.');
            const n = 1 + Math.max(0, ...Object.keys(splits).map(k => +k.slice(6) || 0), ...Object.values(overrides).filter(v => /^split:/.test(v || '')).map(v => +v.slice(6) || 0));
            const id = 'split:' + n, [, e0] = splitOcc(sel[0]);
            splits[id] = { label: lab.value.trim() || (e0.raw || e0.name), from: me };
            sel.forEach(o => { overrides[o] = id; }); splitSel.clear(); draw();
          } }, 'جدا کردن جاهای تیک‌خورده')));
      const undoSplit = () => {
        Object.keys(overrides).forEach(o => { if (overrides[o] === me) delete overrides[o]; });
        delete splits[me];
        for (let j = merges.length - 1; j >= 0; j--) if (merges[j].includes(me)) merges.splice(j, 1);
        pick = null; keepUrl(); draw();
      };
      return [el('h3', {}, 'یکی کردن و جدا کردن'), help('step4.merge_split'),
        isSplit ? el('p', { class: 'small' }, `این شخص را از «${(() => { const f = (splits[me] || {}).from || ''; return (all.get(f) || {}).label || extraLabel[f] || labelOf(f) || '—'; })()}» جدا کرده‌اید. `, c.can ? el('button', { class: 'small', onclick: undoSplit }, 'بازگرداندن این جاها به هویت پیشین') : null) : null,
        curM.length ? el('div', { class: 'small' }, 'یکی‌شده با: ', curM.map(([m, j]) => { const o = m[0] === me ? m[1] : m[0];
          return el('span', { class: 'mchip' }, el('span', { class: 'ar' }, (all.get(o) || { label: labelOf(o) }).label), el('span', { class: 'muted', dir: 'ltr' }, ' ' + short(o, 26)),
            c.can ? el('button', { class: 'small', onclick: () => { merges.splice(j, 1); draw(); } }, 'واگرد') : null); })) : null,
        c.can ? [el('div', { class: 'toolbar small' }, 'یکی کردن این هویت (', el('span', { class: 'ar' }, short(mine.label, 30)), ') با: ',
          el('input', { type: 'text', placeholder: 'پالایش با نام یا شناسه', oninput: ev => { mq = ev.target.value.trim(); drawM(); } })), list, prev] : null,
        splitBox];
    };
    /* a name the source fixed: the person's places in all chains and his date */
    const drawPerson = () => {
      const id = pick.key.slice(3), occ = pick.occ, [ch, e] = splitOcc(occ), s = stateOf(ch, e), isSplit = /^split:/.test(id);
      const places = I.chains.flatMap(c2 => c2.elems.filter(e2 => stateOf(c2, e2).id === id).map(e2 => `${c2.aid}|${e2.i}`));
      fill(part3,
        el('div', { class: 'toolbar', style: 'margin-top:0' }, el('h2', { style: 'margin:0' }, '۳. ', el('span', { class: 'ar' }, isSplit ? labelOf(id) : e.raw || e.name)),
          el('span', { class: 'chip ' + (isSplit ? 'user' : 'exact') }, isSplit ? 'شخص جداشده (انتخاب کاربر)' : ST.exact), el('span', { class: 'small muted', dir: 'ltr' }, id), el('span', { class: 'grow' }),
          isSplit ? null : el('button', { class: 'small', onclick: () => narratorCard(id) }, 'کارت راوی'), el('button', { class: 'small', onclick: () => { pick = null; keepUrl(); draw(); } }, 'بستن')),
        help('step4.name'),
        el('p', { class: 'small muted' }, isSplit ? 'این جاها را شما از هویتی که داشتند جدا کرده‌اید و شخص تازه‌ای‌اند؛ در شجره یک گره‌اند.' : `${id.startsWith('kg:') ? 'گراف دانش' : 'نور'} خودش این نام را به این راوی پیوند داده است؛ تصمیمی لازم نیست.`),
        el('h3', {}, places.length > 1 ? `این شخص در ${fa(places.length)} جا آمده است` : 'این شخص فقط همین جا آمده است'),
        el('div', { class: 'occs' }, places.map(o => occRow(o, occ, false))),
        mergeSplit(ch, e),
        dateBox(ch, e, s));
      if (!isSplit) fetchDeaths([id]).then(more => { if (more) draw(); });
    };
    /* which words of this place are the name and which the transmission phrase («قرأت على» + «أبو محمد»): the user's reading
       replaces the parser's (parts.renames, keyed by place); the candidates are recomputed from the new name */
    const renameOpen = new Set();
    const renameBox = (ch, e) => {
      const occ = `${ch.aid}|${e.i}`, mine = (pdec.renames || {})[occ];
      if (e.exact || e.kind === 'anon') return null;
      const note = e.renamed ? el('span', { class: 'small userc' }, `✎ شما تعیین کرده‌اید — لفظ نقل: «${e.term || '—'}»، نام: «${e.raw || e.name}»`) : null;
      if (!c.can || STATIC) return note ? el('div', {}, note) : null;
      const open = renameOpen.has(occ);
      const head = el('div', { class: 'toolbar small', style: 'margin-top:0' }, el('span', { class: 'muted' }, `لفظ نقل: «${e.term || '—'}»`),
        el('button', { class: 'small', onclick: () => { open ? renameOpen.delete(occ) : renameOpen.add(occ); drawEd(); } }, open ? 'بستن' : '✎ نام و لفظ نقل'), note,
        mine ? el('button', { class: 'small', onclick: async () => {
          if (dirty()) return alert('نخست تغییرهای این مرحله را با «ذخیره» ثبت کنید؛ اصلاح نام نامزدها را دوباره می‌سازد.');
          await saveParts(c.pid, d => { const r = { ...d.renames }; delete r[occ]; return { renames: r }; }, `rename ${occ} reset`); reloadAt(c.pid, 'identities');
        } }, 'بازگشت به خواندن خودکار') : null);
      if (!open) return el('div', {}, head);
      const pre = e.term ? e.term.split(' ') : [], hid = pre.slice(0, Math.max(0, pre.length - 10));       // a long term is shown by its last words
      const tw = [...pre.slice(hid.length), ...(e.raw || e.name).split(' ')];
      let sel = [pre.length - hid.length, tw.length], anchor = null;
      const box = el('div', { class: 'ar pedit pick' }), prev = el('div', { class: 'small' });
      const ok = el('button', { class: 'small primary' }, 'ذخیره و محاسبه‌ی دوباره‌ی نامزدها');
      const name = () => tw.slice(sel[0], sel[1]).join(' '), term = () => [...hid, ...tw.slice(0, sel[0])].join(' ');
      const draw2 = () => {
        box.replaceChildren(...tw.flatMap((_, i) => [i ? ' ' : '', el('button', { type: 'button', class: 'tok' + (i >= sel[0] && i < sel[1] ? ' sel' : ' out') + (anchor === i ? ' anc' : ''), style: `--h:${hue(2)}`,
          onclick: () => { if (anchor === null) { anchor = i; sel = [i, i + 1]; } else { sel = [Math.min(anchor, i), Math.max(anchor, i) + 1]; anchor = null; } draw2(); } }, tw[i])]));
        prev.replaceChildren('لفظ نقل: ', el('b', { class: 'ar' }, term() || '—'), ' — نام راوی: ', el('b', { class: 'ar' }, name()),
          sel[1] < tw.length ? el('span', { class: 'muted' }, ` — واژه‌های پس از نام کنار گذاشته می‌شوند: ${tw.slice(sel[1]).join(' ')}`) : '');
        ok.disabled = anchor !== null || sel[0] >= sel[1];
      };
      ok.onclick = async () => {
        if (dirty()) return alert('نخست تغییرهای این مرحله را با «ذخیره» ثبت کنید؛ اصلاح نام نامزدها را دوباره می‌سازد.');
        await saveParts(c.pid, d => ({ renames: { ...d.renames, [occ]: { name: name(), term: term() } } }), `rename ${occ}`); reloadAt(c.pid, 'identities');
      };
      draw2();
      return el('div', {}, head, el('div', { class: 'small muted' }, 'نخستین و واپسین واژه‌ی نام راوی را بزنید؛ واژه‌های پیش از آن «لفظ نقل» می‌شوند (مثلاً در «قرأت على أبو محمد»: نام فقط «أبو محمد» است).'), box, prev, el('div', { class: 'toolbar' }, ok));
    };

    /* ---- the chain in its text («ویرایش سند»): one popup for the chain of a hadith. The list is the chain as it stands, in text
       order; a removed narrator leaves it (nothing is kept for restoring). The text below is the whole hadith (sanad + matn), where
       only the narrators' NAMES are coloured. Nothing selected: a click on a name selects that narrator; a click on a free word only
       SUGGESTS a narrator (the word is marked; more clicks on the words next to it grow the suggestion) and nothing in the chain
       changes until «افزودن» is pressed. A narrator selected: a free word moves the nearer edge of its name there (if that
       would cover another narrator, the name becomes just that word: a move); a word inside its name takes that word and the ones
       towards the nearer edge out; a word of another narrator changes nothing. No two narrators share a word, and nothing that would
       make them share one is applied (also «↺ خودکار»). Saved as parts.chain[aid]; place ids never change (an added place is 1000 +
       its position in `add`). The transmission phrase in front of a name is found by itself (the transmission words before it). */
    const TOKS = new Map();
    const tokensOf = wid => { if (!TOKS.has(wid)) TOKS.set(wid, api(`/hadith/${encodeURIComponent(wid)}/tokens?pid=${encodeURIComponent(c.pid)}`).catch(e => { TOKS.delete(wid); throw e; })); return TOKS.get(wid); };
    const ADD0 = 1000, PAGE = 400;
    const chainEditor = async (ch, selI, removeSel) => {
      if (!c.can || STATIC) return;
      modal(el('p', { class: 'muted' }, 'در حال بارگذاری…'));
      let T; try { T = await tokensOf(ch.wid); } catch (e) { modal(el('p', {}, 'متن در دسترس نیست: ' + e.message)); return; }
      const TC = T.chains.find(x => x.aid === ch.aid) || { spans: {} };
      const cur0 = TC.spans, auto = TC.auto || TC.spans;          // the reading now (the user's spans included) / the automatic reading
      const saved = (pdec.chain || {})[ch.aid] || {}, savedSpan = saved.span || {};
      const ed = { span: { ...savedSpan }, add: (saved.add || []).map(s => ({ ...s })), drop: [...(saved.drop || [])], term: Object.fromEntries(Object.entries(saved.term || {}).map(([i, v]) => [i, [...v]])) };
      const nSaved = ed.add.length;                     // a saved add keeps its id for good; an unsaved one can simply be taken back
      let sel = selI != null ? selI : null, pend = null, note = '', shown = T.n_sanad + PAGE, pm = false;     // pend: {a, b}, the words suggested as a new narrator; pm: choosing transmission phrases
      const hist = [];                                  // undo: the state of `ed` before each change
      /* every change can be undone; a chosen phrase whose words between names changed (a name moved, added or removed) goes back to all of them */
      const mutate = f => {
        hist.push(JSON.stringify(ed)); const g0 = gaps(); f(); const g1 = gaps();
        Object.keys(ed.term).forEach(i => { const x = g0[i], y = g1[i]; if (!x || !y || x.lo !== y.lo || x.a !== y.a) delete ed.term[i]; });
      };
      const base = i => (i in savedSpan ? auto[i] : cur0[i]) || null;      // a place's words when the user has not set them
      const gone = i => drops.has(`${ch.aid}|${i}`);       // removed in step 4 («حذف از سند» of an older version): removed just the same
      const places = () => {                            // the chain as it stands: removed places are not in it
        const byI = Object.fromEntries(ch.elems.map(e => [e.i, e]));
        const out = ch.elems.filter(e => e.i < ADD0 && !ed.drop.includes(e.i) && !gone(e.i)).map(e => ({ e, i: e.i, span: ed.span[e.i] || base(e.i), user: !!ed.span[e.i], coll: e.role === 'collector', idDrop: drops.has(`${ch.aid}|${e.i}`) }));
        ed.add.forEach((s, k) => { if (!ed.drop.includes(ADD0 + k) && !gone(ADD0 + k)) out.push({ e: byI[ADD0 + k] || null, i: ADD0 + k, span: s, added: true, idDrop: drops.has(`${ch.aid}|${ADD0 + k}`) }); });
        return out;
      };
      const ordered = (all = places()) => {             // the server's rule: by position in the text; a place not found stays behind the one before it
        const ps = [...all], pos = new Map(); let last = -1;
        ps.forEach((p, k) => { if (p.added) pos.set(p, [p.span.a, 1, k]); else if (p.coll) pos.set(p, [-2, 0, k]); else { if (p.span) last = p.span.a; pos.set(p, [last, 0, k]); } });
        return ps.sort((x, y) => { const a = pos.get(x), b = pos.get(y); return a[0] - b[0] || a[1] - b[1] || a[2] - b[2]; });
      };
      const live = () => places().filter(p => p.span);
      /* the server's rule (identity.fill_terms): the words before each placed name since the name before it, never back over
         the start of the matn: [lo, a); by default its phrase is all of them, a blessing («رضي الله عنه») of the name before excepted */
      const gaps = (ps = places()) => {
        const out = {}; let prev = 0;
        ps.filter(p => p.span && !p.coll).sort((x, y) => x.span.a - y.span.a).forEach(p => {
          const a = p.span.a, lo = prev < T.n_sanad && T.n_sanad <= a ? T.n_sanad : prev;
          let d = lo;
          if (d < a && T.hflag && T.hflag[d]) for (let j = d + 1; j < a; j++) if (T.tflag[j]) { d = j; break; }
          out[p.i] = { lo, d, a }; prev = p.span.b;
        });
        return out;
      };
      const phraseOf = (p, G = gaps()) => {
        const g = G[p.i], q = ed.term[p.i]; if (!g) return null;
        return q && g.lo <= q[0] && q[0] <= q[1] && q[1] <= g.a ? { t: q[0], e: q[1], pin: true } : { t: g.d, e: g.a, pin: false };
      };
      const nameOf = p => p.span ? T.words.slice(p.span.a, p.span.b).join(' ') : p.e ? p.e.raw || p.e.name : '';
      const termOf = p => { const ph = phraseOf(p); return ph ? T.words.slice(ph.t, ph.e).join(' ') : (p.e && p.e.term) || ''; };
      const owner = j => live().find(p => j >= p.span.a && j < p.span.b) || null;          // the narrator whose NAME has this word
      const overlaps = (a, b, me) => live().find(q => q.i !== me && a < q.span.b && q.span.a < b) || null;
      const inName = (j, me) => live().some(p => p.i !== me && j >= p.span.a && j < p.span.b);
      const autoTerm = (a, me) => { let t = a; while (t > 0 && a - t < 4 && T.tflag[t - 1] && !inName(t - 1, me)) t--; return t; };
      const clampT = (t, a, me) => Math.max(t, ...live().filter(p => p.i !== me && p.span.b <= a).map(p => p.span.b));   // the phrase never reaches into the place before
      const mkSpan = (a, b, s) => ({ t: s && s.span && a === s.span.a ? s.span.t : clampT(autoTerm(a, s ? s.i : null), a, s ? s.i : null), a, b });
      const selP = () => sel == null ? null : places().find(p => p.i === sel) || null;
      const setSpan = (p, s) => { if (p.added) ed.add[p.i - ADD0] = s; else ed.span[p.i] = s; };
      /* the name of `s` becomes [a, b); back to no user span when that is what it would be anyway (no false «✎ مرز تازه») */
      const setRegion = (s, a, b) => {
        const q = overlaps(a, b, s.i);
        if (q) { note = `این واژه‌ها نامِ «${nameOf(q)}» را هم می‌پوشاند؛ تغییر داده نشد.`; return false; }
        const b0 = s.added ? null : base(s.i);
        mutate(() => { if (b0 && b0.a === a && b0.b === b) delete ed.span[s.i]; else setSpan(s, mkSpan(a, b, s)); });
        return true;
      };
      /* «افزودن» of the suggestion: a narrator of those words, selected; taken back if anything goes wrong */
      const confirmNew = () => {
        const k = ed.add.length, h = hist.length, { a, b } = pend;
        try {
          if (overlaps(a, b, null)) throw new Error('taken');
          mutate(() => ed.add.push(mkSpan(a, b, null)));
          sel = ADD0 + k; pend = null;
          if (!selP()) throw new Error('not in the list');
          note = 'راوی تازه افزوده و برگزیده شد.';
        } catch (_) { ed.add.length = Math.min(ed.add.length, k); hist.length = h; sel = null; pend = null; note = 'افزودن راوی تازه در این‌جا ممکن نشد.'; }
      };
      /* the phrase of a place: [t, e) of its words between names, null: back to all of them (a choice equal to that is no choice) */
      const setPhrase = (p, v) => {
        const g = gaps()[p.i]; if (!g) return;
        if (v && v[0] === g.d && v[1] === g.a) v = null;
        if (sameJ(ed.term[p.i] || null, v)) return;
        mutate(() => { if (v) ed.term[p.i] = v; else delete ed.term[p.i]; });
      };
      /* choosing phrases: a word between two names belongs to the name after it; the first click makes the phrase that word,
         then a click beside it extends it, a click on it shortens it (from the nearer edge), its only word gives all the words back */
      const clickPhrase = j => {
        const ps = places(), G = gaps(ps), p = owner(j);
        if (p) { sel = p.i; return paint(); }
        const n = ps.find(q => G[q.i] && G[q.i].lo <= j && j < G[q.i].a);
        if (!n) { note = 'این واژه پیش از هیچ نامی نیست (پس از واپسین راوی، یا پیش از آغاز متن با نامی در متن)؛ لفظ نقلِ کسی نمی‌شود.'; return paint(); }
        sel = n.i;
        const ph = phraseOf(n, G);
        if (!ph.pin || ph.t === ph.e) setPhrase(n, [j, j + 1]);
        else if (j < ph.t || j >= ph.e) setPhrase(n, [Math.min(ph.t, j), Math.max(ph.e, j + 1)]);
        else if (ph.e - ph.t === 1) setPhrase(n, null);
        else setPhrase(n, j - ph.t < ph.e - 1 - j ? [j + 1, ph.e] : [ph.t, j]);
        paint();
      };
      const clickTok = j => {
        note = '';
        if (pm) return clickPhrase(j);
        const p = owner(j), s = selP();
        if (s) pend = null;
        if (!s) {                                                                                   // nothing selected: a click never changes the chain
          if (p) { sel = p.i; pend = null; return paint(); }                                        // a name: select it
          if (!pend) pend = { a: j, b: j + 1 };                                                     // a free word: only suggest a narrator
          else if (j >= pend.a && j < pend.b) { if (pend.b - pend.a === 1) pend = null; else if (j - pend.a < pend.b - 1 - j) pend = { a: j + 1, b: pend.b }; else pend = { a: pend.a, b: j }; }
          else {
            const na = Math.min(pend.a, j), nb = Math.max(pend.b, j + 1);
            if (overlaps(na, nb, null)) { pend = { a: j, b: j + 1 }; note = 'میان پیشنهاد و این واژه راوی دیگری است؛ پیشنهاد فقط همین واژه شد.'; }
            else pend = { a: na, b: nb };
          }
          return paint();
        }
        if (p && p.i !== s.i) { note = `این واژه از آنِ «${nameOf(p)}» است؛ نخست «لغو انتخاب» را بزنید یا نام او را کوتاه کنید.`; return paint(); }
        const sp = s.span;
        if (p) {                                                                                    // a word of its own name: shorten
          if (sp.b - sp.a === 1) sel = null;
          else if (j - sp.a < sp.b - 1 - j) setRegion(s, j + 1, sp.b); else setRegion(s, sp.a, j);
          return paint();
        }
        if (!sp) { setRegion(s, j, j + 1); return paint(); }                                        // not found in the text: here it is
        const na = Math.min(sp.a, j), nb = Math.max(sp.b, j + 1);
        if (overlaps(na, nb, s.i)) { if (setRegion(s, j, j + 1)) note = 'میان نام و این واژه راوی دیگری است؛ نام فقط همین واژه شد.'; }   // would cover another: a move
        else setRegion(s, na, nb);                                                                  // extend the nearer edge
        paint();
      };
      const HINT = 'روی نام یک راوی بزنید تا برگزیده شود. روی واژه‌ای آزاد بزنید تا راوی تازه‌ای پیشنهاد شود؛ تا «افزودن» را نزنید چیزی تغییر نمی‌کند.';
      const box = el('div', { class: 'ar pedit pick chaintext' }), bar = el('div', { class: 'toolbar small edbar' }), list = el('div', { class: 'edlist' }), tools = el('div', { class: 'toolbar small' });
      const msg = el('div', { class: 'small', style: 'color:var(--warn);min-height:1.3em' });
      box.onclick = ev => {
        const b = ev.target.closest('button'); if (!b || !box.contains(b)) return;
        if (b.classList.contains('more')) { shown += PAGE; return paint(); }
        if (b.dataset.i != null) clickTok(+b.dataset.i);
      };
      const changed = () => !sameJ(ed, { span: saved.span || {}, add: saved.add || [], drop: saved.drop || [], term: saved.term || {} });
      let asking = false;                               // the window is being closed with unsaved edits: save / discard / go on
      const askBox = el('div', {});
      const save = async () => {
        if (dirty()) return alert('نخست تغییرهای این مرحله را با «ذخیره» ثبت کنید؛ ویرایش سند راویان و نامزدها را دوباره می‌سازد.');
        ok.disabled = true;
        try {
          await saveParts(c.pid, d => {
            const chain = { ...d.chain }, out = {};
            if (Object.keys(ed.span).length) out.span = ed.span;
            if (ed.add.length) out.add = ed.add;
            if (ed.drop.length) out.drop = [...new Set(ed.drop)].sort((a, b) => a - b);
            if (Object.keys(ed.term).length) out.term = ed.term;
            if (Object.keys(out).length) chain[ch.aid] = out; else delete chain[ch.aid];
            const renames = { ...d.renames }; [...Object.keys(ed.span), ...Object.keys(ed.term)].forEach(i => delete renames[`${ch.aid}|${i}`]);   // the span or phrase wins over an older reading
            return { chain, renames };
          }, `chain ${ch.aid}`);
          reloadAt(c.pid, 'identities');
        } catch (e) { ok.disabled = false; alert(e.message); }
      };
      const ok = el('button', { class: 'primary', onclick: save }, 'ذخیره و محاسبه‌ی دوباره‌ی راویان');
      const btn = (t, f, cls, title, dis) => el('button', { type: 'button', class: 'small' + (cls ? ' ' + cls : ''), title: title || '', disabled: !!dis, onclick: ev => { ev.stopPropagation(); f(); } }, t);
      const pick = p => { sel = p.i; pend = null; note = ''; paint(); if (p.span) { const b = box.querySelector(`button[data-i='${p.span.a}']`); if (b) b.scrollIntoView({ block: 'center' }); } };
      const remove = p => { mutate(() => { if (p.added && p.i - ADD0 >= nSaved) ed.add.splice(p.i - ADD0, 1); else ed.drop.push(p.i); }); sel = null; note = `«${nameOf(p)}» از سند برداشته شد.`; paint(); };
      /* «↺ خودکار»: only when the automatic words of the place are free now */
      const revert = p => {
        const au = auto[p.i];
        if (!au) { note = 'برای این راوی خواندن خودکاری در متن نیست.'; return paint(); }
        const q = overlaps(au.a, au.b, p.i);
        if (q) { note = `بازگشت ممکن نیست: واژه‌های خودکارِ این راوی اکنون از آنِ «${nameOf(q)}» است.`; return paint(); }
        mutate(() => { delete ed.span[p.i]; }); note = ''; paint();
      };
      const rowOf = (p, k) => {
        const ph = phraseOf(p);
        const badges = [p.added ? 'جدید' : null, p.user ? '✎ مرز تازه' : null, ph && ph.pin ? '✎ لفظ نقل برگزیده' : null, !p.span && !p.coll ? 'در متن پیدا نشد' : null,
          p.coll ? 'گردآورنده (از نام کتاب)' : null].filter(Boolean);
        const acts = p.coll ? [] : [p.user && !p.added ? btn('↺ خودکار', () => revert(p), '', auto[p.i] ? 'مرز را به خواندن خودکار برگردان (اگر واژه‌هایش آزاد باشد)' : 'خواندن خودکاری در متن نیست', !auto[p.i]) : null,
          btn('✕ حذف', () => remove(p), 'danger', 'این راوی از سند برداشته می‌شود')];
        return el('div', { class: 'edrow' + (p.i === sel ? ' on' : ''), style: `--h:${hue(Math.max(k, 0))}`, onclick: p.coll ? null : () => pick(p) },
          el('span', { class: 'sw' }, fa(k + 1)), el('span', { class: 'n ar' }, nameOf(p)),
          termOf(p) ? el('span', { class: 'small muted' }, 'لفظ نقل: ', el('span', { class: 'ar' }, termOf(p))) : ph && ph.pin ? el('span', { class: 'small muted' }, 'بی‌لفظ نقل') : null, badges.map(b => el('span', { class: 'small muted' }, '· ' + b)),
          el('span', { class: 'grow' }), acts);
      };
      const paint = () => {
        if (selP() && selP().coll) sel = null;
        const ps = places(), ord = ordered(ps), lab = new Array(T.tokens.length).fill(null);   // the same objects, so a place's index in `ord` is its number
        ps.forEach(p => { if (!p.span) return; const k = ord.indexOf(p); for (let j = p.span.a; j < p.span.b; j++) lab[j] = { p, k }; });
        const G = gaps(ps), phr = new Array(T.tokens.length).fill(null);      // choosing phrases: whose phrase a word is
        if (pm) ps.forEach(p => { const ph = phraseOf(p, G); if (ph) for (let j = ph.t; j < ph.e; j++) phr[j] = { p, k: ord.indexOf(p) }; });
        const n = Math.min(T.tokens.length, Math.max(shown, ...ps.filter(p => p.span).map(p => p.span.b + 20)));
        const kids = [];
        for (let j = 0; j < n; j++) {
          if (j === T.n_sanad) kids.push(' ', el('span', { class: 'seg', title: 'از این جا متن حدیث آغاز می‌شود' }, T.n_sanad ? 'متن' : 'متن (بی‌سند)'), ' ');
          else if (j) kids.push(' ');
          const L = lab[j], cls = ['tok'];
          const P = !L && phr[j];
          if (L) { cls.push('nm'); if (L.p.i === sel) cls.push('cur'); } else cls.push('out');
          if (P) { cls.push('ph'); if (P.p.i === sel) cls.push('cur'); }
          if (!L && pend && j >= pend.a && j < pend.b) cls.push('pend');
          const H = L || P;
          kids.push(el('button', { type: 'button', class: cls.join(' '), 'data-i': j, style: `--h:${hue(H ? Math.max(H.k, 0) : 0)}`, title: L ? 'راوی: ' + nameOf(L.p) : P ? 'لفظ نقلِ ' + nameOf(P.p) : 'واژه‌ی آزاد' },
            L && j === L.p.span.a ? el('sup', { class: 'pnum' }, fa(L.k + 1)) : null, T.words[j]));
        }
        if (n < T.tokens.length) kids.push(' ', el('button', { type: 'button', class: 'small more' }, `… ${fa(T.tokens.length - n)} واژه‌ی دیگر`));
        box.replaceChildren(...kids);
        const pmBtn = btn(pm ? '✓ پایان انتخاب لفظ نقل' : '✎ انتخاب لفظ نقل', () => { pm = !pm; pend = null; note = ''; paint(); }, pm ? 'primary' : '',
          'لفظ نقلِ هر راوی (عن، حدثنا، سمعت…) به‌طور پیش‌فرض همه‌ی واژه‌های میان او و نام پیشین است؛ در این حالت می‌توانید بخشی از آن را برگزینید');
        fill(tools, pmBtn, btn('↶ واگرد', () => { if (!hist.length) return; Object.assign(ed, JSON.parse(hist.pop())); if (sel != null && !places().some(p => p.i === sel)) sel = null; note = ''; paint(); }, '', 'آخرین تغییر را پس بگیر', !hist.length),
          el('span', { class: 'small muted' }, `${fa(ord.filter(p => !p.coll).length)} راوی در سند`));
        fill(list, ord.map((p, k) => rowOf(p, k)));
        const s = selP(), sph = s && phraseOf(s, G);
        fill(bar, ...(pm ? (s && sph ? [el('span', {}, 'لفظ نقلِ ', el('b', { class: 'ar' }, nameOf(s)), ': «', el('b', { class: 'ar' }, termOf(s) || '—'), '»',
            sph.pin ? ' (برگزیده‌ی شما)' : ' (همه‌ی واژه‌های میان او و نام پیشین)', '. روی واژه‌ای از میانه بزنید تا لفظ نقل همان شود یا تا آن گسترش یابد؛ روی واژه‌ای از خودش تا کوتاه شود.'),
            sph.pin ? btn('همه‌ی میانه', () => { setPhrase(s, null); paint(); }, '', 'لفظ نقل دوباره همه‌ی واژه‌های میان او و نام پیشین شود') : null,
            sph.t !== sph.e || !sph.pin ? btn('بی‌لفظ نقل', () => { setPhrase(s, [sph.e, sph.e]); paint(); }, '', 'این راوی لفظ نقلی ندارد (مثلاً پس از «و»)') : null,
            btn('لغو انتخاب', () => { sel = null; note = ''; paint(); })]
          : s ? [el('span', {}, 'لفظ نقلِ ', el('b', { class: 'ar' }, nameOf(s)), ' را نمی‌توان برگزید: نامش در متن پیدا نشده است.'), btn('لغو انتخاب', () => { sel = null; note = ''; paint(); })]
          : [el('span', { class: 'muted' }, 'حالت انتخاب لفظ نقل: روی واژه‌ای میان دو نام بزنید تا لفظ نقلِ راویِ پس از آن همان واژه شود. با پایان این حالت، زدن روی متن دوباره نام‌ها را ویرایش می‌کند.')])
          : s ? [el('span', {}, 'راوی برگزیده: ', el('b', { class: 'ar' }, nameOf(s) || '—'), s.span
            ? '. روی واژه‌ای آزاد بزنید تا مرز نام به آن برسد (اگر راوی دیگری در میان باشد، نام فقط همان واژه می‌شود)؛ روی واژه‌ای درون نام بزنید تا آن واژه و واژه‌های میان آن و نزدیک‌ترین لبه از نام بیرون روند.'
            : '. در متن پیدا نشد؛ روی واژه‌ای آزاد بزنید تا نامش همان‌جا باشد.', s.e && s.e.exact ? ' (با تغییر مرز، پیوند قطعیِ منبع برداشته و نامزدها از نو ساخته می‌شود.)' : ''),
          btn('لغو انتخاب', () => { sel = null; note = ''; paint(); })]
          : pend ? [el('span', {}, 'راوی تازه: «', el('b', { class: 'ar' }, T.words.slice(pend.a, pend.b).join(' ')), '»؟ برای گسترش نام روی واژه‌های کنارش بزنید؛ برای کوتاه‌کردن روی واژه‌ای از خودش.'),
            btn('افزودن', () => { confirmNew(); paint(); }, 'primary'), btn('لغو', () => { pend = null; note = ''; paint(); })]
          : [el('span', { class: 'muted' }, HINT)]));
        fill(askBox, asking ? el('div', { class: 'help warnbox' }, el('b', {}, 'تغییرهای ذخیره‌نشده دارید. پیش از بستن ذخیره شوند؟'),
          el('div', { class: 'toolbar' }, btn('ذخیره و بستن', save, 'primary'), btn('دور انداختن تغییرها و بستن', closeModal, 'danger'),
            btn('ادامه‌ی ویرایش', () => { asking = false; paint(); }))) : null);
        msg.textContent = note;
        ok.disabled = !changed();
      };
      modal(el('div', {}, el('h3', { style: 'margin-top:0' }, 'ویرایش سند: ', el('span', { class: 'ar' }, wtitle(ch.wid))), help('step4.chainedit'),
        el('div', { class: 'small muted' }, 'فهرست زیر سندِ این حدیث است، به ترتیب متن. در متن فقط نامِ راویان رنگی است (هر راوی یک رنگ)؛ هیچ واژه‌ای از آنِ دو راوی نیست. راوی حذف‌شده از فهرست بیرون می‌رود. لفظ نقلِ هر راوی همه‌ی واژه‌های میان او و نام پیشین است، مگر آن‌که در «✎ انتخاب لفظ نقل» بخشی از آن را برگزینید؛ با جابه‌جایی، افزودن یا حذف نامی کنار آن، دوباره همه‌ی میانه می‌شود. با ذخیره، راویان و نامزدهایشان از نو ساخته می‌شود؛ انتخاب‌های پیشین بر جای خود می‌مانند.'),
        askBox, tools, list, bar, msg, box,
        el('div', { class: 'toolbar' }, ok, el('button', { type: 'button', onclick: tryCloseModal }, 'بستن'), chainPending ? el('span', { class: 'small muted' }, 'ویرایش پیشین این مرحله هنوز در حال محاسبه است.') : null)));
      modalGuard = () => { if (!changed()) return true; asking = true; paint(); askBox.scrollIntoView({ block: 'nearest' }); return false; };
      if (removeSel && selI != null) { const s0 = selP(); if (s0 && !s0.coll) { remove(s0); return; } }   // «حذف از سند» of step 4 opens here, the place taken out
      paint();
    };
    const drawEd = () => {
      if (!pick) { part3.hidden = true; return; }
      part3.hidden = false;
      if (pick.key.startsWith('id:')) return drawPerson();
      const cd = cards[pick.key], occ = pick.occ, [ch, e] = splitOcc(occ), s = stateOf(ch, e), n = cd.occurrences.length;
      if (s.st === 'drop') return fill(part3,
        el('div', { class: 'toolbar', style: 'margin-top:0' }, el('h2', { style: 'margin:0' }, '۳. ', el('span', { class: 'ar' }, e.raw || e.name)),
          el('span', { class: 'chip none' }, ST.drop), el('span', { class: 'grow' }), el('button', { class: 'small', onclick: () => { pick = null; keepUrl(); draw(); } }, 'بستن')),
        el('p', {}, `این راوی (در ${wtitle(ch.wid)}) با «ذخیره» از سند برداشته می‌شود: از سند، از «ویرایش سند» و از شجره بیرون می‌رود، همان‌گونه که با «✕ حذف» در «ویرایش سند».`),
        c.can ? el('button', { class: 'small', onclick: () => { drops.delete(occ); draw(); } }, 'بازگرداندن به سند') : null);
      const here = relOnly(cd) || n === 1 ? true : scopeHere || !!s.here;
      const val = here ? overrides[occ] : choices[cd.key], curId = s.id;
      const radio = (id, kids) => el('label', { class: 'cand' + (curId === id ? ' sel' : '') },
        el('input', { type: 'radio', name: 'ed', checked: curId === id, disabled: !c.can, onchange: () => setVal(cd, occ, here, id) }), ...kids);
      const candLine = k => radio(k.id, [el('span', { class: 'ar', style: 'font-size:15px' }, k.label),
        k.tier || k.derived ? el('span', { class: 'chip ' + (k.derived ? 'derived' : k.tier) }, TIER[k.derived ? 'derived' : k.tier]) : null,
        el('span', { class: 'small muted' }, `هم‌خوانی نام ${pct(k.cov || 0)}${k.ctx != null ? ' · هم‌نشین ' + fa(k.ctx) : ''} · ${fa(k.n || 0)} روایت`), el('span', { class: 'small muted' }, meta(k)),
        k.reason ? el('span', { class: 'small muted' }, k.reason) : null, k.via ? el('span', { class: 'small', style: 'color:var(--accent)' }, VIA[k.via]) : null,
        cd.default === k.id && !relOnly(cd) ? el('span', { class: 'small', style: 'color:var(--good)' }, 'انتخاب خودکار') : null,
        k.also && k.also.length ? k.also.map(o => el('span', { class: 'chip', dir: 'ltr', title: 'همان شخص در پایگاه دیگر' }, o)) : null,
        el('button', { class: 'small', onclick: ev => { ev.preventDefault(); narratorCard(k.id); } }, 'کارت راوی')]);
      const cands = relOnly(cd) ? [...e.cands] : [...cd.candidates]; e.cands.forEach(k => { if (!cands.some(x => x.id === k.id)) cands.push(k); });
      const go3 = (q3) => { sq = q3; sbusy = true; sres = null; drawEd();
        api('/narrators?q=' + encodeURIComponent(q3) + verQ(I)).then(r => { sres = r; }, err => { sres = { error: err.message }; }).finally(() => { sbusy = false; drawEd(); }); };
      const sIn = el('input', { type: 'text', value: sq, placeholder: 'نام راوی', class: 'ar', oninput: ev => { sq = ev.target.value; }, onkeydown: ev => { if (ev.key === 'Enter') go3(sq.trim()); } });
      fill(part3, 
        el('div', { class: 'toolbar', style: 'margin-top:0' }, el('h2', { style: 'margin:0' }, '۳. ', el('span', { class: 'ar' }, relOnly(cd) ? cname({ ...cd, name: e.raw || e.name }) : e.raw || e.name)),
          el('span', { class: 'chip ' + stOf(s) }, ST[stOf(s)] + (s.here ? ' — فقط همین جا' : '')),
          el('span', { class: 'small muted' }, `در ${wtitle(ch.wid)} · بهترین نامزد: ${TIER[cd.tier] || cd.tier}`), el('span', { class: 'grow' }),
          el('button', { class: 'small', onclick: () => { pick = null; keepUrl(); draw(); } }, 'بستن')),
        help('step4.name'),
        c.can && !STATIC && !isGap(e) ? el('div', { class: 'small' }, el('button', { class: 'small', onclick: () => chainEditor(ch, e.i) }, '✎ ویرایش سند در متن کامل'),
          el('span', { class: 'muted' }, ' افزودن راوی از هر جای متن (سند یا متن)، حذف این راوی، یا جابه‌جایی مرز نام و لفظ نقل روی خود متن.')) : null,
        renameBox(ch, e),
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
          val !== undefined ? el('button', { class: 'small', onclick: () => setVal(cd, occ, here, undefined) }, cd.default && !relOnly(cd) || (e.cands[0] || {}).derived ? 'بازگشت به انتخاب خودکار' : 'پاک کردن انتخاب') : null,
          el('span', { class: 'grow' }), el('button', { class: 'small', title: 'این راوی از سند برداشته می‌شود، همان‌گونه که با «✕ حذف» در «ویرایش سند»؛ با «ذخیره» اعمال می‌شود و تا آن زمان «بازگرداندن به سند» آن را پس می‌آورد. (واژه‌ی سند یا تکه‌ای از متن که نام خوانده شده است)', onclick: () => { drops.add(occ); draw(); } }, 'حذف از سند')) : null,
        mergeSplit(ch, e),
        dateBox(ch, e, s));
      if (s.id || cands.length) fetchDeaths([s.id, ...cands.map(k => k.id)].filter(Boolean)).then(more => { if (more) draw(); });
    };
    /* the death date of the person (or the name) at the chosen place */
    const dateBox = (ch, e, s) => {
      const d = dateOf(ch, e), k = d.key, isPerson = !k.startsWith('occ:'), mine = dates[k];
      const hdr = help('step4.dates');
      const kn = s.id && known[s.id] ? known[s.id] : null;
      const set = (lo, hi) => { if (lo == null && hi == null) delete dates[k]; else { lo = lo ?? hi; hi = hi ?? lo; dates[k] = [Math.min(lo, hi), Math.max(lo, hi)]; } setTimeout(draw); };   // after the input's own blur/change
      const num = v => v === '' || isNaN(+v) ? null : +v;
      const lo = el('input', { type: 'number', placeholder: 'از', style: 'width:90px', value: mine ? mine[0] : '', disabled: !c.can });
      const hi = el('input', { type: 'number', placeholder: 'تا', style: 'width:90px', value: mine ? mine[1] : '', disabled: !c.can });
      lo.onchange = hi.onchange = () => set(num(lo.value), num(hi.value));
      return [el('h3', {}, 'تاریخ وفات (اختیاری)'), hdr,
        el('p', { class: 'small' }, d.death && d.src !== 'user' ? `ف ${yr(d.death)} — ${SRCD[d.src] || d.src || 'پایگاه راویان'}. ` : '',
          kn && mine ? `پایگاه: ف ${yr(kn.death)}. ` : '',
          !d.death ? el('span', { class: 'undc' }, 'تاریخی در پایگاه‌ها نیست؛ در شجره از روی جایش در سندها (میان استاد و شاگرد) برآورد می‌شود. ') : null,
          el('span', { class: 'muted' }, isPerson ? `تاریخی که بنویسید برای «${labelOf(s.id, e, cards[e.card])}» در همه‌ی سندها به کار می‌رود${/^split:/.test(k) ? '' : ' و بر تاریخ پایگاه مقدم است'}.`
            : 'این نام به شخصی پیوند نخورده است؛ تاریخ برای همین نام (گره «فقط نام») در شجره به کار می‌رود.')),
        el('div', { class: 'toolbar' }, 'سال هجری: ', lo, hi, mine && c.can ? el('button', { class: 'small', onclick: () => set(null, null) }, 'پاک کردن') : null,
          el('span', { class: 'small muted' }, 'یک سال یا یک بازه؛ با «ذخیره» ثبت می‌شود.'))];
    };

    const status = el('div', { class: 'small' }), save = el('div', { class: 'toolbar actionbar' }), listSum = el('div', { class: 'small muted' });
    const drawStatus = () => {
      const n = counts();
      fill(status, `${fa(Object.keys(cards).length)} نام برای تصمیم: ${fa(n.auto)} خودکار، ${fa(n.user)} انتخاب کاربر، ${fa(n.und)} بی‌تصمیم. `,
        el('span', { class: 'muted' }, `${fa(I.stats.exact)} از ${fa(I.stats.elements)} جای سند را خود منبع به راوی‌ای پیوند داده است (قطعی). `),
        dec.version ? el('span', { class: 'muted' }, `آخرین تصمیم ذخیره‌شده: نسخه‌ی ${fa(dec.version)}، ${dec.user}${dec.note === 'auto' ? ' (پذیرش خودکار)' : ''}.`) : null);
      /* «ذخیره» keeps the decision and stays here; «مرحله‌ی بعد» saves what is unsaved and opens step 5, where the tree is ordered */
      const saveIt = async () => {
        const nu = counts().und;
        if (nu && !confirm(`${fa(nu)} نام بی‌تصمیم است و بدون هویت (فقط نام) به شجره می‌رود. ادامه؟`)) return false;
        await post(`/project/${c.pid}/decide/identities`, { payload: { choices, overrides, dates, merges, drops: [...drops], dismissed: [...dismissed], splits } });
        return true;
      };
      const busy = b => { b.disabled = true; return b; };
      fill(save, [c.can ? [
        el('button', { class: 'primary', disabled: !!dec.version && !dirty(), onclick: async ev => { const b = busy(ev.currentTarget); if (await saveIt()) reloadAt(c.pid, 'identities'); else b.disabled = false; } }, 'ذخیره'),
        el('button', { onclick: async ev => { const b = busy(ev.currentTarget);
          if (!dec.version || dirty()) { if (!(await saveIt())) { b.disabled = false; return; } }
          cache.clear(); go(stepUrl(c.pid, null, 'phylogeny')); } }, 'مرحله‌ی بعد: شجره ←')]
        : el('span', { class: 'muted small' }, 'فقط سازنده‌ی پروژه یا مدیر می‌تواند تصمیم بگیرد.'),
        dirty() ? el('span', { class: 'small', style: 'color:var(--warn)' }, 'تغییرهای ذخیره‌نشده') : null]);
    };

    /* suggestions from the databases, and the automatic choices, to accept or dismiss one by one */
    const VIA = { title: 'از عنوان‌های سندهای نور', strip: 'پس از حذف واژه‌های سند', relative: 'از سندهای نور برای راوی پیشین' };
    const SUG = [
      ['default', 'انتخاب‌های خودکار', 'نامزدی که نامش دست‌کم ۸۸٪ می‌خواند، همسایه‌هایش در سند او را تأیید می‌کنند و از نامزد بعدی آشکارا جلوتر است، خودکار انتخاب شده است؛ نسبتی («عن أبيه») که نور همیشه به یک نفر می‌برد نیز. اگر نپذیرید یا تغییر ندهید، همین‌ها به شجره می‌روند، اما «بازبینی‌نشده» نشان داده می‌شوند.'],
      ['strip_words', 'واژه‌های سند چسبیده به نام', 'مثلاً «عایشه ان رسول الله»: «ان رسول الله» آغاز سخن پیامبر است، نه بخشی از نام. پس از حذف آن واژه‌ها، باقی نام در پایگاه جستجو شده است.'],
      ['title_match', 'نام‌هایی که نور دقیقاً همین‌گونه نوشته', 'در سندهای پیونددارِ نور، همین نام (مثلاً «ابن عباس») بارها آمده و نور آن را به یک راوی پیوند داده است. شمار و سهم آن راوی کنار پیشنهاد آمده است.'],
      ['relative', 'نسبت‌ها (پدر، جد…)', '«عن أبيه»، «عن جده»: پدر یا جدِ راوی پیشین. پدر امامان از جدول امامان، دیگران از سندهای نور که برای همان راوی همین نسبت را به نام آورده‌اند. این پیشنهاد تنها وقتی نشان داده می‌شود که راوی پیشین همان کسی باشد که پیشنهاد بر او استوار است.'],
      ['not_a_name', 'عبارت‌هایی که نام نیستند', 'واژه‌ی سند («اقول»، «واخبرنا»، «الحافظ») یا آغاز متن («لما ثقل») که نام راوی خوانده شده است. با پذیرش، این جا از سند بیرون می‌رود.'],
      ['hidden_junk', 'واژه‌های میان نام‌های نور', 'نور میان دو راوی واژه‌هایی چون «فقد اخبرنی به» را نگه می‌دارد؛ برنامه آن‌ها را دوباره خوانده تا راویان پنهان را بیابد و گاهی واژه‌ای را نام خوانده است.'],
      ['same_person', 'یک شخص با دو شناسه', 'یک راوی گاهی هم در نور و هم در گراف دانش آمده است. در پایگاه «خام» هیچ‌چیز خودکار یکی نمی‌شود و پیوندها و جفت‌های هم‌نام با وفات نزدیک همه پیشنهادند. در یک پایگاه ادغام‌شده، جفت‌های همان نسخه از پیش یکی شده‌اند و بقیه (کم‌اطمینان‌تر یا ردشده) و شناسه‌هایی که پایگاه راویان ویکی‌مانند (ebadollah.ir/narrator) یک شخص می‌داند پیشنهاد می‌مانند.'],
      ['witness_without_chain', 'شاهدهای بی‌سند', 'سند این شاهدها جز گردآورنده کسی را نام نمی‌برد. در شجره هر کدام طریقی جداگانه از خودِ گردآورنده می‌شود؛ برای پرسش مبدأ بهتر است در مرحله‌ی ۲ کنار گذاشته شوند.']];
    const CONF = { high: 'زیاد', medium: 'متوسط', low: 'کم' };
    const OPFA = { choice: 'پذیرش', drop: 'حذف از سند', merge: 'یکی کردن', exclude: 'کنار گذاشتن شاهد' };
    const autos = () => Object.values(cards).filter(cd => cd.default && !(relOnly(cd) && cd.occurrences.length > 1)).map(cd => {
      const k = cd.candidates.find(x => x.id === cd.default) || {}, occ = cd.occurrences[0].join('|');
      return { id: 'default:' + cd.key, kind: 'default', occ, wid: splitOcc(occ)[0].wid, card: cd.key, confidence: 'high',
        proposal: { op: 'choice', id: cd.default, label: k.label || cd.default },
        evidence: k.reason || `هم‌خوانی نام ${pct(k.cov || 0)} · هم‌نشین ${fa(k.ctx || 0)}${cd.occurrences.length > 1 ? ` · ${fa(cd.occurrences.length)} جا` : ''}` };
    });
    const sugAll = () => [...autos(), ...(I.suggestions || [])];
    const isMerged = p => merges.some(m => m[0] === p[0] && m[1] === p[1] || m[0] === p[1] && m[1] === p[0]);
    const applied = sg => {
      const p = sg.proposal;
      if (p.op === 'choice') { const st = stateOf(...splitOcc(sg.occ)); return st.st === 'user' && st.id === p.id; }
      if (p.op === 'drop') return drops.has(sg.occ);
      if (p.op === 'merge') return isMerged(p.pair);
      return false;
    };
    const holds = sg => !sg.depends_on || stateOf(...splitOcc(sg.depends_on.occ)).id === sg.depends_on.id;
    const accept = async sg => {
      const p = sg.proposal;
      if (p.op === 'choice') { const [, e] = splitOcc(sg.occ); extraLabel[p.id] = p.label; return setVal(cards[sg.card || e.card], sg.occ, false, p.id); }
      if (p.op === 'drop') { drops.add(sg.occ); return draw(); }
      if (p.op === 'merge') { merges.push([...p.pair]); return draw(); }
      if (p.op === 'exclude') {
        if (dirty()) return alert('نخست تغییرهای این مرحله را با «ذخیره» ثبت کنید؛ کنار گذاشتن شاهد مرحله‌ی ۲ را دوباره اجرا می‌کند.');
        if (!confirm(`«${wtitle(p.wid)}» از شاهدهای برگزیده (مرحله‌ی ۲) کنار گذاشته شود؟ انتخاب‌های این مرحله می‌مانند.`)) return;
        const sel = await api(`/project/${c.pid}/decision/selection`);
        await post(`/project/${c.pid}/decide/selection`, { payload: { selected: (sel.selected || []).filter(w => w !== p.wid) } }); reload(c.pid);
      }
    };
    const undo = sg => {
      const p = sg.proposal;
      if (p.op === 'choice') return setVal(cards[sg.card || splitOcc(sg.occ)[1].card], sg.occ, false, undefined);
      if (p.op === 'drop') drops.delete(sg.occ);
      if (p.op === 'merge') { const j = merges.findIndex(m => m.includes(p.pair[0]) && m.includes(p.pair[1])); if (j >= 0) merges.splice(j, 1); }
      draw();
    };
    const sugBox = el('div'), sugOpen = new Set(JSON.parse(pref('icm.sugopen', '[]') || '[]'));
    let showDismissed = false;
    const sugRow = sg => {
      const ok = applied(sg), p = sg.proposal, gone = dismissed.has(sg.id);
      const where = sg.occ ? el('button', { class: 'lnk small', onclick: () => { const [ch2, e2] = splitOcc(sg.occ); if (keyAt(ch2, e2)) setPick(keyAt(ch2, e2), sg.occ); else setCur(ch2.wid); } }, wtitle(sg.wid))
        : sg.wid ? el('button', { class: 'lnk small', onclick: () => setCur(sg.wid) }, wtitle(sg.wid)) : null;
      return el('div', { class: 'sug' + (ok ? ' done' : '') + (gone ? ' gone' : '') },
        el('div', { class: 'sugw' }, where, sg.occ ? snippet(sg.occ) : null,
          p.op === 'merge' ? p.pair.map(id => el('button', { class: 'small', onclick: () => narratorCard(id) }, el('span', { dir: 'ltr' }, id))) : null),
        el('div', { class: 'suge' }, p.label ? el('b', { class: 'ar' }, p.op === 'merge' ? p.label : '← ' + p.label) : null, el('div', { class: 'small' }, sg.evidence),
          sg.via === 'wiki' && sg.person ? el('a', { class: 'small', href: `${SITE}/narrator/p/${sg.person}`, target: '_blank' }, 'صفحه در پایگاه راویان ↗') : null),
        el('div', { class: 'suga' }, el('span', { class: 'chip ' + sg.confidence, title: 'اطمینان پیشنهاد' }, 'اطمینان ' + CONF[sg.confidence]),
          !c.can ? null : ok ? [el('span', { class: 'small', style: 'color:var(--good)' }, '✓ پذیرفته'), el('button', { class: 'small', onclick: () => undo(sg) }, 'واگرد')]
            : gone ? el('button', { class: 'small', onclick: () => { dismissed.delete(sg.id); draw(); } }, 'بازگرداندن')
            : [p.op === 'exclude' && p.wid === S.seed.wid ? el('span', { class: 'small muted' }, 'حدیث مبدأ را نمی‌توان کنار گذاشت')
              : el('button', { class: 'small primary', onclick: () => accept(sg) }, OPFA[p.op]),
              el('button', { class: 'small', onclick: () => { dismissed.add(sg.id); draw(); } }, 'رد')]));
    };
    const drawSug = () => {
      const all = sugAll().filter(holds), by = {};
      all.forEach(sg => (by[sg.kind] ||= []).push(sg));
      const nGone = all.filter(sg => dismissed.has(sg.id) && !applied(sg)).length;
      const open = all.filter(sg => !applied(sg) && !dismissed.has(sg.id)).length;
      fill(sugBox, el('div', { class: 'small muted' }, all.length ? `${fa(open)} پیشنهاد باز از ${fa(all.length)}. پذیرفته‌ها مانند انتخاب شما ثبت می‌شوند؛ ردشده‌ها دیگر نشان داده نمی‌شوند. همه با «ذخیره» ثبت می‌شوند.` : 'پیشنهادی نیست.',
        nGone ? [' ', el('label', {}, el('input', { type: 'checkbox', checked: showDismissed, onchange: ev => { showDismissed = ev.target.checked; drawSug(); } }), ` نمایش ${fa(nGone)} ردشده`)] : null),
        SUG.filter(([k]) => by[k]).map(([k, title, help]) => {
          const rows = by[k].filter(sg => showDismissed || !dismissed.has(sg.id) || applied(sg)), nOpen = rows.filter(sg => !applied(sg) && !dismissed.has(sg.id)).length;
          const d = el('details', { class: 'sugd', open: sugOpen.has(k) }, el('summary', {}, title, ' ', el('span', { class: 'chip ' + (nOpen ? 'medium' : 'auto') }, nOpen ? fa(nOpen) + ' باز' : '✓'),
            el('span', { class: 'small muted' }, ` از ${fa(by[k].length)}`)),
            el('p', { class: 'small muted' }, info(help, { title: 'راهنمای این گروه' }), ' این گروه چیست؟'),
            c.can && nOpen > 1 && k !== 'witness_without_chain' && k !== 'same_person' ? el('div', { class: 'toolbar small' },
              el('button', { class: 'small', onclick: () => { rows.filter(sg => !applied(sg) && !dismissed.has(sg.id) && sg.confidence !== 'low').forEach(sg => {
                const pp = sg.proposal; if (pp.op === 'choice') { const [, e2] = splitOcc(sg.occ); extraLabel[pp.id] = pp.label; const cd2 = cards[sg.card || e2.card]; choices[cd2.key] = pp.id; delete overrides[sg.occ]; }
                else if (pp.op === 'drop') drops.add(sg.occ); }); draw(); } }, 'پذیرش همه‌ی با اطمینان زیاد و متوسط')) : null,
            rows.map(sugRow));
          d.addEventListener('toggle', () => { d.open ? sugOpen.add(k) : sugOpen.delete(k); setPref('icm.sugopen', JSON.stringify([...sugOpen])); });
          return d;
        }));
    };
    const draw = () => { drawStatus(); drawSug(); drawList(); drawChain(); drawEd(); };

    const seg = el('span', { class: 'seg' }, [['hadith', 'حدیث‌ها'], ['names', 'نام‌ها']].map(([v, t]) => {
      const b = el('button', { class: 'small', onclick: () => { listView = v; setPref('icm.idlist', v); drawList(); } }, t); b.dataset.v = v; return b; }));
    const legend = el('div', { class: 'small' }, 'راهنما: ', ['exact', 'auto', 'user', 'und', 'none', 'drop'].map(s => el('span', { class: 'nc demo ' + s }, ST[s])), el('span', { class: 'muted' }, ' ◆ = فقط همین جا · ✂ = شخص جداشده'));
    const oldRun = (I.stage_version || 1) < 3;
    main.replaceChildren(
      card(['تعیین هویت راویان', info(() =>
          [el('h3', {}, 'این گزینه‌ها از کجا می‌آیند؟'), el('ul', {},
            el('li', {}, el('b', {}, 'قطعی: '), 'منبع (نور یا گراف دانش) خودش این نام را به یک راوی پیوند داده است؛ تصمیمی لازم نیست.'),
            el('li', {}, el('b', {}, 'نامزدها: '), 'برای نام‌های دیگر، راویانی که نامشان می‌خواند از دو پایگاه آمده‌اند: فهرست راویان نور (شیعه) و رجال گراف دانش (اهل سنت). پس یک شخص ممکن است دو بار بیاید (مثلاً «زيد بن علي» یک بار از نور و یک بار از گراف دانش)؛ اگر پایگاه راویان آن دو را یکی دانسته باشد، در شجره یکی می‌شوند. اگر شخص درست در نامزدها نیست، جستجو کنید.'),
            el('li', {}, el('b', {}, 'نسبت‌ها: '), '«عن أبيه»، «حدثني أبي»، «عن جده»، «عن أخيه»… نام نیستند؛ هر کدام جداگانه در سند خودش تعیین می‌شود: پدر و جد امامان از جدول امامان، دیگران از سندهای نور که همان نسبت را برای همان راوی به نام آورده‌اند. «عن أبيه عن جده» یعنی پدرِ همان پدر.'),
            el('li', {}, el('b', {}, 'رتبه‌بندی: '), '«هم‌خوانی نام» = چه اندازه نام سند با نام راوی می‌خواند؛ «هم‌نشین» = چند همسایه‌ی این نام در همین سند، در سندهای دیگر هم استاد یا شاگرد این راوی‌اند؛ «روایت» = شمار روایت‌های او.'),
            el('li', {}, el('b', {}, 'بالا / متوسط / پایین: '), 'اطمینان به نامزد نخست. «بالا» = نام دست‌کم ۸۸٪ می‌خواند، دست‌کم یک همسایه او را تأیید می‌کند و از نامزد بعدی آشکارا جلوتر است؛ چنین نامزدی خودکار انتخاب می‌شود. «متوسط» و «پایین» خودکار انتخاب نمی‌شوند.'),
            el('li', {}, el('b', {}, 'وضع حدیث: '), '«همه قطعی» = منبع همه‌ی راویان را تعیین کرده؛ «همه شناخته» = هر نام یا قطعی است یا (خودکار یا با انتخاب کاربر) به شخصی پیوند دارد؛ «همه تصمیم‌گرفته» = برخی نام‌ها عمداً «فقط نام» یا نامعلوم‌اند؛ وگرنه شمار نام‌های بی‌تصمیم.'),
            el('li', {}, el('b', {}, 'تاریخ: '), 'زیر هر نام سال وفات او آمده است (از پایگاه راویان، گراف دانش یا تاریخ مؤلف برای گردآورنده). «بی‌تاریخ» در شجره از روی جایش در سندها برآورد می‌شود؛ اگر تاریخ را می‌دانید، در بخش ۳ بنویسید.'),
            el('li', {}, el('b', {}, 'خودکار / انتخاب کاربر / بی‌تصمیم: '), 'نامی که کسی برایش انتخاب نکرده، اگر نامزد «بالا» داشته باشد خودکار همان را می‌گیرد؛ وگرنه بی‌تصمیم است و فقط با نامش به شجره می‌رود. انتخاب شما برای همه‌ی جاهای همان نام است، مگر «فقط همین جا» را بزنید.'),
            el('li', {}, el('b', {}, 'پیشنهادها: '), 'آنچه پایگاه‌ها درباره‌ی نام‌های مانده می‌گویند (بخش «پیشنهادها»)؛ هیچ‌کدام بی‌پذیرش شما به کار نمی‌رود. انتخاب‌های خودکار نیز آنجا یک‌به‌یک برای بازبینی آمده‌اند.'),
            el('li', {}, el('b', {}, 'حذف از سند: '), 'جایی که واژه‌ی سند یا تکه‌ای از متن نام خوانده شده است؛ در شجره از سند بیرون می‌ماند.'))], { modal: true })],
        help('step4.overview'),
        el('p', { class: 'small' }, 'پایگاه راویان: ', el('b', {}, I.narrator_version ? (I.narrator_version.label_fa || (I.narrator_version.name === 'raw' ? 'خام (بدون ادغام)' : I.narrator_version.name)) : 'خام (نسخه‌ی پیش از پایگاه‌ها)'), ' ',
          el('a', { href: BASE + stepUrl(c.pid, null, 'selection'), onclick: ev => { ev.preventDefault(); go(stepUrl(c.pid, null, 'selection')); } }, '(تغییر در مرحله‌ی ۲)')),
        renPending ? el('div', { class: 'help', style: 'border-color:var(--accent)' }, 'اصلاح نام راوی ثبت شد؛ نامزدها دوباره ساخته می‌شوند و این صفحه به‌روز می‌شود.') : null,
        el('p', { class: 'muted small' }, 'یک حدیث را در بخش ۱ برگزینید؛ سندش در بخش ۲ می‌آید. یک نام را بزنید؛ در بخش ۳ نخست همه‌ی جاهای دیگر آن نام در سندهای دیگر (هر کدام با متن کامل حدیث) می‌آید و سپس گزینه‌ها، جستجو، یکی کردن و جدا کردن.'),
        oldRun && !c.readonly ? el('div', { class: 'help warnbox' }, 'نامزدهای این اجرا با نسخه‌ی پیشینِ برنامه ساخته شده‌اند: «عن أبيه / عن جده» یک نام شمرده می‌شد (و در همه‌ی سندها امام باقر گرفته می‌شد)، و برخی نام‌ها به راوی نادرست می‌خوردند (مثلاً «علی بن ابراهیم» به محمد بن یحیی). برنامه‌ی تازه هر نسبت را در سند خودش و از روی سندهای نور تعیین می‌کند. ',
          c.can ? el('button', { class: 'small', onclick: async () => { await post(`/project/${c.pid}/rerun`, {}); reload(c.pid); } }, 'اجرای دوباره با برنامه‌ی تازه') : 'سازنده‌ی پروژه می‌تواند آن را دوباره اجرا کند.',
          el('span', { class: 'small muted' }, ' انتخاب‌های ذخیره‌شده می‌مانند؛ شجره دوباره ساخته می‌شود و باید دوباره تأیید شود.')) : null,
        status, legend,
        I.flags.map(f => el('div', { class: 'small', style: 'color:var(--warn)' }, `${wtitle(f.ref)}: ${flagText(f)}`))),
      card(['پیشنهادها از پایگاه‌ها', info('برای نام‌هایی که منبع به راوی پیوند نداده، آنچه پایگاه‌ها (نور، گراف دانش، پایگاه راویان) می‌گویند. هر پیشنهاد با دلیلش آمده است؛ هیچ‌کدام بی‌پذیرش شما به کار نمی‌رود.')], help('step4.suggestions'), sugBox),
      el('div', { class: 'card' }, el('div', { class: 'toolbar', style: 'margin-top:0' }, el('h2', { style: 'margin:0' }, '۱. حدیث‌ها'), seg,
          el('label', { class: 'small' }, el('input', { type: 'checkbox', onchange: ev => { onlyUnd = ev.target.checked; drawList(); } }), ' فقط با نام بی‌تصمیم'),
          el('label', { class: 'small' }, el('input', { type: 'checkbox', onchange: ev => { onlyUndated = ev.target.checked; drawList(); } }), ' فقط با راوی بی‌تاریخ'),
          el('input', { type: 'text', placeholder: 'پالایش با نام راوی یا کتاب', oninput: ev => { q = ev.target.value.trim(); drawList(); } })),
        help('step4.list'), listSum, listBox),
      part2, part3, save);
    let auto = false;
    if (!cur) { cur = order.find(w => hstate(w).st === 'und') || order[0] || null; auto = !!cur; }
    draw();
    if (cur && !auto) (pick ? part3 : part2).scrollIntoView({ block: 'start' });
    if (await fetchDeaths(I.chains.flatMap(ch => ch.elems.map(e => stateOf(ch, e).id)).filter(Boolean))) draw();
  },

  /* ----- 5: phylogeny ----- */
  async phylogeny(main, c) {
    const G = await stage(c.pid, c.runId, 'graph').catch(e => e.status === 404 ? null : Promise.reject(e));
    const dec = await api(`/project/${c.pid}/decision/phylogeny`);
    if (!G) {                 // the tree is built when the user orders it, from the step-4 decisions saved so far
      const gh = P.project.graph_hash, building = !!P.job;
      return main.replaceChildren(card('ساخت شجره', help('step5.build'),
        el('p', { class: 'small' }, 'راویان در مرحله‌ی ۴ ذخیره شده‌اند، اما شجره هنوز ساخته نشده است. شجره با همان تصمیم‌های ذخیره‌شده ساخته می‌شود؛ اگر هنوز می‌خواهید چیزی را عوض کنید، ',
          el('a', { href: BASE + stepUrl(c.pid, null, 'identities'), onclick: ev => { ev.preventDefault(); go(stepUrl(c.pid, null, 'identities')); } }, 'به مرحله‌ی ۴ برگردید'), '.'),
        building ? el('p', { class: 'small muted' }, 'در حال کار… این صفحه خودش به‌روز می‌شود.')
          : c.can && !c.readonly && gh ? el('div', { class: 'toolbar' }, el('button', { class: 'primary', onclick: async ev => { ev.currentTarget.disabled = true;
            await post(`/project/${c.pid}/decide/phylogeny`, { payload: { build: gh, dates: dec.dates || {}, chain_edits: dec.chain_edits || {}, preset: dec.preset || null, compute: dec.compute || 'browser' } });
            reloadAt(c.pid, 'phylogeny'); } }, 'ساخت شجره'))
          : el('span', { class: 'muted' }, c.readonly ? 'در این نسخه شجره ساخته نشده بود.' : 'فقط سازنده‌ی پروژه یا مدیر می‌تواند شجره را بسازد.')));
    }
    /* the parts not yet confirmed (step 3) and the seed's segmentation (step 2): a reminder, never a block */
    const [A5, S5, pd5] = await Promise.all([stage(c.pid, c.runId, 'align').catch(() => null), stage(c.pid, c.runId, 'search').catch(() => null), api(`/project/${c.pid}/decision/parts`)]);
    const unconf = A5 && A5.clauses.length && A5.clauses[0].key ? A5.clauses.filter(cl => partStatus(cl, pd5, A5.witnesses).k !== 'auto') : [];
    const seedUnconf = S5 && !seedPartsState(S5, pd5).confirmed;
    const toStep = (k, t) => el('a', { href: BASE + stepUrl(c.pid, null, k), onclick: ev => { ev.preventDefault(); go(stepUrl(c.pid, null, k)); } }, t);
    const partsNote = !c.readonly && (unconf.length || seedUnconf) ? el('p', { class: 'small', style: 'color:var(--warn)' }, 'فرازبندی هنوز کامل تأیید نشده است: ',
      seedUnconf ? ['بریدن حدیث مبدأ به بخش‌ها (', toStep('selection', 'مرحله‌ی ۲'), ')', unconf.length ? '، و ' : ''] : null,
      unconf.length ? [`${fa(unconf.length)} از ${fa(A5.clauses.length)} بخش در شاهدها (`, toStep('alignment', 'مرحله‌ی ۳'), ')'] : null,
      '. تحلیل با پیشنهادهای خودکار همان‌ها انجام می‌شود.') : null;
    const preset = el('select', { 'aria-label': 'دقت اجرا' }, ...['', 'quick', 'standard', 'thorough'].map(p => el('option', { value: p, selected: (dec.preset || '') === p }, p ? { quick: 'سریع', standard: 'معمول', thorough: 'دقیق' }[p] : 'خودکار (بر پایه‌ی اندازه)')));
    const computeSel = el('select', { 'aria-label': 'جای محاسبه' }, ...[['browser', 'در مرورگر من (سریع، موازی)'], ['helper', 'رایانه‌ی کمکی مدیر'], ['server', 'روی سرور (کند، نزدیک یک ساعت)']].map(([v, t]) => el('option', { value: v, selected: (dec.compute || 'browser') === v }, t)));
    const undated = Object.entries(G.nodes).filter(([n, i]) => (!i.date_source || i.date_source === 'interpolated') && !/^anon:/.test(n) && !/^(موضع |اسم مبهم|من ذكره)/.test(i.name || ''));
    /* links into step 4: a hadith, or a name at one of its places */
    const wtitle = {}; G.attestations.forEach(a => { wtitle[a.wid] = a.collection; });
    const occIn = (n, aid) => (G.nodes[n] && G.nodes[n].occ || []).find(o => !aid || o.startsWith(aid + '|'));
    const step4 = (wid, occ, txt) => { const q = new URLSearchParams({ h: wid }); if (occ) q.set('n', occ); const href = BASE + stepUrl(c.pid, null, 'identities') + '?' + q;
      return el('a', { href, onclick: ev => { ev.preventDefault(); go(href.slice(BASE.length)); } }, txt); };
    const nodeLink = (n, aid) => { const o = occIn(n, aid), a = G.attestations.find(x => (!aid || x.id === aid) && x.sanad.includes(n));
      const wid = o ? o.slice(0, o.lastIndexOf('|')).split('#')[0] : a && a.wid;
      const nm = G.narrators[n] ? G.narrators[n].name : n, d = G.narrators[n] && G.narrators[n].death, und = !G.nodes[n] || !G.nodes[n].date_source || G.nodes[n].date_source === 'interpolated';
      return el('span', { class: 'ar' }, wid ? step4(wid, o, nm) : nm, d && !und ? el('span', { class: 'muted' }, ` (ف ${yearTxt(d)})`) : null); };
    const flagItem = f => {
      const wid = f.wid || String(f.ref).split('#')[0], aid = String(f.ref).includes('#') ? f.ref : null, t = f.title || wtitle[wid] || wid;
      const what = f.kind === 'chronology' && f.nodes ? [nodeLink(f.nodes[0], aid), ' استادِ ', nodeLink(f.nodes[1], aid), ' آمده است.']
        : f.kind === 'repeated_node' && f.nodes ? [nodeLink(f.nodes[0], aid), ' دو بار آمده است.']
        : f.kind === 'chronology' ? [f.message] : [];
      return el('li', {}, step4(wid, null, t), el('span', { class: 'small muted wid', dir: 'ltr' }, wid), what.length ? el('div', { class: 'small' }, ...what) : null);
    };
    const groups = {}; G.flags.forEach(f => (groups[f.kind] ||= []).push(f));
    const flagsCard = G.flags.length ? card('هشدارها', help('step5.warnings'), el('p', { class: 'small muted' }, 'هر هشدار را باز کنید تا حدیث‌ها و راویان آن را ببینید؛ نام حدیث یا راوی شما را به همان جا در مرحله‌ی ۴ می‌برد.'),
      Object.entries(groups).map(([k, fs]) => el('details', { class: 'flagd' }, el('summary', {}, FLAG_FA[k] || k, el('span', { class: 'chip medium' }, fa(fs.length))),
        el('ul', { class: 'flags' }, fs.map(flagItem))))) : null;
    main.replaceChildren(...[
      card('شجره‌ی انتقال ثبت‌شده', help('step5.graph'), el('p', { class: 'muted small' }, `${fa(G.stats.narrators)} راوی، ${fa(G.stats.attestations)} سند، ${fa(G.stats.witnesses)} شاهد؛ ${fa(G.stats.dated)} راوی تاریخ دارند، ${fa(G.stats.name_only)} فقط نام‌اند.`,
          (G.dropped || []).length ? [` ${fa(G.dropped.length)} جای سند که نام نبود حذف شد: `, G.dropped.map((o, j) => [j ? '، ' : '', step4(o.slice(0, o.lastIndexOf('|')).split('#')[0], o, fa(j + 1))])] : null),
        graphSvg(G)),
      flagsCard,
      undated.length ? card('راویان بی‌تاریخ', help('step5.undated'), el('p', { class: 'small' }, `${fa(undated.length)} راوی تاریخ وفات ندارند و تاریخشان از روی جایشان در سندها برآورد شده است. `,
        'هر نام را بزنید تا در ', el('b', {}, 'مرحله‌ی ۴'), ' باز شود و در بخش ۳ «تاریخ وفات» را بنویسید.'),
        el('div', { class: 'small undl' }, undated.map(([n]) => nodeLink(n)))) : null,
      card('تأیید', help('step5.confirm'), partsNote, c.can ? el('div', { class: 'toolbar' }, 'دقت اجرا: ', preset, ' محاسبه: ', computeSel,
        el('button', { class: 'primary', onclick: async () => {
          await post(`/project/${c.pid}/decide/phylogeny`, { payload: { confirmed: P.project.graph_hash, dates: dec.dates || {}, preset: preset.value || null, compute: computeSel.value } }); if (computeSel.value === 'browser') sessionStorage.setItem('icm_auto', c.pid); reload(c.pid);
        } }, 'تأیید شجره و اجرای تحلیل')) : el('span', { class: 'muted' }, 'فقط سازنده‌ی پروژه یا مدیر می‌تواند تأیید کند.'),
        P.project.step === 'done' ? el('p', { class: 'small muted' }, 'تحلیل این شجره انجام شده است.') : null)].filter(Boolean));
  },

  /* ----- 6: analysis ----- */
  async analysis(main, c) {
    /* the current run predates the latest decisions: its results belong to the previous version */
    const cr = P.runs.find(r => r.run === c.p.current_run);
    if (!c.readonly && cr && Object.keys(P.versions).some(s => ((cr.versions || {})[s] || 0) !== P.versions[s])) return pendingPanel(main, c, cr);
    if (c.p.status === 'awaiting' && !c.readonly) return awaitingPanel(main, c);
    /* the results as sub-tabs (results.js): the verdict, the tree with the model's results on it, the texts, ... */
    const [R, D, G, A, S] = await Promise.all(['results', 'viz', 'graph', 'align', 'search'].map(x => stage(c.pid, c.runId, x).catch(() => null)));
    if (!R || !D) { main.replaceChildren(card('نتیجه', el('p', { class: 'muted' }, 'نتیجه‌ی این اجرا در دسترس نیست.'))); return; }
    const texts = {};
    if (S) [S.seed, ...S.candidates].forEach(x => [x.wid, ...(x.copies || [])].forEach(w => { texts[w] = { matn: x.matn, marks: x.marks || [] }; }));
    const qs = new URLSearchParams(query());
    const host = el('div');
    const top = () => { const hd = document.querySelector('header.top'); host.style.setProperty('--rv-top', (hd && getComputedStyle(hd).position === 'sticky' ? hd.getBoundingClientRect().height : 0) + 'px'); };
    top(); addEventListener('resize', top);
    main.replaceChildren(host);
    const align = A ? { seed: A.seed, clauses: A.clauses, grids: A.grids, witnesses: A.witnesses.map(w => w.wid), segments: A.segments } : null;
    ICMResults.mount(host, D, { R, units: A && A.units, texts, clauses: S ? S.clauses.map(x => x.text) : null, nodes: G && G.nodes, narratorCard, align,
      openHadith: wid => hadithModal(wid), help: popover, tab: qs.get('tab'), standalone: STATIC ? null : `${API}/project/${c.pid}/runs/${c.runId}/viewer`,
      goStep4: occ => { const q = new URLSearchParams({ h: occ.slice(0, occ.lastIndexOf('|')).split('#')[0], n: occ }); go(stepUrl(c.pid, null, 'identities') + '?' + q); },
      onTab: t => { const u = new URLSearchParams(query()); u.set('tab', t); setUrl(here() + '?' + u); } });
  },
};

function pendingPanel(main, c, prev) {
  const failed = c.p.status === 'failed' && !P.job;
  main.replaceChildren(card('تحلیل نسخه‌ی تازه', el('p', {}, failed ? 'اجرای نسخه‌ی تازه به خطا خورد (پیام بالای صفحه).' : 'نسخه‌ی تازه ساخته شد و در حال آماده شدن است؛ این صفحه خودش به‌روز می‌شود.'),
    prev.stages && prev.stages.infer ? el('p', { class: 'small muted' }, 'نتیجه‌ی نسخه‌ی پیشین اینجا نشان داده نمی‌شود؛ ',
      el('a', { href: BASE + stepUrl(c.pid, prev.run, 'analysis'), onclick: ev => { ev.preventDefault(); go(stepUrl(c.pid, prev.run, 'analysis')); } }, 'دیدن آن (فقط‌خواندنی)'), '.') : null));
}

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
  /* the desktop app: icm://run?... opens the installed app (registered by `python3 -m icm app install`), which fetches the input, asks to run, then asks to upload */
  const desk = el('div', { class: 'small muted' });
  const jobToken = async () => post(`/project/${c.pid}/desktop-token`, {});
  const deskStart = async () => {
    desk.textContent = '';
    try {
      const t = await jobToken();
      const link = `icm://run?server=${encodeURIComponent(location.origin + BASE)}&pid=${encodeURIComponent(c.pid)}&token=${encodeURIComponent(t.token)}`;
      let left = false; const onHide = () => { if (document.hidden) left = true; };
      document.addEventListener('visibilitychange', onHide); window.addEventListener('blur', () => { left = true; }, { once: true });
      location.href = link;
      setTimeout(() => { document.removeEventListener('visibilitychange', onHide);
        desk.replaceChildren(left ? 'برنامه باز شد؛ در آن «شروع محاسبه» را بزنید و پس از پایان، «فرستادن نتیجه» را. این صفحه خودش به‌روز می‌شود.'
          : 'برنامه باز نشد. اگر آن را نصب نکرده‌اید، راهنمای نصب (چند دقیقه، یک بار): github.com/quran-hadith-studies/icm/blob/main/APP.md — یا پایین، «دریافت فایل کار» را بزنید.'); }, 2500);
    } catch (e) { desk.textContent = 'خطا: ' + e.message; }
  };
  const jobFile = async () => {
    try {
      const [t, inp] = await Promise.all([jobToken(), api(`/project/${c.pid}/compute-input`)]);
      const blob = new Blob([JSON.stringify({ server: location.origin + BASE, pid: c.pid, token: t.token, input: inp })], { type: 'application/json' });
      const a = el('a', { href: URL.createObjectURL(blob), download: c.pid + '.icmjob' }); document.body.append(a); a.click(); a.remove();
      desk.textContent = 'فایل کار را با برنامه باز کنید (دوبار کلیک)، یا: python3 -m icm app open ' + c.pid + '.icmjob';
    } catch (e) { desk.textContent = 'خطا: ' + e.message; }
  };
  const upFile = el('input', { type: 'file', accept: '.json,.icmresult', hidden: true, onchange: async ev => {
    const f = ev.target.files[0]; if (!f) return;
    try { const res = JSON.parse(await f.text()); await post(`/project/${c.pid}/compute-result`, { key: res.key, results: res.results, viz: res.viz, provenance: res.provenance || {} }); desk.textContent = 'نتیجه ثبت شد.'; setTimeout(() => reload(c.pid), 1500); }
    catch (e) { desk.textContent = 'بارگذاری نشد: ' + e.message; }
  } });
  const deskBox = c.can ? el('div', { class: 'toolbar' }, el('button', { onclick: deskStart }, 'اجرا با برنامه‌ی رومیزی'), el('button', { onclick: jobFile }, 'دریافت فایل کار'),
    el('button', { onclick: () => upFile.click() }, 'بارگذاری فایل نتیجه'), upFile) : null;
  if (c.can) {                                    /* the app (or a helper) may finish while this page is open */
    const watch = async () => { const q = await api('/project/' + c.pid).catch(() => null); if (q && q.project.status !== 'awaiting') { cache.clear(); render(); return; } timer = setTimeout(watch, 4000); };
    timer = setTimeout(watch, 4000);
  }
  main.replaceChildren(card('محاسبه‌ی مدل', el('p', {}, aw.compute === 'helper' ? 'این پروژه منتظر رایانه‌ی کمکی مدیر است. اگر نمی‌خواهید صبر کنید، می‌توانید همین‌جا در مرورگر خودتان اجرا کنید.' : 'تحلیل در مرورگر شما اجرا می‌شود و نتیجه به سرور فرستاده می‌شود. صفحه را نبندید.'),
    el('p', { class: 'small muted' }, `دقت: ${aw.preset}. نامزدهای منبع جداگانه و موازی محاسبه می‌شوند؛ بار اول حدود ۱۵ مگابایت (مفسر پایتون و numpy) بارگیری می‌شود.`),
    el('div', { class: 'toolbar' }, btn, c.can ? el('button', { onclick: async () => {
      if (!confirm('محاسبه روی سرور نزدیک یک ساعت طول می‌کشد. ادامه؟')) return;
      const d = await api(`/project/${c.pid}/decision/phylogeny`);
      await post(`/project/${c.pid}/decide/phylogeny`, { payload: { confirmed: P.project.graph_hash, dates: d.dates || {}, preset: d.preset || null, compute: 'server' } }); reload(c.pid);
    } }, 'اجرا روی سرور (کند)') : null), deskBox, desk, bar, msg));
  if (c.can && sessionStorage.getItem('icm_auto') === c.pid) { sessionStorage.removeItem('icm_auto'); start(); }
}

/* ---------- pieces ---------- */
const fill = (node, ...kids) => node.replaceChildren(...kids.flat(2).filter(x => x != null && x !== false));
function card(title, ...kids) { return el('div', { class: 'card' }, el('h2', {}, title), ...kids); }
/* the full explanation of a section, folded under its title (help_fa.js); inner = the entry's body alone (for a popover) */
function help(key, inner) {
  if (!window.ICMHelp) return null;
  if (!inner) return ICMHelp.block(key);
  const b = ICMHelp.block(key); if (!b) return null;
  b.open = true; b.querySelector('summary').remove(); return b;
}
/* full texts: the record of /api/hadith (sanad and matn without markup) and the hadith's page on the site */
const FULL = new Map();
function hadithFull(wid) {
  if (!FULL.has(wid)) FULL.set(wid, api('/hadith/' + encodeURIComponent(wid)).catch(e => { FULL.delete(wid); throw e; }));
  return FULL.get(wid);
}
const hadithUrl = wid => (/^v02:\d+$/.test(wid || '') ? `${SITE}/h/i/${wid.slice(4)}` : null);
const hadithLink = wid => { const u = hadithUrl(wid); return u ? el('a', { href: u, target: '_blank', class: 'small', title: 'صفحه‌ی این حدیث در جستجوی احادیث' }, 'صفحه‌ی حدیث ↗') : null; };
const DIA = '[\\u0610-\\u061A\\u064B-\\u065F\\u0670\\u06D6-\\u06ED\\u0640\\u200c]*';
const LET = { 'ا': '[اأإآٱ]', 'أ': '[اأإآٱ]', 'إ': '[اأإآٱ]', 'آ': '[اأإآٱ]', 'ی': '[يیىئ]', 'ي': '[يیىئ]', 'ى': '[يیى]', 'ک': '[كک]', 'ك': '[كک]', 'ه': '[هةۀھ]', 'ة': '[هةۀ]', 'ۀ': '[هةۀ]' };
function nameRe(name) {        // a name as written: any diacritics, hamza and ya/kaf/ha forms
  const ws = String(name || '').replace(/[()«»"،,.:؛]/g, ' ').trim().split(/\s+/).filter(w => w.length > 1);
  if (!ws.length) return null;
  return new RegExp(ws.map(w => [...w].map(ch => (LET[ch] || ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) + DIA).join('')).join('[\\s\\u200c]+'), 'g');
}
function highlight(text, name, cls) {
  const out = el('div', { class: cls || 'ar matn' }), re = name ? nameRe(name) : null;
  text = String(text || '');
  if (!re) { out.textContent = text; return out; }
  let at = 0, m;
  while ((m = re.exec(text))) { if (!m[0]) { re.lastIndex++; continue; } out.append(text.slice(at, m.index), el('mark', {}, m[0])); at = m.index + m[0].length; }
  out.append(text.slice(at));
  return out;
}
function fullTextBlock(h, hl) {
  return el('div', { class: 'fulltext' },
    el('div', { class: 'small muted' }, [h.title, h.author, h.death ? 'ف ' + fa(h.death) : '', h.vol ? 'ج ' + fa(h.vol) : '', h.page ? 'ص ' + fa(h.page) : '', h.wid].filter(Boolean).join(' · '), ' ', hadithLink(h.wid)),
    h.sanad ? highlight(h.sanad, hl, 'ar matn sanad') : null, highlight(h.matn, hl, 'ar matn'),
    h.translation ? el('details', { class: 'small' }, el('summary', {}, 'ترجمه'), el('div', {}, h.translation)) : null);
}
async function hadithModal(wid, hl) {
  modal(el('p', { class: 'muted' }, 'در حال بارگذاری…'));
  try { modal(fullTextBlock(await hadithFull(wid), hl)); } catch (e) { modal(el('p', {}, 'متن در دسترس نیست: ' + e.message)); }
}
/* a folded «full text»: loaded when first opened; `state` (a Set) remembers it open across redraws */
function fullDetails(wid, hl, label, state, key) {
  const body = el('div', { class: 'small muted' }, 'در حال بارگذاری…');
  const d = el('details', { class: 'fullt' }, el('summary', { class: 'small' }, label || 'متن کامل'), body);
  const load = () => { if (d._done) return; d._done = true; hadithFull(wid).then(h => body.replaceWith(fullTextBlock(h, hl)), e => { body.textContent = 'متن در دسترس نیست: ' + e.message; }); };
  d.addEventListener('toggle', () => { if (state) { d.open ? state.add(key) : state.delete(key); } if (d.open) load(); });
  if (state && state.has(key)) { d.open = true; load(); }
  return d;
}
/* (i): a small round button next to a title or a control; its help opens beside it (long help in the modal) */
function info(content, opts = {}) {
  return el('button', { class: 'info', type: 'button', title: opts.title || 'راهنما', 'aria-label': opts.title || 'راهنما', 'aria-haspopup': 'dialog', onclick: ev => {
    ev.preventDefault(); ev.stopPropagation();
    const body = typeof content === 'function' ? content() : content;
    if (opts.modal) { closePop(); modal(el('div', { class: 'helpbox' }, body)); } else popover(ev.currentTarget, body);
  } }, 'i');
}
/* a long explanation behind (i) next to the title it explains: opens in the modal */
const helpOf = (title, ...kids) => info(() => [el('h3', {}, title), ...kids], { modal: true, title });
let POP = null;
function popover(anchor, body) {
  const again = POP && POP._a === anchor;
  closePop();
  if (again) return;
  POP = el('div', { class: 'pop', role: 'dialog' }, typeof body === 'string' ? el('p', {}, body) : body);
  POP._a = anchor; document.body.append(POP);
  const r = anchor.getBoundingClientRect(), pr = POP.getBoundingClientRect();
  const x = Math.min(Math.max(8, r.right - pr.width), innerWidth - pr.width - 8);     // right-to-left: right edges meet
  let y = r.bottom + 6;
  if (y + pr.height > innerHeight - 8 && r.top - pr.height - 6 > 8) y = r.top - pr.height - 6;
  POP.style.left = x + 'px'; POP.style.top = y + 'px';
}
function closePop() { if (POP) { POP.remove(); POP = null; } }
document.addEventListener('click', ev => { if (POP && !POP.contains(ev.target)) closePop(); });
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') closePop(); });
addEventListener('resize', closePop);
document.addEventListener('scroll', ev => { if (POP && !POP.contains(ev.target)) closePop(); }, true);
function hadithBlock(h, colored) {
  return el('div', {}, el('div', { class: 'small muted' }, [h.title, h.author, h.death ? 'ف ' + fa(h.death) : '', h.tradition === 'sunni' ? 'اهل سنت' : 'شیعه', h.wid].filter(Boolean).join(' · '), ' ', hadithLink(h.wid)),
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
  return helpOf('بخش‌ها چگونه ساخته شدند؟',
    el('p', {}, 'متن حدیث مبدأ خودکار و فقط با قاعده‌های نگارشی به بخش‌ها بریده شده است (بی‌آنکه معنا فهمیده شود): در نشانه‌های نگارشی (، ؛ . : ؟)، پیش از «و/ف» که اسمی با «ال» را آغاز می‌کند (آغاز هر بند در فهرست‌هایی مثل «و المکذب… و التارک…»)، بخش‌های بلندتر از ۱۲ واژه در نزدیک‌ترین حرف ربط (و، ف، ثم، الذی، من…) به میانه، و بخش یک‌واژه‌ای به همسایه‌اش چسبانده می‌شود. پیش از بریدن، اعراب حذف و املا یکسان می‌شود (ي→ی، ك→ک، ة→ه، أ/إ/آ→ا).'),
    el('p', {}, 'چرا بخش‌به‌بخش؟ روایت‌های یک حدیث بندها را جابه‌جا، کوتاه یا بلند می‌کنند. هر بخش جداگانه جستجو و جداگانه مقایسه می‌شود، پس روایتی که فقط یک بند را دارد یا ترتیب دیگری دارد هم پیدا و درست سنجیده می‌شود. چون بریدن خودکار است، گاهی دو بند در یک بخش می‌مانند یا یک بند دو تکه می‌شود؛ با «ویرایش فرازبندی» می‌توانید بخش‌ها را درست و تأیید کنید، و در مرحله‌ی ۳ جای هر بخش را در هر شاهد.'));
}
/* ---------- the parts (فرازبندی): the user corrects and confirms the automatic segmentation ----------
   One decision, `parts` (complete state each time): the seed's parts (`seed`, token spans; null = automatic) and the
   spans confirmed (`seed_confirmed`), extra parts dropped/added, `slots` (part key -> wid -> [a, b) | absent | unknown,
   in the tokens of /runs/<run>/words/<wid>) and `confirmed` (part key -> the witnesses it was checked in). */
const sameJ = (a, b) => JSON.stringify(a == null ? null : a) === JSON.stringify(b == null ? null : b);
const partsBase = d => ({ seed: d.seed ?? null, seed_confirmed: d.seed_confirmed ?? null, extras_added: d.extras_added || [],
  extras_dropped: d.extras_dropped || [], slots: d.slots || {}, confirmed: d.confirmed || {},
  words: d.words || {}, unit_cls: d.unit_cls || {}, units: d.units || {}, renames: d.renames || {}, chain: d.chain || {} });   // words, unit_cls, units: step 3 per-word corrections and edits of the variation units; renames, chain: step 4 name/term spans, places added/removed/re-bounded
async function saveParts(pid, patch, note) {
  const d = partsBase(await api(`/project/${pid}/decision/parts`));
  await post(`/project/${pid}/decide/parts`, { payload: { ...d, ...patch(d) }, note: note || '' });
}
/* after a segmentation decision: stay on the step (the project's own step may be another one) */
function reloadAt(pid, step) { cache.clear(); closeModal(); setUrl(BASE + stepUrl(pid, null, step), true); render(); }
function closeModal() { modalGuard = null; document.getElementById('modal').hidden = true; }      // closes for good (after a save, or «discard»)
const WORDS = new Map();
function wordsOf(pid, run, wid) {
  const k = `${pid}|${run}|${wid}`;
  if (!WORDS.has(k)) WORDS.set(k, api(`/project/${pid}/runs/${run}/words/${encodeURIComponent(wid)}`).catch(e => { WORDS.delete(k); throw e; }));
  return WORDS.get(k);
}
/* the raw spelling of token i (several tokens of one raw word: the normalised token for the later ones) */
const rawTok = (W, i) => { const o = W.offsets[i]; if (!o || (i && W.offsets[i - 1] && W.offsets[i - 1][0] === o[0])) return W.tokens[i]; return Array.from(W.matn).slice(o[0], o[1]).join(''); };
const rawSpan = (W, a, b) => { const o = W.offsets; return o[a] && o[b - 1] ? Array.from(W.matn).slice(o[a][0], o[b - 1][1]).join('') : W.tokens.slice(a, b).join(' '); };

/* the state of the seed's parts: by whom (automatic / the user), confirmed, and whether a newer decision is still running */
function seedPartsState(S, pd) {
  const cur = S.clauses.map(x => x.span), user = S.clauses_by === 'user';
  return { cur, user, confirmed: sameJ(pd.seed_confirmed, cur), pending: !sameJ(pd.seed, user ? cur : null) };
}
function seedPartsBox(S, c, pd, step) {
  const st = seedPartsState(S, pd), box = el('div', { class: 'partsbox' }), ed = el('div');
  const chip = st.pending ? el('span', { class: 'chip running' }, 'فرازبندی تازه ثبت شد؛ جستجو دوباره اجرا می‌شود')
    : st.user ? el('span', { class: 'chip user' }, 'فرازبندی دستی' + (st.confirmed ? '، تأییدشده' : ''))
    : st.confirmed ? el('span', { class: 'chip auto' }, 'فرازبندی خودکار، تأییدشده') : el('span', { class: 'chip und' }, 'فرازبندی خودکار، تأییدنشده');
  const hai = /hadith\.ai/.test(S.auto_by || '');
  const from = !st.user ? el('span', { class: 'small muted', title: hai ? 'مرزهای بخش‌بندی hadith.ai برای همین حدیث و مرزهای قاعده‌های نگارشی، هر دو؛ گوینده‌ی هر بخش از hadith.ai' : 'بریدن با قاعده‌های نگارشی' },
    hai ? 'پیشنهاد: بخش‌بندی hadith.ai و قاعده‌های نگارشی' : 'پیشنهاد: قاعده‌های نگارشی') : null;
  const by = pd.user && pd.created ? el('span', { class: 'small muted' }, `${pd.user}، ${fa(pd.created.slice(0, 10))}`) : null;
  const editBtn = c.can && !st.pending ? el('button', { class: 'small', onclick: () => { editBtn.disabled = true; seedPartsEditor(ed, S, c, () => { ed.replaceChildren(); editBtn.disabled = false; }, step); } }, 'ویرایش فرازبندی') : null;
  const okBtn = c.can && !st.pending && !st.confirmed ? el('button', { class: 'small primary', onclick: async () => {
    await saveParts(c.pid, () => ({ seed: st.user ? st.cur : null, seed_confirmed: st.cur }), 'seed parts confirmed'); reloadAt(c.pid, step);
  } }, 'تأیید همین فرازبندی') : null;
  box.append(el('div', { class: 'toolbar' }, el('b', {}, 'فرازبندی حدیث مبدأ: '), chip, from, by, editBtn, okBtn, info(() => help('parts.seed', true) || 'بخش‌ها را می‌توانید جابه‌جا، یکی یا جدا کنید.', { modal: true })), ed);
  return box;
}
/* the seed's text as words: a word click takes it in or out of the parts, a click between two words cuts or joins */
async function seedPartsEditor(box, S, c, done, step) {
  box.replaceChildren(el('p', { class: 'muted small' }, 'در حال بارگذاری…'));
  let W;
  try { W = await wordsOf(c.pid, c.runId, S.seed.wid); } catch (e) { box.replaceChildren(el('p', {}, 'خطا: ' + e.message)); return; }
  const n = W.tokens.length, inP = new Array(n).fill(false), cut = new Array(n).fill(false);
  const load = sp => { inP.fill(false); cut.fill(false); sp.forEach(([a, b], k) => { for (let i = a; i < b; i++) inP[i] = true; if (k && sp[k - 1][1] === a) cut[a] = true; }); };
  const spans = () => { const out = []; let s0 = -1;
    for (let i = 0; i <= n; i++) { if (s0 >= 0 && (i === n || !inP[i] || cut[i])) { out.push([s0, i]); s0 = -1; } if (i < n && inP[i] && s0 < 0) s0 = i; }
    return out; };
  load(S.clauses.map(x => x.span));
  const text = el('div', { class: 'ar pedit' }), list = el('div'), msg = el('div', { class: 'small', style: 'color:var(--warn)' });
  const draw = () => {
    const sp = spans(), lab = new Array(n).fill(-1);
    sp.forEach(([a, b], k) => { for (let i = a; i < b; i++) lab[i] = k; });
    const kids = [];
    for (let i = 0; i < n; i++) {
      if (i && inP[i - 1] && inP[i]) kids.push(el('button', { class: 'cut' + (cut[i] ? ' on' : ''), type: 'button', title: cut[i] ? 'یکی کردن دو بخش' : 'بریدن بخش در اینجا', onclick: () => { cut[i] = !cut[i]; draw(); } }, '|'));
      else if (i) kids.push(' ');
      if (lab[i] >= 0 && (i === 0 || lab[i - 1] !== lab[i])) kids.push(el('span', { class: 'sw pnum', style: `--h:${hue(lab[i])}` }, fa(lab[i] + 1)));
      kids.push(el('button', { class: 'tok' + (lab[i] < 0 ? ' out' : ''), type: 'button', style: lab[i] >= 0 ? `--h:${hue(lab[i])}` : null,
        title: lab[i] < 0 ? 'بیرون از بخش‌ها؛ برای افزودن بزنید' : 'برای بیرون بردن از بخش بزنید', onclick: () => { inP[i] = !inP[i]; draw(); } }, rawTok(W, i)));
    }
    text.replaceChildren(...kids);
    list.replaceChildren(el('table', { class: 'small' }, el('tr', {}, el('th', {}, 'بخش'), el('th', {}, 'متن'), el('th', {}, 'واژه‌ها')),
      sp.map(([a, b], k) => el('tr', {}, el('td', {}, swatch(k)), el('td', { class: 'ar' }, rawSpan(W, a, b)), el('td', {}, fa(b - a))))));
    msg.textContent = sp.length ? (sp.some(([a, b]) => b - a < 2) ? 'بخش یک‌واژه‌ای جستجوی کم‌اعتمادی دارد.' : '') : 'دست‌کم یک بخش لازم است.';
  };
  draw();
  const save = el('button', { class: 'primary', onclick: async () => {
    const sp = spans();
    if (!sp.length) return;
    const same = sameJ(sp, S.clauses.map(x => x.span));
    if (!same && !confirm('با این فرازبندی جستجو دوباره اجرا می‌شود و هم‌ترازی و مرحله‌های بعد از نو ساخته می‌شوند (شاهدهای انتخاب‌شده می‌مانند؛ تیک‌های «در جستجو» برداشته می‌شوند). ادامه؟')) return;
    save.disabled = true;
    try { await saveParts(c.pid, () => ({ seed: sp, seed_confirmed: sp }), same ? 'seed parts confirmed' : 'seed parts edited'); reloadAt(c.pid, step); }
    catch (e) { save.disabled = false; msg.textContent = 'خطا: ' + e.message; }
  } }, 'ذخیره و تأیید');
  box.replaceChildren(el('div', { class: 'edbox' },
    el('p', { class: 'small' }, 'واژه‌ای را بزنید تا از بخش‌ها بیرون رود (خط‌خورده) یا به آن‌ها برگردد. میان دو واژه‌ی یک بخش «|» را بزنید تا بخش آنجا بریده شود؛ «|» پررنگ را بزنید تا دو بخش یکی شوند.'),
    text, list, msg,
    el('div', { class: 'toolbar' }, save,
      el('button', { onclick: () => { load(S.auto_clauses || S.clauses.map(x => x.span)); draw(); } }, 'بازگشت به پیشنهاد خودکار'),
      el('button', { onclick: done }, 'انصراف'))));
}

/* a witness's text as words; the first and the last word clicked are a part's new slot */
function wordPicker(W, opts) {
  const box = el('div', { class: 'ar pedit pick' }), lab = new Array(W.tokens.length).fill(-1);
  (opts.others || []).forEach(([ci, a, b]) => { for (let i = Math.max(a, 0); i < Math.min(b, lab.length); i++) lab[i] = ci; });
  let sel = opts.span ? [...opts.span] : null, anchor = null;
  const draw = () => box.replaceChildren(...W.tokens.flatMap((_, i) => {
    const on = sel && i >= sel[0] && i < sel[1];
    return [i ? ' ' : '', el('button', { type: 'button', class: 'tok' + (on ? ' sel' : lab[i] >= 0 ? ' oth' : ' out') + (anchor === i ? ' anc' : ''),
      style: `--h:${hue(on ? opts.hue : lab[i] >= 0 ? lab[i] : opts.hue)}`, title: lab[i] >= 0 && !on ? 'بخش ' + fa(lab[i] + 1) : '',
      onclick: () => { if (anchor === null) { anchor = i; sel = [i, i + 1]; } else { sel = [Math.min(anchor, i), Math.max(anchor, i) + 1]; anchor = null; } draw(); opts.onPick(sel); } }, rawTok(W, i))];
  }));
  draw();
  return el('div', {}, el('div', { class: 'small muted' }, 'نخستین و واپسین واژه را بزنید.', W.truncated ? ' (متن تا ۱۵۰۰ نویسه‌ی نخست آمده است.)' : ''), box);
}

/* step 3: where each part is in each witness, corrected and confirmed part by part */
function partStatus(cl, pd, W) {
  const conf = (pd.confirmed || {})[cl.key];
  if (!conf) return { k: 'und', t: 'تأییدنشده' };
  const fresh = W.filter(w => !conf.includes(w.wid)).length;
  return fresh ? { k: 'medium', t: `تأییدشده؛ ${fa(fresh)} شاهد تازه بازبینی نشده` } : { k: 'auto', t: 'تأییدشده' };
}
const PART_KIND = cl => cl.kind === 'seed' ? 'بخش حدیث مبدأ' : cl.by === 'user' ? 'افزوده‌ی شما' : `افزوده‌ی خودکار، در ${fa(cl.support)} شاهد`;
function alignPending(A, pd) {        /* corrections saved but not in this alignment yet (it is being recomputed) */
  const pin = A.parts_in || {}, f = k => (pd[k] && (Array.isArray(pd[k]) ? pd[k].length : Object.keys(pd[k]).length)) ? pd[k] : null;
  return ['extras_dropped', 'extras_added', 'slots', 'words', 'unit_cls', 'units'].some(k => !sameJ(f(k), pin[k] || null));
}
/* a word of the alignment grid: «افزوده» (an addition of this witness) or «متفاوت» (a variant of another witness's word).
   A word is named "<word>#<n>", the n-th time it stands in the witness's row (icm/align.py row_keys), as columns move between runs. */
const rowKeys = (cols, wid) => { const seen = {}, out = []; cols.forEach((col, j) => { const t = col[wid]; if (t != null) { seen[t] = (seen[t] || 0) + 1; out.push([j, `${t}#${seen[t]}`]); } }); return out; };
function wordModal(cl, w, j, g, A, c, pd) {
  const key = cl.key, wid = w.wid, rk = rowKeys(g.columns, wid), mine = (rk.find(([jj]) => jj === j) || [])[1], tok = g.columns[j][wid];
  const refW = g.kind === 'seed' && wid !== A.seed ? A.seed : g.witnesses.find(x => x !== wid);
  const here = refTok(g, j, A.seed), now = (((pd.words || {})[key] || {})[wid] || {})[mine];
  const myEd = ((pd.words || {})[key] || {})[wid] || {};
  const others = rk.filter(([jj, k]) => jj !== j && myEd[k] !== 'add').map(([jj]) => jj);       // a word the user called an addition does not bound the others: it is placed in front of or behind the column it is in
  const lo = Math.max(-1, ...others.filter(jj => jj < j)), hi = Math.min(g.columns.length, ...others.filter(jj => jj > j));
  /* the other witness's words nearby that this one has no word opposite (and that keep this witness's words in order) */
  const near = refW ? rowKeys(g.columns, refW).filter(([jj]) => jj !== j && jj > lo && jj < hi && Math.abs(jj - j) <= 3 && g.columns[jj][wid] == null) : [];
  /* «افزوده است؛ واژه‌ی بعدی/پیشین به‌جای …»: the word is an addition and its neighbour in the row (the one that does not belong
     opposite nothing: the same word as the seed's, or with no seed word opposite) takes this column. Both are saved at once. */
  const hereKey = here == null ? null : (rowKeys(g.columns, refW).find(([jj]) => jj === j) || [])[1], at = rk.findIndex(([jj]) => jj === j);
  const combo = hereKey && refW && mkey(tok) !== mkey(here) ? [['بعدی', rk[at + 1]], ['پیشین', rk[at - 1]]].filter(([, n]) => n && (mkey(g.columns[n[0]][wid]) === mkey(here) || g.columns[n[0]][refW] == null)) : [];
  const save = async (x, extra) => {
    await saveParts(c.pid, d => {
      const words = JSON.parse(JSON.stringify(d.words || {})), m = ((words[key] ||= {})[wid] ||= {});
      if (x == null) delete m[mine]; else m[mine] = x;
      Object.assign(m, extra || {});
      if (!Object.keys(m).length) delete words[key][wid];
      if (!Object.keys(words[key]).length) delete words[key];
      return { words };
    }, `word ${mine} of ${wid} in ${key}`);
    reloadAt(c.pid, 'alignment');
  };
  const nowTxt = now === 'add' ? 'افزوده' : now ? `متفاوت (واژه‌ی «${(now.to[1] || '').replace(/#\d+$/, '')}» در ${now.to[0] === A.seed ? 'حدیث مبدأ' : 'شاهد دیگر'})` : 'خودکار';
  modal(el('div', {},
    el('div', { class: 'toolbar', style: 'margin-top:0' }, swatch(cl.i), el('b', {}, w.title || wid), el('span', { class: 'ar', style: 'font-size:20px' }, tok)),
    el('p', { class: 'small' }, here == null ? 'این واژه در جایی است که حدیث مبدأ واژه‌ای ندارد.' : here === tok ? 'این واژه با واژه‌ی همان جای حدیث مبدأ یکی است.' : `هم‌ترازی خودکار این واژه را به‌جای «${here}» گذاشته است.`,
      ' تصمیم کنونی: ', el('b', {}, nowTxt), '.'),
    el('p', { class: 'small muted' }, '«افزوده»: این شاهد واژه‌ای را افزوده است؛ در ستونی جدا می‌ایستد و (اگر ستون پیشین واژه‌ای داشت) برای این شاهد «افتاده» حساب می‌شود، و مدل شجره آن را افزایش می‌شمارد. ',
      '«متفاوت»: همان جایگاه است و این شاهد به‌جای واژه‌ای دیگر آورده؛ واژه را به ستون آن واژه ببرید. اگر این شاهد دو واژه به‌جای یک واژه آورده، دکمه‌ی «افزوده است؛ واژه‌ی … به‌جای …» یکی را افزوده می‌شمارد و دیگری را به جای واژه‌ی حدیث مبدأ می‌نشاند. «و» جدا جزء واژه‌ی بعدی است. تغییر با ذخیره ثبت می‌شود و هم‌ترازی دوباره ساخته می‌شود (مرحله‌ی ۵ دوباره تأیید می‌خواهد).'),
    el('div', { class: 'toolbar' },
      el('button', { class: 'primary', disabled: now === 'add', onclick: () => save('add') }, 'افزوده است'),
      combo.map(([nm, [jn, kn]]) => el('button', { class: 'primary', title: 'این واژه افزوده است و واژه‌ی همسایه‌اش جای آن را در ستون واژه‌ی حدیث مبدأ می‌گیرد',
        onclick: () => save('add', { [kn]: { to: [refW, hereKey] } }) }, `افزوده است؛ واژه‌ی ${nm} «${g.columns[jn][wid]}» به‌جای «${here}»`)),
      near.map(([jj, k]) => el('button', { disabled: !!now && now.to && now.to[1] === k, onclick: () => save({ to: [refW, k] }) }, `متفاوت: به‌جای «${g.columns[jj][refW]}»`)),
      now ? el('button', { onclick: () => save(null) }, 'خودکار') : null),
    here == null && !near.length ? el('p', { class: 'small muted' }, 'واژه‌ای نزدیک نیست که بتوان این واژه را به‌جای آن شمرد.') : null));
}
function partsReviewCard(A, S, c, pd, cname) {
  const W = A.witnesses, keys = A.clauses.map(cl => cl.key), st = seedPartsState(S, pd);
  const nOk = A.clauses.filter(cl => partStatus(cl, pd, W).k === 'auto').length, pending = alignPending(A, pd);
  const count = cl => { const n = { present: 0, absent: 0, unclear: 0, user: 0 };
    W.forEach(w => { const sg = A.segments[`${cl.i}|${w.wid}`] || {}; n[sg.state] = (n[sg.state] || 0) + 1; if (sg.user) n.user++; }); return n; };
  const step2 = el('a', { href: BASE + stepUrl(c.pid, null, 'selection'), onclick: ev => { ev.preventDefault(); go(stepUrl(c.pid, null, 'selection')); } }, 'مرحله‌ی ۲');
  return card('فرازبندی بخش‌ها در شاهدها: اصلاح و تأیید', help('step3.parts'),
    el('p', { class: 'small' }, 'بخش‌ها و جای هر بخش در متن هر شاهد خودکار پیدا شده‌اند و پیشنهادند. هر بخش را بازبینی کنید: جای آن را در شاهدی که اشتباه است درست کنید، یا بگویید آن شاهد این بخش را ندارد یا نامعلوم است؛ سپس بخش را تأیید کنید. ',
      'بریدن خود حدیث مبدأ به بخش‌ها در ', step2, ' است', st.confirmed ? ' (تأییدشده).' : ' (هنوز تأیید نشده).'),
    pending ? el('div', { class: 'help', style: 'border-color:var(--accent)' }, 'تغییرهای ثبت‌شده در حال اعمال‌اند؛ هم‌ترازی دوباره ساخته می‌شود و این صفحه به‌روز می‌شود.') : null,
    el('div', { class: 'toolbar' }, el('b', {}, `${fa(nOk)} از ${fa(A.clauses.length)} بخش تأییدشده`),
      c.can ? el('button', { class: 'small', disabled: nOk === A.clauses.length, onclick: async () => {
        await saveParts(c.pid, d => ({ confirmed: { ...d.confirmed, ...Object.fromEntries(keys.map(k => [k, W.map(w => w.wid)])) } }), 'all parts confirmed'); reloadAt(c.pid, 'alignment');
      } }, 'تأیید همه‌ی بخش‌ها همان‌گونه که هست') : null,
      c.can ? el('button', { class: 'small', onclick: () => addPartModal(A, c) }, 'افزودن بخش تازه از متن یک شاهد') : null),
    el('table', { class: 'small parts' }, el('tr', {}, ...['بخش', 'متن', 'گونه', 'در شاهدها', 'وضع', ''].map(t => el('th', {}, t))),
      A.clauses.map(cl => { const n = count(cl), ps = partStatus(cl, pd, W);
        return el('tr', {}, el('td', {}, swatch(cl.i)), el('td', { class: 'ar' }, short(cname(cl), 70)), el('td', {}, PART_KIND(cl)),
          el('td', {}, `دارد ${fa(n.present)} · ندارد ${fa(n.absent)} · نامعلوم ${fa(n.unclear)}`, n.user ? el('span', { class: 'userc' }, ` · ${fa(n.user)} دستی`) : null),
          el('td', {}, el('span', { class: 'chip ' + ps.k }, ps.t)),
          el('td', {}, el('button', { class: 'small', onclick: () => partModal(cl, A, S, c, pd, cname) }, c.can ? 'بازبینی و اصلاح' : 'دیدن')));
      })));
}
/* one part in every witness: its slot (automatic or the user's), and the user's corrections before they are saved */
function partModal(cl, A, S, c, pd, cname) {
  const W = A.witnesses, key = cl.key, mine = { ...((pd.slots || {})[key] || {}) }, pend = {};
  const ps = partStatus(cl, pd, W), open = new Set();
  const body = el('tbody'), foot = el('div', { class: 'toolbar' });
  const rows = () => body.replaceChildren(...W.flatMap((w, i) => {
    const sg = A.segments[`${cl.i}|${w.wid}`] || {}, own = w.wid === A.seed && cl.kind === 'seed';
    const v = w.wid in pend ? pend[w.wid] : mine[w.wid];
    const changed = w.wid in pend;
    const stTxt = own ? 'خودِ حدیث مبدأ' : v === 'absent' ? 'ندارد' : v === 'unknown' ? 'نامعلوم' : Array.isArray(v) ? 'دارد' : sg.state === 'present' ? 'دارد' : sg.state === 'absent' ? (w.marker != null ? 'خلاصه‌شده (نامعلوم)' : 'ندارد') : 'نامعلوم';
    const who = own ? '' : v != null || (sg.user && !changed) ? 'دستی' : 'خودکار';
    const txt = el('span', { class: 'ar' }, Array.isArray(v) && changed ? '…' : v === 'absent' || v === 'unknown' ? '—' : (sg.tokens || []).join(' '));
    if (Array.isArray(v) && changed) wordsOf(c.pid, c.runId, w.wid).then(Wd => { txt.textContent = rawSpan(Wd, v[0], v[1]); }).catch(() => {});
    const set = x => { pend[w.wid] = x; open.delete(w.wid); rows(); drawFoot(); };
    const acts = !c.can || own ? [] : [
      el('button', { class: 'small', onclick: () => { open.has(w.wid) ? open.delete(w.wid) : open.add(w.wid); rows(); } }, open.has(w.wid) ? 'بستن' : 'جای دیگر…'),
      el('button', { class: 'small', onclick: () => set('absent') }, 'ندارد'), el('button', { class: 'small', onclick: () => set('unknown') }, 'نامعلوم'),
      v != null || sg.user ? el('button', { class: 'small', onclick: () => set(null), title: 'پیشنهاد خودکار به کار رود' }, 'خودکار') : null];
    const tr = el('tr', { class: changed ? 'on' : '' }, el('td', { class: 'nm', onclick: () => hadithModal(w.wid) }, el('span', { class: 'dot ' + w.cls }), `${fa(i)}. ${w.title || w.wid}`),
      el('td', {}, stTxt, who ? el('span', { class: 'small ' + (who === 'دستی' ? 'userc' : 'muted') }, ` (${who}${changed ? '، ذخیره‌نشده' : ''})`) : null), el('td', {}, txt), el('td', {}, ...acts));
    if (!open.has(w.wid)) return [tr];
    const cell = el('td', { colspan: 4 }, el('span', { class: 'small muted' }, 'در حال بارگذاری…'));
    wordsOf(c.pid, c.runId, w.wid).then(Wd => {
      const w0 = Wd.window[0], others = A.clauses.filter(x => x.i !== cl.i).map(x => { const s2 = A.segments[`${x.i}|${w.wid}`]; return s2 && s2.span ? [x.i, s2.span[0] + w0, s2.span[1] + w0] : null; }).filter(Boolean);
      const cur = Array.isArray(v) ? v : sg.span ? [sg.span[0] + w0, sg.span[1] + w0] : null;
      let picked = null;
      const ok = el('button', { class: 'small primary', disabled: true, onclick: () => picked && set(picked) }, 'همین جا');
      cell.replaceChildren(wordPicker(Wd, { span: cur, others, hue: cl.i, onPick: sp => { picked = sp; ok.disabled = false; } }), el('div', { class: 'toolbar' }, ok));
    }, e => { cell.textContent = 'متن در دسترس نیست: ' + e.message; });
    return [tr, el('tr', {}, cell)];
  }));
  const apply = (d, confirmIt) => {
    const slots = { ...d.slots }, m = { ...(slots[key] || {}) };
    Object.entries(pend).forEach(([w, x]) => { if (x == null) delete m[w]; else m[w] = x; });
    if (Object.keys(m).length) slots[key] = m; else delete slots[key];
    const confirmed = { ...d.confirmed };
    if (confirmIt === true) confirmed[key] = W.map(w => w.wid); else if (confirmIt === false) delete confirmed[key];
    return { slots, confirmed };
  };
  const drawFoot = () => {
    const n = Object.keys(pend).length;
    fill(foot, !c.can ? null : [
      el('button', { class: 'primary', onclick: async () => { await saveParts(c.pid, d => apply(d, true), `part ${key} confirmed`); reloadAt(c.pid, 'alignment'); } }, n ? `ذخیره‌ی ${fa(n)} تغییر و تأیید این بخش` : 'تأیید این بخش'),
      n ? el('button', { onclick: async () => { await saveParts(c.pid, d => apply(d, null), `part ${key} edited`); reloadAt(c.pid, 'alignment'); } }, 'ذخیره بی‌تأیید') : null,
      ps.k !== 'und' ? el('button', { onclick: async () => { await saveParts(c.pid, d => apply(d, false), `part ${key} unconfirmed`); reloadAt(c.pid, 'alignment'); } }, 'برداشتن تأیید') : null,
      cl.kind === 'extra' ? el('button', { class: 'small', style: 'color:var(--bad)', onclick: async () => {
        if (!confirm('این بخش افزوده کنار گذاشته شود؟ (واحدهای آن از ورودی مدل بیرون می‌روند)')) return;
        await saveParts(c.pid, d => {
          const slots = { ...d.slots }, confirmed = { ...d.confirmed }; delete slots[key]; delete confirmed[key];
          return cl.by === 'user' ? { slots, confirmed, extras_added: d.extras_added.filter(a => !(cl.source && a.wid === cl.source.wid && sameJ(a.span, cl.source.span))) }
            : { slots, confirmed, extras_dropped: [...new Set([...d.extras_dropped, key])] };
        }, `part ${key} dropped`); reloadAt(c.pid, 'alignment');
      } }, 'کنار گذاشتن این بخش') : null]);
  };
  rows(); drawFoot();
  modal(el('div', { class: 'partmodal' },
    el('div', { class: 'toolbar', style: 'margin-top:0' }, swatch(cl.i), el('b', { class: 'ar' }, cname(cl)), el('span', { class: 'chip partial' }, PART_KIND(cl)), el('span', { class: 'chip ' + ps.k }, ps.t)),
    el('p', { class: 'small muted' }, 'برای هر شاهد: آیا این بخش را دارد و کجای متنش. اگر جایی درست نیست «جای دیگر…» را بزنید و در متن شاهد نخستین و واپسین واژه‌ی بخش را برگزینید، یا «ندارد» / «نامعلوم» را بزنید. «ندارد» یعنی شاهد این بخش را نیاورده (حتی اگر متنش خلاصه شده باشد)؛ «نامعلوم» یعنی از متن این شاهد نمی‌توان گفت. تغییرها با «ذخیره» ثبت می‌شوند و هم‌ترازی دوباره ساخته می‌شود.'),
    el('div', { class: 'grid-wrap', style: 'max-height:62vh' }, el('table', { class: 'small prow' }, el('thead', {}, el('tr', {}, ...['شاهد', 'وضع', 'متن بخش در این شاهد', ''].map(t => el('th', {}, t)))), body)),
    foot));
}
/* a new part cut from one witness's text (an extra part the automatic search missed) */
function addPartModal(A, c) {
  const sel = el('select', {}, el('option', { value: '' }, 'شاهد را برگزینید…'), A.witnesses.map((w, i) => el('option', { value: w.wid }, `${fa(i)}. ${w.title || w.wid}`)));
  const box = el('div'), msg = el('div', { class: 'small' });
  let picked = null;
  const ok = el('button', { class: 'primary', disabled: true, onclick: async () => {
    ok.disabled = true;
    await saveParts(c.pid, d => ({ extras_added: [...d.extras_added, { wid: sel.value, span: picked }] }), 'part added'); reloadAt(c.pid, 'alignment');
  } }, 'افزودن این تکه به‌عنوان بخش');
  sel.onchange = async () => {
    picked = null; ok.disabled = true; box.replaceChildren(el('p', { class: 'small muted' }, 'در حال بارگذاری…'));
    if (!sel.value) { box.replaceChildren(); return; }
    const Wd = await wordsOf(c.pid, c.runId, sel.value), w0 = Wd.window[0];
    const others = A.clauses.map(x => { const s2 = A.segments[`${x.i}|${sel.value}`]; return s2 && s2.span ? [x.i, s2.span[0] + w0, s2.span[1] + w0] : null; }).filter(Boolean);
    box.replaceChildren(wordPicker(Wd, { others, hue: A.clauses.length, onPick: sp => { picked = sp; ok.disabled = false; msg.textContent = `${fa(sp[1] - sp[0])} واژه برگزیده شد.`; } }));
  };
  modal(el('div', {}, el('h3', {}, 'افزودن بخش تازه'),
    el('p', { class: 'small muted' }, 'تکه‌ای از متن یک شاهد که در حدیث مبدأ نیست و سامانه آن را بخش نشمرده است. پس از افزودن، جای آن در شاهدهای دیگر خودکار پیدا می‌شود و می‌توانید آن را مانند بخش‌های دیگر اصلاح و تأیید کنید. واژه‌های رنگی جای بخش‌های موجودند.'),
    sel, box, msg, el('div', { class: 'toolbar' }, ok)));
}

/* ---------- alignment helpers ---------- */
const PARTICLES = new Set('لا ما ان هو هی من لم لن فی له به کل لو قد هم عن بل مع کم اذ ذا'.split(' '));
const mkey = t => { if (t.length > 3 && 'وف'.includes(t[0])) t = t.slice(1); else if (t.length === 3 && 'وف'.includes(t[0]) && PARTICLES.has(t.slice(1))) t = t.slice(1); if (t.length > 3 && t.startsWith('ال')) t = t.slice(2); return t; };   // similarity.match_key
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
const UKIND = u => (u.unit_kind === 'clause' ? 'حضور بخش' : u.kind === 'presence' ? 'حضور واژه' : u.kind === 'numeric' ? 'عدد' : u.ortho ? 'املای دیگر' : 'واژه‌ی جایگزین') + (u.by === 'user' ? ' (دستی)' : '');
const ulab = l => ({ absent: 'ندارد', present: 'دارد', '*other*': 'دیگر' }[l] || (/^\d+$/.test(l) ? fa(l) : l));
function uwhere(u, A) {
  if (u.unit_kind === 'clause') return short(A.clauses[u.clause].text, 40);
  if (u.kind === 'presence') return '«' + u.name.split(':').slice(1).join(':') + '»';
  return u.labels.map(ulab).join(' / ');
}
const short = (t, n) => t.length > n ? t.slice(0, n) + '…' : t;
/* the user's decisions about units (parts.units): part key -> unit key -> {drop, keep, states, def}; a unit key is stable (icm/align.py _unit_keys), "m:<n>" for a unit of the user's own */
const uentry = (pd, key, ukey) => ((pd.units || {})[key] || {})[ukey] || {};
function patchUnits(d, key, ukey, fn) {
  const units = JSON.parse(JSON.stringify(d.units || {})), e = ((units[key] ||= {})[ukey] ||= {});
  fn(e);
  if (e.states && !Object.keys(e.states).length) delete e.states;
  if (!Object.keys(e).length) delete units[key][ukey];
  if (!Object.keys(units[key]).length) delete units[key];
  return { units };
}
const foldUnits = (d, edits) => { let cur = d; for (const [key, ukey, fn] of edits) cur = { ...cur, ...patchUnits(cur, key, ukey, fn) }; return { units: cur.units }; };
const nextUnitId = d => 1 + Math.max(0, ...Object.values(d.units || {}).flatMap(m => Object.keys(m)).filter(k => /^m:\d+$/.test(k)).map(k => +k.slice(2)));
/* the words each witness of a part has in grid columns j0..j1: [] = none there inside its row (absent), null = outside its row (unknown) */
function rangeSeqs(g, j0, j1) {
  const out = {};
  g.witnesses.forEach(w => {
    const idx = g.columns.map((col, j) => col[w] != null ? j : -1).filter(j => j >= 0), seq = [];
    for (let j = j0; j <= j1; j++) if (g.columns[j][w] != null) seq.push(g.columns[j][w]);
    out[w] = seq.length ? seq : idx.length && idx[0] <= j0 && j1 <= idx[idx.length - 1] ? [] : null;
  });
  return out;
}
/* the states of a unit defined over columns, as icm/align.py _manual_unit makes them */
function unitPreview(kind, seqs) {
  const st = {}, form = s => s.map(mkey).join(' ');
  if (kind === 'presence') { Object.entries(seqs).forEach(([w, s]) => { st[w] = s == null ? -1 : s.length ? 1 : 0; }); return { labels: ['absent', 'present'], st }; }
  const groups = new Map(); Object.values(seqs).forEach(s => { if (s != null && !groups.has(form(s))) groups.set(form(s), s); });
  const cnt = g => Object.values(seqs).filter(s => s != null && form(s) === g).length, order = [...groups.keys()].sort((a, b) => cnt(b) - cnt(a));
  let labels = order.map(g => groups.get(g).join(' ') || 'absent');
  if (labels.length > 6) labels = [...labels.slice(0, 5), '*other*'];
  Object.entries(seqs).forEach(([w, s]) => { st[w] = s == null ? -1 : Math.min(order.indexOf(form(s)), labels.length - 1); });
  return { labels, st };
}
const ubadgeDemo = () => el('span', { class: 'small muted' }, el('span', { class: 'ub' }, '۱'), ' واحد تغییر');
function pref(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } }
function setPref(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }
function candRow(x, S, sel, can, upd) {
  const SIG = { 'rel:noor_group': 'گروه نور', 'rel:noor_cluster': 'خوشه‌ی نور', 'rel:noor_related': 'مرتبط در نور', 'rel:consensus': 'هم‌خوانی v02', 'rel:kg_group': 'گروه گراف دانش', 'q:pair': 'دو واژه‌ی کمیاب', 'q:phrase': 'عبارت کمیاب', user: 'افزوده‌ی کاربر' };
  return el('tr', { class: sel.has(x.wid) ? 'on' : '' },
    el('td', {}, el('input', { type: 'checkbox', 'aria-label': 'انتخاب این شاهد: ' + (x.title || x.book || x.wid), checked: sel.has(x.wid), disabled: !can, onchange: ev => { ev.target.checked ? sel.add(x.wid) : sel.delete(x.wid); ev.target.closest('tr').classList.toggle('on', ev.target.checked); upd(); } })),
    el('td', {}, el('span', { class: 'chip ' + x.suggested }, CLS[x.suggested]), x.score.reordered ? el('div', { class: 'small muted' }, 'ترتیب بخش‌ها فرق دارد') : null,
      x.reference ? el('div', { class: 'small', style: 'color:var(--warn)', title: 'مدخلی از الدرر السنیة بی‌سند: نقل یا داوری عالمی متأخر درباره‌ی حدیثی از کتابی دیگر، نه روایتی مستقل. از پیش تیک نمی‌خورد؛ اگر لازم است خودتان تیک بزنید.' }, 'ارجاع بی‌سند (الدرر)') : null),
    el('td', {}, el('b', {}, x.title || x.book), el('div', { class: 'small muted' }, [x.author, x.tradition === 'sunni' ? 'سنی' : 'شیعه', x.vol ? 'ج ' + fa(x.vol) : '', x.page ? 'ص ' + fa(x.page) : ''].filter(Boolean).join(' · ')),
      x.copies && x.copies.length ? el('div', { class: 'small muted', title: x.copies.join(' ') }, `+${fa(x.copies.length)} نسخه‌ی یکسان`) : null, el('div', { class: 'small muted', dir: 'ltr' }, x.wid),
      el('div', { class: 'small muted', title: x.signals.join(' ') }, 'یافته با: ', x.signals.map(g => SIG[g] || g).join('، '), x.round > 1 ? ` (دور ${fa(x.round)}، از متن ${x.via})` : '')),
    el('td', {}, el('div', { class: 'small' }, 'از حدیث مبدأ ', pct(x.score.cov_seed)), el('div', { class: 'bar' }, el('i', { style: `width:${100 * x.score.cov_seed}%` })),
      el('div', { class: 'small' }, 'از این متن ', pct(x.score.cov_cand)), el('div', { class: 'bar' }, el('i', { style: `width:${100 * x.score.cov_cand}%` }))),
    el('td', {}, strip(x.parts, S)),
    el('td', { class: 'txt' }, x.marks ? marked(x.matn, x.marks, 'ar matn', S) : el('div', { class: 'ar matn' }, x.matn),
      x.matn_len > x.matn.length ? el('div', { class: 'small muted' }, `… (${fa(x.matn_len)} نویسه، ${fa(x.matn.length)} نخست نمایش داده شد)`) : null,
      fullDetails(x.wid, null, 'سند و متن کامل')));
}
/* step 2: which narrator database (raw or a merged version) steps 4-6 are computed with */
function narratorsBox(c, VERS) {
  if (!VERS.length) return el('span');
  const cur = (P && P.narrator_version) || (STATIC && STATIC.narrator_version && STATIC.narrator_version.name) || 'raw';
  const sel = el('select', { 'aria-label': 'پایگاه راویان' }, VERS.map(v => el('option', { value: v.name, selected: v.name === cur }, `${verLabel(v)}${v.built ? ' — ' + v.built.slice(0, 10) : ''}${v.name !== 'raw' ? ' — ' + fa(v.n_groups) + ' شخص ادغام‌شده' : ''}`)));
  const desc = el('p', { class: 'small' }), line = el('p', { class: 'small muted' });
  const show = () => { const v = VERS.find(x => x.name === sel.value) || VERS[0]; desc.textContent = v.description_fa || ''; line.textContent = verLine(v); };
  sel.addEventListener('change', show); show();
  return card(['پایگاه راویان', help('step2.narrators')],
    el('p', { class: 'muted small' }, 'راوی‌ای که هم در نور و هم در گراف دانش (و درایة النور) آمده دو شناسه دارد. پایگاهِ برگزیده می‌گوید کدام شناسه‌ها یک شخص‌اند؛ در پایگاه «خام» هیچ‌چیز ادغام نمی‌شود و همه‌ی پیوندها فقط پیشنهادند.'),
    el('div', { class: 'toolbar' }, sel, c.can ? el('button', { onclick: async () => {
      if (sel.value === cur) return;
      await post(`/project/${c.pid}/decide/narrators`, { payload: { version: sel.value } }); reload(c.pid);
    } }, 'اعمال') : null), desc, line,
    el('p', { class: 'small muted' }, 'با تغییر پایگاه، مرحله‌های ۴ تا ۶ برای پایگاه تازه از نو محاسبه می‌شوند؛ اجراهای پیشین (با پایگاه پیشین) همچنان در «نسخه» قابل دیدن‌اند. تصمیم‌های شما درباره‌ی راویان می‌مانند.'));
}
async function narratorCard(id) {
  const vname = NV || (P && P.narrator_version) || null;
  const n = await api('/narrator/' + encodeURIComponent(id) + (vname ? '?version=' + encodeURIComponent(vname) : '')).catch(e => (e.status === 400 ? api('/narrator/' + encodeURIComponent(id)) : Promise.reject(e)));
  const kv = (k, v) => v != null && v !== '' ? [el('dt', {}, k), el('dd', {}, typeof v === 'object' ? JSON.stringify(v) : String(v))] : [];
  const box = el('div', {}, el('h2', { class: 'ar' }, n.name || id), el('div', { class: 'small muted', dir: 'ltr' }, id),
    n.wiki ? el('p', { class: 'small' }, el('a', { href: `${SITE}/narrator/p/${n.wiki.person_id}`, target: '_blank' }, 'صفحه‌ی این شخص در پایگاه راویان ↗'),
      n.wiki.also && n.wiki.also.length ? el('span', { class: 'muted' }, ' — شناسه‌های دیگر همین شخص: ', el('span', { dir: 'ltr' }, n.wiki.also.join('، '))) : null) : null,
    help('narrator.card'));
  if (n.merged && n.merged.members && n.merged.members.length > 1) {
    const edges = n.merged.edges || [];
    box.append(el('h3', {}, `شناسه‌هایی که پایگاه «${n.merged.version}» همین شخص می‌داند`), el('table', {}, n.merged.members.map(m => {
      const e = edges.find(x => x.a === m.id || x.b === m.id);
      return el('tr', {}, el('td', { dir: 'ltr' }, m.id), el('td', { class: 'small' }, m.rule || ''), el('td', { class: 'small muted' }, m.score != null ? fa(Math.round(m.score * 100) / 100) : ''),
        el('td', { class: 'small muted', dir: 'ltr' }, e ? `${e.a} ↔ ${e.b}` : ''));
    })));
  }
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

const yearTxt = d => d[0] === d[1] ? fa(Math.round(d[0])) : `${fa(Math.round(d[0]))}–${fa(Math.round(d[1]))}`;
const DSRC = { user: 'واردشده‌ی کاربر', 'author-death': 'درگذشت مؤلف کتاب', 'hadith-kg': 'گراف دانش', kg: 'گراف دانش', noor: 'نور', imam: 'تاریخ امام', interpolated: 'برآورد از جای سند', wikishia: 'ویکی‌شیعه', fixed: 'ثابت' };
const shortName = (s, n = 4) => { const w = String(s).replace(/\s*،\s*/g, ' ').split(/\s+/).filter(Boolean); return w.length > n ? w.slice(0, n).join(' ') + '…' : w.join(' '); };
const TREE_HELP = 'درخت بی‌تقاطع: سندها از نخستین راویشان شاخه می‌شوند؛ راوی‌ای که از دو راه رسیده دو بار آمده است (نشانگر را رویش ببرید تا همه‌ی جاهایش حلقه بگیرند). هر راوی یک بار: هر راوی زیر استادی است که بیشتر سندها می‌آورند و استادهای دیگر خط‌چین‌اند. دایره را بزنید تا زیرشاخه بسته یا باز شود؛ نام را بزنید برای کارت راوی. راویان پنهان‌شده از سند حذف نمی‌شوند: سند از رویشان می‌گذرد (خط نقطه‌چین). خط پررنگ‌تر = سندهای بیشتر.';
/* the recorded graph (tree.js): step 5 draws it as confirmed, step 6 with the model's results on it */
function graphSvg(G) {
  return ICMTree.make({ narrators: G.narrators, nodes: G.nodes, attestations: G.attestations, prefKey: 'icm.g', onNode: narratorCard,
    onBook: ai => hadithModal(G.attestations[ai].wid),
    extraTools: () => info(TREE_HELP) }).root;
}

render();

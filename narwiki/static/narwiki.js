'use strict';
/* Narrator wiki (/narrator): search, person pages with both source databases side by side, edit / merge / split,
   history with before/after, review queue and merge suggestions. All DOM is built with el() and textContent;
   data is never inserted as HTML. An exported person page carries its data in window.NARWIKI_STATIC. */
const BASE = document.body.dataset.base;
const API = BASE + '/api';
const STATIC = window.NARWIKI_STATIC || null;
const ORIGIN = STATIC ? BASE.replace(/^(https?:\/\/[^/]+).*$/, '$1') : '';      // site links from an exported file
const $app = document.getElementById('app');
const FA = '۰۱۲۳۴۵۶۷۸۹';
const fa = x => x == null ? '' : String(x).replace(/[0-9]/g, d => FA[d]);
let ME = { user: null, admin: false, open: true };
/* narrator-database version ("version mode"): the URL's ?version= is the truth; localStorage only remembers the last choice */
const VKEY = 'narwiki.version';
const lsGet = () => { try { return localStorage.getItem(VKEY) || ''; } catch (_) { return ''; } };
const lsSet = v => { try { if (v) localStorage.setItem(VKEY, v); else localStorage.removeItem(VKEY); } catch (_) {} };
const verFromUrl = () => STATIC ? '' : (new URLSearchParams(location.search).get('version') || '');
let VER = verFromUrl();
let VERSIONS = null;
const withVer = path => !VER || /[?&]version=/.test(path) ? path : path + (path.includes('?') ? '&' : '?') + 'version=' + encodeURIComponent(VER);
const vmeta = () => (VERSIONS || []).find(v => v.name === VER);
const vLabel = () => { const m = vmeta(); return (m && m.label_fa) || VER; };
const vEditable = () => !!VER && !STATIC && VER !== 'raw' && (!vmeta() || vmeta().editable);
const vApi = rest => `/version/${encodeURIComponent(VER)}${rest}`;

/* ---------- helpers ---------- */
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (/^(aria-|data-)/.test(k) || ['role', 'tabindex', 'dir', 'title', 'href', 'type', 'value', 'placeholder', 'name', 'target', 'style', 'colspan', 'for', 'id', 'min', 'max', 'rel', 'download'].includes(k)) e.setAttribute(k, v);
    else if (v === true) e.setAttribute(k, '');
    else e[k] = v;
  }
  for (const c of kids.flat(3)) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
const fill = (node, ...kids) => node.replaceChildren(...kids.flat(3).filter(x => x != null && x !== false));
/* the server's messages are English; the page shows Persian */
const ERR_FA = [[/^no such person/i, 'چنین راوی‌ای نیست.'], [/^login required/i, 'برای این کار وارد شوید.'], [/^missing X-NARWIKI/i, 'درخواست نامعتبر است.'],
  [/^only an admin/i, 'فقط مدیر می‌تواند این کار را بکند.'], [/not installed yet/i, 'پایگاه داده‌ی ویکی هنوز روی کارگزار نصب نشده است.'],
  [/^unknown version/i, 'نسخه‌ی راویان ناشناخته است.'], [/^unknown id/i, 'شناسه‌ی ناشناخته.'], [/^id not in this version/i, 'این شناسه در این نسخه نیست.'],
  [/^no person carries this id/i, 'هنوز هیچ راوی‌ای این شناسه را ندارد.'], [/^only active persons can be merged/i, 'فقط راویان فعال را می‌توان ادغام کرد.'],
  [/^the raw version merges nothing/i, 'نسخه‌ی raw ادغامی ندارد و تصمیمی در آن ثبت نمی‌شود.'], [/^this version has no policy file/i, 'این نسخه فایل قاعده ندارد و بازساخته نمی‌شود.'],
  [/^rebuild failed/i, 'ساخت دوباره‌ی نسخه شکست خورد و تصمیم برگردانده شد.'], [/^(JSON|a, b|keep must|ext must|fields must|display_name|id must|decision must|src must|bad |a and b)/i, 'ورودی نامعتبر است.'],
  [/^not found$/i, 'پیدا نشد.']];
function faErr(m) {
  m = String(m || '');
  if (/[\u0600-\u06FF]/.test(m)) return m;
  for (const [re, t] of ERR_FA) if (re.test(m)) return t;
  return 'کارگزار پذیرفت نکرد: ' + m;
}
async function api(path, opts) {
  if (STATIC) return staticApi(path);
  const r = await fetch(API + path, opts);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (_) {} const err = new Error(faErr(typeof m === 'string' ? m : JSON.stringify(m))); err.status = r.status; throw err; }
  return r.json();
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-NARWIKI': '1' }, body: JSON.stringify(body || {}) });
function staticApi(path) {
  const S = STATIC, pid = S.person.person.id;
  if (path === '/me') return { user: null, admin: false, static: true };
  if (path.startsWith(`/person/${pid}/hadiths`)) {
    const q = new URLSearchParams(path.split('?')[1] || ''); const src = q.get('src') || 'noor', off = +(q.get('offset') || 0);
    const h = S.hadiths[src] || { total: 0, items: [] };
    if (off && off >= h.items.length) throw new Error(`در نسخه‌ی ذخیره‌شده فقط ${fa(h.items.length)} مورد نخست هست؛ بقیه را در وب‌گاه ببینید.`);
    return { total: h.total, items: h.items.slice(off, off + +(q.get('limit') || 30)) };
  }
  if (path === `/person/${pid}/history`) return S.history;
  if (path === `/person/${pid}`) return S.person;
  throw new Error('این بخش در نسخه‌ی ذخیره‌شده نیست؛ آن را در وب‌گاه باز کنید.');
}
function modal(content) { fill(document.getElementById('modal-body'), content); document.getElementById('modal').hidden = false; }
document.getElementById('modal-x').onclick = () => document.getElementById('modal').hidden = true;
document.getElementById('modal').addEventListener('click', e => { if (e.target.id === 'modal') e.target.hidden = true; });
/* in-app link: pushState inside the app, a plain link in an exported file */
function link(path, ...kids) {
  return el('a', { href: BASE + withVer(path), onclick: STATIC ? null : (ev => { if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button) return; ev.preventDefault(); go(path); }) }, ...kids);
}
function go(path) { path = withVer(path); if (STATIC) { location.href = BASE + path; return; } history.pushState({}, '', BASE + path); render(); window.scrollTo(0, 0); }
const url = path => BASE + withVer(path);                      // for history.replaceState
function setVersion(v) {
  lsSet(v); VER = v;
  const u = new URL(location.href);
  if (v) u.searchParams.set('version', v); else u.searchParams.delete('version');
  history.pushState({}, '', u.pathname + u.search);
  render();
}
window.addEventListener('popstate', () => render());
function help(...kids) { return el('details', { class: 'help' }, el('summary', {}, 'توضیحات'), ...kids); }
const P = t => el('p', {}, t);
const UL = (...items) => el('ul', {}, items.map(x => el('li', {}, x)));
const errBox = e => el('div', { class: 'err', role: 'alert' }, 'خطا: ' + (e && e.message || e));
const card = (title, ...kids) => el('div', { class: 'card' }, title ? el('h2', {}, title) : null, ...kids);
function busy(btn, fn) {
  return async ev => {
    if (ev) ev.preventDefault();
    btn.disabled = true;
    try { await fn(); } catch (e) { alert('خطا: ' + e.message); } finally { btn.disabled = false; }
  };
}

/* ---------- vocabularies ---------- */
const FIELD_FA = { name: 'نام', alias: 'نام دیگر / شهرت', kunya: 'کنیه', laqab: 'لقب', nisba: 'نسبت', mazhab: 'مذهب', grade: 'حکم رجالی',
  rank: 'مرتبه (ابن حجر)', tabaqa: 'طبقه', death_lo: 'درگذشت (از سال)', death_hi: 'درگذشت (تا سال)', birth: 'ولادت', places: 'شهرها', note: 'یادداشت' };
const FIELDS = Object.keys(FIELD_FA);
const KIND_FA = { noor: 'نور', kg: 'گراف دانش', dirayah: 'درایة النور', wiki: 'ویکی' };
const SUG_FA = { link_kg: 'پیوند پیشین نور↔گراف', name_chrono: 'نام + تاریخ + سند (نور↔گراف)', link_dirayah: 'سند مشترک (نور↔درایة النور)', noor_dup: 'تکراری در نور', kg_dup: 'تکراری در گراف دانش' };
const REV_FA = { seed: 'ساخت از منبع', add: 'افزودن راوی', edit: 'ویرایش', merge: 'ادغام', split: 'جداسازی', revert: 'بازگردانی' };
const REVIEW_FA = { unreviewed: 'بازبینی‌نشده', approved: 'تأییدشده', rejected: 'ردشده' };
const SSTAT_FA = { proposed: 'پیشنهادی', accepted: 'پذیرفته (ادغام شد)', dismissed: 'رد شد' };
const CONF_FA = { high: 'بالا', medium: 'متوسط', low: 'پایین' };
const WHAT_FA = { display_name: 'نام نمایشی', status: 'وضعیت', merged_into: 'ادغام‌شده در', ext: 'شناسه‌ی منبع' };
const PSTAT_FA = { active: 'فعال', merged: 'ادغام‌شده', deleted: 'حذف‌شده' };
const extKind = x => String(x).split(':')[0];
const extChip = x => el('span', { class: 'chip ' + extKind(x), title: KIND_FA[extKind(x)] || extKind(x) }, (KIND_FA[extKind(x)] || extKind(x)) + ' ' + fa(String(x).split(':').slice(1).join(':')));
const SUBSRC_FA = { fixed: 'ثابت', facts: 'واقعیت‌های استخراج‌شده', najashi: 'نجاشی', tusi: 'طوسی', fihrist: 'فهرست طوسی' };
function srcLabel(s) {
  if (!s) return '';
  const [a, b] = s.split('/');
  const base = a === 'user' ? 'ویرایش کاربر' : (KIND_FA[a] || a);
  return b ? `${base} (${SUBSRC_FA[b] || b})` : base;
}
const srcChip = s => el('span', { class: 'chip ' + (s || '').split('/')[0], title: s }, srcLabel(s));
const deathText = p => p.death_lo ? (p.death_hi && p.death_hi !== p.death_lo ? `د. ${fa(p.death_lo)}–${fa(p.death_hi)}` : `د. ${fa(p.death_lo)}`) : '';
const SITE_TITLE = 'ویکی راویان';
const setTitle = (...parts) => { document.title = [...parts.filter(Boolean), SITE_TITLE].join(' — '); };
const PAGE_TITLE = { home: 'جستجوی راویان', suggestions: 'پیشنهادهای ادغام', queue: 'صف بازبینی', merge: 'ادغام دستی', new: 'راوی تازه' };
const fmtTs = t => t ? fa(t.replace('T', ' ').replace('Z', '')) : '';


/* ---------- version selector, banner ---------- */
const VSTATE_FA = { proposed: 'پیشنهادی', merged: 'ادغام‌شده', rejected: 'ردشده' };
const RULE_FA = { link_kg: 'پیوند پیشین نور↔گراف', name_chrono: 'نام + تاریخ + سند', noor_dup: 'تکراری نور', kg_dup: 'تکراری گراف', certain_dirayah: 'درایه (قطعی)', noor_kg: 'نور↔گراف', dup: 'تکراری یک پایگاه', dirayah_same_id: 'درایه (شناسه و نام یکسان)',
  dirayah: 'درایه', wiki_merge: 'ادغام ویکی', manual: 'دستی', seed: 'آغازین' };
const CONFLICT_FA = { one_to_one: 'برنده‌ی دیگری داشت (ادغام یک‌به‌یک)', threshold: 'زیر آستانه', manual_reject: 'ردشده‌ی دستی', manual_reject_transitive: 'ردشده‌ی دستی، ولی از راه دیگری هنوز با هم‌اند',
  manual_reject_ignored: 'ردِ دستی اعمال نشد: ویکی خودش این دو را یکی کرده', manual_unknown_id: 'شناسه‌ی ناشناخته برای ویکی', kind_clash: 'دو شناسه از یک پایگاه در یک گروه' };
const ruleFa = r => RULE_FA[r] || r;
const DEC_FA = { accept: 'ادغام', reject: 'رد' };
const VSEL_HELP = () => help(
  P('«نسخه‌ی راویان» یک پایگاه راویان زنده است که از ویکی ساخته می‌شود: هر شناسه‌ی نور یا گراف دانش در آن به یک گروه (یک راوی) می‌رسد، طبق قاعده‌های آن نسخه (مثلاً v1m: فقط ادغام‌های با اطمینان بالا). پروژه‌های ICM از یکی از این نسخه‌ها می‌خوانند.'),
  UL('بی انتخاب نسخه، ویکی مثل همیشه کار می‌کند (حالت کلاسیک). با انتخاب نسخه، پیشنهادهای ادغام، صفحه‌ی راوی و ادغام دستی وضعیت همان نسخه را نشان می‌دهند و می‌توانید در آن «ادغام» یا «رد» کنید.',
    'تصمیم‌ها جدا برای هر نسخه ثبت می‌شوند و فقط روی همان نسخه اثر دارند؛ ادغام‌های خودِ ویکی (صفحه‌ی «ادغام دستی» و دکمه‌های حالت کلاسیک) به نسخه‌ها وابسته نیستند و برای پیراستن ویکی‌اند.',
    'هر تصمیم فایل نسخه را دوباره می‌سازد (چند ثانیه). چون وضعیت ویکی در فایل عوض می‌شود، پروژه‌های ICM که از آن نسخه می‌خوانند در پیشروی بعدی از مرحله‌ی ۴ دوباره حساب می‌کنند.',
    'نسخه‌ی «raw» هیچ ادغامی ندارد و فقط‌خواندنی است. انتخاب نسخه در نشانی (?version=) می‌ماند و هنگام رفتن میان صفحه‌ها حفظ می‌شود.'));
function versionBar() {
  const sel = document.getElementById('vsel'), ban = document.getElementById('vbanner');
  if (STATIC || !sel || !ban) return;
  const opts = [el('option', { value: '' }, 'بدون نسخه (ویکی کلاسیک)')];
  const list = VERSIONS || [];
  for (const v of list) {
    const bits = [v.name === 'raw' ? 'خام (raw)' : (v.label_fa || v.name)];
    if (v.name === 'raw') bits.push('فقط‌خواندنی'); else if (v.n_decisions) bits.push(`${fa(v.n_decisions)} تصمیم`);
    const o = el('option', { value: v.name }, bits.join(' · '));
    o.title = [v.label_fa || v.name, `(${v.name})`, v.built ? fa(String(v.built).slice(0, 10)) : '', v.n_groups != null ? `${fa(v.n_groups)} گروه` : '', `${fa(v.n_decisions || 0)} تصمیم`].filter(Boolean).join(' · ');
    opts.push(o);
  }
  if (VER && !list.some(v => v.name === VER)) opts.push(el('option', { value: VER }, VER));
  const s = el('select', { id: 'vselect', title: 'نسخه‌ی راویان', onchange: ev => setVersion(ev.target.value) }, ...opts);
  s.value = VER;
  const last = lsGet();
  fill(sel, el('label', { class: 'small muted', for: 'vselect' }, 'نسخه: '), s,
    !VER && last && list.some(v => v.name === last) ? el('a', { href: '#', class: 'small', onclick: ev => { ev.preventDefault(); setVersion(last); } }, ' ادامه با ' + last) : null);
  fill(ban, VER ? el('div', { class: 'wrap' }, el('div', { class: 'vbanner' }, el('span', {}, `نسخه‌ی راویان: ${vLabel()} (${VER}) — تصمیم‌ها فقط روی این نسخه اعمال می‌شود`),
    VER === 'raw' ? el('span', { class: 'chip ghost' }, 'فقط‌خواندنی') : null, ' ',
    el('a', { href: BASE + '/', onclick: ev => { ev.preventDefault(); setVersion(''); } }, 'بازگشت به حالت کلاسیک ویکی'),
    VSEL_HELP())) : null);
  document.querySelectorAll('[data-path]').forEach(a => a.setAttribute('href', BASE + withVer(a.dataset.path)));
}

/* ---------- routing ---------- */
function route() {
  const m = location.pathname.slice(BASE.length).split('/').filter(Boolean);
  if (STATIC) return { page: 'person', id: STATIC.person.person.id };
  if (!m.length) return { page: 'home' };
  if (m[0] === 'p' && m[1]) return { page: m[2] || 'person', id: +m[1] };
  return { page: m[0] };
}
async function render() {
  VER = verFromUrl();
  if (!STATIC) ME = await api('/me').catch(() => ME);
  if (!STATIC && VERSIONS === null) VERSIONS = await api('/versions').then(r => r.items).catch(() => []);
  versionBar();
  const who = document.getElementById('who');
  if (who) fill(who, STATIC ? 'نسخه‌ی ذخیره‌شده' : ME.user ? `${ME.user}${ME.admin ? ' (مدیر)' : ''}` : el('a', { href: '/account/login?next=' + encodeURIComponent(location.pathname) }, 'ورود'));
  const r = route();
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('on', a.dataset.nav === r.page));
  setTitle(PAGE_TITLE[r.page]);
  fill($app, el('p', { class: 'muted' }, 'در حال بارگذاری…'));
  try {
    if (!STATIC && ME.installed === false) throw new Error('پایگاه داده‌ی ویکی هنوز روی کارگزار نصب نشده است.');
    const pages = { home, person, edit, history: historyPage, split: splitPage, merge: mergePage, queue: queuePage, suggestions: suggestionsPage, new: newPage };
    const f = pages[r.page];
    if (!f) throw new Error('این صفحه وجود ندارد');
    await f(r.id);
  } catch (e) { setTitle(e.status === 404 ? 'پیدا نشد' : 'خطا'); fill($app, errBox(e), el('p', {}, link('/', 'بازگشت به جستجو'))); }
}

/* ---------- home: search ---------- */
const HOME_HELP = () => help(
  P('ویکی راویان یک پایگاه داده‌ی ویرایش‌پذیر از راویان حدیث است. هر «راوی» (شخص) در این ویکی یک شماره دارد و می‌تواند یک یا چند شناسه در پایگاه‌های بیرونی داشته باشد: شناسه‌ی راوی در «نور» (RaviId جامع الاحادیث نور، ۸٬۱۲۰ راوی، بیشتر شیعی، همراه با مدخل‌های کتاب‌های رجالی) و شناسه‌ی راوی در «گراف دانش» (Hadith Knowledge Graph، ۴۹٬۸۱۹ راوی اهل سنت با سال وفات، مرتبه‌ی ابن حجر و اقوال رجالیان). در آغاز برای هر راوی هر پایگاه یک شخص جدا ساخته شده است؛ یکی کردن آن‌ها کار کاربران است.'),
  P('در این صفحه می‌توانید راویان را جستجو کنید:'),
  UL('با نام (هر بخشی از نام، کنیه، لقب یا نسبت؛ اعراب، همزه و «ال» مهم نیست؛ هر واژه از آغاز سنجیده می‌شود)،',
    'با شناسه‌ی منبع، مثل noor:2323 یا kg:3795، یا با شماره‌ی شخص در ویکی،',
    'با پالایه‌ها: کدام پایگاه (فقط نور، فقط گراف، هر دو = ادغام‌شده، یا ساخته‌شده در ویکی)، دارای تاریخ وفات یا بی‌تاریخ، و «پیشنهاد ادغام دارد».'),
  P('بی‌جستجو، پرروایت‌ترین راویان نخست می‌آیند (شمار روایت‌ها در منبع‌ها). روی نام هر راوی بزنید تا صفحه‌اش باز شود. جستجو هیچ چیزی را تغییر نمی‌دهد.'),
  P('در بالای صفحه می‌توانید یک «نسخه‌ی راویان» برگزینید (مثلاً v1m): آن‌گاه پیشنهادهای ادغام و صفحه‌ی راوی وضعیت همان نسخه را نشان می‌دهند و می‌توانید در آن نسخه ادغام یا رد کنید؛ بی انتخاب نسخه، ویکی مثل همیشه کار می‌کند. جزئیات در «توضیحات» بالای صفحه‌ی هر نسخه.'),
  P('بخش‌های دیگر: «پیشنهادهای ادغام» (جفت‌هایی که به احتمال زیاد یک نفرند و خودکار پیدا شده‌اند؛ هیچ‌کدام خودکار ادغام نشده است)، «صف بازبینی» (همه‌ی تغییرهای کاربران که هنوز بازبینی نشده‌اند)، «ادغام دستی» و «راوی تازه».'));

async function home() {
  const qs = new URLSearchParams(location.search);
  const q = el('input', { type: 'search', placeholder: 'نام راوی، noor:… / kg:… یا شماره‌ی شخص', value: qs.get('q') || '', class: 'wide' });
  const kind = el('select', { 'aria-label': 'پایگاه راوی' }, [['', 'همه‌ی پایگاه‌ها'], ['noor', 'در نور'], ['kg', 'در گراف دانش'], ['dirayah', 'در درایة النور'], ['both', 'در نور و گراف (ادغام‌شده)'], ['noor_only', 'فقط نور'], ['kg_only', 'فقط گراف دانش'], ['wiki', 'فقط ویکی']].map(([v, t]) => el('option', { value: v }, t)));
  kind.value = qs.get('kind') || '';
  const dated = el('select', { 'aria-label': 'تاریخ وفات' }, [['', 'با یا بی تاریخ'], ['1', 'دارای تاریخ وفات'], ['0', 'بی تاریخ وفات']].map(([v, t]) => el('option', { value: v }, t)));
  dated.value = qs.get('dated') || '';
  const sugg = el('input', { type: 'checkbox', checked: qs.get('sugg') === '1' });
  const out = el('div');
  let offset = +(qs.get('offset') || 0);
  const run = async (keepOffset) => {
    if (!keepOffset) offset = 0;
    const p = new URLSearchParams({ q: q.value.trim(), kind: kind.value, dated: dated.value, sugg: sugg.checked ? '1' : '', offset, limit: 50 });
    history.replaceState({}, '', url('/?' + [...p].filter(([k, v]) => v && v !== '0' || (k === 'dated' && v === '0')).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')));
    fill(out, el('p', { class: 'muted' }, 'جستجو…'));
    try {
      const r = await api('/search?' + p);
      fill(out, el('div', { class: 'muted small' }, `${fa(r.total)} راوی`), personTable(r.items), pager(r.total, offset, 50, o => { offset = o; run(true); }));
    } catch (e) { fill(out, errBox(e)); }
  };
  const form = el('form', { class: 'toolbar', onsubmit: ev => { ev.preventDefault(); run(); } }, q, kind, dated, el('label', { class: 'ck' }, sugg, 'پیشنهاد ادغام دارد'), el('button', { class: 'primary', type: 'submit' }, 'جستجو'));
  [kind, dated, sugg].forEach(x => x.onchange = () => run());
  const stats = el('div', { class: 'small muted' });
  fill($app, el('h1', {}, 'ویکی راویان'), HOME_HELP(), card(null, form, stats, out));
  api('/stats').then(s => fill(stats, `${fa(s.persons.active || 0)} راوی فعال — در نور ${fa(s.ext.noor || 0)}، در گراف دانش ${fa(s.ext.kg || 0)}، ساخته‌شده در ویکی ${fa(s.ext.wiki || 0)}؛ `,
    `${fa(Object.entries(s.suggestions).filter(([k]) => k.endsWith('|proposed')).reduce((t, [, n]) => t + n, 0))} پیشنهاد ادغام باز؛ ${fa(s.unreviewed)} تغییر بازبینی‌نشده.`)).catch(() => {});
  await run(true);
}
function personTable(items, onPick) {
  if (!items.length) return el('p', { class: 'muted' }, 'چیزی پیدا نشد.');
  return el('div', { class: 'tscroll' }, el('table', { class: 'list' },
    el('thead', {}, el('tr', {}, ['#', 'نام', 'شناسه‌ها', 'وفات', 'مذهب / حکم', 'روایت', ''].map(t => el('th', {}, t)))),
    el('tbody', {}, items.map(p => el('tr', {},
      el('td', { class: 'small muted' }, fa(p.id)),
      el('td', {}, onPick ? el('a', { href: '#', onclick: ev => { ev.preventDefault(); onPick(p); } }, p.display_name) : link('/p/' + p.id, p.display_name),
        p.aliases && p.aliases.length ? el('div', { class: 'alias' }, p.aliases.join(' · ')) : null),
      el('td', {}, p.ext.filter(x => !x.startsWith('wiki:')).map(extChip), p.ext.some(x => x.startsWith('wiki:')) && !p.ext.some(x => !x.startsWith('wiki:')) ? el('span', { class: 'chip wiki' }, 'ویکی') : null),
      el('td', { class: 'small' }, deathText(p)),
      el('td', { class: 'small' }, [p.mazhab, p.grade].filter(Boolean).join(' · ')),
      el('td', { class: 'small' }, p.n_narr ? fa(p.n_narr) : ''),
      el('td', {}, p.n_suggestions ? el('span', { class: 'chip proposed', title: 'پیشنهاد ادغام باز' }, fa(p.n_suggestions)) : null))))));
}
function pager(total, offset, size, onGo) {
  if (total <= size) return null;
  return el('div', { class: 'pager' },
    el('button', { disabled: offset <= 0, onclick: () => onGo(Math.max(0, offset - size)) }, 'قبلی'),
    el('span', { class: 'small muted' }, `${fa(offset + 1)}–${fa(Math.min(total, offset + size))} از ${fa(total)}`),
    el('button', { disabled: offset + size >= total, onclick: () => onGo(offset + size) }, 'بعدی'));
}

/* ---------- person page ---------- */
const PERSON_HELP = () => help(
  P('این صفحه همه‌ی دانسته‌ها درباره‌ی یک راوی را از همه‌ی منبع‌ها کنار هم نشان می‌دهد.'),
  UL(
    '«ویژگی‌ها»: هر مقدار با منبعش آمده است: «نور» (از پایگاه راویان نور، و برای تاریخ وفات و شهرها نام کتاب رجالی که از آن خوانده شده، مثلاً نور (najashi))، «گراف دانش»، یا «ویرایش کاربر» با نام کاربر و زمان. یک ویژگی می‌تواند چند مقدار از چند منبع داشته باشد؛ هیچ مقداری پاک نمی‌شود، فقط «کنار گذاشته» می‌شود و در تاریخچه می‌ماند.',
    'دو ستون «نور» و «گراف دانش»: کارت هر شناسه‌ی منبع همان‌گونه که در پایگاه خودش آمده (مذهب و حکم نور، جرح و توثیق، واقعیت‌های استخراج‌شده با شاهدشان؛ مرتبه‌ی ابن حجر، سال‌ها و اقوال رجالیان در گراف). اگر راوی هنوز با شخص دیگری ادغام نشده باشد، یکی از دو ستون خالی است.',
    '«مدخل‌های رجالی»: مدخل‌های کتاب‌های رجال شیعه (نجاشی، رجال و فهرست طوسی، خلاصه، خویی…) که با اطمینان بالا یا متوسط به این راوی نور پیوند خورده‌اند.',
    '«استادان و شاگردان»: راویانی که در سندها بلافاصله پیش یا پس از این راوی آمده‌اند (در نور فقط جایگاه‌های قطعی، دست‌کم دو بار؛ در گراف دانش از سندهای گراف)، به شخص‌های ویکی نگاشته شده‌اند.',
    '«پیشنهادهای ادغام»: جفت‌هایی که این راوی در آن‌هاست، با شواهد. پذیرفتن یعنی ادغام؛ رد کردن فقط پیشنهاد را می‌بندد.',
    '«حدیث‌ها»: هر جا این راوی (هر یک از شناسه‌هایش) در سندی آمده، از پایگاه‌های منبع خوانده می‌شود، صفحه‌به‌صفحه؛ برای نور پیوند به صفحه‌ی حدیث در /h هم هست. «پروژه‌های ICM» پروژه‌های تحلیل منبعی‌اند که این راوی در آن‌ها هست.'),
  P('کارها: «ویرایش» (افزودن یا کنار گذاشتن مقدار، تغییر نام نمایشی)، «جداسازی» (اگر دو نفر به اشتباه یکی شده‌اند)، «ادغام با…» (اگر این راوی با شخص دیگری یکی است)، «تاریخچه» و «ذخیره‌ی صفحه» (یک فایل HTML تنها با همه‌ی داده‌ها، که بی‌اینترنت هم باز می‌شود). هر تغییر فوراً اعمال می‌شود و در «صف بازبینی» می‌ماند تا بازبین تأیید یا رد کند (رد کردن می‌تواند تغییر را برگرداند).'));

async function person(id) {
  const d = await api('/person/' + id + (VER ? '?version=' + encodeURIComponent(VER) : ''));
  const p = d.person;
  setTitle(p.display_name + (p.ext.length ? ' (' + p.ext.filter(x => !x.startsWith('wiki:')).slice(0, 2).join('، ') + ')' : ''));
  if (!STATIC && p.id !== id) history.replaceState({}, '', url('/p/' + p.id));
  const head = el('div', { class: 'card' },
    el('h1', {}, p.display_name, ' ', el('span', { class: 'small muted' }, '#' + fa(p.id))),
    p.requested !== p.id ? el('div', { class: 'warn small' }, `شخص #${fa(p.requested)} در این شخص ادغام شده است.`) : null,
    p.status !== 'active' ? el('div', { class: 'warn' }, 'وضعیت: ' + (PSTAT_FA[p.status] || p.status)) : null,
    el('div', {}, p.ext.map(extChip)),
    p.merged_from.length ? el('div', { class: 'small muted' }, 'ادغام‌شده از: ', p.merged_from.map(x => [link('/p/' + x + '/history', '#' + fa(x)), ' '])) : null,
    STATIC ? el('div', { class: 'small muted' }, `نسخه‌ی ذخیره‌شده در ${fmtTs(STATIC.exported)}؛ نسخه‌ی زنده: `, el('a', { href: BASE + '/p/' + p.id }, BASE + '/p/' + p.id)) :
      el('div', { class: 'toolbar' },
        link(`/p/${p.id}/edit`, el('span', { class: 'btn primary' }, 'ویرایش')),
        link(`/p/${p.id}/split`, el('span', { class: 'btn' }, 'جداسازی')),
        link(`/merge?a=${p.id}`, el('span', { class: 'btn' }, 'ادغام با…')),
        link(`/p/${p.id}/history`, el('span', { class: 'btn' }, `تاریخچه (${fa(d.n_revisions)})`)),
        el('a', { class: 'btn', href: `${API}/person/${p.id}/export.html`, download: `narrator-${p.id}.html` }, 'ذخیره‌ی صفحه (HTML)')));
  const noor = d.cards.filter(c => extKind(c.id) === 'noor'), kg = d.cards.filter(c => extKind(c.id) === 'kg');
  const entries = noor.flatMap(c => (c.entries || []).map(e => ({ ...e, via: c.id })));
  fill($app, head, PERSON_HELP(), VER ? versionGroupCard(d) : null,
    card('ویژگی‌ها', fieldsTable(p.fields), p.retired.length ? el('details', { class: 'small' }, el('summary', { class: 'muted' }, `${fa(p.retired.length)} مقدار کنارگذاشته`), fieldsTable(p.retired)) : null),
    el('div', { class: 'cols' },
      el('div', { class: 'card srccol' }, el('h2', {}, el('span', { class: 'chip noor' }, 'نور'), ' راویان نور (جامع الاحادیث)'), noor.length ? noor.map(noorCard) : el('p', { class: 'muted' }, 'شناسه‌ای در نور ندارد.')),
      el('div', { class: 'card srccol' }, el('h2', {}, el('span', { class: 'chip kg' }, 'گراف'), ' گراف دانش'), kg.length ? kg.map(kgCard) : el('p', { class: 'muted' }, 'شناسه‌ای در گراف دانش ندارد.'))),
    entries.length ? card('مدخل‌های رجالی', entries.map(e => el('div', { class: 'entry' },
      el('div', {}, el('b', {}, e.book), ' ', e.heading ? el('span', { class: 'ar' }, e.heading) : null, ' ', el('span', { class: 'small muted' }, [e.vol ? 'ج ' + fa(e.vol) : '', e.page ? 'ص ' + fa(e.page) : ''].filter(Boolean).join('، ')),
        ' ', el('span', { class: 'chip ' + e.confidence }, 'پیوند ' + (CONF_FA[e.confidence] || e.confidence)), ' ', el('span', { class: 'small muted' }, e.via)),
      el('div', { class: 'ar body small' }, e.body)))) : null,
    el('div', { class: 'cols' }, card('استادان (از چه کسانی روایت کرده)', neighList(d.teachers)), card('شاگردان (چه کسانی از او روایت کرده‌اند)', neighList(d.students))),
    card('پیشنهادهای ادغام', d.suggestions.length ? d.suggestions.map(s => sugBox(s, () => render(), p.id)) : el('p', { class: 'muted' }, 'پیشنهادی نیست.')),
    card('پروژه‌های ICM', d.projects.length ? el('ul', {}, d.projects.map(x => el('li', {}, el('a', { href: ORIGIN + '/icm/p/' + x.pid }, x.title || x.pid), ' ', x.status ? el('span', { class: 'chip ghost' }, x.status) : null, ' ', el('span', { class: 'small muted' }, x.via))))
      : el('p', { class: 'muted small' }, 'این راوی در هیچ پروژه‌ی تحلیل منبع (ICM) نیامده است (یا فهرست پروژه‌ها هنوز ساخته نشده).')),
    hadithCard(p, d.counts));
}
function provCell(f) {
  return el('div', { class: 'prov' }, srcChip(f.source), f.ext_kind ? [' ', el('bdi', { dir: 'ltr' }, `${f.ext_kind}:${f.ext_id}`)] : '',
    f.source === 'user' ? ` — ${f.rev_user || ''} ${fmtTs(f.rev_ts)}` : '', f.rev_kind && f.rev_kind !== 'seed' ? [' ', el('span', { class: 'chip ' + f.rev_review }, REVIEW_FA[f.rev_review] || f.rev_review)] : null,
    f.revision_id ? [' ', el('span', { class: 'small muted' }, 'نسخه #' + fa(f.revision_id))] : null);
}
/* one row per (property, value): the same value from several sources is one row with every source under it */
function fieldsTable(fields) {
  if (!fields.length) return el('p', { class: 'muted' }, 'مقداری نیست.');
  const groups = new Map();
  for (const f of fields) { const k = f.field + '\u0000' + f.value; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(f); }
  return el('div', { class: 'tscroll' }, el('table', { class: 'ftab' }, el('tbody', {}, [...groups.values()].map(fs => { const f = fs[0]; return el('tr', {},
    el('td', { class: 'muted fl' }, FIELD_FA[f.field] || f.field),
    el('td', { class: 'fvv' }, el('span', { class: ['name', 'alias', 'kunya', 'laqab', 'nisba'].includes(f.field) ? 'ar' : '' }, f.field.startsWith('death') ? fa(f.value) : f.value)),
    el('td', {}, fs.map(provCell))); }))));
}
function kv(pairs) {
  return el('dl', { class: 'kv' }, pairs.filter(([, v]) => v != null && v !== '' && !(Array.isArray(v) && !v.length)).map(([k, v]) => [el('dt', {}, k), el('dd', {}, v)]));
}
const texts = xs => (xs || []).map(x => typeof x === 'string' ? x : x.text + (x.books && x.books.length ? ` (${x.books.map(b => b.book).join('، ')})` : '')).join(' · ');
function noorCard(c) {
  if (c.error || c.missing) return el('div', { class: 'srccard noor' }, extChip(c.id), ' ', c.error || 'در پایگاه نور پیدا نشد');
  return el('div', { class: 'srccard noor' },
    el('div', {}, extChip(c.id), ' ', el('b', { class: 'ar' }, c.name), c.infallible ? el('span', { class: 'chip' }, 'معصوم') : null),
    kv([['مذهب', c.mazhab], ['حکم', c.grade], ['کنیه', c.kunya], ['لقب', texts(c.laqab)], ['طبقه', texts(c.tabaqa)],
      ['جمع‌بندی ارزیابی', c.eval_summary], ['توثیق عام', c.tawthiq], ['جرح', texts(c.jarh)],
      ['جایگاه در سندها', c.n_slots != null ? `${fa(Math.round(c.n_slots))} جایگاه در ${fa(c.n_hadith)} حدیث` : null],
      ['گراف دانش (پیوند پیشین)', (c.same_person || []).map(x => `${x.id} (${CONF_FA[x.confidence] || x.confidence})`).join('، ')]]),
    c.facts && c.facts.length ? el('details', { class: 'small' }, el('summary', { class: 'muted' }, `${fa(c.facts.length)} واقعیت استخراج‌شده (وفات، شهر، امام)`),
      el('table', {}, el('tbody', {}, c.facts.map(f => el('tr', {}, el('td', {}, f.kind), el('td', {}, f.value, f.lo != null ? ` [${fa(f.lo)}–${fa(f.hi)}]` : ''), el('td', { class: 'muted' }, f.source),
        el('td', { class: 'ar small' }, f.evidence || '')))))) : null);
}
function kgCard(c) {
  if (c.error || c.missing) return el('div', { class: 'srccard kg' }, extChip(c.id), ' ', c.error || 'در گراف دانش پیدا نشد');
  return el('div', { class: 'srccard kg' },
    el('div', {}, extChip(c.id), ' ', el('b', { class: 'ar' }, c.name)),
    kv([['شهرت', c.nickname], ['مرتبه', c.rank], ['طبقه', c.tabaka != null ? fa(c.tabaka) : null], ['ولادت', fa(c.birth)], ['وفات', fa(c.death)],
      ['اقامت', c.iqama], ['محل وفات', c.death_place], ['نسبت', c.nasab], ['حرفه', c.profession],
      ['تدلیس / اختلاط', [c.has_tadlis ? 'مدلس' : '', c.has_ikhtilat ? 'اختلاط' : ''].filter(Boolean).join('، ')], ['روایت‌ها', fa(c.narrations)]]),
    c.aqwal && c.aqwal.length ? el('details', { class: 'small' }, el('summary', { class: 'muted' }, `${fa(c.aqwal.length)} قول رجالیان`),
      el('table', {}, el('tbody', {}, c.aqwal.map(a => el('tr', {}, el('td', {}, a.alem, a.death ? ` (د. ${fa(a.death)})` : ''), el('td', { class: 'ar small' }, a.qawl)))))) : null);
}
function neighList(xs) {
  if (!xs.length) return el('p', { class: 'muted small' }, 'در سندهای منبع‌ها کسی پیدا نشد.');
  return el('ul', {}, xs.map(x => el('li', {}, x.person_id ? link('/p/' + x.person_id, x.name || '#' + x.person_id) : el('span', {}, x.name),
    ' ', el('span', { class: 'small muted' }, `${fa(x.n)} بار — ${x.ids.join('، ')}`))));
}
const SRC_TAB = { noor: 'نور', kg: 'گراف دانش', hadithai: 'پیشنهاد hadith.ai' };
const HAI_FA = { ctx3: 'همان راوی پیش و پس از آن', ctx_prev: 'همان راوی پیش از آن', ctx_next: 'همان راوی پس از آن' };
function hadithCard(p, counts) {
  const box = el('div');
  const srcs = ['noor', 'kg'].filter(s => p.ext.some(x => x.startsWith(s + ':')));
  if (srcs.includes('noor') && counts.hadithai) srcs.push('hadithai');
  if (!srcs.length) return card('حدیث‌ها', el('p', { class: 'muted' }, 'این راوی شناسه‌ای در پایگاه‌های حدیثی ندارد.'));
  let src = srcs[0];
  const tabs = el('div', { class: 'tabs' });
  const note = el('div');
  const show = async (s, offset) => {
    src = s;
    fill(tabs, srcs.map(x => el('button', { class: x === src ? 'on' : '', onclick: () => show(x, 0) }, `${SRC_TAB[x]} (${fa(counts[x] || 0)})`)));
    fill(note, s === 'hadithai' ? el('div', { class: 'small warn' },
      'این‌ها پیوند نیستند، پیشنهادند: hadith.ai نام راویان را در متن برچسب می‌زند ولی شناسه‌ی شخص ندارد. ',
      'هر مورد در کتابی است که نور سندش را پیوند نداده (بحار، وافی، مستدرک…)؛ همین نام با همان همسایه‌ها در همه‌ی سندهای پیوندخورده‌ی نور همین راوی بوده است. ',
      'دقت سنجیده (کنار گذاشتن کتاب‌به‌کتاب): ۹۹٫۷٪ با هر دو همسایه، ۹۹٫۲٪ با یک همسایه.') : null);
    fill(box, el('p', { class: 'muted' }, 'خواندن…'));
    try {
      const r = await api(`/person/${p.id}/hadiths?src=${s}&offset=${offset}&limit=30`);
      fill(box, r.error ? errBox(r.error) : null, r.items.map(h => el('div', { class: 'hrow' },
        el('div', { class: 'small' }, h.src !== 'kg' ? el('a', { href: ORIGIN + h.url, target: '_blank', rel: 'noopener' }, 'حدیث ' + fa(h.hadith_id)) : el('span', {}, 'kg:' + fa(h.hadith_id)),
          ' ', el('span', { class: 'muted' }, [h.book, h.no ? 'ش ' + fa(h.no) : '', h.page ? 'ص ' + fa(h.page) : '', h.sanad_idx ? 'سند ' + fa(h.sanad_idx + 1) : '', h.self].filter(Boolean).join(' · ')),
          h.sugg ? el('span', { class: 'chip sg', title: 'پیشنهاد، نه پیوند' }, `پیشنهاد: ${HAI_FA[h.sugg] || h.sugg} در ${fa(h.sugg_n)} سند نور`) : null),
        el('div', { class: 'chain' }, h.chain.map(x => [x.term ? el('span', { class: 'tm' }, x.term) : null,
          el('span', { class: 'nm' + (x.me ? ' me' : '') + (x.sugg ? ' sg' : ''), title: x.id ? `${x.id}${x.label ? ' — ' + x.label : ''}${x.sugg ? ' (پیشنهاد)' : ''}` : '' }, x.title || '؟')])),
        h.sanad ? el('details', { class: 'small' }, el('summary', { class: 'muted' }, 'متن سند در کتاب'), el('div', { class: 'ar' }, h.sanad)) : null)),
        pager(r.total, offset, 30, o => show(src, o)));
    } catch (e) { fill(box, errBox(e)); }
  };
  show(src, 0);
  return card('حدیث‌ها (جایگاه‌های این راوی در سندها)', el('div', { class: 'small muted' }, 'نام برجسته همین راوی است؛ ترتیب سند از گردآورنده به سوی معصوم/پیامبر.',
    counts.hadithai_confirmed ? ` hadith.ai در ${fa(counts.hadithai_confirmed)} جا از همین سندهای نور همین نام را برچسب زده است (هم‌خوان با نور).` : ''), tabs, note, box);
}


/* ---------- version group block on the person page ---------- */
const VGROUP_HELP = () => help(
  P('هر شناسه‌ی نور یا گراف دانش این راوی در نسخه‌ی انتخاب‌شده به یک گروه می‌رسد؛ اعضای گروه همان شناسه‌هایی‌اند که این نسخه یکی شمرده است، با قاعده‌ای که آن‌ها را به هم وصل کرده (آغازین = خودِ شناسه) و امتیازش.'),
  UL('تصمیم‌های دستی زیر فقط برای همین نسخه‌اند. «بازگرداندن تصمیم» آن را برمی‌دارد و نسخه دوباره ساخته می‌شود؛ پروژه‌های ICM آن نسخه از مرحله‌ی ۴ دوباره حساب می‌کنند.',
    '«ردکردن» جفت را از پیشنهادهای ادغامِ آن نسخه برمی‌دارد، ولی چیزی را که خودِ ویکی یکی کرده جدا نمی‌کند؛ اگر دو شناسه از راه دیگری هم به هم وصل باشند (زنجیره)، رد دومی لازم است.',
    'در نسخه‌ی raw ادغامی نیست و همه‌چیز فقط‌خواندنی است.'));
function versionGroupCard(d) {
  const groups = d.version_group || [];
  const title = `گروه در نسخه‌ی ${VER}`;
  const body = groups.length ? groups.map(g => {
    const decs = g.group_decisions || g.decisions || [];
    return el('div', { class: 'sug' },
      g.name ? el('div', {}, el('b', {}, g.name), ' ', el('span', { class: 'small muted' }, g.canon || '')) : null,
      el('div', { class: 'tscroll' }, el('table', {}, el('thead', {}, el('tr', {}, ['پایگاه', 'شناسه', 'قاعده', 'امتیاز', 'راوی در ویکی'].map(t => el('th', {}, t)))),
        el('tbody', {}, g.members.map(m => el('tr', {}, el('td', {}, el('span', { class: 'chip ' + m.kind }, KIND_FA[m.kind] || m.kind)), el('td', {}, m.id),
          el('td', {}, ruleFa(m.rule)), el('td', {}, m.score != null ? fa(Math.round(m.score * 100) / 100) : ''),
          el('td', {}, m.kind === 'noor' || m.kind === 'kg' ? extPersonLink(m.id) : '')))))),
      el('h3', {}, 'تصمیم‌های دستی'),
      decs.length ? decs.map(dec => vDecisionRow(dec)) : el('p', { class: 'muted small' }, 'تصمیم دستی‌ای برای این گروه نیست.'));
  }) : el('p', { class: 'muted' }, 'این راوی شناسه‌ای از نور یا گراف دانش ندارد.');
  return card(title, VGROUP_HELP(), VER === 'raw' ? el('p', { class: 'small muted' }, 'نسخه‌ی raw ادغامی ندارد؛ هر شناسه گروه خودش است.') : null, body);
}
function extPersonLink(ext) {
  const span = el('span', { class: 'small muted' }, '…');
  const [k, ...r] = ext.split(':');
  api(`/by-ext/${k}/${r.join(':')}`).then(p => fill(span, link('/p/' + p.id, p.display_name))).catch(() => fill(span, '—'));
  return span;
}
function vDecisionRow(dec, after) {
  const row = el('div', { class: 'erow' }, el('span', { class: 'chip ' + (dec.decision === 'accept' ? 'accepted' : 'rejected') }, DEC_FA[dec.decision] || dec.decision),
    extChip(dec.a), '⟷', extChip(dec.b), dec.note ? el('span', { class: 'small' }, '«' + dec.note + '»') : null,
    el('span', { class: 'small muted' }, [dec.user || dec.by || dec.username, fmtTs(dec.ts || dec.created)].filter(Boolean).join(' ')));
  if (vEditable()) {
    const b = el('button', { class: 'danger' }, 'بازگرداندن تصمیم');
    b.onclick = busy(b, async () => {
      const r = await post(vApi('/decide'), { a: dec.a, b: dec.b, decision: 'clear' });
      await refreshVersions(); toast('تصمیم برداشته شد؛ نسخه دوباره ساخته شد.' + (r.warning ? ' ' + r.warning : '')); (after || render)();
    });
    row.append(b);
  }
  return row;
}
async function refreshVersions() { VERSIONS = await api('/versions').then(r => r.items).catch(() => VERSIONS || []); versionBar(); }

/* ---------- suggestions ---------- */
function pbox(s, side) {
  const p = s[side + '_person'];
  if (!p) return el('div', { class: 'pbox muted' }, 'شخص پیدا نشد');
  return el('div', { class: 'pbox' }, el('div', { class: 'nm' }, link('/p/' + p.id, p.display_name), ' ', el('span', { class: 'small muted' }, '#' + fa(p.id))),
    p.aliases.length ? el('div', { class: 'small muted' }, p.aliases.join(' · ')) : null,
    el('div', {}, p.ext.filter(x => !x.startsWith('wiki:')).map(extChip)),
    el('div', { class: 'small' }, [deathText(p), p.mazhab, p.grade, p.n_narr ? fa(p.n_narr) + ' روایت' : ''].filter(Boolean).join(' · ')));
}
/* the backend's own wording of how a suggestion was found, in Persian */
function methodFa(m) {
  m = String(m || '');
  if (/^narrators\/link_sunni\.py link_kg/.test(m)) return 'پیوند پیشین پایگاه راویان میان نور و گراف دانش';
  if (/^name \+ chronology/.test(m)) return 'شباهت نام، زمان و بافت سند میان نور و گراف دانش';
  if (/^narrators\/dirayah\.py/.test(m)) return 'سندهای مشترک نور و درایة النور';
  if (/^same /.test(m)) return 'راویان هم‌نام در یک پایگاه';
  return m;
}
function noteFa(n) {
  n = String(n || '');
  const m = /^from (\w+):(\d+)(?:, certain link to (\w+):(\d+) \((\d+) shared isnad places\))?\s*$/.exec(n);
  if (!m) return n;
  return `از ${KIND_FA[m[1]] || m[1]} ${fa(m[2])}` + (m[3] ? `؛ پیوند قطعی با ${KIND_FA[m[3]] || m[3]} ${fa(m[4])} (${fa(m[5])} جای مشترک در سندها)` : '');
}
function warnFa(w) {
  let m;
  if ((m = /^death dates differ by (\d+) years/.exec(w))) return `سال وفات‌ها ${fa(m[1])} سال فاصله دارند`;
  if ((m = /^both have ids in the same database: (.*)$/.exec(w))) return `هر دو راوی در یک پایگاه شناسه دارند: ${m[1].split(', ').map(x => (KIND_FA[x] || x)).join('، ')}`;
  return w;
}
function briefOf(s) {
  const e = s.evidence || {};
  return [SUG_FA[s.kind] || s.kind, e.confidence ? 'اطمینان ' + (CONF_FA[e.confidence] || e.confidence) : '', 'امتیاز ' + fa(s.score), e.death_gap != null ? `فاصله‌ی وفات ${fa(e.death_gap)} سال` : '', e.n_aligned ? `${fa(e.n_aligned)} جای مشترک در سندها` : '']
    .filter(Boolean).join(' · ') + ' — شواهد';
}
function evidence(s) {
  const e = s.evidence || {};
  const rows = [];
  if (s.kind === 'link_dirayah') {
    rows.push(el('li', {}, e.n_aligned ? `در ${fa(e.n_aligned)} جای یکسان از سندهای مشترک، این راوی نور (${fa(e.noor_positions)} جا) و این راوی درایة النور (${fa(e.dirayah_positions)} جا) کنار هم آمده‌اند: ${fa(Math.round((e.share_noor || 0) * 100))}٪ از جاهای راوی نور و ${fa(Math.round((e.share_dirayah || 0) * 100))}٪ از جاهای راوی درایه.` : 'شناسه‌ی یکسان و نام یکسان، بی هیچ سند مشترکی برای سنجش.'));
    rows.push(el('li', {}, 'نور: ', el('span', { class: 'ar' }, e.noor_name || ''), ' — درایة النور: ', el('span', { class: 'ar' }, e.dirayah_name || ''),
      e.same_id ? ' (شناسه‌ی یکسان)' : ' (شناسه‌ی متفاوت)'));
    if (e.name_conflict) rows.push(el('li', { class: 'warn' }, 'نام‌ها در نام یا نام پدر با هم نمی‌خوانند؛ برای همین قطعی شمرده نشد.'));
    if (e.noor_other && e.noor_other.length) rows.push(el('li', { class: 'small muted' }, 'در جاهای دیگرِ این راوی نور، درایه این شناسه‌ها را آورده: ' + e.noor_other.map(x => `dirayah:${x[0]} (${fa(x[1])} جا)`).join('؛ ')));
  } else if (s.kind === 'link_kg' || s.kind === 'name_chrono') {
    rows.push(el('li', {}, `اطمینان: ${CONF_FA[e.confidence] || e.confidence || '-'}؛ امتیاز کل ${fa(e.score)} = شباهت نام ${fa(e.name_score)}`,
      ` + زمان ${fa(e.chrono)} + بافت سند ${fa(Math.round(0.35 * Math.min(e.ctx || 0, 3) * 100) / 100)} (${fa(e.ctx || 0)} همسایه‌ی مشترک).`));
    if (e.noor) rows.push(el('li', {}, 'نور: ', el('span', { class: 'ar' }, e.noor.name || ''), ' ', [e.noor.mazhab, e.noor.grade, e.noor.death ? `وفات ${fa(e.noor.death.join('–'))}${e.noor.death_source ? ' (' + e.noor.death_source + ')' : ''}` : 'بی تاریخ وفات'].filter(Boolean).join(' · ')));
    if (e.kg) rows.push(el('li', {}, 'گراف دانش: ', el('span', { class: 'ar' }, e.kg.name || ''), e.kg.nickname ? ` («${e.kg.nickname}»)` : '', ' ', [e.kg.rank, e.kg.death ? 'وفات ' + fa(e.kg.death) : 'بی تاریخ وفات', e.kg.tabaka != null ? 'طبقه ' + fa(e.kg.tabaka) : ''].filter(Boolean).join(' · ')));
    if (e.death_gap != null) rows.push(el('li', {}, `فاصله‌ی سال وفات: ${fa(e.death_gap)} سال (تا ۳۰ سال +۰٫۱۵، تا ۷۰ سال ۰، بیشتر −۰٫۴).`));
    if (e.name && e.name.notes && e.name.notes.length) rows.push(el('li', { class: 'warn' }, 'هشدار نام: ' + e.name.notes.map(n => n === 'father' ? 'نام نخست پرسش، نام پدرِ نامزد است' : 'بخش «ابن …» در نام نامزد نیست').join('؛ ')));
    if (e.context && e.context.length) rows.push(el('li', {}, 'بافت سند: ', el('ul', {}, e.context.map(x => el('li', {},
      `${x.role === 'teacher' ? 'استاد' : 'شاگرد'} در نور: ${x.ravi_name || x.ravi} ↔ در گراف: ${x.kg_name || x.kg_id} (${fa(x.chains)} سند)`)))));
    if (e.alternatives && e.alternatives.length) rows.push(el('li', { class: 'small muted' }, 'نامزدهای دیگر در گراف: ' + e.alternatives.map(a => `${a[1]} (kg:${a[0]}، ${fa(a[2])})`).join('؛ ')));
  } else {
    rows.push(el('li', {}, e.same_name_key ? 'نام یکسان (پس از یکسان‌سازی املا).' : 'همان واژه‌های نام با همان نام و نام پدر (ترتیب بقیه فرق دارد).'));
    rows.push(el('li', {}, e.death_gap != null ? `هر دو تاریخ وفات دارند، با ${fa(e.death_gap)} سال فاصله.` : 'هیچ‌کدام تاریخ وفات ندارند.'));
    rows.push(el('li', {}, `${fa(e.group_size)} راوی با این نام در همین پایگاه هست؛ ${fa(e.shared_neighbours || 0)} استاد یا شاگرد مشترک`,
      e.shared && e.shared.length ? ': ' + e.shared.map(x => x.name || x.id).join('، ') : '.'));
    for (const side of ['a', 'b']) if (e[side]) rows.push(el('li', { class: 'small' }, e[side].id + ': ', el('span', { class: 'ar' }, e[side].name || ''), ' ',
      [e[side].nickname, e[side].mazhab, e[side].grade, e[side].rank, e[side].death ? 'وفات ' + fa([].concat(e[side].death).join('–')) : '', e[side].n_slots ? fa(Math.round(e[side].n_slots)) + ' جایگاه' : '', e[side].riwaya_qty ? fa(e[side].riwaya_qty) + ' روایت' : ''].filter(Boolean).join(' · ')));
  }
  return el('div', { class: 'ev' }, el('div', { class: 'small muted' }, methodFa(e.method)), el('ul', {}, rows),
    el('details', { class: 'small' }, el('summary', { class: 'muted' }, 'همه‌ی شواهد (JSON، برای بررسی فنی)'), el('pre', { class: 'json' }, JSON.stringify(e, null, 1))));
}
function sugBox(s, after, here) {
  const acts = el('div', { class: 'toolbar' });
  if (!STATIC && s.status === 'proposed' && !s.same_now) {
    const keepA = el('button', { class: 'primary' }, 'یکی‌اند — نگه داشتن ' + (s.a_person ? s.a_person.display_name.slice(0, 30) : 'اولی'));
    keepA.onclick = busy(keepA, async () => { await decide(s, 'accepted', s.a_now); after(); });
    const keepB = el('button', {}, 'نگه داشتن ' + (s.b_person ? s.b_person.display_name.slice(0, 30) : 'دومی'));
    keepB.onclick = busy(keepB, async () => { await decide(s, 'accepted', s.b_now); after(); });
    const dis = el('button', { class: 'danger' }, 'یکی نیستند (رد پیشنهاد)');
    dis.onclick = busy(dis, async () => { await decide(s, 'dismissed'); after(); });
    acts.append(keepA, keepB, dis, link(`/merge?a=${s.a_now}&b=${s.b_now}`, el('span', { class: 'btn' }, 'مقایسه‌ی کامل')));
  } else if (!STATIC && s.status === 'dismissed') {
    const re = el('button', {}, 'بازگشایی پیشنهاد');
    re.onclick = busy(re, async () => { await decide(s, 'proposed'); after(); });
    acts.append(re);
  } else if (!STATIC && s.status === 'proposed' && s.same_now) {
    const ok = el('button', {}, 'بستن (اکنون یک نفرند)');
    ok.onclick = busy(ok, async () => { await decide(s, 'accepted'); after(); });
    acts.append(ok);
  }
  return el('div', { class: 'sug' + (s.same_now ? ' same' : '') },
    el('div', { class: 'toolbar' }, el('b', {}, SUG_FA[s.kind] || s.kind), el('span', { class: 'chip ' + s.status }, SSTAT_FA[s.status] || s.status),
      el('span', { class: 'small muted' }, `امتیاز ${fa(s.score)} · پیشنهاد #${fa(s.id)}`), s.same_now ? el('span', { class: 'small good' }, 'این دو اکنون یک شخص‌اند.') : null,
      s.decided_by ? el('span', { class: 'small muted' }, `${s.decided_by} ${fmtTs(s.decided_ts)}`) : null),
    el('div', { class: 'pair' }, pbox(s, 'a'), el('div', { class: 'mid' }, '⟷'), pbox(s, 'b')),
    el('details', { class: 'evd' }, el('summary', { class: 'small' }, briefOf(s)), evidence(s)), acts);
}
async function decide(s, decision, keep) {
  const note = decision === 'dismissed' ? (prompt('چرا یکی نیستند؟ (اختیاری)') || '') : '';
  const r = await post('/suggestion/' + s.id, { decision, keep: keep || null, note });
  if (decision === 'accepted' && r.person) toast(`ادغام شد؛ شخص #${fa(r.person)}`);
  return r;
}
function toast(t) { const d = el('div', { class: 'ok toast', role: 'status' }, t); document.body.append(d); setTimeout(() => d.remove(), 3500); }

const SUG_HELP = () => help(
  P('این‌ها جفت‌هایی از راویان‌اند که برنامه گمان می‌کند یک نفر باشند. هیچ‌کدام خودکار ادغام نشده است؛ تصمیم با شماست. پنج گونه پیشنهاد هست:'),
  UL('«پیوند پیشین نور↔گراف» (link_kg): پیوندهای پایگاه راویان (narrators/link_sunni.py) با اطمینان بالا یا متوسط میان یک راوی نور و یک راوی گراف دانش، از روی نام، زمان و بافت سند.',
    '«نام + تاریخ + سند» (name_chrono): همان روش، دوباره روی همه‌ی راویان نور اجرا شده: شباهت نام (سهم واژه‌های نام نور که در نام و شهرت راوی گراف آمده، با جریمه برای وقتی نام نخست، نام پدر نامزد است)، زمان (اگر فاصله‌ی سال وفات تا ۳۰ سال باشد +۰٫۱۵، تا ۷۰ سال ۰، بیشتر −۰٫۴؛ برای نور، وفات از کتاب‌های رجال یا دوره‌ی امامی که از او روایت کرده) و بافت سند (برای هر استاد یا شاگرد نور که نامزد گرافیِ او در سندهای گراف کنار این نامزد آمده +۰٫۳۵، حداکثر سه). برای راویان امامی دست‌کم یک همسایه‌ی مشترک لازم است.',
    '«سند مشترک (نور↔درایة النور)» (link_dirayah): درایة النور (نرم‌افزار رجال مرکز نور) همان کتاب‌ها را سندشناسی کرده است. هر سند درایه به حدیث نورِ خودش وصل شده و راویان دو سند جای‌به‌جای کنار هم گذاشته شده‌اند. جفتی که دست‌کم ۳ بار در یک جا آمده، ۹۵٪ جاهای هر دو طرف را دارد و نامشان (نام و نام پدر) ناسازگار نیست، قطعی است و از پیش در یک شخص است؛ این‌جا فقط جفت‌های ضعیف‌تر می‌آیند (شواهد کمتر، نام ناسازگار، یا شناسه و نام یکسان بی سند مشترک). شماره‌ی یکسان به تنهایی دلیل نیست: در دو پایگاه گاه یک شماره دو نفر است.',
    '«تکراری در نور» و «تکراری در گراف دانش»: دو راوی یک پایگاه با نام یکسان (یا همان واژه‌ها با همان نام و نام پدر) که سال وفاتشان کمتر از ۵ سال فاصله دارد یا هیچ‌کدام تاریخ ندارند؛ در گراف، جفت بی‌تاریخ باید دست‌کم یک استاد یا شاگرد مشترک داشته باشد. نام‌هایی که بیش از ۸ نفر دارند کنار گذاشته شده‌اند.'),
  P('«یکی‌اند» دو شخص را ادغام می‌کند: شخصی که نگه می‌دارید شماره و نام نمایشی‌اش می‌ماند، همه‌ی شناسه‌ها و مقدارهای دیگری (با منبعشان) به آن منتقل می‌شود، و شخص دیگر «ادغام‌شده» علامت می‌خورد (پیوندهای قدیمی به آن، به شخص نگه‌داشته می‌روند). ادغام یک نسخه در تاریخچه و یک مورد در صف بازبینی می‌سازد و با «بازگردانی» برمی‌گردد. «یکی نیستند» فقط پیشنهاد را می‌بندد (و با اجرای دوباره‌ی جستجوی شباهت، دیگر پیشنهاد نمی‌شود)؛ می‌توانید آن را دوباره باز کنید.'),
  P('ادغام در ویکی، تصمیم پروژه‌های ICM را عوض نمی‌کند: در مرحله‌ی ۴ هر پروژه فقط به صورت پیشنهاد دیده می‌شود.'));

/* a suggestion card that, once decided, folds to one line without reloading the list */
function sugHolder(s, onDone) {
  const holder = el('div', { class: 'sugh', tabindex: 0, 'aria-label': `پیشنهاد ${fa(s.id)}` });
  const after = () => {
    const next = holder.nextElementSibling;
    holder.replaceChildren(el('div', { class: 'sug done small', role: 'status' }, `✓ تصمیم پیشنهاد #${fa(s.id)} ثبت شد.`));
    holder.removeAttribute('tabindex');
    if (onDone) onDone();
    if (next && next.classList.contains('sugh')) next.focus();
  };
  holder.append(sugBox(s, after));
  return holder;
}
function sugKeys(root) {
  root.addEventListener('keydown', ev => {
    if (ev.ctrlKey || ev.metaKey || ev.altKey || ev.target.closest('input,textarea,select,summary,button')) return;
    const cards = [...root.querySelectorAll('.sugh[tabindex]')], i = cards.findIndex(c => c.contains(ev.target) || c === ev.target);
    const go1 = n => { if (cards[n]) { ev.preventDefault(); cards[n].focus(); cards[n].scrollIntoView({ block: 'center' }); } };
    if (ev.key === 'j') go1(i < 0 ? 0 : i + 1); else if (ev.key === 'k') go1(i < 0 ? 0 : i - 1);
    else if (i >= 0 && ev.key === 'a') { const b = cards[i].querySelector('button.primary'); if (b) { ev.preventDefault(); b.click(); } }
    else if (i >= 0 && ev.key === 'd') { const b = cards[i].querySelector('button.danger'); if (b) { ev.preventDefault(); b.click(); } }
    else if (i >= 0 && ev.key === 'Enter' && ev.target === cards[i]) { const d = cards[i].querySelector('details.evd'); if (d) { ev.preventDefault(); d.open = !d.open; } }
  });
}
async function suggestionsPage() {
  if (VER) return vSuggestionsPage();
  const qs = new URLSearchParams(location.search);
  const kind = el('select', { 'aria-label': 'گونه‌ی پیشنهاد' }, [['', 'همه‌ی گونه‌ها'], ...Object.entries(SUG_FA)].map(([v, t]) => el('option', { value: v }, t)));
  kind.value = qs.get('kind') || '';
  const status = el('select', { 'aria-label': 'وضعیت پیشنهاد' }, [['proposed', 'باز'], ['accepted', 'پذیرفته'], ['dismissed', 'ردشده'], ['', 'همه']].map(([v, t]) => el('option', { value: v }, t)));
  status.value = qs.has('status') ? qs.get('status') : 'proposed';
  const out = el('div'), counts = el('div', { class: 'small muted' });
  let offset = 0;
  const run = async () => {
    history.replaceState({}, '', url(`/suggestions?kind=${kind.value}&status=${status.value}`));
    fill(out, el('p', { class: 'muted' }, 'خواندن…'));
    try {
      const r = await api(`/suggestions?kind=${kind.value}&status=${status.value}&offset=${offset}&limit=20`);
      fill(counts, Object.entries(SUG_FA).map(([k, t]) => `${t}: ${fa(r.counts[k + '|proposed'] || 0)} باز، ${fa(r.counts[k + '|accepted'] || 0)} پذیرفته، ${fa(r.counts[k + '|dismissed'] || 0)} رد`).join(' — '));
      fill(out, el('div', { class: 'small muted' }, `${fa(r.total)} پیشنهاد، به ترتیب امتیاز. میان‌بُرها (کارت را با Tab یا j / k برگزینید): a پذیرفتن (نگه داشتن راوی نخست)، d رد پیشنهاد، Enter شواهد.`),
        r.items.map(s => sugHolder(s, refreshCounts)), pager(r.total, offset, 20, o => { offset = o; run(); window.scrollTo(0, 0); }));
    } catch (e) { fill(out, errBox(e)); }
  };
  kind.onchange = status.onchange = () => { offset = 0; run(); };
  const refreshCounts = async () => { try { const r = await api(`/suggestions?kind=${kind.value}&status=${status.value}&offset=0&limit=1`); fill(counts, Object.entries(SUG_FA).map(([k, t]) => `${t}: ${fa(r.counts[k + '|proposed'] || 0)} باز، ${fa(r.counts[k + '|accepted'] || 0)} پذیرفته، ${fa(r.counts[k + '|dismissed'] || 0)} رد`).join(' — ')); } catch (_) {} };
  sugKeys(out);
  fill($app, el('h1', {}, 'پیشنهادهای ادغام'), SUG_HELP(), card(null, el('div', { class: 'toolbar' }, kind, status), counts), out);
  await run();
}


/* ---------- suggestions in version mode ---------- */
const VSUG_HELP = () => help(
  P('این‌جا همان پیشنهادهای ادغامِ ویکی را می‌بینید، ولی با وضعیتشان در نسخه‌ی انتخاب‌شده: «ادغام‌شده» (قاعده‌های نسخه این دو شناسه را یکی کرده‌اند)، «ردشده» (زیر آستانه، بازنده‌ی ادغام یک‌به‌یک، یا ردِ دستی) یا «پیشنهادی» (هنوز تصمیمی نیست). شواهد همان‌هایی است که در حالت کلاسیک هم می‌بینید.'),
  UL('«ادغام در این نسخه» دو شناسه را به زور یکی می‌کند (قاعده‌ی «دستی»، امتیاز ۱) و بر قاعده‌های خودکار پیشی دارد. «ردکردن در این نسخه» این جفت را از ادغام برمی‌دارد. «بازگرداندن تصمیم» تصمیم دستی را پاک می‌کند.',
    'هر تصمیم فقط روی همین نسخه اثر دارد، نسخه را دوباره می‌سازد، و پروژه‌های ICM آن نسخه در پیشروی بعدی از مرحله‌ی ۴ دوباره حساب می‌کنند. ادغامِ ویکی تغییر نمی‌کند.',
    'ردکردن فقط همان جفت را مسدود می‌کند: چیزی را که خودِ ویکی یکی کرده (ادغام ویکی، پیوند قطعی درایه) جدا نمی‌کند و هشدار می‌دهد؛ اگر دو شناسه از راه دیگری هم (گروه‌های زنجیره‌ای) به هم وصل باشند، ردِ جفت دوم هم لازم است.',
    'در نسخه‌ی raw هیچ ادغامی نیست و دکمه‌ای نیست.'));
function groupChips(members) {
  return el('div', { class: 'erow' }, (members || []).map(m => el('span', {}, extChip(m.id), el('span', { class: 'small muted' }, ' ' + ruleFa(m.rule) + (m.score != null && m.rule !== 'seed' ? ' ' + fa(Math.round(m.score * 100) / 100) : '')), ' ')));
}
function vsugBox(s, onChange) {
  const box = el('div');
  let result = null, form = null;
  const draw = () => {
    const st = s.version_state, editable = vEditable() && s.a_ext && s.b_ext;
    const chips = [el('span', { class: 'chip ' + (st === 'merged' ? 'accepted' : st === 'rejected' ? 'rejected' : 'proposed') }, VSTATE_FA[st] || st)];
    if (st === 'merged' && s.version_rule) chips.push(el('span', { class: 'chip ghost' }, 'قاعده: ' + ruleFa(s.version_rule)));
    if (s.conflict_reason) chips.push(el('span', { class: 'chip ghost' }, CONFLICT_FA[s.conflict_reason] || s.conflict_reason));
    const acts = el('div', { class: 'toolbar' });
    if (editable) {
      if (st !== 'merged') acts.append(el('button', { class: 'primary', onclick: () => ask('accept') }, 'ادغام در این نسخه'));
      if (st !== 'rejected') acts.append(el('button', { class: 'danger', onclick: () => ask('reject') }, 'ردکردن در این نسخه'));
      if (s.decision) { const c = el('button', {}, 'بازگرداندن تصمیم'); c.onclick = busy(c, () => send('clear', '')); acts.append(c); }
    } else if (VER === 'raw') acts.append(el('span', { class: 'small muted' }, 'نسخه‌ی raw ادغامی ندارد و فقط‌خواندنی است.'));
    else if (!(s.a_ext && s.b_ext)) acts.append(el('span', { class: 'small muted' }, 'شناسه‌ی منبع یکی از دو طرف پیدا نشد؛ تصمیم ممکن نیست.'));
    if (s.a_ext && s.b_ext) acts.append(link(`/merge?a=${s.a_now}&b=${s.b_now}`, el('span', { class: 'btn' }, 'مقایسه‌ی کامل')));
    fill(box, el('div', { class: 'sug' },
      el('div', { class: 'toolbar' }, el('b', {}, SUG_FA[s.kind] || s.kind), ...chips, el('span', { class: 'small muted' }, `امتیاز ${fa(s.score)} · پیشنهاد #${fa(s.id)}`),
        s.a_ext && s.b_ext ? el('span', { class: 'small muted' }, `${s.a_ext} ⟷ ${s.b_ext}`) : null),
      s.decision ? vDecisionRow(s.decision, () => { s.decision = null; onChange(true); }) : null,
      el('div', { class: 'pair' }, pbox(s, 'a'), el('div', { class: 'mid' }, '⟷'), pbox(s, 'b')),
      el('details', { class: 'evd' }, el('summary', { class: 'small' }, briefOf(s)), evidence(s)), acts, form, result));
  };
  async function send(decision, note) {
    const r = await post(vApi('/decide'), { a: s.a_ext, b: s.b_ext, decision, note, kind: s.kind });
    s.version_state = r.state_after; s.conflict_reason = r.conflict;
    s.decision = decision === 'clear' ? null : { a: s.a_ext, b: s.b_ext, decision, note };
    s.version_rule = r.state_after === 'merged' ? (decision === 'accept' ? 'manual' : ((r.group.find(m => m.rule !== 'seed' && (m.id === s.a_ext || m.id === s.b_ext)) || {}).rule || null)) : null;
    form = null;
    result = el('div', { class: r.warning ? 'err' : 'ok' },
      el('div', {}, `وضعیت: ${VSTATE_FA[r.state_before] || r.state_before} ← ${VSTATE_FA[r.state_after] || r.state_after}` + (r.conflict ? ` (${CONFLICT_FA[r.conflict] || r.conflict})` : '')),
      r.warning ? el('div', {}, 'هشدار: ' + (WARN_FA[r.conflict] || r.warning)) : null,
      el('div', { class: 'small' }, 'گروه حاصل:'), groupChips(r.group), el('div', { class: 'small muted' }, 'نسخه دوباره ساخته شد؛ پروژه‌های ICM این نسخه از مرحله‌ی ۴ دوباره حساب می‌کنند.'));
    draw(); onChange(false);
  }
  function ask(decision) {
    const note = el('input', { type: 'text', placeholder: 'یادداشت کوتاه (اختیاری)', class: 'wide' });
    const ok = el('button', { class: decision === 'accept' ? 'primary' : 'danger' }, decision === 'accept' ? 'تأیید ادغام' : 'تأیید رد');
    ok.onclick = busy(ok, () => send(decision, note.value));
    form = el('div', { class: 'card' }, el('div', {}, (decision === 'accept' ? 'ادغام' : 'ردکردن') + ` ${s.a_ext} و ${s.b_ext} فقط در نسخه‌ی ${VER}؟`),
      el('div', { class: 'toolbar' }, note, ok, el('button', { onclick: () => { form = null; draw(); } }, 'انصراف')));
    draw();
  }
  draw();
  return box;
}
const WARN_FA = { manual_reject_ignored: 'این دو شناسه در خودِ ویکی یک شخص‌اند؛ هویت ویکی غالب است و ردِ شما اعمال نشد.',
  manual_reject_transitive: 'این دو شناسه از راه ادغام‌های دیگر در این نسخه هنوز با هم‌اند؛ فقط یالِ مستقیم برداشته شد (برای جدا شدن، جفت دیگر زنجیره را هم رد کنید).' };
async function vSuggestionsPage() {
  const qs = new URLSearchParams(location.search);
  const kind = el('select', { 'aria-label': 'گونه‌ی پیشنهاد' }, [['', 'همه‌ی گونه‌ها'], ...Object.entries(SUG_FA)].map(([v, t]) => el('option', { value: v }, t)));
  kind.value = qs.get('kind') || '';
  const STAT = [['proposed', 'پیشنهادی'], ['merged', 'ادغام‌شده'], ['rejected', 'ردشده'], ['all', 'همه']];
  const status = el('select', { 'aria-label': 'وضعیت پیشنهاد' }, STAT.map(([v, t]) => el('option', { value: v }, t)));
  status.value = STAT.some(x => x[0] === qs.get('status')) ? qs.get('status') : 'proposed';
  const out = el('div');
  let offset = 0;
  const setCounts = c => {
    const tot = (c.proposed || 0) + (c.merged || 0) + (c.rejected || 0);
    [...status.options].forEach(o => { const base = STAT.find(x => x[0] === o.value)[1]; o.textContent = `${base} (${fa(o.value === 'all' ? tot : c[o.value] || 0)})`; });
  };
  const run = async () => {
    history.replaceState({}, '', url(`/suggestions?kind=${kind.value}&status=${status.value}`));
    fill(out, el('p', { class: 'muted' }, 'خواندن…'));
    try {
      const r = await api(`${vApi('/suggestions')}?kind=${kind.value}&status=${status.value}&offset=${offset}&limit=20`);
      setCounts(r.counts);
      const onChange = async full => {
        const c = await api(`${vApi('/suggestions')}?kind=${kind.value}&status=proposed&limit=1`).catch(() => null);
        if (c) setCounts(c.counts);
        if (full) run();
      };
      fill(out, el('div', { class: 'small muted' }, `${fa(r.total)} پیشنهاد، به ترتیب امتیاز`), r.items.length ? r.items.map(s => vsugBox(s, onChange)) : el('p', { class: 'muted' }, 'موردی نیست.'),
        pager(r.total, offset, 20, o => { offset = o; run(); window.scrollTo(0, 0); }));
    } catch (e) { fill(out, errBox(e)); }
  };
  kind.onchange = status.onchange = () => { offset = 0; run(); };
  fill($app, el('h1', {}, `پیشنهادهای ادغام در نسخه‌ی ${VER}`), VSUG_HELP(),
    VER === 'raw' ? el('div', { class: 'card muted' }, 'نسخه‌ی raw هیچ ادغامی ندارد؛ این‌جا فقط می‌توانید پیشنهادها را ببینید و تصمیمی ثبت نمی‌شود.') : null,
    card(null, el('div', { class: 'toolbar' }, kind, status)), out);
  await run();
}

/* ---------- edit ---------- */
const EDIT_HELP = () => help(
  P('در این صفحه مقدارهای این راوی را تغییر می‌دهید. سه کار ممکن است:'),
  UL('تغییر «نام نمایشی» (عنوان صفحه و نامی که در جستجو و پیشنهادها دیده می‌شود)،',
    'کنار گذاشتن هر مقدار، از هر منبعی (مثلاً تاریخ وفات نادرستی که از گراف آمده): مقدار پاک نمی‌شود؛ «کنارگذاشته» علامت می‌خورد، در صفحه‌ی راوی زیر «مقدارهای کنارگذاشته» و در تاریخچه می‌ماند،',
    'افزودن مقدار تازه برای هر ویژگی؛ منبع آن «ویرایش کاربر» با نام شما و زمان ثبت می‌شود. سال وفات (از / تا) باید عدد هجری قمری باشد؛ برای تاریخ دقیق هر دو را یکی بنویسید.'),
  P('با «ذخیره» تغییر فوراً اعمال می‌شود و یک نسخه‌ی «ویرایش» در تاریخچه و در صف بازبینی ثبت می‌شود. بازبین می‌تواند آن را تأیید یا رد کند؛ رد کردن (با بازگردانی) فقط همین مقدارها را برمی‌گرداند و ویرایش‌های بعدی دیگران را دست نمی‌زند. «یادداشت» دلیل یا منبع تغییر را برای بازبین می‌نویسد.'));

async function edit(id) {
  const d = await api('/person/' + id);
  const p = d.person;
  setTitle('ویرایش: ' + p.display_name);
  const dn = el('input', { type: 'text', value: p.display_name, class: 'wide', 'aria-label': 'نام نمایشی' });
  const retire = new Set();
  const rows = el('div');
  const addRow = () => {
    const f = el('select', { 'aria-label': 'ویژگی' }, FIELDS.map(k => el('option', { value: k }, FIELD_FA[k])));
    const v = el('input', { type: 'text', class: 'wide', placeholder: 'مقدار', 'aria-label': 'مقدار' });
    const row = el('div', { class: 'erow' }, f, v, el('button', { type: 'button', 'aria-label': 'برداشتن این ردیف', onclick: () => row.remove() }, '×'));
    row._get = () => [f.value, v.value.trim()];
    rows.append(row);
  };
  addRow();
  const note = el('textarea', { placeholder: 'یادداشت برای بازبین: دلیل یا منبع تغییر (اختیاری)' });
  const save = el('button', { class: 'primary' }, 'ذخیره');
  save.onclick = busy(save, async () => {
    const add = [...rows.children].map(r => r._get()).filter(([, v]) => v);
    const body = { display_name: dn.value.trim() !== p.display_name ? dn.value.trim() : null, add, retire: [...retire], note: note.value };
    if (!body.display_name && !add.length && !retire.size) throw new Error('چیزی تغییر نکرده است');
    await post('/person/' + p.id, body);
    go('/p/' + p.id);
  });
  fill($app, el('h1', {}, 'ویرایش: ', link('/p/' + p.id, p.display_name)), EDIT_HELP(),
    card('نام نمایشی', dn),
    card('مقدارهای کنونی', el('p', { class: 'small muted' }, 'تیک = کنار گذاشتن این مقدار'),
      el('table', {}, el('tbody', {}, p.fields.map(f => el('tr', {},
        el('td', {}, el('input', { type: 'checkbox', 'aria-label': `کنار گذاشتن: ${FIELD_FA[f.field] || f.field} — ${f.value}`, onchange: ev => ev.target.checked ? retire.add(f.id) : retire.delete(f.id) })),
        el('td', { class: 'muted' }, FIELD_FA[f.field] || f.field), el('td', {}, f.value), el('td', { class: 'prov' }, srcChip(f.source), f.ext_kind ? ` ${f.ext_kind}:${f.ext_id}` : '')))))),
    card('افزودن مقدار', rows, el('button', { type: 'button', onclick: addRow }, '+ مقدار دیگر')),
    card('یادداشت', note, el('div', { class: 'toolbar' }, save, link('/p/' + p.id, el('span', { class: 'btn' }, 'انصراف')))));
}

/* ---------- new person ---------- */
async function newPage() {
  const dn = el('input', { type: 'text', class: 'wide', placeholder: 'نام راوی' });
  const rows = el('div');
  const addRow = (k) => {
    const f = el('select', { 'aria-label': 'ویژگی' }, FIELDS.map(x => el('option', { value: x }, FIELD_FA[x])));
    if (k) f.value = k;
    const v = el('input', { type: 'text', class: 'wide', 'aria-label': 'مقدار' });
    const row = el('div', { class: 'erow' }, f, v, el('button', { type: 'button', 'aria-label': 'برداشتن این ردیف', onclick: () => row.remove() }, '×'));
    row._get = () => [f.value, v.value.trim()];
    rows.append(row);
  };
  ['kunya', 'death_lo', 'death_hi', 'mazhab'].forEach(addRow);
  const note = el('textarea', { placeholder: 'منبع: این راوی را کجا یافتید؟' });
  const save = el('button', { class: 'primary' }, 'ساختن');
  save.onclick = busy(save, async () => {
    const r = await post('/person', { display_name: dn.value, fields: [...rows.children].map(r => r._get()).filter(([, v]) => v), note: note.value });
    go('/p/' + r.person);
  });
  fill($app, el('h1', {}, 'راوی تازه'), help(
    P('برای راوی‌ای که در هیچ‌کدام از دو پایگاه نیست (یا هنوز پیدا نشده) یک شخص تازه بسازید. شخص تازه یک شناسه‌ی «ویکی» می‌گیرد و همه‌ی مقدارهایش منبع «ویرایش کاربر» دارند.'),
    P('پیش از ساختن، او را جستجو کنید: اگر در نور یا گراف دانش هست، همان را ویرایش کنید. اگر بعداً معلوم شد همان راوی منبعی است، دو شخص را ادغام کنید. ساختن یک نسخه‌ی «افزودن» در صف بازبینی ثبت می‌کند؛ رد کردن آن، شخص را «حذف‌شده» علامت می‌زند.')),
    card('نام', dn), card('ویژگی‌ها', rows, el('button', { type: 'button', onclick: () => addRow() }, '+ ویژگی دیگر')), card('یادداشت', note, save));
}

/* ---------- split ---------- */
async function splitPage(id) {
  const d = await api('/person/' + id);
  const p = d.person;
  setTitle('جداسازی: ' + p.display_name);
  const ext = new Set(), fids = new Set();
  const label = el('input', { type: 'text', class: 'wide', placeholder: 'نام شخص تازه' });
  const note = el('textarea', { placeholder: 'چرا این‌ها دو نفرند؟' });
  const go1 = el('button', { class: 'primary' }, 'جدا کردن');
  go1.onclick = busy(go1, async () => {
    const r = await post(`/person/${p.id}/split`, { ext: [...ext], fields: [...fids], label: label.value, note: note.value });
    go('/p/' + r.person);
  });
  fill($app, el('h1', {}, 'جداسازی: ', link('/p/' + p.id, p.display_name)), help(
    P('اگر این شخص در واقع دو نفر است (مثلاً یک ادغام نادرست، یا دو راوی هم‌نام در یک منبع)، بخشی از آن را به یک شخص تازه منتقل کنید.'),
    UL('شناسه‌های منبعی (نور / گراف) را که به نفر دیگر تعلق دارند تیک بزنید. مقدارهایی که از همان شناسه آمده‌اند (منبعشان همان شناسه است) خودکار با آن می‌روند.',
      'مقدارهای دیگر (مثلاً ویرایش‌های کاربران) را هم جداگانه می‌توانید تیک بزنید.',
      'برای شخص تازه یک نام بنویسید. او یک شناسه‌ی «ویکی» تازه می‌گیرد؛ شخص اصلی شماره‌ی خودش و دست‌کم یک شناسه را نگه می‌دارد.'),
    P('جداسازی فوراً اعمال می‌شود، در تاریخچه‌ی هر دو شخص و در صف بازبینی می‌آید و با «بازگردانی» برمی‌گردد (اگر پس از آن کسی یکی از این دو را تغییر نداده باشد).')),
    card('شناسه‌های منبع', p.ext.filter(x => !x.startsWith('wiki:')).map(x => el('label', { class: 'ck' }, el('input', { type: 'checkbox', onchange: ev => ev.target.checked ? ext.add(x) : ext.delete(x) }), extChip(x)))),
    card('مقدارها', p.fields.map(f => el('label', { class: 'ck' }, el('input', { type: 'checkbox', onchange: ev => ev.target.checked ? fids.add(f.id) : fids.delete(f.id) }),
      el('span', { class: 'muted' }, FIELD_FA[f.field] || f.field), ' ', f.value, ' ', srcChip(f.source), f.ext_kind ? el('span', { class: 'prov' }, ` ${f.ext_kind}:${f.ext_id}`) : null))),
    card('شخص تازه', label, note, el('div', { class: 'toolbar' }, go1)));
}

/* ---------- merge ---------- */
function picker(title, initial, onPick) {
  const box = el('div', { class: 'pick' });
  const q = el('input', { type: 'search', placeholder: 'نام یا شناسه', class: 'wide' });
  const res = el('div', { class: 'res' });
  const chosen = el('div');
  const pick = p => { fill(chosen, el('div', { class: 'pbox sel' }, el('b', {}, p.display_name), ' #' + fa(p.id), ' ', p.ext.filter(x => !x.startsWith('wiki:')).map(extChip), ' ', el('span', { class: 'small' }, deathText(p)))); fill(res); onPick(p); };
  const run = async () => {
    const r = await api('/search?limit=15&q=' + encodeURIComponent(q.value.trim()));
    fill(res, r.items.map(p => el('div', { onclick: () => pick(p) }, p.display_name, ' ', el('span', { class: 'small muted' }, `#${fa(p.id)} ${p.ext.filter(x => !x.startsWith('wiki:')).join(' ')} ${deathText(p)}`))));
  };
  q.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); run(); } });
  fill(box, el('h3', {}, title), el('div', { class: 'toolbar' }, q, el('button', { onclick: run }, 'جستجو')), res, chosen);
  if (initial) api('/search?q=' + initial).then(r => r.items[0] && pick(r.items[0])).catch(() => {});
  return box;
}
async function mergePage() {
  const qs = new URLSearchParams(location.search);
  let A = null, B = null;
  const prev = el('div');
  let update = async () => {
    history.replaceState({}, '', url(`/merge?a=${A ? A.id : ''}&b=${B ? B.id : ''}`));
    if (!A || !B) { fill(prev, el('p', { class: 'muted' }, 'دو راوی را برگزینید.')); return; }
    if (A.id === B.id) { fill(prev, errBox('هر دو یک شخص‌اند.')); return; }
    fill(prev, el('p', { class: 'muted' }, 'مقایسه…'));
    try {
      const m = await api(`/merge/preview?a=${A.id}&b=${B.id}`);
      let keep = m.a.id;
      const radio = (p, on) => el('label', { class: 'ck' }, el('input', { type: 'radio', name: 'keep', checked: on, onchange: () => keep = p.id }), 'نگه داشتن ', el('b', {}, p.display_name), ' #' + fa(p.id));
      const note = el('textarea', { placeholder: 'چرا یک نفرند؟ (منبع، شاهد)' });
      const ok = el('button', { class: 'primary' }, 'ادغام کن');
      ok.onclick = busy(ok, async () => { const r = await post('/merge', { a: m.a.id, b: m.b.id, keep, note: note.value }); go('/p/' + r.person); });
      fill(prev, card('پیش‌نمایش',
        m.warnings.length ? el('div', { class: 'err', role: 'alert' }, 'هشدار: ', m.warnings.map(warnFa).join('؛ ')) : null,
        el('div', { class: 'tscroll' }, el('table', { class: 'diff' },
          el('thead', {}, el('tr', {}, el('th', {}, 'ویژگی'), el('th', {}, '#' + fa(m.a.id) + ' ' + m.a.display_name), el('th', {}, '#' + fa(m.b.id) + ' ' + m.b.display_name), el('th', {}, ''))),
          el('tbody', {},
            el('tr', {}, el('td', { class: 'muted' }, 'شناسه‌ها'), el('td', {}, m.a_ext.map(extChip)), el('td', {}, m.b_ext.map(extChip)), el('td', {})),
            m.fields.map(f => el('tr', {}, el('td', { class: 'muted' }, FIELD_FA[f.field] || f.field), el('td', {}, f.a.join(' · ')), el('td', {}, f.b.join(' · ')),
              el('td', { class: 'small ' + (f.conflict ? 'warn' : 'muted') }, f.same ? 'یکسان' : f.conflict ? 'ناسازگار' : '')))))),
        el('p', { class: 'small muted' }, 'پس از ادغام همه‌ی مقدارهای هر دو ستون با منبعشان در شخص نگه‌داشته می‌مانند؛ مقدار ناسازگار را بعداً در «ویرایش» کنار بگذارید.'),
        m.suggestions.length ? el('div', {}, el('h3', {}, 'پیشنهادهای مربوط'), m.suggestions.map(s => evidence(s))) : null,
        radio(m.a, true), radio(m.b, false), note, el('div', { class: 'toolbar' }, ok)));
    } catch (e) { fill(prev, errBox(e)); }
  };
  const vbox = el('div');
  const vUpdate = async () => {
    if (!VER) return;
    if (!A || !B || A.id === B.id) { fill(vbox, card('ادغام فقط در نسخه‌ی ' + VER, el('p', { class: 'muted' }, 'پس از برگزیدن دو راوی، می‌توانید آن‌ها را فقط در این نسخه ادغام کنید.'))); return; }
    const pick = p => { const xs = p.ext.filter(x => /^(noor|kg):/.test(x)); const n = xs.filter(x => x.startsWith('noor:')), g = xs.filter(x => x.startsWith('kg:')); return { xs, def: (n[0] || g[0] || ''), ambiguous: n.length > 1 || (!n.length && g.length > 1) || (xs.length > 1 && n.length === 0) }; };
    const pa = pick(A), pb = pick(B);
    const selFor = (pp, p) => { const s = el('select', { 'aria-label': 'شناسه‌ی منبع ' + p.display_name }, pp.xs.map(x => el('option', { value: x }, x))); s.value = pp.def; return s; };
    const sa = selFor(pa, A), sb = selFor(pb, B);
    const note = el('input', { type: 'text', placeholder: 'یادداشت کوتاه (اختیاری)', class: 'wide' });
    const ok = el('button', { class: 'primary' }, 'ادغام فقط در این نسخه');
    const res = el('div');
    ok.onclick = busy(ok, async () => {
      if (!sa.value || !sb.value) throw new Error('هر دو راوی باید شناسه‌ی نور یا گراف دانش داشته باشند.');
      if (!confirm(`ادغام ${sa.value} و ${sb.value} فقط در نسخه‌ی ${VER}؟`)) return;
      const r = await post(vApi('/decide'), { a: sa.value, b: sb.value, decision: 'accept', note: note.value });
      await refreshVersions();
      fill(res, el('div', { class: 'ok' }, `وضعیت: ${VSTATE_FA[r.state_before] || r.state_before} ← ${VSTATE_FA[r.state_after] || r.state_after}`, ' ', groupChips(r.group)));
    });
    fill(vbox, card('ادغام فقط در نسخه‌ی ' + VER,
      el('p', { class: 'small muted' }, 'این صفحه ادغامِ خودِ ویکی است و به نسخه وابسته نیست. برای اعمال روی همین نسخه از دکمه‌ی زیر استفاده کنید (دو شناسه‌ی اصلی هر راوی؛ در صورت چند شناسه یکی را برگزینید).'),
      !vEditable() ? el('p', { class: 'warn' }, 'این نسخه فقط‌خواندنی است.') : null,
      el('div', { class: 'toolbar' }, el('span', {}, '#' + fa(A.id)), sa, '⟷', el('span', {}, '#' + fa(B.id)), sb),
      pa.xs.length > 1 || pb.xs.length > 1 ? el('p', { class: 'small warn' }, 'یکی از دو راوی چند شناسه دارد؛ شناسه‌ی درست را برگزینید.') : null,
      vEditable() ? el('div', { class: 'toolbar' }, note, ok) : null, res));
  };
  const _update = update;
  update = async () => { await _update(); await vUpdate(); };
  fill($app, el('h1', {}, 'ادغام دو راوی'), VER ? el('div', { class: 'err' }, `نسخه‌ی ${VER} انتخاب شده است؛ ادغامِ این صفحه در خودِ ویکی انجام می‌شود و مخصوص نسخه نیست. برای ادغام فقط در نسخه، از بخش «ادغام فقط در نسخه» زیر استفاده کنید.`) : null, help(
    P('دو شخص را که در واقع یک راوی‌اند یکی کنید: مثلاً راوی نور و همان راوی در گراف دانش، یا دو مدخل تکراری در یک پایگاه.'),
    UL('هر دو را با جستجو پیدا کنید (نام، noor:…، kg:… یا شماره‌ی شخص).', 'پیش‌نمایش همه‌ی ویژگی‌های دو طرف را کنار هم نشان می‌دهد؛ «ناسازگار» یعنی هر دو مقدار دارند و هیچ مقدار مشترکی ندارند (مثلاً سال وفات متفاوت). هشدارها: فاصله‌ی وفات بیش از ۳۰ سال، یا هر دو شناسه‌ای در یک پایگاه دارند.',
      'برگزینید کدام شخص بماند: شماره و نام نمایشی او می‌ماند؛ شناسه‌ها و مقدارهای دیگری به او منتقل می‌شوند و دیگری «ادغام‌شده» علامت می‌خورد.'),
    P('ادغام فوراً اعمال می‌شود، یک نسخه در تاریخچه و یک مورد در صف بازبینی می‌سازد و با «بازگردانی» برمی‌گردد (تا وقتی کسی این دو را پس از آن تغییر نداده باشد). اگر پیشنهادی میان این دو باز باشد، «پذیرفته» علامت می‌خورد.')),
    el('div', { class: 'cols' }, card(null, picker('راوی نخست', qs.get('a'), p => { A = p; update(); })), card(null, picker('راوی دوم', qs.get('b'), p => { B = p; update(); }))), vbox, prev);
  update();
}

/* ---------- history and review ---------- */
function diffTable(rows) {
  if (!rows || !rows.length) return null;
  return el('div', { class: 'tscroll' }, el('table', { class: 'diff small' },
    el('thead', {}, el('tr', {}, ['شخص', 'چه', 'پیش از', 'پس از', 'منبع'].map(t => el('th', {}, t)))),
    el('tbody', {}, rows.map(r => el('tr', {},
      el('td', {}, '#' + fa(r.person)),
      el('td', {}, r.what === 'field' ? (FIELD_FA[r.field] || r.field) : (WHAT_FA[r.what] || r.what)),
      el('td', { class: r.before != null && r.after == null ? 'del' : '' }, r.what === 'status' ? (PSTAT_FA[r.before] || r.before || '') : r.before == null ? '' : String(r.before)),
      el('td', { class: r.after != null && r.before == null ? 'add' : r.after != null ? 'add' : '' }, r.what === 'status' ? (PSTAT_FA[r.after] || r.after || '') : r.after == null ? '' : String(r.after)),
      el('td', { class: 'prov' }, r.source ? srcLabel(r.source) : ''))))));
}
function revBox(rv, after, withPerson) {
  const acts = el('div', { class: 'toolbar' });
  if (!STATIC && rv.kind !== 'seed' && ME.admin) {
    if (rv.review_status !== 'approved') { const b = el('button', { class: 'primary' }, 'تأیید'); b.onclick = busy(b, async () => { await post(`/revision/${rv.id}/review`, { status: 'approved' }); after(); }); acts.append(b); }
    if (rv.review_status !== 'rejected') {
      if (!rv.reverted_by && rv.kind !== 'revert') { const b = el('button', { class: 'danger' }, 'رد و بازگردانی'); b.onclick = busy(b, async () => { await post(`/revision/${rv.id}/review`, { status: 'rejected', revert: true }); after(); }); acts.append(b); }
      const b2 = el('button', {}, 'رد بی بازگردانی'); b2.onclick = busy(b2, async () => { await post(`/revision/${rv.id}/review`, { status: 'rejected' }); after(); }); acts.append(b2);
    }
    if (!rv.reverted_by && (rv.kind !== 'revert' || (rv.after && rv.after.added))) {
      const b3 = el('button', {}, 'بازگردانی');
      b3.onclick = busy(b3, async () => { await post(`/revision/${rv.id}/revert`, { note: prompt('یادداشت بازگردانی (اختیاری)') || '' }); after(); });
      acts.append(b3);
    }
  }
  const seedInfo = rv.kind === 'seed' ? el('div', { class: 'small muted' }, `ساخته از ${(rv.after && rv.after.source || '').replace(/^(\w+):(\d+)/, (_, k, n) => (KIND_FA[k] || k) + ' ' + fa(n))} با ${fa((rv.seed_fields || []).length)} مقدار: `,
    (rv.seed_fields || []).map(f => `${FIELD_FA[f.field] || f.field}: ${f.value}${f.status !== 'current' ? ' (کنارگذاشته)' : ''}`).join(' · ')) : null;
  return el('div', { class: 'rev' },
    el('div', { class: 'hd' }, el('b', {}, REV_FA[rv.kind] || rv.kind), el('span', { class: 'small muted' }, '#' + fa(rv.id)),
      el('span', { class: 'chip ' + rv.review_status }, REVIEW_FA[rv.review_status] || rv.review_status),
      withPerson && rv.person_id ? link('/p/' + rv.person_id, rv.person_id_name || '#' + rv.person_id) : null,
      rv.related_person ? el('span', { class: 'small' }, '↔ ', link('/p/' + rv.related_person + '/history', rv.related_person_name || '#' + rv.related_person)) : null,
      el('span', { class: 'small muted' }, `${rv.user === 'seed' ? 'ساخت اولیه' : (rv.user || '')} — ${fmtTs(rv.ts)}`),
      rv.reviewed_by ? el('span', { class: 'small muted' }, `بازبین: ${rv.reviewed_by} ${fmtTs(rv.reviewed_ts)}`) : null,
      rv.reverts ? el('span', { class: 'small' }, 'بازگرداندن نسخه‌ی #' + fa(rv.reverts)) : null,
      rv.reverted_by ? el('span', { class: 'small warn' }, 'بازگردانده شد با #' + fa(rv.reverted_by)) : null),
    rv.note ? el('div', { class: 'small' }, 'یادداشت: ', noteFa(rv.note)) : null,
    seedInfo, diffTable(rv.diff), acts);
}
async function historyPage(id) {
  const d = await api(`/person/${id}/history`);
  setTitle('تاریخچه: ' + d.person.display_name);
  const draw = () => historyPage(id);
  fill($app, el('h1', {}, 'تاریخچه: ', link('/p/' + d.person.id, d.person.display_name)), help(
    P('همه‌ی نسخه‌هایی که این شخص را ساخته یا تغییر داده‌اند، تازه‌ترین در بالا. هر نسخه نشان می‌دهد چه کسی، کی و چه چیزی را تغییر داده است: جدول «پیش از / پس از» هر مقدار افزوده (سبز) یا کنارگذاشته (قرمز)، شناسه‌های منبعی که جابه‌جا شده‌اند، و تغییر نام نمایشی یا وضعیت.'),
    UL('«ساخت از منبع»: شخص هنگام ساخت پایگاه از نور یا گراف دانش ساخته شد (همیشه تأییدشده؛ برنمی‌گردد — مقدار نادرست منبع را در ویرایش کنار بگذارید).',
      '«ویرایش»، «افزودن راوی»، «ادغام»، «جداسازی» و «بازگردانی»: کارهای کاربران، هر کدام با وضعیت بازبینی.',
      '«بازگردانی» یک نسخه را برمی‌گرداند و خودش نسخه‌ی تازه‌ای است. ویرایش‌ها مقدار‌به‌مقدار برمی‌گردند؛ ادغام و جداسازی فقط وقتی برمی‌گردند که پس از آن‌ها نسخه‌ی دیگری این شخص‌ها را تغییر نداده باشد (نخست آن را برگردانید).')),
    d.revisions.length ? d.revisions.map(rv => revBox(rv, draw, false)) : el('p', { class: 'muted' }, 'نسخه‌ای نیست.'));
}
async function queuePage() {
  const qs = new URLSearchParams(location.search);
  const status = el('select', { 'aria-label': 'وضعیت بازبینی' }, [['unreviewed', 'بازبینی‌نشده'], ['approved', 'تأییدشده'], ['rejected', 'ردشده'], ['', 'همه']].map(([v, t]) => el('option', { value: v }, t)));
  status.value = qs.has('status') ? qs.get('status') : 'unreviewed';
  const kind = el('select', { 'aria-label': 'نوع کار' }, [['', 'همه‌ی کارها'], ...Object.entries(REV_FA).filter(([k]) => k !== 'seed')].map(([v, t]) => el('option', { value: v }, t)));
  kind.value = qs.get('kind') || '';
  const out = el('div');
  let offset = 0;
  const run = async () => {
    history.replaceState({}, '', url(`/queue?status=${status.value}&kind=${kind.value}`));
    fill(out, el('p', { class: 'muted' }, 'خواندن…'));
    try {
      const r = await api(`/queue?status=${status.value}&kind=${kind.value}&offset=${offset}&limit=20`);
      fill(out, el('div', { class: 'small muted' }, `${fa(r.total)} نسخه`), r.items.map(rv => revBox(rv, run, true)), pager(r.total, offset, 20, o => { offset = o; run(); }));
    } catch (e) { fill(out, errBox(e)); }
  };
  status.onchange = kind.onchange = () => { offset = 0; run(); };
  fill($app, el('h1', {}, 'صف بازبینی'), help(
    P('هر تغییری که کاربران در ویکی می‌دهند (ویرایش، افزودن راوی، ادغام، جداسازی، بازگردانی) فوراً اعمال می‌شود، ولی «بازبینی‌نشده» می‌ماند تا یک بازبین آن را ببیند. این صفحه آن‌ها را به ترتیب زمان (قدیمی‌ترین نخست) نشان می‌دهد، هر کدام با جدول «پیش از / پس از».'),
    UL('«تأیید»: تغییر درست است؛ فقط وضعیت بازبینی عوض می‌شود.',
      '«رد و بازگردانی»: تغییر نادرست است؛ برگردانده می‌شود (یک نسخه‌ی «بازگردانی» تأییدشده ساخته می‌شود) و نسخه «ردشده» علامت می‌خورد. اگر ادغام یا جداسازی‌ای پس از خودش تغییر دیگری خورده باشد، بازگردانی رد می‌شود و پیام آن نسخه‌ی بعدی را نام می‌برد.',
      '«رد بی بازگردانی»: فقط علامت می‌زند (مثلاً وقتی تغییر را دستی درست کرده‌اید).', '«بازگردانی»: برگرداندن بی تغییر وضعیت بازبینی.'),
    P(ME.open ? 'اکنون ویکی «باز» است: هر بازدیدکننده (بی‌نام: guest) می‌تواند ویرایش و بازبینی کند، مانند ICM. هر کار با نام کاربر ثبت می‌شود.' : 'ویرایش نیاز به ورود دارد و بازبینی و بازگردانی فقط برای مدیران است.')),
    card(null, el('div', { class: 'toolbar' }, status, kind)), out);
  await run();
}

render();

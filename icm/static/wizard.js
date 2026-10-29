'use strict';
/* ICMWizard: the project page. Its navigation is the site's top bar, which becomes one bar with the project in it: the project's name and
   state, ONE step button (the step on screen; it opens the list of all eleven with their marks), results, settings, and the version.
   The page itself is the step on screen: its title with the run button (nothing runs by itself), what the earlier steps decided that
   matters here, the step's data (the panels of icm.js, one card each) and a bar that stays at the bottom with save / previous / next.
   Loaded before icm.js; uses its globals when called (api, post, el, panels, ...). */
window.ICMWizard = (() => {
  /* slug, title, the page of the first wizard it was part of (the export files and old links use those names) */
  const WIZ = [
    ['hadiths', 'حدیث‌ها', 'seed'], ['parts', 'حدیث‌ها و بخش‌هایشان', 'selection'], ['candidates', 'نامزدها', 'selection'], ['semantic', 'هم‌معنا', 'selection'],
    ['added', 'حدیث‌های افزوده', 'selection'], ['alignment', 'هم‌ترازی', 'alignment'],
    ['narrators', 'راویان', 'identities'], ['graph', 'شجره', 'phylogeny'], ['run', 'اجرا', 'analysis']];
  const SLUGS = WIZ.map(w => w[0]);
  const OLD = { slots: 'alignment', units: 'alignment', seed: 'hadiths', search: 'hadiths', selection: 'candidates', identities: 'narrators', phylogeny: 'graph', analysis: 'results', done: 'results' };
  const LEGACY = { slots: 'alignment', units: 'alignment', seed: 'hadiths', search: 'hadiths', selection: 'candidates', identities: 'narrators', phylogeny: 'graph', analysis: 'run' };    // the step names of the first wizard, as the server's index may still hold them
  const PANEL = { hadiths: ['seed'], parts: ['selection', 'parts'], candidates: ['selection', 'candidates'], semantic: ['selection', 'semantic'], added: ['selection', 'added'],
    alignment: ['alignment'], narrators: ['identities'], graph: ['phylogeny'] };
  const STAGE_OF = ['search', 'search', 'search', 'search', 'search', 'align', 'identify', 'graph', 'infer'];
  const STATE_FA = { done: 'به‌روز', idle: 'اختیاری', todo: 'منتظر تصمیم شما', needs_run: 'نیازمند اجرا', stale: 'قدیمی', running: 'در حال محاسبه', awaiting: 'منتظر محاسبه', unreached: 'هنوز نرسیده‌اید' };
  const MARK = { done: '✓', needs_run: '●', todo: '●', stale: '⚠', running: '⟳', awaiting: '⟳', idle: '', unreached: '' };
  const idx = slug => SLUGS.indexOf(slug) + 1;
  /* things stick under the top bar: its height (it wraps on a narrow screen) is a variable the CSS uses */
  const headerH = () => { const hd = document.querySelector('header.top'); document.documentElement.style.setProperty('--hdr', (hd ? Math.round(hd.getBoundingClientRect().height) : 54) + 'px'); };
  addEventListener('resize', headerH);
  if (window.ResizeObserver && document.querySelector('header.top')) new ResizeObserver(headerH).observe(document.querySelector('header.top'));
  const RAIL_GROUPS = ['جستجو و انتخاب', 'هم‌ترازی و تحلیل'];
  const railGroup = i => (i <= 5 ? 0 : 1);

  const normStep = s => (!s ? null : SLUGS.includes(s) || s === 'results' || s === 'settings' ? s : OLD[s] || s);
  const stepTitle = s => {
    if (s === 'done') return 'پایان';
    const i = idx(SLUGS.includes(s) ? s : LEGACY[s]);
    return i ? WIZ[i - 1][1] : (s || '—');
  };
  /* where a save that names a page of the first wizard ('alignment', 'identities', ...) leaves the reader: on the step they are on, when it is one of that page's */
  function target(step) {
    const cur = route().step;
    if (SLUGS.includes(step)) return step;
    const grp = WIZ.filter(w => w[2] === step).map(w => w[0]);
    if (!grp.length) return OLD[step] || cur || SLUGS[0];
    return grp.includes(cur) ? cur : grp[0];
  }
  const busyDot = b => (b ? el('span', { class: 'busy', role: 'img', 'aria-label': 'در حال محاسبه', title: b.status === 'queued' ? 'کاری برای این پروژه در صف است' : 'این پروژه در حال محاسبه است' }) : null);
  const stepLink = (pid, run, slug, text) => el('a', { href: BASE + stepUrl(pid, run, slug), onclick: ev => { ev.preventDefault(); go(stepUrl(pid, run, slug)); } }, text || stepTitle(slug));

  /* the wizard's marks for a read-only run (the stages it is made of) or an exported file (the steps it carries) */
  function offlineWizard(run) {
    const have = STATIC ? null : ((P.runs.find(x => x.run === run) || {}).stages || {});
    return { current: null, stale_any: false, steps: WIZ.map((w, i) => ({ n: i + 1, slug: w[0], title: w[1], state: (STATIC ? STATIC.steps.includes(w[2]) : !!have[STAGE_OF[i]]) ? 'done' : 'unreached', changed_since: [], run: null })) };
  }

  /* ---------- the page ---------- */
  async function project(r) {
    NV = null;
    const pid = r.pid, run = r.run, readonly = !!run;
    P = await api('/project/' + pid);
    const p = P.project, pname = p.label || p.title;
    const can = !!(ME.user && (ME.user === p.owner || ME.admin)) && !readonly && !STATIC;
    const live = !readonly && !STATIC;
    let W = live && P.wizard && P.wizard.steps && P.wizard.steps.length ? P.wizard : offlineWizard(run);
    const shown = STATIC ? WIZ.filter(w => STATIC.steps.includes(w[2])) : WIZ;
    let tab = r.step === 'results' ? 'results' : r.step === 'settings' && live ? 'settings' : 'steps';
    let slug = SLUGS.includes(r.step) ? r.step : null;
    if (STATIC && tab === 'steps' && !shown.some(w => w[0] === slug)) { slug = shown[0] ? shown[0][0] : null; if (!slug) tab = 'results'; }
    if (!slug && tab === 'steps') {
      if (readonly) slug = (W.steps.find(x => x.state !== 'unreached') || W.steps[0]).slug;
      else if (W.current === 'done') tab = 'results';
      else slug = W.current || 'hadiths';
    }
    const section = r.section || 'general';
    if (!STATIC) { const canon = BASE + stepUrl(pid, run, tab === 'steps' ? slug : tab === 'results' ? 'results' : 'settings/' + section); if (here() !== canon) setUrl(canon + query(), false); }     // the address says where the reader is
    const n = tab === 'steps' ? idx(slug) : 0;
    const runId = run || p.current_run || 'current';
    const group = tab === 'steps' ? WIZ[n - 1][2] : 'analysis';
    document.querySelector('header.top').classList.add('proj'); headerH();

    /* ----- header ----- */
    const B = P.branches || { active: null, versions: [] };
    const viewed = run && B.versions.find(b => b.current_run === run);
    /* the version picker of the top bar; its last entry opens the page that lists, activates, creates and deletes versions */
    const MANAGE = '__manage';
    const runSel = STATIC ? el('span', { class: 'small', dir: 'ltr' }, run) : (() => {
      const sel = el('select', { 'aria-label': 'نسخه‌ی پروژه', title: 'نسخه‌ی پروژه', onchange: () => {
          if (sel.value !== MANAGE) return pickVersion(pid, sel.value, tab === 'steps' ? slug : 'results');
          sel.value = sel.dataset.cur; live ? leave('/p/' + pid + '/settings/versions') : versionsModal(pid, slug || 'results');
        } },
        run && !viewed ? el('option', { value: '', selected: true }, `اجرای ${run.split('-')[0]} (قدیمی)`) : null,
        B.versions.map(b => el('option', { value: b.id, selected: viewed ? b === viewed : !run && b.id === B.active }, b.name + (b.id === B.active ? ' ✓' : ''))),
        el('option', { value: MANAGE }, 'مدیریت نسخه‌ها…'));
      sel.dataset.cur = sel.value; return sel;
    })();
    const dlStep = el('a', { class: 'btn small', download: '', title: 'یک فایل HTML یک‌تکه، بی‌نیاز از اینترنت' }, 'دانلود HTML این مرحله');
    const dlAll = el('a', { class: 'btn small', download: '', title: 'یک فایل HTML یک‌تکه، بی‌نیاز از اینترنت' }, 'دانلود کل پروژه');
    dlStep.href = `${API}/project/${pid}/runs/${runId}/export/${group}.html`; dlAll.href = `${API}/project/${pid}/runs/${runId}/export/all.html`;
    const exportSel = STATIC ? null : el('span', { class: 'toolbar m0' }, dlStep, dlAll);
    const dot = el('span'), chipSt = el('span'), status = el('div');
    const paintHead = () => {
      dot.replaceChildren(busyDot(P.busy || P.job) || '');
      chipSt.className = 'chip ' + P.project.status; chipSt.textContent = STATUS[P.project.status] || P.project.status;
    };
    /* the top bar: name, state, (i) · previous, the step button, next, results, settings · save · the version. One link of the bar: */
    const plain = ev => ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button;
    const navLink = (text, u, on) => el('a', { class: 'hl' + (on ? ' on' : ''), href: BASE + u, 'aria-current': on ? 'page' : null,
      onclick: ev => { if (plain(ev)) return; ev.preventDefault(); if (!on) leave(u); } }, text);
    const stepBtn = el('button', { type: 'button', class: 'hstep', 'aria-haspopup': 'true', title: 'همه‌ی مرحله‌ها', onclick: ev => { ev.stopPropagation(); popover(ev.currentTarget, stepMenu()); } });
    const resLink = !STATIC || STATIC.steps.includes('analysis') ? navLink('نتایج', stepUrl(pid, run, 'results'), tab === 'results') : null;
    const setLink = live ? navLink('تنظیمات', '/p/' + pid + '/settings/general', tab === 'settings') : null;
    const mainBox = el('div');
    const prevHost = el('span', { class: 'hgo-host' }), nextHost = el('span', { class: 'hgo-host' }), saveHost = el('span', { class: 'hsave' });     // previous / next step and save: in the bar (a phone gets them in a bar at the bottom)
    const hproj = document.getElementById('hproj');
    hproj.replaceChildren(el('h1', { class: 'pname', title: pname }, pname),
      el('span', { class: 'hmeta' }, dot, chipSt, info(() => projInfo(p, exportSel), { modal: true, title: 'سازنده، دانلود، تصمیم‌ها و گزارش اجرا' }), readonly ? el('span', { class: 'chip partial' }, 'فقط‌خواندنی') : null),
      el('span', { class: 'hnav' }, prevHost, stepBtn, nextHost, resLink, setLink),
      saveHost,
      el('span', { class: 'hver' }, runSel));
    hproj.hidden = false;
    $app.replaceChildren(status, mainBox);
    paintHead(); statusBox(status, p, pid);
    const titleNow = () => setTitle((P.job || P.busy ? '● ' : '') + pname, tab === 'steps' ? WIZ[n - 1][1] : tab === 'results' ? 'نتایج' : 'تنظیمات');
    titleNow();

    /* ----- the dirty guard ----- */
    let reg = null;
    const isDirty = () => !!((reg && reg.dirty && reg.dirty()) || (pageDirty && pageDirty()));
    const register = x => { reg = x; pageDirty = x.dirty; };
    window.onbeforeunload = () => (isDirty() ? 'تغییر ذخیره‌نشده‌ای هست.' : undefined);
    const askSave = () => new Promise(resolve => {
      const done = v => { closeModal(); resolve(v); };
      modal(el('div', { class: 'helpbox' }, el('h3', {}, 'تغییر ذخیره‌نشده'), el('p', {}, 'در این صفحه تغییری داده‌اید که هنوز ذخیره نشده است.'),
        el('div', { class: 'toolbar' }, reg && reg.save ? el('button', { class: 'primary', onclick: () => done('save') }, 'ذخیره و ادامه') : null,
          el('button', { onclick: () => done('drop') }, 'ادامه بدون ذخیره'), el('button', { onclick: () => done('stay') }, 'ماندن در این صفحه'))));
      modalGuard = () => { resolve('stay'); return true; };
    });
    const saveNow = async () => { if (reg && reg.save) await reg.save(); };
    /* go to a path (of the app, without BASE); a page with unsaved work asks first */
    async function leave(path) {
      if (isDirty() && can) {
        const how = await askSave();
        if (how === 'stay') return;
        if (how === 'save') { try { await saveNow(); } catch (e) { toast(faErr(e.message), 'bad'); return; } }
      }
      pageDirty = null; go(path);
    }

    /* ----- the steps ----- */
    /* one step as a link with its number, its mark and its state in the tooltip (in the list of the step button) */
    const stepItem = w => {
      const i = idx(w[0]), st = (W.steps[i - 1] || { state: 'unreached' }), on = tab === 'steps' && w[0] === slug, cur = W.current;
      const cls = 'st ' + (st.state === 'needs_run' || st.state === 'todo' ? 'needs' : st.state) + (on ? ' cur' : '') + (!readonly && w[0] === cur ? ' now' : '');
      const body = [el('span', { class: 'n' }, fa(i)), el('span', { class: 'tt' }, w[1]), MARK[st.state] ? el('span', { class: 'mark', 'aria-hidden': 'true' }, MARK[st.state]) : null];
      const tip = `${w[1]} — ${STATE_FA[st.state] || ''}${st.changed_since && st.changed_since.length ? '؛ تغییر در مرحله‌ی ' + st.changed_since.map(x => fa(x.n)).join('، ') : ''}`;
      if (st.state === 'unreached') return el('span', { class: cls, title: tip, 'aria-disabled': 'true' }, ...body);
      const u = stepUrl(pid, run, w[0]);
      return el('a', { class: cls, href: BASE + u, title: tip, 'aria-current': on ? 'step' : null, onclick: ev => { if (plain(ev)) return; ev.preventDefault(); closePop(); if (!on) leave(u); } }, ...body);
    };
    /* the list behind the step button: the steps in three groups */
    const stepMenu = () => {
      const kids = []; let last = -1;
      shown.forEach(w => { const g = railGroup(idx(w[0])); if (g !== last) { kids.push(el('div', { class: 'rg' }, RAIL_GROUPS[g])); last = g; } kids.push(stepItem(w)); });
      return el('nav', { class: 'stlist', 'aria-label': 'مرحله‌ها' }, kids);
    };
    /* the step button: the step on screen with its mark (on the results or settings page: «مراحل») */
    const paintStepper = () => {
      const st = tab === 'steps' ? (W.steps[n - 1] || { state: 'unreached' }) : null;
      stepBtn.className = 'hstep' + (st ? ' on st ' + (st.state === 'needs_run' || st.state === 'todo' ? 'needs' : st.state) : '');
      stepBtn.replaceChildren(...(st ? [el('span', { class: 'n' }, fa(n)), el('span', { class: 'tt' }, WIZ[n - 1][1]), MARK[st.state] ? el('span', { class: 'mark', 'aria-hidden': 'true' }, MARK[st.state]) : null] : [el('span', { class: 'tt' }, 'مرحله‌ها')]).filter(Boolean),
        icon('M6 9l6 6 6-6'));
    };

    /* ----- the pieces of a step page ----- */
    const ctx = el('div', { class: 'ctx' }), runrow = el('div', { class: 'runrow' }), runwarn = el('div', { class: 'runwarn' }), data = el('div'), actions = el('div', { class: 'actionbar wiz' }), repl = el('div', { class: 'replbanner', role: 'status', hidden: true });
    const stepInfo = el('span', { class: 'stepinfo' });
    const c = { pid, p, runId, can, readonly, only: null, busy: () => !!(P.job || P.busy || P.project.status === 'running'), setInfo: (title, body) => stepInfo.replaceChildren(info(() => [el('h3', {}, title), ...[body()].flat()], { modal: true, title })),
      register, counts: v => { c.nums = v; paintRun(); }, changed: () => setTimeout(() => paintActions(), 0) };
    let runErr = '';
    const knobs = {};     // the inputs of the run bar of a step (4: the cut-off; 9: precision and place of computation)
    if (live && tab === 'steps' && (slug === 'semantic' || slug === 'run')) {          // their values are the last press's
      const dec = await decOf(c, slug === 'run' ? 'phylogeny' : 'search').catch(() => ({}));
      knobs.dec = slug === 'run' ? dec : dec.ret_sem || {};
    }
    const knobSem = () => {
      const d = knobs.dec || {};
      const mn = knobs.min || (knobs.min = el('input', { class: 'w5', type: 'number', min: '0.4', max: '0.95', step: '0.01', value: String(d.sem_min || 0.6), 'aria-label': 'کمترین هم‌معنایی' }));
      const k = knobs.k || (knobs.k = el('input', { class: 'w5', type: 'number', min: '1', max: '100', step: '1', value: String(d.sem_k || 40), 'aria-label': 'شمار قطعه برای هر بخش' }));
      return el('span', { class: 'small', style: 'display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap' }, help('step2.semantic'), 'هم‌معنایی دست‌کم', mn, '(۰٫۴ تا ۰٫۹۵؛ حدود ۰٫۶ متن‌های هم‌معنا با واژه‌های دیگر را هم می‌آورد، ۰٫۷۵ فقط نزدیک‌ترین‌ها را؛ زیر ۰٫۵۵ بیشتر نامرتبط است) · تا', k, 'متن در هر دسته برای هر بخش · هر بخش را می‌توان جدا و با حد خودش دوباره جست (زیر همان بخش در صفحه) ');
    };
    const knobInfer = () => {
      const d = knobs.dec || {};
      if (!knobs.preset) {
        knobs.preset = el('select', { 'aria-label': 'دقت اجرا' }, ...['', 'quick', 'standard', 'thorough'].map(x => el('option', { value: x, selected: (d.preset || '') === x }, x ? { quick: 'سریع', standard: 'معمول', thorough: 'دقیق' }[x] : 'خودکار (بر پایه‌ی اندازه)')));
        knobs.compute = el('select', { 'aria-label': 'جای محاسبه' }, ...[['browser', 'در مرورگر من (سریع، موازی)'], ['helper', 'رایانه‌ی کمکی مدیر'], ['server', 'روی سرور (کند، نزدیک یک ساعت)']].map(([v, t]) => el('option', { value: v, selected: (d.compute || 'browser') === v }, t)));
      }
      return ['دقت: ', knobs.preset, ' محاسبه: ', knobs.compute, ' '];
    };

    let runSig = '';       // what the run bar showed last: the page repaints it every few seconds, and a repaint takes the focus from a field being typed in
    function paintRun() {
      if (!live || tab !== 'steps') { runrow.hidden = true; runwarn.hidden = true; runSig = ''; return; }
      const ae = document.activeElement;
      if (ae && runrow.contains(ae) && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName)) return;      // somebody is typing in a field of the bar: the next repaint catches up
      const st = W.steps[n - 1], busy = !!(P.job || P.busy || P.project.status === 'running'), dirty = isDirty();
      const warn = [];
      if (st.changed_since && st.changed_since.length && st.state !== 'done' && st.state !== 'idle') {
        warn.push(el('div', { class: 'help warnbox' }, 'هشدار: ', st.state === 'stale' ? 'نتیجه‌ی این مرحله به‌روز نشده است؛ ' : 'این مرحله باید دوباره اجرا شود؛ ', 'مرحله‌ی ',
          st.changed_since.map((x, i) => [i ? '، ' : '', stepLink(pid, null, x.slug, fa(x.n) + ' (' + x.title + ')')]).flat(), ' پس از آخرین اجرا تغییر کرده است'
          + (st.run ? '؛ آنچه اینجا می‌بینید از اجرای پیشین است.' : '.')));
      } else if (st.state === 'stale') {
        warn.push(el('div', { class: 'help warnbox' }, 'هشدار: نسخه‌ی برنامه یا پایگاه راویان عوض شده و نتیجه‌ی این مرحله از اجرای پیشین است.'));
      }
      if (n === 1 && st.state === 'needs_run') warn.push(el('div', { class: 'help warnbox' }, 'فهرست یا متن حدیث‌ها تغییر کرده است. نامزدها و مرحله‌های بعد وقتی در ', stepLink(pid, null, 'parts', 'مرحله‌ی ۲'), ' «اجرا» را بزنید به‌روز می‌شوند.'));
      const rp = st.run;
      const sig = JSON.stringify([n, st.state, st.ready, st.ran, busy, dirty, can, P.semantic_available, runErr, rp && rp.what, rp && rp.label, warn.map(x => x.textContent)]);
      if (sig === runSig && !runrow.hidden && runrow.firstChild) return;                          // nothing the bar shows has changed: leave its elements (and their focus) alone
      runSig = sig;
      const bits = [el('span', { class: 'chip ' + (st.state === 'done' ? 'finalized' : st.state === 'running' ? 'running' : 'partial') }, STATE_FA[st.state] || ''), help(st.state === 'stale' ? 'stale' : 'runs'), ' '];
      if (rp && can) {
        if (rp.what === 'semantic') bits.push(knobSem());
        if (rp.what === 'infer') bits.push(...knobInfer());
        const again = st.state === 'stale' || !!st.ran;
        const noSem = rp.what === 'semantic' && P.semantic_available === false;
        const hint = busy ? 'تا پایان محاسبه‌ی کنونی چیزی دیگر اجرا نمی‌شود' : dirty ? 'نخست تغییرهای این صفحه را ذخیره کنید' : !st.ready ? 'مرحله‌های پیشین هنوز اجرا نشده‌اند' : '';
        bits.push(el('button', { class: 'primary', disabled: busy || dirty || !st.ready || noSem, title: hint, onclick: ev => runClick(ev.currentTarget, st) }, again ? 'اجرای دوباره' : 'اجرا'),
          el('span', { class: 'what' }, ' ' + rp.label));
        if (busy) bits.push(el('span', { class: 'small muted' }, ' در حال محاسبه… '), el('button', { class: 'small danger', onclick: () => cancelJob(pid) }, 'لغو محاسبه'));
        else if (hint) bits.push(el('span', { class: 'small muted' }, ' ' + hint));
        if (noSem) bits.push(el('span', { class: 'small warn' }, ' جستجوی معنایی در این سرور آماده نیست.'));
      } else if (rp) bits.push(el('span', { class: 'small muted' }, 'فقط سازنده‌ی پروژه یا مدیر می‌تواند این مرحله را اجرا کند.'));
      else if (n === 1) bits.push(el('span', { class: 'small muted' }, 'این مرحله دکمه‌ی اجرا ندارد: تغییرهایش با «اجرا»ی مرحله‌ی ۲ به نامزدها می‌رسد.'));
      if (runErr) bits.push(el('div', { class: 'small', role: 'alert', style: 'color:var(--bad);margin-top:6px' }, runErr));
      runwarn.replaceChildren(...warn); runwarn.hidden = !warn.length;
      runrow.classList.toggle('knobs', !!(rp && can && (rp.what === 'semantic' || rp.what === 'infer')));      // knobs (steps 4 and 9) take a line of their own
      runrow.replaceChildren(...bits); runrow.hidden = false;
    }

    /* ----- while the job computes THIS step: what the page shows (the earlier result, or nothing yet) is on its way out: dimmed, read-only, with a line that says so ----- */
    function paintReplaced() {
      const st = live && tab === 'steps' ? W.steps[n - 1] : null, j = P.job || P.busy;
      const on = !!(st && (st.state === 'running' || st.state === 'awaiting') && (j || P.project.status === 'running' || st.state === 'awaiting'));
      data.classList.toggle('replacing', on);
      if (on) data.setAttribute('aria-busy', 'true'); else data.removeAttribute('aria-busy');
      for (const x of data.querySelectorAll('button:not(.info), input, select, textarea')) {      // (i) stays: reading is allowed
        if (on && !x.disabled) { x.disabled = true; x.dataset.repl = '1'; } else if (!on && x.dataset.repl) { x.disabled = false; delete x.dataset.repl; }
      }
      repl.hidden = !on;
      if (!on) return;
      const pr = j && j.progress && j.progress.msg ? ' (' + j.progress.msg + (j.progress.frac ? '، ' + fa(Math.round(j.progress.frac * 100)) + '٪' : '') + ')' : '';
      const queued = j && j.status === 'queued';
      repl.replaceChildren(st.state === 'awaiting' ? 'این مرحله منتظر محاسبه‌ی بیرون از سرور است؛ آنچه می‌بینید از اجرای پیشین است و پس از آن جایگزین می‌شود.'
        : (queued ? 'محاسبه‌ی این مرحله در صف است. ' : 'این مرحله اکنون محاسبه می‌شود' + pr + '. ') + (st.ran ? 'آنچه زیر می‌بینید نتیجه‌ی پیشین است؛ فقط‌خواندنی است و پس از پایان محاسبه جایگزین می‌شود.'
          : 'هنوز نتیجه‌ای نیست؛ پس از پایان محاسبه همین‌جا می‌آید.'));
    }
    data.addEventListener('click', ev => {                     // buttons added to the page after the mark (a fold opened, a list drawn later) are still shut
      if (data.classList.contains('replacing')) { const t = ev.target.closest && ev.target.closest('button, input, select, textarea'); if (t && !t.closest('.info')) { ev.preventDefault(); ev.stopPropagation(); } }
    }, true);

    const ASKS = {
      align: nSel => ({ title: 'ساخت هم‌ترازی', yes: 'بله، هم‌ترازی ساخته شود', check: 'شاهدهای برگزیده را بررسی کرده‌ام و همین‌ها را می‌خواهم.',
        body: [el('p', {}, `متن ${nSel ? fa(nSel) + ' ' : ''}شاهد برگزیده، همراه حدیث‌های فهرست مرحله‌ی ۱، بخش‌به‌بخش و واژه‌به‌واژه هم‌تراز می‌شود.`),
          el('p', { class: 'small muted' }, 'تا پایان محاسبه نمی‌توانید نسخه را عوض کنید یا پروژه را حذف کنید؛ اگر لازم شد «لغو محاسبه» هست. با تغییر شاهدها یا جستجو، هم‌ترازی را باید دوباره بسازید.')] }),
      identify: () => ({ title: 'یافتن راویان', yes: 'بله، راویان یافته شوند', check: 'هم‌ترازی (مرحله‌ی ۶) را دیده‌ام و همین را می‌خواهم.',
        body: [el('p', {}, 'راویان هر سند از متن شاهدها استخراج و با پایگاه راویان سنجیده می‌شوند.'),
          el('p', { class: 'small muted' }, 'تا پایان محاسبه نمی‌توانید نسخه را عوض کنید یا پروژه را حذف کنید؛ اگر لازم شد «لغو محاسبه» هست.')] }),
    };
    async function runClick(btn, st) {
      const what = st.run.what;
      let body = {};
      runErr = '';
      try {
        if (what === 'search') {
          const m = await chooseSearchMode();
          if (!m) return;
          body = { mode: m };
        } else if (what === 'semantic') {
          body = { sem_min: Number(knobs.min.value), sem_k: Number(knobs.k.value) };
        } else if (what === 'infer') {
          body = { preset: knobs.preset.value || null, compute: knobs.compute.value };
          if (body.compute === 'server' && !(await sure('محاسبه روی سرور نزدیک یک ساعت طول می‌کشد. ادامه؟'))) return;
        } else if (what === 'align' || what === 'identify') {
          let nSel = null;
          try { nSel = ((await decOf(c, 'selection')).selected || []).length || null; } catch (_) {}
          if (!(await ask(ASKS[what](nSel)))) return;
        }
        btn.disabled = true;
        await post(`/project/${pid}/run/${what}`, body);
        if (what === 'infer' && body.compute === 'browser') sessionStorage.setItem('icm_auto', pid);
        cache.clear(); await render();
      } catch (e) { runErr = e.message; btn.disabled = false; paintRun(); }
    }
    /* a second «جستجو»: what becomes of the candidates already there (the hadiths the user added always stay) */
    async function chooseSearchMode() {
      let S = null, dec = {};
      try { [S, dec] = await Promise.all([stage(pid, runId, 'search'), decOf(c, 'selection')]); } catch (_) {}
      const nominees = S && S.searched !== false ? S.candidates.filter(x => !isMine(x)) : [];
      if (!nominees.length) return 'rebuild';
      const sel = new Set(dec.selected || []);
      return new Promise(resolve => {
        const pick = m => { closeModal(); resolve(m); };
        const opt = (mode, title, text) => el('div', { class: 'cardbox', style: 'margin:8px 0' }, el('b', {}, title), el('p', { class: 'small' }, text), el('button', { class: mode === 'rebuild' ? '' : 'primary', onclick: () => pick(mode) }, 'انجام'));
        modal(el('div', {}, el('h2', {}, 'جستجوی دوباره'),
          el('p', { class: 'small muted' }, `اکنون ${fa(nominees.length)} نامزد هست و ${fa(nominees.filter(x => sel.has(x.wid)).length)} از آن‌ها ذخیره و تیک‌خورده است. حدیث‌هایی که خودتان افزوده‌اید و حدیث‌های فهرست مرحله‌ی ۱ در هر سه حالت می‌مانند. جستجوی معنایی (مرحله‌ی ۴) در هر سه حالت باید دوباره اجرا شود.`),
          opt('rebuild', 'ساختن دوباره‌ی نامزدها', 'فهرست نامزدها فقط از نتیجه‌ی جستجوی تازه ساخته می‌شود. تیک‌ها برداشته می‌شود و باید دوباره شاهدها را برگزینید (مرحله‌های بعد را باید دوباره اجرا کنید).'),
          opt('selected', 'نگه داشتن تیک‌خورده‌ها', 'نامزدهای ذخیره‌شده و تیک‌خورده می‌مانند، بقیه‌ی نامزدهای پیشین برداشته می‌شوند و آنچه جستجوی تازه می‌یابد افزوده می‌شود.'),
          opt('all', 'نگه داشتن همه', 'همه‌ی نامزدهای پیشین (با تیک‌هایشان) می‌مانند و فقط یافته‌های تازه به فهرست افزوده می‌شوند.'),
          el('div', { class: 'toolbar' }, el('button', { onclick: () => pick(null) }, 'انصراف'))));
        modalGuard = () => { resolve(null); return true; };
      });
    }

    function paintActions() {
      if (!live || tab !== 'steps' || !can) { for (const h of [prevHost, nextHost, saveHost, actions]) h.replaceChildren(); actions.hidden = true; document.documentElement.style.removeProperty('--wizbar'); return; }
      const dirty = isDirty(), prev = n > 1 ? WIZ[n - 2] : null, next = n < WIZ.length ? WIZ[n] : null;
      const nst = next && W.steps[n] ? W.steps[n].state : null;
      const own = reg && reg.own;
      /* previous / next: an arrow in the top bar (full = with the step's name, in the bar at the bottom) */
      const nav = (w, dir, full) => {
        const dis = !w || (dir === 'next' && nst === 'unreached'), name = dir === 'prev' ? 'مرحله‌ی پیش' : 'مرحله‌ی بعد';
        return el('button', { type: 'button', class: full ? null : 'hgo', disabled: dis, title: !w ? '' : dis ? 'هنوز به آن مرحله نرسیده‌اید' : name + ': ' + w[1], 'aria-label': name + (w ? ': ' + w[1] : ''),
          onclick: () => leave(stepUrl(pid, null, w[0])) },
          full ? (dir === 'prev' ? ['→ ' + name, el('span', { class: 'navt' }, ': ' + w[1])] : [name, el('span', { class: 'navt' }, ': ' + w[1]), ' ←'])
            : icon(dir === 'prev' ? 'M9 6l6 6-6 6' : 'M15 6l-6 6 6 6'));
      };
      const saveBtn = () => (own ? null : el('button', { type: 'button', class: 'primary', disabled: !reg || !(dirty || (reg.pending && reg.pending())), title: reg ? '' : 'تغییرهای این صفحه همان لحظه ذخیره می‌شود', onclick: async ev => {
        ev.currentTarget.disabled = true;
        try { await saveNow(); cache.clear(); await render(); } catch (e) { toast(faErr(e.message), 'bad'); paintActions(); } } }, reg ? 'ذخیره' : 'ذخیره‌شده ✓'));
      const dirtyTxt = () => el('span', { class: dirty ? 'small warn' : 'small muted', role: 'status' }, dirty ? 'تغییرهای ذخیره‌نشده' : '');
      prevHost.replaceChildren(nav(prev, 'prev')); nextHost.replaceChildren(nav(next, 'next'));
      saveHost.replaceChildren(...[dirtyTxt(), saveBtn()].filter(Boolean));
      actions.replaceChildren(el('div', { class: 'toolbar m0' },
        prev ? nav(prev, 'prev', true) : null, saveBtn(), dirtyTxt(), el('span', { class: 'grow' }), next ? nav(next, 'next', true) : null));
      actions.hidden = false; document.documentElement.style.setProperty('--wizbar', actions.offsetHeight + 'px');
    }
    const refreshBars = () => { paintRun(); paintActions(); };
    mainBox.addEventListener('input', () => setTimeout(refreshBars, 0));
    mainBox.addEventListener('change', () => setTimeout(refreshBars, 0));
    mainBox.addEventListener('click', () => setTimeout(refreshBars, 0));

    /* ----- the three tabs ----- */
    paintStepper();          // on every tab: the step button
    if (tab === 'settings') {
      mainBox.replaceChildren(el('p', { class: 'muted' }, 'در حال بارگذاری…'));
      await ICMSettings.page(mainBox, { pid, p, can, W, section });
    } else if (tab === 'results') {
      mainBox.replaceChildren(el('p', { class: 'muted' }, 'در حال بارگذاری…'));
      await resultsPage(mainBox, c, W, live);
    } else {
      mainBox.replaceChildren(el('div', { class: 'sthead' }, el('h2', {}, el('span', { class: 'stn' }, fa(n)), WIZ[n - 1][1], stepInfo), runrow), runwarn, ctx, repl, data, actions);
      data.replaceChildren(el('p', { class: 'muted' }, 'در حال بارگذاری…'));
      refreshBars();
      ctxStrip(slug, c, W).then(items => { ctx.replaceChildren(...items.flatMap((x, i) => [x, i < items.length - 1 ? ' ' : ''])); }).catch(() => {});
      try {
        if (slug === 'run') await runPage(data, c, W);
        else {
          const px = PANEL[slug];
          c.only = px[1] || null;
          await panels[px[0]](data, c);
        }
      } catch (e) {
        data.replaceChildren(e.status === 404 ? card(WIZ[n - 1][1], el('p', { class: 'muted' }, W.steps[n - 1].state === 'running' ? 'این مرحله اکنون در حال ساخته شدن است.' : 'این مرحله هنوز اجرا نشده است.' + (live && can ? ' با دکمه‌ی «اجرا» در بالای صفحه آن را بسازید.' : '')))
          : el('div', { class: 'card err', role: 'alert' }, 'خطا: ' + e.message));
      }
      paintReplaced();
      refreshBars();
    }

    /* ----- while a job runs: the dot, the bar and the marks follow it; one redraw when it is over, never over unsaved work ----- */
    if (live && (P.job || P.busy || p.status === 'running')) {
      const poll = async () => {
        const q = await api('/project/' + pid).catch(() => null);
        if (q) { P = q; if (q.wizard && q.wizard.steps && q.wizard.steps.length) W = q.wizard; paintHead(); statusBox(status, q.project, pid); titleNow(); paintStepper(); if (tab === 'steps') { paintRun(); paintReplaced(); } }
        if (!q || q.job || q.busy || q.project.status === 'running') { timer = setTimeout(poll, 3000); return; }
        const redraw = async () => { const y = scrollY; cache.clear(); await render(); scrollTo(0, y); };
        if (!isDirty()) return redraw();
        status.append(el('div', { class: 'toolbar small accent' }, 'محاسبه تمام شد؛ با «ذخیره» این صفحه به‌روز می‌شود.',
          el('button', { class: 'small', onclick: redraw }, 'به‌روزرسانی اکنون (تغییرهای ذخیره‌نشده از دست می‌روند)')));
      };
      timer = setTimeout(poll, 3000);
    }
  }

  /* ---------- what the earlier steps decided that matters on this one ---------- */
  const item = (label, ...v) => el('span', { class: 'ci' }, el('b', {}, label + ': '), ...v);
  const cnt = o => (Array.isArray(o) ? o.length : o && typeof o === 'object' ? Object.keys(o).length : 0);
  async function ctxStrip(slug, c, W) {
    const pid = c.pid, N = x => fa(x), link = (s, t) => stepLink(pid, c.readonly ? c.runId : null, s, t);
    const get = async f => { try { return await f(); } catch (_) { return null; } };
    const S = await get(() => stage(pid, c.runId, 'search'));
    const [sdec, pdec, seldec, iddec, phdec] = await Promise.all(['search', 'parts', 'selection', 'identities', 'phylogeny'].map(s => get(() => decOf(c, s)).then(x => x || {})));
    const hadiths = listOf(sdec).length, nParts = S ? S.clauses.length : null, nSel = (seldec.selected || []).length;
    const sp = S ? seedPartsState(S, pdec) : null;
    const out = [];
    const add = (...x) => out.push(item(...x));
    const hadithsItem = () => add('حدیث‌های فهرست', N(hadiths), ' (', link('hadiths', 'مرحله‌ی ۱'), ')');
    const partsItem = () => nParts != null && add('بخش‌ها', N(nParts), sp ? (sp.confirmed ? '، بریدن تأییدشده' : '، بریدن تأییدنشده') : '', ' (', link('parts', 'مرحله‌ی ۲'), ')');
    const tickItem = () => S && add('بخش‌های پرسیده‌شده', N((sdec.part_query || S.clauses.map(x => x.i)).length), ' از ', N(nParts), ' (', link('parts', 'مرحله‌ی ۲'), ')');
    const pressItem = () => S && add('آخرین جستجو', S.searched === false ? 'انجام نشده' : `${N((S.candidates || []).length)} نامزد، ${fa((S.generated || '').replace('T', ' ').slice(0, 16))}`, ' (', link('candidates', 'مرحله‌ی ۳'), ')');
    const selItem = () => add('شاهدهای تیک‌خورده', N(nSel), ' (', link('candidates', 'مرحله‌ی ۳'), ')');
    const dbItem = () => add('پایگاه راویان', P.narrator_version || 'خام', ' (', el('a', { href: BASE + '/p/' + pid + '/settings/narrators', onclick: ev => { ev.preventDefault(); go('/p/' + pid + '/settings/narrators'); } }, 'تنظیمات'), ')');
    const words = Object.values(pdec.words || {}).reduce((t, v) => t + cnt(v), 0);
    switch (slug) {
      case 'hadiths':
        add('پروژه', c.p.label || c.p.title); add('سازنده', c.p.owner); add('حدیث‌های فهرست', N(hadiths));
        if (cnt(sdec.edits)) add('متن‌های ویرایش‌شده', N(cnt(sdec.edits)));
        break;
      case 'parts': hadithsItem(); partsItem(); if (S && S.join) add('بخش‌های مشترک حدیث‌ها', S.join.built ? (S.join.stale ? 'یکی‌شده، قدیمی' : 'یکی‌شده') : 'یکی نشده'); break;
      case 'candidates': hadithsItem(); partsItem(); tickItem(); pressItem(); selItem(); break;
      case 'semantic': {
        hadithsItem(); tickItem();
        const rs = sdec.ret_sem, pr = S && S.params;
        if (rs) { add('آخرین یافتن بخش‌های هم‌معنا', `شماره‌ی ${N(rs.n)}، مدل ${rs.model === true ? '—' : rs.model}، هم‌معنایی ≥ ${N(rs.sem_min)}، تا ${N(rs.sem_k)} ${rs.mode === 'parts' ? 'متن در هر دسته' : 'حدیث'} برای هر بخش` + (Object.keys(rs.part_min || {}).length ? `؛ ${N(Object.keys(rs.part_min).length)} بخش حد ویژه دارد` : ''));
          if (rs.mode === 'parts') add('قطعه‌های تیک‌خورده', (rs.picks || []).length ? N(rs.picks.length) : 'هنوز جستجو نشده'); }
        else if (sdec.ret && sdec.ret.sem && pr) add('جستجوی معنایی', `در همان فشار «جستجو» انجام شده بود (هم‌معنایی ≥ ${N(pr.sem_min)})؛ با «اجرا» به‌تنهایی دوباره انجام می‌شود`);
        else add('جستجوی معنایی', 'هنوز انجام نشده (اختیاری)');
        pressItem();
        if (S) add('نامزدهای هم‌معنا', N(S.candidates.filter(x => rs && rs.mode === 'parts' ? x.signals.includes('q:sem') : x.suggested === 'semantic').length));
        break; }
      case 'added': hadithsItem(); partsItem(); selItem(); add('حدیث‌های افزوده', N((sdec.extra_ids || []).length)); break;
      case 'alignment': selItem(); partsItem(); add('بخش‌های تأییدشده در شاهدها', N(cnt(pdec.confirmed))); add('جایگاه‌های دستی', N(cnt(pdec.slots))); add('اصلاح‌های دستی واژه‌ها', N(words));
        add('واحدهای دستی', N(cnt(pdec.units && (pdec.units.own || pdec.units.keep)))); add('اجرای هم‌ترازی', c.p.current_run ? fa(c.p.current_run.split('-')[0]) : '—'); break;
      case 'narrators': selItem(); dbItem(); add('اصلاح نام‌ها', N(cnt(pdec.renames)), '، اصلاح سندها ', N(cnt(pdec.chain)));
        add('هم‌ترازی', W.steps[5].state === 'done' ? 'به‌روز' : 'قدیمی (مرحله‌ی ۶)'); break;
      case 'graph': {
        selItem(); dbItem();
        add('انتخاب‌های راویان', N(cnt(iddec.choices) + cnt(iddec.overrides)), '، ادغام ', N(cnt(iddec.merges)), '، تاریخ ', N(cnt(iddec.dates)), ' (', link('narrators', 'مرحله‌ی ۷'), ')');
        const I = await get(() => stage(pid, c.runId, 'identify'));
        if (I) add('نام‌های بی‌تصمیم', N(undecidedKeys(I, iddec).length));
        break; }
      case 'run': {
        const G = await get(() => stage(pid, c.runId, 'graph'));
        if (G) { add('شجره', `${N(G.stats.narrators)} راوی، ${N(G.stats.attestations)} سند، ${N(G.stats.witnesses)} شاهد`, ' (', link('graph', 'مرحله‌ی ۸'), ')'); add('هشدارها', N((G.flags || []).length)); }
        add('دقت', phdec.preset ? { quick: 'سریع', standard: 'معمول', thorough: 'دقیق' }[phdec.preset] : 'خودکار'); break; }
    }
    return out;
  }

  /* ---------- step 9: the last step shows warnings and the running; the results are a page of their own ---------- */
  async function runPage(main, c, W) {
    const pid = c.pid, st = W.steps[8];
    if (c.p.status === 'awaiting' && !c.readonly) return awaitingPanel(main, c);
    const [G, A5, S5, I5] = await Promise.all(['graph', 'align', 'search', 'identify'].map(x => stage(pid, c.runId, x).catch(() => null)));
    const pd = await decOf(c, 'parts').catch(() => ({}));
    const kids = [];
    if (!G) kids.push(card('اجرای تحلیل', el('p', { class: 'muted' }, 'شجره هنوز ساخته نشده است: نخست در ', stepLink(pid, null, 'graph', 'مرحله‌ی ۸'), ' «ساخت شجره» را بزنید.')));
    else {
      const unconf = A5 && A5.clauses.length && A5.clauses[0].key ? A5.clauses.filter(cl => partStatus(cl, pd, A5.witnesses).k !== 'auto') : [];
      const seedUnconf = S5 && !seedPartsState(S5, pd).confirmed;
      const warns = [
        !c.readonly ? undNote(pid, G.undecided || [], I5, true) : null,
        !c.readonly && (unconf.length || seedUnconf) ? el('p', { class: 'small warn' }, 'فرازبندی هنوز کامل تأیید نشده است: ',
          seedUnconf ? ['بریدن حدیث‌ها به بخش‌ها (', stepLink(pid, null, 'parts', 'مرحله‌ی ۲'), ')', unconf.length ? '، و ' : ''] : null,
          unconf.length ? [`${fa(unconf.length)} از ${fa(A5.clauses.length)} بخش در شاهدها (`, stepLink(pid, null, 'alignment', 'مرحله‌ی ۶'), ')'] : null,
          '. تحلیل با پیشنهادهای خودکار همان‌ها انجام می‌شود.') : null,
        (G.flags || []).length ? el('p', { class: 'small warn' }, `${fa(G.flags.length)} هشدار در شجره هست (`, stepLink(pid, null, 'graph', 'مرحله‌ی ۸'), ').') : null,
      ].filter(Boolean);
      kids.push(card('پیش از اجرا', help('step5.confirm'), warns.length ? warns : el('p', { class: 'small muted' }, 'هشداری نیست.'),
        el('p', { class: 'small muted' }, `${fa(G.stats.narrators)} راوی، ${fa(G.stats.attestations)} سند، ${fa(G.stats.witnesses)} شاهد؛ ${fa(G.stats.dated)} راوی تاریخ دارند. دقت و جای محاسبه را بالا، کنار دکمه‌ی «اجرا» برگزینید.`)));
      if (st.state === 'done') kids.push(card('تحلیل انجام شده است', el('p', {}, 'نتیجه‌ها در ', stepLink(pid, c.readonly ? c.runId : null, 'results', 'صفحه‌ی نتایج'), ' است.')));
    }
    fill(main, kids);
  }

  /* ---------- results: a page of its own ---------- */
  async function resultsPage(main, c, W, live) {
    const pid = c.pid, p = c.p;
    const wrap = el('div');
    const cur = P.runs.find(x => x.run === p.current_run);
    let show = c.readonly ? c.runId : null;
    if (!show) {
      if (STATIC) show = c.runId;
      else if (cur && cur.stages && cur.stages.infer) show = cur.run;
      else { const prev = [...P.runs].reverse().find(x => x.stages && x.stages.infer); if (prev) show = prev.run; }
    }
    const notes = [];
    if (live) {
      const late = W.steps.filter(x => x.state === 'stale' || (x.state === 'needs_run' && x.edited));
      const list = late.map((x, i) => [i ? '، ' : '', stepLink(pid, null, x.slug, fa(x.n))]).flat();
      if (p.status === 'awaiting') notes.push(el('div', { class: 'help warnbox' }, 'تحلیل منتظر محاسبه است؛ ', stepLink(pid, null, 'run', 'مرحله‌ی ۱۱'), ' را باز کنید.'));
      if (show && show !== p.current_run) notes.push(el('div', { class: 'help warnbox' }, 'هشدار: این نتیجه از اجرای پیشین است و با تصمیم‌های کنونی نمی‌خواند',
        late.length ? ['؛ مرحله‌های ', list, ' را دوباره اجرا کنید'] : '', '. ', stepLink(pid, show, 'results', 'این اجرا (فقط‌خواندنی)')));
      else if (show && late.length) notes.push(el('div', { class: 'help warnbox' }, 'هشدار: پس از این اجرا تصمیم‌هایی تغییر کرده است؛ مرحله‌های ', list, ' به‌روز نیستند و نتیجه‌ی زیر از اجرای پیشین است.'));
    }
    if (!show) {
      main.replaceChildren(...notes, card('نتایج', el('p', { class: 'muted' }, 'هنوز تحلیلی انجام نشده است. پس از ساختن شجره، در ', stepLink(pid, null, 'run', 'مرحله‌ی ۱۱'), ' «اجرا» را بزنید.')));
      return;
    }
    main.replaceChildren(...notes, wrap);
    await panels.analysis(wrap, { pid, p, runId: show, can: c.can, readonly: true, setInfo: () => {} });
  }

  return { WIZ, normStep, stepTitle, target, busyDot, project };
})();

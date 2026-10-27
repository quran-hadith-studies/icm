'use strict';
/* ICMSettings: a project's settings as a page (its sections are a row of links above the page), and the pop-up of the project list (ⓘ): owner, downloads, decisions, the run log and delete. Loaded before icm.js;
   uses its globals when called. */
window.ICMSettings = (() => {
  const SECTIONS = [['general', 'عمومی'], ['narrators', 'پایگاه راویان'], ['versions', 'نسخه‌ها'], ['download', 'دانلود'], ['danger', 'حذف پروژه']];
  const GROUPS = [['seed', 'حدیث‌ها'], ['selection', 'جستجو و انتخاب (مرحله‌های ۲ تا ۵)'], ['alignment', 'هم‌ترازی (مرحله‌های ۶ تا ۸)'], ['identities', 'راویان'], ['phylogeny', 'شجره'], ['analysis', 'نتایج']];

  /* one link per step's file and one for the whole project: self-contained HTML (export.py) */
  function exportLinks(pid, run) {
    if (!run) return el('p', { class: 'small muted' }, 'هنوز اجرایی نیست که دانلود شود.');
    const a = (what, t) => el('a', { class: 'btn small', download: '', href: `${API}/project/${pid}/runs/${run}/export/${what}.html` }, t);
    return el('div', { class: 'toolbar m0' }, ...GROUPS.map(([k, t]) => a(k, t)), a('all', 'کل پروژه'));
  }

  async function page(host, c) {
    const { pid, p, can, W } = c, section = SECTIONS.some(s => s[0] === c.section) ? c.section : 'general';
    const nav = el('nav', { class: 'snav', 'aria-label': 'بخش‌های تنظیمات' }, SECTIONS.map(([k, t]) => el('a', { class: (section === k ? 'on' : '') + (k === 'danger' ? ' danger' : ''), href: BASE + '/p/' + pid + '/settings/' + k,
      'aria-current': section === k ? 'page' : null, onclick: ev => { ev.preventDefault(); go('/p/' + pid + '/settings/' + k); } }, t)));
    const body = el('div', { class: 'sbody' });
    host.replaceChildren(el('div', { class: 'settings' }, nav, body));
    const readOnly = () => el('p', { class: 'small muted' }, 'فقط سازنده‌ی پروژه یا مدیر می‌تواند این بخش را تغییر دهد.');
    if (section === 'general') {
      const title = el('input', { type: 'text', maxlength: 120, value: p.label || p.title, 'aria-label': 'نام پروژه', style: 'min-width:20em' });
      const err = el('span', { class: 'small bad' });
      body.append(card('عمومی', help('settings.general'),
        el('div', { class: 'toolbar' }, el('label', {}, 'نام پروژه: ', title), can ? el('button', { class: 'primary', onclick: async () => {
          err.textContent = '';
          try { await post(`/project/${pid}/rename`, { title: title.value }); cache.clear(); render(); } catch (e) { err.textContent = e.message; } } }, 'ذخیره‌ی نام') : null, err),
        can ? null : readOnly(),
        el('table', { class: 'kv' }, el('tbody', {},
          [['حدیث آغازین', el('span', { dir: 'ltr' }, p.seed)], ['سازنده', p.owner], ['ساخته‌شده', el('span', { dir: 'ltr' }, (p.created || '').replace('T', ' ').slice(0, 16))],
            ['شناسه‌ی پروژه', el('span', { dir: 'ltr' }, pid)], ['وضعیت', STATUS[p.status] || p.status]].map(([k, v]) => el('tr', {}, el('th', {}, k), el('td', {}, v)))))));
    } else if (section === 'narrators') {
      const VERS = await narratorVersions();
      const late = W && W.steps ? W.steps.filter(x => x.n >= 9 && (x.state === 'stale' || (x.state === 'needs_run' && x.edited))) : [];
      body.append(narratorsBox({ pid, can }, VERS));
      if (late.length) body.append(el('div', { class: 'help warnbox' }, 'پایگاه راویان عوض شده است: مرحله‌های ', late.map((x, i) => [i ? '، ' : '', el('a', { href: BASE + stepUrl(pid, null, x.slug), onclick: ev => { ev.preventDefault(); go(stepUrl(pid, null, x.slug)); } }, fa(x.n) + ' (' + x.title + ')')]).flat(),
        ' با پایگاه تازه اجرا نشده‌اند؛ هر کدام را در صفحه‌ی خودش با «اجرا» بسازید.'));
      if (!can) body.append(readOnly());
    } else if (section === 'versions') {
      const inner = el('div');
      body.append(el('div', { class: 'card' }, inner));
      versionsModal(pid, route().step && route().step !== 'settings' ? route().step : 'hadiths', inner);
    } else if (section === 'download') {
      body.append(card('دانلود', help('export'), el('p', { class: 'small muted' }, 'هر فایل یک صفحه‌ی HTML یک‌تکه است که بی‌اینترنت باز می‌شود و فقط‌خواندنی است. نسخه‌ی کنونی پروژه (آخرین اجرا) دانلود می‌شود.'), exportLinks(pid, p.current_run)));
    } else {
      body.append(card('حذف پروژه', help('settings.danger'), can ? [
        el('p', {}, 'همه‌ی داده‌های این پروژه پاک می‌شود: تصمیم‌ها، نسخه‌ها، اجراها و نتیجه‌ها. این کار برگشت‌پذیر نیست.'),
        el('p', { class: 'small muted' }, 'برای نگه‌داشتن یک کپی، نخست از بخش «دانلود» فایل بگیرید. حذف تا پایان محاسبه‌ی در جریان ممکن نیست.'),
        el('div', { class: 'toolbar' }, el('button', { class: 'danger', onclick: () => deleteProject(pid, p.label || p.title, () => go('/')) }, '× حذف این پروژه با همه‌ی داده‌هایش'))] : readOnly()));
      body.firstChild.classList.add('dangerbox');
    }
  }

  /* the project list's ⓘ: what the project's page shows behind its own (i) */
  async function projectMenu(pr) {
    const box = el('div', { class: 'helpbox' }, el('p', { class: 'muted' }, 'در حال بارگذاری…'));
    modal(box);
    let q;
    try { q = await api('/project/' + pr.pid); } catch (e) { box.replaceChildren(el('p', {}, 'خطا: ' + e.message)); return; }
    const p = q.project, mine = ME.user && (ME.user === p.owner || ME.admin);
    box.replaceChildren(el('h2', {}, p.label || p.title, ' ', ICMWizard.busyDot(q.busy)),
      el('p', { class: 'small muted' }, `${STATUS[p.status] || p.status} · مرحله: ${ICMWizard.stepTitle(p.step)}`),
      el('div', { class: 'toolbar' }, el('a', { class: 'btn', href: BASE + '/p/' + p.pid, onclick: ev => { ev.preventDefault(); closeModal(); go('/p/' + p.pid); } }, 'باز کردن'),
        mine ? el('a', { class: 'btn', href: BASE + '/p/' + p.pid + '/settings/general', onclick: ev => { ev.preventDefault(); closeModal(); go('/p/' + p.pid + '/settings/general'); } }, 'تنظیمات') : null),
      ...projInfo(p, exportLinks(p.pid, p.current_run), q));
  }

  return { page, projectMenu, exportLinks, SECTIONS };
})();

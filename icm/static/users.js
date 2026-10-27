'use strict';
/* the wizard's own accounts (the server's icm/users.py, on when ME.accounts): sign-in, sign-up confirmed through the
   Bale bot, the account page, the admin's users menu, and the account's part of the header menu. Loaded before icm.js and uses its
   helpers (el, api, post, go, fa, ME, popover…) only when called. */
window.ICMUsers = (() => {
  const ROLE = { admin: 'مدیر', editor: 'ویرایشگر', viewer: 'خواننده' };
  const ROLE_HELP = { admin: 'همه‌ی کارها و مدیریت کاربران', editor: 'پروژه می‌سازد و پروژه‌های خودش را تغییر می‌دهد', viewer: 'فقط می‌خواند' };
  const STAT = { bale: 'منتظر تأیید بله', pending: 'منتظر تأیید مدیر', active: 'فعال', blocked: 'بسته' };
  const STAT_CLS = { bale: 'ghost', pending: 'awaiting', active: 'finalized', blocked: 'failed' };
  const next = () => new URLSearchParams(location.search).get('next') || BASE || '/';
  const link = (path, text) => el('a', { href: BASE + path, onclick: ev => { ev.preventDefault(); go(path); } }, text);
  const when = t => t ? new Date(t * 1000).toLocaleString('fa-IR', { dateStyle: 'short', timeStyle: 'short' }) : '—';
  const field = (label, input) => el('label', { class: 'ufield' }, el('span', {}, label), input);
  const input = (type, { fa: rtl, ...attrs } = {}) => el('input', { type, dir: type === 'text' && !rtl ? 'ltr' : null, ...attrs });
  const msgBox = () => el('div', { class: 'umsg', role: 'status', 'aria-live': 'polite' });
  const say = (box, text, bad) => { box.className = 'umsg' + (bad ? ' bad' : ' ok'); box.textContent = text; };
  /* a form whose submit runs fn(); errors land in its message line */
  function form(title, kids, submit, fn, foot) {
    const msg = msgBox(), btn = el('button', { class: 'primary', type: 'submit' }, submit);
    const f = el('form', { class: 'card uform', onsubmit: async ev => {
      ev.preventDefault(); btn.disabled = true; msg.textContent = '';
      try { await fn(msg); } catch (e) { say(msg, e.message, true); } finally { btn.disabled = false; }
    } }, el('h2', {}, title), ...kids, el('div', { class: 'toolbar' }, btn), msg, foot || null);
    return f;
  }

  /* ---------- the header: the sign-in link, and the account's entries for the top bar's menu ---------- */
  function loginLink() {
    return el('a', { href: `${BASE}/login?next=${encodeURIComponent(location.pathname)}`, onclick: ev => { ev.preventDefault(); go('/login?next=' + encodeURIComponent(location.pathname)); } }, 'ورود');
  }
  const badge = () => (ME.pending ? el('span', { class: 'ubadge', title: 'کاربران منتظر تأیید' }, fa(ME.pending)) : null);
  function items() {
    const item = (path, text, extra) => el('a', { class: 'uitem', role: 'menuitem', href: BASE + path, onclick: ev => { ev.preventDefault(); closePop(); go(path); } }, text, extra || null);
    return [
      item('/account', 'حساب من'),
      ME.admin ? item('/users', 'مدیریت کاربران', ME.pending ? el('span', { class: 'ubadge' }, fa(ME.pending)) : null) : null,
      el('button', { class: 'uitem lnk', role: 'menuitem', onclick: async () => { closePop(); await post('/auth/logout', {}); go('/'); } }, 'خروج')].filter(Boolean);
  }

  async function page(kind) {
    if (kind === 'login') return login();
    if (kind === 'register') return register();
    if (!ME.user) return go('/login?next=' + encodeURIComponent(BASE + '/' + kind));
    if (kind === 'account') return account();
    if (kind === 'users') return usersPage();
  }

  /* ---------- sign-in, and «forgotten password» through Bale ---------- */
  function login() {
    setTitle('ورود');
    if (ME.user) return go(next().startsWith(BASE) ? next().slice(BASE.length) || '/' : '/');
    const u = input('text', { autocomplete: 'username', autocapitalize: 'none', required: true }), pw = input('password', { autocomplete: 'current-password', required: true });
    const forgot = el('details', { class: 'ufold' }, el('summary', {}, 'گذرواژه را فراموش کرده‌اید؟'), resetForm(u));
    $app.replaceChildren(el('div', { class: 'uwrap' },
      form('ورود', [field('نام کاربری', u), field('گذرواژه', pw)], 'ورود', async msg => {
        const r = await post('/auth/login', { username: u.value, password: pw.value });
        if (r.need_bale) { $app.replaceChildren(el('div', { class: 'uwrap' }, baleStep(r.need_bale, 'حساب شما هنوز با بله تأیید نشده است.'))); return; }
        const n = next(); location.href = n.startsWith('/') && !n.startsWith('//') ? n : BASE || '/';
      }, el('p', { class: 'small' }, 'حساب ندارید؟ ', link('/register', 'ساخت حساب'))),
      forgot));
    u.focus();
  }
  function resetForm(userInput) {
    const code = input('text', { inputMode: 'numeric', autocomplete: 'one-time-code', maxLength: 6 }), pw = input('password', { autocomplete: 'new-password' });
    const msg = msgBox(), second = el('div', { hidden: true }, field('کد ۶ رقمی', code), field('گذرواژه‌ی تازه', pw),
      el('button', { class: 'primary', type: 'button', onclick: async () => {
        try { await post('/auth/reset', { username: userInput.value, code: code.value, password: pw.value }); say(msg, 'گذرواژه عوض شد؛ اکنون وارد شوید.'); second.hidden = true; }
        catch (e) { say(msg, e.message, true); }
      } }, 'تغییر گذرواژه'));
    return el('div', { class: 'small' },
      el('p', {}, 'نام کاربری را در بالا بنویسید؛ ربات بله یک کد برای حسابی که به آن وصل است می‌فرستد.'),
      el('button', { type: 'button', onclick: async () => {
        if (!userInput.value.trim()) { say(msg, 'نخست نام کاربری را بنویسید.', true); userInput.focus(); return; }
        try { await post('/auth/forgot', { username: userInput.value }); say(msg, 'اگر این حساب به بله وصل باشد، کد را آن‌جا می‌بینید.'); second.hidden = false; code.focus(); }
        catch (e) { say(msg, e.message, true); }
      } }, 'فرستادن کد به بله'), second, msg);
  }

  /* ---------- sign-up: the account, then the Bale confirmation ---------- */
  function register() {
    setTitle('ساخت حساب');
    const u = input('text', { autocomplete: 'username', autocapitalize: 'none', placeholder: 'مثلاً ali.ahmadi', required: true });
    const name = input('text', { fa: true, autocomplete: 'name', placeholder: 'نامی که دیگران می‌بینند', required: true });
    const pw = input('password', { autocomplete: 'new-password', required: true }), pw2 = input('password', { autocomplete: 'new-password', required: true });
    $app.replaceChildren(el('div', { class: 'uwrap' }, form('ساخت حساب', [
      el('p', { class: 'small muted' }, 'پس از ساختن حساب، آن را با ربات بله تأیید می‌کنید؛ سپس مدیر حساب را فعال می‌کند.'),
      field('نام کاربری (لاتین)', u), field('نام نمایشی', name), field('گذرواژه (دست‌کم ۸ نویسه)', pw), field('تکرار گذرواژه', pw2)],
    'ساخت حساب و رفتن به بله', async () => {
      if (pw.value !== pw2.value) throw new Error('دو گذرواژه یکی نیستند.');
      const r = await post('/auth/register', { username: u.value, name: name.value, password: pw.value });
      $app.replaceChildren(el('div', { class: 'uwrap' }, baleStep(r)));
    }, el('p', { class: 'small' }, 'حساب دارید؟ ', link('/login', 'ورود')))));
    u.focus();
  }
  /* the deep link ble.ir/<bot>?start=<code>; the page asks every few seconds whether the bot has seen it */
  function baleStep(r, intro) {
    const state = el('div', { class: 'umsg', role: 'status', 'aria-live': 'polite' }, 'منتظر پیام شما در بله…');
    const copy = el('button', { type: 'button', class: 'small', onclick: () => navigator.clipboard && navigator.clipboard.writeText(r.code).then(() => { copy.textContent = 'رونوشت شد'; }) }, 'رونوشت');
    const box = el('div', { class: 'card uform' }, el('h2', {}, 'تأیید با بله'),
      intro ? el('p', {}, intro) : null,
      el('ol', { class: 'usteps' },
        el('li', {}, r.link ? el('a', { class: 'btn primary', href: r.link, target: '_blank', rel: 'noopener' }, 'باز کردن ربات در بله') : 'ربات بله‌ی سایت را باز کنید', r.bot ? el('span', { class: 'small muted', dir: 'ltr' }, ' @' + r.bot) : null),
        el('li', {}, 'دکمه‌ی «شروع / Start» را بزنید؛ یا این کد را برای ربات بفرستید: ', el('code', { dir: 'ltr', class: 'ucode' }, r.code), ' ', copy),
        el('li', {}, 'این صفحه خودش به‌روز می‌شود.')),
      el('p', { class: 'small muted' }, `کد تا ${fa(Math.round(r.expires_in / 86400))} روز معتبر است؛ پس از آن با ورود دوباره کد تازه می‌گیرید.`), state);
    const poll = async () => {
      if (!box.isConnected) return;
      const s = await api('/auth/link-status?code=' + encodeURIComponent(r.code)).catch(() => null);
      if (s && s.status === 'pending') { state.className = 'umsg ok'; state.replaceChildren('✅ بله تأیید شد. حساب «', s.username, '» اکنون منتظر تأیید مدیر است؛ پس از آن پیامی در بله می‌گیرید.'); return; }
      if (s && s.status === 'active') { state.className = 'umsg ok'; state.replaceChildren('✅ حساب فعال است. ', link('/login', 'اکنون وارد شوید')); return; }
      setTimeout(poll, 3000);
    };
    setTimeout(poll, 3000);
    return box;
  }

  /* ---------- my account ---------- */
  function account() {
    setTitle('حساب من');
    const old = input('password', { autocomplete: 'current-password' }), pw = input('password', { autocomplete: 'new-password' }), pw2 = input('password', { autocomplete: 'new-password' });
    $app.replaceChildren(el('div', { class: 'uwrap' },
      el('div', { class: 'card' }, el('h2', {}, 'حساب من'), el('dl', { class: 'udl' },
        el('dt', {}, 'نام'), el('dd', {}, ME.name || '—'), el('dt', {}, 'نام کاربری'), el('dd', { dir: 'ltr' }, ME.user),
        el('dt', {}, 'نقش'), el('dd', {}, ROLE[ME.role] || ME.role, el('span', { class: 'small muted' }, ' — ', ROLE_HELP[ME.role] || '')),
        el('dt', {}, 'بله'), el('dd', {}, ME.bale ? '✓ وصل است (پیام‌های بازیابی گذرواژه آن‌جا می‌آید)' : '—'),
        el('dt', {}, 'سقف پروژه‌های ناتمام'), el('dd', {}, ME.admin ? 'بی‌سقف' : fa(ME.quota))),
        el('div', { class: 'toolbar' }, el('button', { onclick: async () => { await post('/auth/logout', {}); go('/'); } }, 'خروج از حساب'))),
      form('تغییر گذرواژه', [field('گذرواژه‌ی کنونی', old), field('گذرواژه‌ی تازه', pw), field('تکرار گذرواژه‌ی تازه', pw2)], 'تغییر گذرواژه', async msg => {
        if (pw.value !== pw2.value) throw new Error('دو گذرواژه یکی نیستند.');
        await post('/auth/password', { old: old.value, new: pw.value });
        old.value = pw.value = pw2.value = ''; say(msg, 'گذرواژه عوض شد.');
      })));
  }

  /* ---------- the admin's users menu ---------- */
  let filt = 'all';
  async function usersPage() {
    setTitle('مدیریت کاربران');
    if (!ME.admin) throw Object.assign(new Error('فقط مدیران به این بخش دسترسی دارند.'), { status: 403 });
    const d = await api('/users');
    const msg = msgBox();
    const act = async (u, action, value, ask) => {
      if (ask && !(await sure(ask))) return;
      try { await post('/users/' + encodeURIComponent(u.username), { action, value }); await render(); }
      catch (e) { say(msg, e.message, true); }
    };
    const count = s => d.users.filter(u => s === 'all' || u.status === s).length;
    const chips = ['all', 'pending', 'active', 'bale', 'blocked'].map(s => el('button', { class: 'chip ' + (filt === s ? 'running' : 'ghost'), 'aria-pressed': filt === s ? 'true' : 'false',
      onclick: () => { filt = s; usersPage(); } }, (s === 'all' ? 'همه' : STAT[s]) + ' ' + fa(count(s))));
    const rows = d.users.filter(u => filt === 'all' || u.status === filt).map(u => {
      const me = u.username === ME.user;
      const role = el('select', { 'aria-label': 'نقش ' + u.username, disabled: me, onchange: ev => act(u, 'role', ev.target.value) },
        d.roles.map(r => el('option', { value: r, selected: r === u.role, title: ROLE_HELP[r] }, ROLE[r])));
      const quota = input('number', { min: 0, max: 1000, value: u.quota ?? '', placeholder: fa(d.default_quota), 'aria-label': 'سقف پروژه‌های ' + u.username, class: 'uquota',
        title: 'سقف پروژه‌های ناتمام؛ خالی = پیش‌فرض (' + fa(d.default_quota) + '). مدیران بی‌سقف‌اند.',
        onchange: ev => act(u, 'quota', ev.target.value === '' ? null : parseInt(ev.target.value, 10)) });
      return el('tr', { class: 'u-' + u.status },
        el('td', {}, el('b', {}, u.name), el('div', { class: 'small muted', dir: 'ltr' }, u.username)),
        el('td', {}, el('span', { class: 'chip ' + STAT_CLS[u.status] }, STAT[u.status])),
        el('td', {}, role),
        el('td', {}, u.role === 'admin' ? el('span', { class: 'small muted' }, 'بی‌سقف') : quota),
        el('td', { class: 'small' }, u.bale ? ['✓ ', u.bale_handle ? el('span', { dir: 'ltr' }, '@' + u.bale_handle) : null] : el('span', { class: 'muted' }, '—')),
        el('td', { class: 'small', title: 'باز / همه' }, `${fa(u.open_projects)} / ${fa(u.projects)}`),
        el('td', { class: 'small' }, el('div', {}, 'ورود: ', when(u.last_login)), el('div', { class: 'muted' }, 'فعالیت: ', when(u.last_seen)),
          el('div', { class: 'muted' }, 'عضویت: ', when(u.created), u.approved_by ? ` (تأیید: ${u.approved_by})` : '')),
        el('td', { class: 'uacts' },
          u.status === 'pending' ? el('button', { class: 'primary small', onclick: () => act(u, 'approve') }, 'تأیید') : null,
          u.status === 'active' && !me ? el('button', { class: 'small', onclick: () => act(u, 'block', null, `حساب «${u.username}» بسته شود؟ از همه‌جا خارج می‌شود.`) }, 'بستن') : null,
          u.status === 'blocked' ? el('button', { class: 'small', onclick: () => act(u, 'unblock') }, 'بازکردن') : null,
          !me ? el('button', { class: 'small danger', onclick: () => act(u, 'delete', null, `حساب «${u.username}» برای همیشه پاک شود؟ پروژه‌هایش می‌مانند.`) }, 'حذف') : null));
    });
    $app.replaceChildren(el('div', { class: 'card' },
      el('div', { class: 'toolbar utools m0' }, el('h2', { class: 'm0' }, 'مدیریت کاربران'), el('span', { class: 'grow' }), ...chips),
      el('p', { class: 'small muted' }, 'هر حساب تازه نخست با ربات بله تأیید می‌شود و سپس منتظر تأیید شما می‌ماند. نقش‌ها: ',
        Object.keys(ROLE).flatMap((r, i) => [i ? '؛ ' : '', el('b', {}, ROLE[r]), ': ', ROLE_HELP[r]]), '.'), msg,
      rows.length ? el('div', { class: 'tscroll' }, el('table', { class: 'utable' },
        el('thead', {}, el('tr', {}, ...['کاربر', 'وضعیت', 'نقش', 'سقف پروژه', 'بله', 'پروژه‌ها', 'فعالیت', ''].map(t => el('th', {}, t)))), el('tbody', {}, rows)))
        : el('p', { class: 'empty-state' }, 'کاربری در این دسته نیست.')));
  }

  return { loginLink, badge, items, role: () => ROLE[ME.role] || ME.role, page };
})();

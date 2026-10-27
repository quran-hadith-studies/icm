'use strict';
/* Shared by the ICM wizard and the narrator wiki. Pages are drawn by their own scripts after load and on every step,
   so accessibility details that cannot be set where an element is made are fixed here, after each redraw:
   - a form control with no name takes its title, placeholder, or the heading of the card it sits in;
   - a scrolling region (a wide table, a long list, a tree) becomes reachable with the keyboard;
   - a modal gets focus when it opens and returns it when it closes; Escape closes it;
   - the theme (window.ICMTheme; the narrator wiki has a header button, the wizard a choice in its account menu): automatic (the system's choice) → light → dark → automatic, remembered in localStorage. */
(function () {
  const SCROLLERS = '.grid-wrap,.gwrap,.hlist,.matn,.tscroll,.insp,.pick .res,.log,.mlist,pre.json';
  const named = e => e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || (e.labels && e.labels.length) || e.closest('label');
  function pass() {
    document.querySelectorAll('select,input:not([type=hidden]):not([type=submit]),textarea').forEach(e => {
      if (named(e)) return;
      const card = e.closest('.card,.pick,section');
      const h = card && card.querySelector('h1,h2,h3');
      const t = e.title || e.getAttribute('placeholder') || (h && h.textContent.trim());
      if (t) e.setAttribute('aria-label', t);
    });
    document.querySelectorAll(SCROLLERS).forEach(e => {
      if (e.hasAttribute('tabindex')) return;
      if (e.scrollHeight > e.clientHeight + 2 || e.scrollWidth > e.clientWidth + 2) { e.tabIndex = 0; e.setAttribute('role', 'region'); if (!e.getAttribute('aria-label')) e.setAttribute('aria-label', 'ناحیه‌ی قابل‌پیمایش'); }
    });
  }
  let t = null;
  const later = () => { clearTimeout(t); t = setTimeout(pass, 150); };
  new MutationObserver(later).observe(document.body, { childList: true, subtree: true });
  addEventListener('load', later); addEventListener('resize', later);
  /* theme: the choice is [data-theme] on <html> (the inline script in each page's head restores it before the first paint) */
  (function () {
    const btn = document.getElementById('theme');
    const mq = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;
    const get = () => { try { const t = localStorage.getItem('theme'); return t === 'light' || t === 'dark' ? t : 'auto'; } catch (_) { return 'auto'; } };
    const NS = 'http://www.w3.org/2000/svg';
    const icon = d => { const s = document.createElementNS(NS, 'svg'); s.setAttribute('class', 'ico'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true');
      d.forEach(x => { const p = document.createElementNS(NS, 'path'); p.setAttribute('d', x); s.append(p); }); return s; };
    const SUN = ['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z', 'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'];
    const MOON = ['M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z'];
    const AUTO = ['M12 3a9 9 0 1 0 0 18V3z', 'M12 3a9 9 0 0 1 0 18'];
    const LABEL = { auto: 'پوسته: خودکار (مطابق دستگاه)', light: 'پوسته: روشن', dark: 'پوسته: تیره' };
    const paint = () => {
      const m = get();
      if (m === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = m;
      if (!btn) return;
      btn.replaceChildren(icon(m === 'light' ? SUN : m === 'dark' ? MOON : AUTO));
      btn.title = LABEL[m]; btn.setAttribute('aria-label', LABEL[m] + ' — برای تغییر بزنید');
    };
    const set = m => { try { m === 'auto' ? localStorage.removeItem('theme') : localStorage.setItem('theme', m); } catch (_) {} paint(); };
    if (btn) btn.addEventListener('click', () => set({ auto: 'light', light: 'dark', dark: 'auto' }[get()]));
    window.ICMTheme = { get, set };      // the wizard's account menu sets it too
    paint();
  })();
  /* modal focus */
  const modal = document.getElementById('modal');
  if (modal) {
    let back = null;
    new MutationObserver(() => {
      if (!modal.hidden && !back) { back = document.activeElement; const x = document.getElementById('modal-x'); if (x) x.focus(); }
      else if (modal.hidden && back) { try { back.focus(); } catch (_) {} back = null; }
    }).observe(modal, { attributes: true, attributeFilter: ['hidden'] });
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !modal.hidden) { if (window.ICMCloseModal) window.ICMCloseModal(); else modal.hidden = true; } });
  }
})();

'use strict';
/* Shared by the ICM wizard and the narrator wiki. Pages are drawn by their own scripts after load and on every step,
   so accessibility details that cannot be set where an element is made are fixed here, after each redraw:
   - a form control with no name takes its title, placeholder, or the heading of the card it sits in;
   - a scrolling region (a wide table, a long list, a tree) becomes reachable with the keyboard;
   - a modal gets focus when it opens and returns it when it closes; Escape closes it. */
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
  /* modal focus */
  const modal = document.getElementById('modal');
  if (modal) {
    let back = null;
    new MutationObserver(() => {
      if (!modal.hidden && !back) { back = document.activeElement; const x = document.getElementById('modal-x'); if (x) x.focus(); }
      else if (modal.hidden && back) { try { back.focus(); } catch (_) {} back = null; }
    }).observe(modal, { attributes: true, attributeFilter: ['hidden'] });
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !modal.hidden) modal.hidden = true; });
  }
})();

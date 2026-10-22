/* The reconstructed text (متن بازسازی‌شده): the running text a narrator most probably transmitted, built from the
   model's ancestral state probabilities and the step-3 alignment. Post-processing only; nothing here is fed back.

   ICMRecon.build({align, units, variants, vec}) -> {clauses: [{clause, kind, text, present, p, unit, tokens}], decided}
     align     {seed, clauses: [{i, kind, text}], grids: [{clause, witnesses, columns: [{wid: token}]}], witnesses: [wid],
                segments: {"ci|wid": {state, span, tokens}}}           (the align stage, icm.serve.align_view)
     units     the align stage's units (name, kind, unit_kind, clause, labels, states, cols), matched to variants by name
     variants  the bundle's variants (the model's order)
     vec       per variant k the probabilities of its states in the narrator's text (null: no reconstruction)
   A token is {t, p, unit, state, alts, omit}: p = probability of the chosen state of the unit that decided it (null
   where the witnesses agree or their difference is not modelled), omit = the narrator most probably lacked it.

   ICMRecon.render(host, rec, {hue, fa, unitName}) draws it (DOM, textContent only). */
(function () {
  const argmax = p => p.reduce((b, x, i) => (x > p[b] ? i : b), 0);
  function majority(col, ws, prefer) {
    const n = new Map();
    ws.forEach(w => { const t = col[w]; if (t != null) n.set(t, (n.get(t) || 0) + 1); });
    let best = null, bc = 0;
    n.forEach((c, t) => { if (c > bc || (c === bc && t === col[prefer])) { best = t; bc = c; } });
    return best;
  }

  function build(o) {
    const A = o.align, seed = A.seed, grid = {};
    A.grids.forEach(g => { grid[g.clause] = g; });
    const kOf = {}; (o.variants || []).forEach((v, k) => { kOf[v.name] = k; });
    const units = (o.units || []).map(u => Object.assign({}, u, { k: kOf[u.name] })).filter(u => u.k !== undefined);
    const vecOf = k => (o.vec && o.vec[k] && o.vec[k].length ? o.vec[k] : null);
    const seg = (ci, w) => A.segments[`${ci}|${w}`];

    /* clause order: the seed's clauses in order; a clause the seed lacks after the seed clause that most often
       precedes it in the witnesses that have it */
    const seedCl = A.clauses.filter(c => c.kind === 'seed').map(c => c.i);
    const extra = A.clauses.filter(c => c.kind !== 'seed');
    const after = {};
    extra.forEach(c => {
      const votes = {};
      A.witnesses.forEach(w => {
        const s = seg(c.i, w); if (!s || s.state !== 'present' || !s.span) return;
        let bi = -1, bs = -1;
        seedCl.forEach(si => { const s2 = seg(si, w); if (s2 && s2.span && s2.span[0] < s.span[0] && s2.span[0] > bs) { bs = s2.span[0]; bi = si; } });
        votes[bi] = (votes[bi] || 0) + 1;
      });
      const v = Object.entries(votes).sort((a, b) => b[1] - a[1])[0];
      after[c.i] = v ? +v[0] : seedCl[seedCl.length - 1];
    });
    const order = [];
    extra.filter(c => after[c.i] === -1).forEach(c => order.push(c.i));
    seedCl.forEach(si => { order.push(si); extra.filter(c => after[c.i] === si).forEach(c => order.push(c.i)); });
    extra.forEach(c => { if (!order.includes(c.i)) order.push(c.i); });

    const decided = [];
    const clauses = order.map(ci => {
      const cl = A.clauses.find(c => c.i === ci), g = grid[ci];
      const cu = units.find(u => u.unit_kind === 'clause' && u.clause === ci);
      let p = null, present;
      if (cu && vecOf(cu.k)) {
        p = vecOf(cu.k)[1] || 0; present = p >= 0.5;
        decided.push({ k: cu.k, state: present ? 1 : 0, p: present ? p : 1 - p, label: present ? 'present' : 'absent' });
      } else {                                         // not a modelled unit: what most witnesses show
        let a = 0, b = 0;
        A.witnesses.forEach(w => { const s = seg(ci, w); if (!s) return; if (s.state === 'present') a++; else if (s.state === 'absent') b++; });
        present = cl.kind === 'seed' ? a >= b : a > b;
      }
      const tokens = [];
      if (g && g.columns.length) {
        const ext = {};
        g.witnesses.forEach(w => { let lo = -1, hi = -1; g.columns.forEach((col, j) => { if (col[w] != null) { if (lo < 0) lo = j; hi = j; } }); ext[w] = [lo, hi]; });
        const colUnit = {};
        units.filter(u => u.clause === ci && u.cols && u.unit_kind !== 'clause').forEach(u => { for (let j = u.cols[0]; j <= u.cols[1]; j++) if (!colUnit[j]) colUnit[j] = u; });
        const seen = new Set();
        g.columns.forEach((col, j) => {
          const u = colUnit[j], pv = u ? vecOf(u.k) : null;
          if (u && pv) {
            const s = argmax(pv), ws = Object.keys(u.states).filter(w => u.states[w] === s);
            const alts = u.labels.map((l, i) => [l, pv[i] || 0]).filter((x, i) => i !== s && x[1] > 0.05);
            if (!seen.has(u.k)) { seen.add(u.k); decided.push({ k: u.k, state: s, p: pv[s], label: u.labels[s], alts }); }
            const t = majority(col, ws, seed);
            if (t != null) tokens.push({ t, p: pv[s], unit: u.k, state: u.labels[s], alts });
            else if (u.kind === 'presence') { const t0 = majority(col, g.witnesses, seed); if (t0 != null) tokens.push({ t: t0, p: pv[s], unit: u.k, state: u.labels[s], alts, omit: true }); }
            else if (j === u.cols[0] && u.labels[s] !== '*other*' && u.labels[s] !== 'absent') tokens.push({ t: u.labels[s], p: pv[s], unit: u.k, state: u.labels[s], alts });
          } else {
            const have = g.witnesses.filter(w => col[w] != null);
            const miss = g.witnesses.filter(w => col[w] == null && j > ext[w][0] && j < ext[w][1]);
            if (have.length && have.length >= miss.length) tokens.push({ t: majority(col, have, seed), p: null });
          }
        });
      } else {
        const own = seg(ci, seed);
        const s = own && own.tokens && own.tokens.length ? own : A.witnesses.map(w => seg(ci, w)).find(x => x && x.state === 'present' && x.tokens && x.tokens.length);
        (s ? s.tokens : String(cl.text || '').split(/\s+/).filter(Boolean)).forEach(t => tokens.push({ t, p: null }));
      }
      return { clause: ci, kind: cl.kind, text: cl.text, present, p, unit: cu ? cu.k : null, tokens };
    });
    return { clauses, decided };
  }

  const level = p => (p == null ? '' : p >= 0.9 ? 'hi' : p >= 0.6 ? 'me' : 'lo');
  function render(host, rec, opt) {
    const o = opt || {}, fa = o.fa || (x => String(x)), hue = o.hue || (() => 210), uname = o.unitName || (k => 'واحد ' + fa(k + 1));
    const pc = p => fa(Math.round(100 * p)) + '٪';
    const mk = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
    const box = mk('div', 'rc');
    rec.clauses.forEach(c => {
      const row = mk('div', 'rc-cl' + (c.present ? '' : ' off'));
      row.style.setProperty('--h', hue(c.clause));
      const tag = mk('span', 'rc-n', (c.kind === 'seed' ? 'بخش ' + fa(c.clause + 1) : 'افزوده') + (c.p != null ? ` · ${c.present ? 'دارد' : 'ندارد'} ${pc(c.present ? c.p : 1 - c.p)}` : ''));
      if (c.p != null) tag.title = `حضور این بخش در متن این راوی: ${pc(c.p)}`;
      row.append(tag);
      const txt = mk('span', 'rc-tx ar');
      c.tokens.forEach((t, i) => {
        if (i) txt.append(' ');
        const s = mk('span', 'rc-t ' + level(t.p) + (t.omit ? ' om' : ''), t.t);
        if (t.unit != null) s.title = `${uname(t.unit)}: ${t.omit ? 'نبودن' : '«' + t.state + '»'} با احتمال ${pc(t.p)}` +
          (t.alts && t.alts.length ? '\nدیگر حالت‌ها: ' + t.alts.map(([l, q]) => `«${l}» ${pc(q)}`).join('، ') : '');
        txt.append(s);
      });
      row.append(txt);
      box.append(row);
    });
    host.replaceChildren(box);
  }
  window.ICMRecon = { build, render, level };
})();

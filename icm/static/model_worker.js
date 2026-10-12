'use strict';
// One worker = one Python interpreter (Pyodide) running the model for the sources it is given.
// Messages in:  {id, cmd: 'boot', base, v} | {id, cmd: 'init', payload} | {id, cmd: 'source', i}
//               | {id, cmd: 'graphs', i} | {id, cmd: 'finish', light, best, seconds, provenance}
// Messages out: {id, ok: true, value} | {id, ok: false, error}
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/';
importScripts(PYODIDE + 'pyodide.js');
let py = null;

async function boot(base, v) {
  // PYTHONHASHSEED must be fixed before the interpreter starts: the sampler iterates sets of strings
  py = await loadPyodide({ indexURL: PYODIDE, env: { PYTHONHASHSEED: '0' } });
  await py.loadPackage('numpy');
  const zip = await (await fetch(base + '/static/model.zip' + (v ? '?v=' + v : ''), { cache: 'no-cache' })).arrayBuffer();   // versioned: Cloudflare keeps /static for hours
  py.unpackArchive(zip, 'zip', { extractDir: '/home/pyodide/model' });
  py.runPython("import sys\nsys.path.insert(0, '/home/pyodide/model')\nfrom icm import client");
}
function call(fn, ...args) {
  py.globals.set('_args', args);
  const r = py.runPython(`client.${fn}(*_args)`);
  py.globals.delete('_args');
  return r;
}
onmessage = async e => {
  const m = e.data;
  try {
    let v = null;
    if (m.cmd === 'boot') await boot(m.base, m.v);
    else if (m.cmd === 'init') v = call('init', m.payload);
    else if (m.cmd === 'source') v = call('source', m.i);
    else if (m.cmd === 'graphs') v = call('graphs', m.i);
    else if (m.cmd === 'finish') v = call('finish', m.light, m.best, m.seconds, m.provenance);
    postMessage({ id: m.id, ok: true, value: v });
  } catch (err) {
    postMessage({ id: m.id, ok: false, error: String(err && err.message || err) });
  }
};

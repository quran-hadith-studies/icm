/* The standalone results page (scripts/make_html.py): the bundle in the page, shown as in step 6 of the wizard. */
const D = JSON.parse(document.getElementById('bundle').textContent);
ICMResults.mount(document.getElementById('results'), D, D.page_context || {});      // the wizard adds texts, units and identities

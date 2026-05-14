"""HTML templates for the web UI."""

_COMMON_CSS = """
:root {
  --bg: #fafaf9; --fg: #1c1917; --muted: #78716c;
  --border: #e7e5e4; --accent: #0c4a6e; --accent-soft: #f0f9ff;
  --good: #166534; --warn: #92400e; --bad: #991b1b;
  --good-bg: #f0fdf4; --warn-bg: #fffbeb; --bad-bg: #fef2f2;
  --bucket-match: #dbeafe; --bucket-match-fg: #1e40af;
  --bucket-new: #fef3c7; --bucket-new-fg: #92400e;
  --bucket-uncertain: #fee2e2; --bucket-uncertain-fg: #991b1b;
}
* { box-sizing: border-box; }
body { font: 15px/1.5 system-ui, -apple-system, sans-serif;
       color: var(--fg); background: var(--bg); margin: 0;
       padding: 24px 16px; }
.container { max-width: 1100px; margin: 0 auto; }
nav { display: flex; gap: 16px; margin-bottom: 24px; font-size: 14px;
      padding-bottom: 16px; border-bottom: 1px solid var(--border); }
nav a { color: var(--muted); text-decoration: none; }
nav a:hover, nav a.active { color: var(--fg); }
nav .grow { flex: 1; }
nav form { display: inline; }
nav button.logout { background: none; border: none; color: var(--muted);
                    cursor: pointer; font-size: 14px; padding: 0; }
nav button.logout:hover { color: var(--fg); }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 0 0 12px; }
.subtitle { color: var(--muted); margin: 0 0 24px; font-size: 14px; }
.card { background: white; border: 1px solid var(--border);
        border-radius: 8px; padding: 20px; margin-bottom: 16px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 8px 12px;
         border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: 12px;
     text-transform: uppercase; letter-spacing: 0.04em; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
         font-size: 11px; font-weight: 600; text-transform: uppercase; }
.badge.match { background: var(--bucket-match); color: var(--bucket-match-fg); }
.badge.new { background: var(--bucket-new); color: var(--bucket-new-fg); }
.badge.uncertain { background: var(--bucket-uncertain); color: var(--bucket-uncertain-fg); }
.badge.imported { background: var(--good-bg); color: var(--good); }
.badge.extracted { background: #f3f4f6; color: #6b7280; }
.badge.failed { background: var(--bad-bg); color: var(--bad); }
.warnings { background: var(--warn-bg); border: 1px solid #fde68a;
            color: var(--warn); padding: 12px 16px; border-radius: 8px;
            margin-bottom: 16px; font-size: 13px; }
.warnings ul { margin: 4px 0 0; padding-left: 20px; }
button.primary {
  background: var(--accent); color: white; border: none;
  padding: 10px 20px; border-radius: 6px; font-size: 14px;
  font-weight: 500; cursor: pointer;
}
button.primary:hover { background: #075985; }
button.primary:disabled { background: var(--muted); cursor: not-allowed; }
button.secondary {
  background: white; color: var(--fg); border: 1px solid var(--border);
  padding: 6px 12px; border-radius: 4px; font-size: 13px; cursor: pointer;
}
button.secondary:hover { border-color: var(--accent); color: var(--accent); }
input[type=text], input[type=password], input[type=number] {
  font: inherit; padding: 8px 10px; border: 1px solid var(--border);
  border-radius: 4px; width: 100%;
}
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 12px 24px; }
.meta div { font-size: 13px; }
.meta label { color: var(--muted); display: block; font-size: 12px; }
.meta span { font-weight: 500; }
.spinner {
  display: inline-block; width: 14px; height: 14px;
  border: 2px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%; animation: spin 0.8s linear infinite;
  vertical-align: middle; margin-right: 8px;
}
@keyframes spin { to { transform: rotate(360deg); } }
.success { background: var(--good-bg); border: 1px solid #bbf7d0;
           color: var(--good); padding: 16px; border-radius: 8px; }
.error { background: var(--bad-bg); border: 1px solid #fecaca;
         color: var(--bad); padding: 16px; border-radius: 8px; }
"""

_NAV = """<nav>
<a href="/" id="nav-home">Upload</a>
<a href="/history" id="nav-history">History</a>
<a href="/settings" id="nav-settings">Settings</a>
<span class="grow"></span>
<form action="/logout" method="post"><button class="logout" type="submit">Sign out</button></form>
</nav>"""


LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>Sign in</title>
<style>""" + _COMMON_CSS + """
.login { max-width: 320px; margin: 80px auto; }
.login form { display: flex; flex-direction: column; gap: 12px; }
.login button { width: 100%; }
</style></head><body>
<div class="login">
  <h1>Invoice Importer</h1>
  <p class="subtitle">Sign in to continue</p>
  <div class="card">
    <form method="post" action="/login">
      <input type="password" name="password" placeholder="Password" autofocus required />
      {{ERROR_HTML}}
      <button class="primary" type="submit">Sign in</button>
    </form>
  </div>
</div>
<script>
  const err = "{{ERROR}}";
  if (err) {
    document.querySelector('form').insertAdjacentHTML(
      'afterbegin',
      '<div class="error" style="font-size:13px;padding:8px 12px">' + err + '</div>'
    );
  }
</script>
</body></html>"""

# Strip the placeholder div now that JS injects it.
LOGIN_HTML = LOGIN_HTML.replace("{{ERROR_HTML}}", "")


INDEX_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Upload invoice</title>
<style>""" + _COMMON_CSS + """
.drop {
  border: 2px dashed var(--border); border-radius: 12px;
  padding: 56px 24px; text-align: center; cursor: pointer;
  background: white; transition: all 0.15s ease; display: block;
}
.drop:hover, .drop.over {
  border-color: var(--accent); background: var(--accent-soft);
}
.drop p { margin: 8px 0; color: var(--muted); }
.drop input { display: none; }
.status { margin-top: 24px; padding: 16px 20px; border-radius: 8px;
          background: white; border: 1px solid var(--border); }
.status.hidden { display: none; }
</style></head><body>
<div class="container">
""" + _NAV.replace('id="nav-home">Upload<', 'id="nav-home" class="active">Upload<') + """
  <h1>Upload an invoice</h1>
  <p class="subtitle">Drop a PDF. We'll extract, match, and price it for review.</p>
  <label class="drop" id="drop">
    <input type="file" id="file" accept="application/pdf" />
    <p><strong>Drop a PDF here</strong> or click to select</p>
    <p style="font-size:12px">Max 30 MB</p>
  </label>
  <div class="status hidden" id="status"></div>
</div>
<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const statusEl = document.getElementById('status');

drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', e => {
  e.preventDefault(); drop.classList.remove('over');
  if (e.dataTransfer.files[0]) handle(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', e => {
  if (e.target.files[0]) handle(e.target.files[0]);
});

async function handle(file) {
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    show('Only PDF files are supported.', 'error'); return;
  }
  show('<span class="spinner"></span>Extracting, matching, and pricing... (15-45 seconds)');
  const form = new FormData(); form.append('file', file);
  try {
    const resp = await fetch('/invoices/process', { method: 'POST', body: form });
    const data = await resp.json();
    if (!resp.ok) { show('Error: ' + (data.detail || resp.statusText), 'error'); return; }
    if (data.duplicate) {
      show('<strong>Duplicate.</strong> ' + escape(data.message) +
           ' <a href="/review/' + data.existing_invoice_id + '">View existing</a>', 'error');
      return;
    }
    window.location.href = data.redirect;
  } catch (err) { show('Network error: ' + err.message, 'error'); }
}
function show(html, kind) {
  statusEl.innerHTML = html;
  statusEl.className = 'status' + (kind ? ' ' + kind : '');
}
function escape(s) { return String(s||'').replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
})[c]); }
</script>
</body></html>"""


HISTORY_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<title>History</title>
<style>""" + _COMMON_CSS + """
tr.row:hover { background: var(--accent-soft); cursor: pointer; }
</style></head><body>
<div class="container">
""" + _NAV.replace('id="nav-history">History<', 'id="nav-history" class="active">History<') + """
  <h1>Processed invoices</h1>
  <p class="subtitle">Latest 50</p>
  <div class="card">
    <table id="tbl">
      <thead><tr>
        <th>When</th><th>Supplier</th><th>Invoice #</th><th>Date</th>
        <th class="num">Total</th><th>Status</th>
      </tr></thead>
      <tbody><tr><td colspan="6" style="color:var(--muted)">Loading...</td></tr></tbody>
    </table>
  </div>
</div>
<script>
(async () => {
  const resp = await fetch('/invoices');
  const data = await resp.json();
  const tb = document.querySelector('#tbl tbody');
  if (!data.data.length) {
    tb.innerHTML = '<tr><td colspan="6" style="color:var(--muted)">No invoices yet.</td></tr>';
    return;
  }
  tb.innerHTML = '';
  for (const r of data.data) {
    const tr = document.createElement('tr');
    tr.className = 'row';
    tr.onclick = () => window.location = '/review/' + r.id;
    tr.innerHTML =
      '<td>' + new Date(r.created_at).toLocaleString() + '</td>' +
      '<td>' + escape(r.supplier_name) + '</td>' +
      '<td>' + escape(r.supplier_invoice_number) + '</td>' +
      '<td>' + escape(r.invoice_date) + '</td>' +
      '<td class="num">' + (r.total == null ? '—' : r.total.toFixed(2)) + '</td>' +
      '<td><span class="badge ' + r.status.toLowerCase() + '">' + r.status + '</span></td>';
    tb.appendChild(tr);
  }
})();
function escape(s) { return s == null ? '—' : String(s).replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
})[c]); }
</script>
</body></html>"""


REVIEW_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Review invoice</title>
<style>""" + _COMMON_CSS + """
.line-row { background: white; border: 1px solid var(--border);
            border-radius: 6px; padding: 12px 16px; margin-bottom: 8px;
            display: grid; grid-template-columns: 1fr auto auto auto;
            gap: 16px; align-items: center; }
.line-row .from { font-size: 13px; }
.line-row .from strong { display: block; }
.line-row .from small { color: var(--muted); }
.line-row .qty-cost { text-align: right; font-variant-numeric: tabular-nums;
                      font-size: 13px; color: var(--muted); }
.line-row .price-cell { display: flex; align-items: center; gap: 6px; }
.line-row .price-cell input { width: 80px; text-align: right; }
.decision { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.decision-controls { font-size: 12px; color: var(--muted); margin-top: 6px;
                     padding-top: 6px; border-top: 1px dashed var(--border); }
.cand-btn { background: white; border: 1px solid var(--border);
            border-radius: 4px; padding: 4px 8px; cursor: pointer;
            font-size: 12px; margin: 2px; }
.cand-btn:hover { border-color: var(--accent); }
.cand-btn.picked { background: var(--accent); color: white; border-color: var(--accent); }
.bucket-title { display: flex; align-items: center; gap: 12px;
                margin: 24px 0 12px; }
.bucket-title h2 { margin: 0; }
.bucket-title small { color: var(--muted); font-size: 13px; }
.search-result { padding: 6px 8px; cursor: pointer; border-radius: 4px;
                 display: flex; justify-content: space-between; font-size: 13px; }
.search-result:hover { background: var(--accent-soft); }
.search-result.picked { background: var(--accent); color: white; }
.search-box { position: relative; }
.search-results { position: absolute; top: 100%; left: 0; right: 0;
                  background: white; border: 1px solid var(--border);
                  border-radius: 0 0 4px 4px; max-height: 200px;
                  overflow-y: auto; z-index: 10; }
.actions { display: flex; gap: 12px; align-items: center; margin-top: 24px;
           padding-top: 16px; border-top: 1px solid var(--border); }
.actions .grow { flex: 1; }
label.opt { font-size: 13px; color: var(--muted);
            display: inline-flex; align-items: center; gap: 6px; }
.pricing-pill { font-size: 11px; padding: 2px 6px; border-radius: 3px;
                background: #f3f4f6; color: #6b7280; }
.pricing-pill.msrp { background: #dcfce7; color: #166534; }
.pricing-pill.scrape { background: #ede9fe; color: #5b21b6; }
.pricing-pill.rule { background: #f3f4f6; color: #6b7280; }
.pricing-pill.none { background: var(--bad-bg); color: var(--bad); }
</style></head><body>
<div class="container">
""" + _NAV + """
  <div id="content" style="min-height:300px"><p style="color:var(--muted)">Loading...</p></div>
</div>
<script>
const INVOICE_ID = {{INVOICE_ID}};
let DATA = null;
let DECISIONS = {};      // index in uncertain[] -> decision object
let MATCHED_PRICE_OVERRIDES = {};  // index in matched[] -> retail price

(async () => {
  const resp = await fetch('/invoices/' + INVOICE_ID);
  const result = await resp.json();
  if (!resp.ok) {
    document.getElementById('content').innerHTML =
      '<div class="error">' + escape(result.detail || 'Failed to load') + '</div>';
    return;
  }
  DATA = result;
  // Initialize matched overrides to suggested retail
  (DATA.data.matched || []).forEach((m, i) => {
    if (m.suggested_retail_price != null) {
      MATCHED_PRICE_OVERRIDES[i] = m.suggested_retail_price;
    }
  });
  render();
})();

function render() {
  const d = DATA.data;
  const inv = d.invoice;
  const isImported = DATA.status === 'IMPORTED';
  let html = '';

  if (d.warnings && d.warnings.length) {
    html += '<div class="warnings"><strong>Warnings</strong><ul>';
    for (const w of d.warnings) html += '<li>' + escape(w) + '</li>';
    html += '</ul></div>';
  }

  if (isImported) {
    html += '<div class="success">✓ Imported as consignment '
         + '<code>' + escape(DATA.consignment_id) + '</code></div>';
  }

  html += '<div class="card"><h2>Invoice</h2><div class="meta">';
  html += metaRow('Supplier', inv.supplier_name);
  html += metaRow('Supplier ID', inv.supplier_id || 'NOT FOUND');
  html += metaRow('Invoice #', inv.invoice_number);
  html += metaRow('Date', inv.invoice_date);
  html += metaRow('Pages', inv.page_count);
  html += metaRow('Lines', (d.matched.length + d.uncertain.length));
  html += metaRow('Subtotal', fmtMoney(inv.subtotal));
  html += metaRow('Total', fmtMoney(inv.total));
  html += '</div>';
  html += '<div style="margin-top:12px"><a href="/invoices/' + INVOICE_ID
       + '/csv" class="cand-btn">Download CSV backup</a></div>';
  html += '</div>';

  // Matched bucket
  if (d.matched.length) {
    html += '<div class="bucket-title"><span class="badge match">Match</span>'
         + '<h2>Existing products to update</h2>'
         + '<small>(' + d.matched.length + ' — cost + retail price will be updated on import)</small>'
         + '</div>';
    d.matched.forEach((m, i) => {
      html += renderMatchedRow(m, i, isImported);
    });
  }

  // Uncertain bucket
  if (d.uncertain.length) {
    html += '<div class="bucket-title"><span class="badge uncertain">Uncertain</span>'
         + '<h2>Needs your decision</h2>'
         + '<small>(' + d.uncertain.length + ' — pick existing, create new, or skip)</small>'
         + '</div>';
    d.uncertain.forEach((u, i) => {
      html += renderUncertainRow(u, i, isImported);
    });
  }

  if (!isImported) {
    html += '<div class="actions">'
         + '<label class="opt"><input type="checkbox" id="receive" />'
         + 'Mark RECEIVED immediately (updates inventory)</label>'
         + '<label class="opt"><input type="checkbox" id="updateCosts" checked />'
         + 'Update cost + retail on matched products</label>'
         + '<span class="grow"></span>'
         + '<button class="primary" id="finalBtn" onclick="finalize()">'
         + 'Push to Lightspeed</button></div>'
         + '<div id="finalResult" style="margin-top:16px"></div>';
  }

  document.getElementById('content').innerHTML = html;
  updateFinalButton();
}

function renderMatchedRow(m, i, locked) {
  const pricing = m.suggested_retail_price;
  const source = m.pricing_source || 'none';
  return '<div class="line-row">' +
    '<div class="from"><strong>' + escape(m.product_name) + '</strong>' +
      '<small>' + escape(m.product_sku || '') + ' · matched by ' + m.matched_by + '</small>' +
      '<small>Invoice: ' + escape(m.description || m.supplier_code || '—') + '</small></div>' +
    '<div class="qty-cost">' + m.quantity + ' × $' + m.unit_cost.toFixed(2) +
      '<br><small>= $' + (m.quantity * m.unit_cost).toFixed(2) + '</small></div>' +
    '<div class="price-cell">' +
      '<span class="pricing-pill ' + sourceClass(source) + '">' + sourceLabel(source) + '</span>' +
      (locked ? ('<span style="font-size:13px">$' + (pricing != null ? pricing.toFixed(2) : '—') + '</span>')
              : ('$<input type="number" step="0.01" value="' + (pricing != null ? pricing.toFixed(2) : '')
                + '" onchange="MATCHED_PRICE_OVERRIDES[' + i + ']=parseFloat(this.value)||null" />')) +
    '</div>' +
    '<div></div></div>';
}

function renderUncertainRow(u, i, locked) {
  const dec = DECISIONS[i];
  let controls;
  if (locked) {
    controls = '<small>Locked</small>';
  } else if (dec && dec.decision === 'match_existing') {
    controls = '<small>✓ Matched to <strong>' + escape(dec._name) + '</strong></small>'
            + ' <button class="cand-btn" onclick="clearDecision(' + i + ')">change</button>';
  } else if (dec && dec.decision === 'create_new') {
    controls = '<small>✓ Will create new product: <strong>' + escape(dec.new_product_name) + '</strong>'
            + ' @ $' + (dec.new_retail_price != null ? dec.new_retail_price.toFixed(2) : '—') + '</small>'
            + ' <button class="cand-btn" onclick="clearDecision(' + i + ')">change</button>';
  } else if (dec && dec.decision === 'skip') {
    controls = '<small style="color:var(--muted)">skipped</small>'
            + ' <button class="cand-btn" onclick="clearDecision(' + i + ')">undo</button>';
  } else {
    let buttons = '';
    if (u.candidates && u.candidates.length) {
      for (const c of u.candidates) {
        buttons += '<button class="cand-btn" onclick="matchExisting(' + i + ',\\''
                + c.product_id + '\\',\\'' + escAttr(c.name) + '\\')">'
                + escape(c.name) + ' <span style="color:var(--muted)">('
                + Math.round(c.confidence * 100) + '%)</span></button>';
      }
    }
    buttons += '<button class="cand-btn" onclick="openSearch(' + i + ')">Search...</button>';
    buttons += '<button class="cand-btn" onclick="openCreateNew(' + i + ')">Create new</button>';
    buttons += '<button class="cand-btn" onclick="skipUncertain(' + i + ')">Skip</button>';
    controls = '<div class="decision">' + buttons + '</div>';
  }

  return '<div class="line-row" id="urow-' + i + '">' +
    '<div class="from"><strong>' + escape(u.description || '—') + '</strong>' +
      (u.supplier_code ? '<small>Code: ' + escape(u.supplier_code) + '</small>' : '') +
      (u.barcode ? '<small>Barcode: ' + escape(u.barcode) + '</small>' : '') + '</div>' +
    '<div class="qty-cost">' + u.quantity + ' × $' + u.unit_cost.toFixed(2) +
      '<br><small>= $' + (u.quantity * u.unit_cost).toFixed(2) + '</small></div>' +
    '<div class="price-cell">' +
      '<span class="pricing-pill ' + sourceClass(u.pricing_source) + '">'
      + sourceLabel(u.pricing_source) + '</span>' +
      (u.suggested_retail_price != null ?
        '<small>suggested $' + u.suggested_retail_price.toFixed(2) + '</small>'
        : '<small>—</small>') +
    '</div>' +
    '<div style="grid-column: 1 / -1">' + controls + '</div>' +
    '</div>';
}

function matchExisting(i, productId, name) {
  const u = DATA.data.uncertain[i];
  DECISIONS[i] = {
    decision: 'match_existing',
    supplier_code: u.supplier_code, description: u.description,
    barcode: u.barcode, quantity: u.quantity, unit_cost: u.unit_cost,
    lightspeed_product_id: productId,
    retail_price_override: u.suggested_retail_price,
    _name: name,
  };
  render();
}

async function openSearch(i) {
  const q = prompt('Search Lightspeed for product name:');
  if (!q) return;
  const resp = await fetch('/products/search?q=' + encodeURIComponent(q));
  const data = await resp.json();
  if (!data.data.length) { alert('No matches'); return; }
  const options = data.data.map(p => p.name + (p.sku ? ' (' + p.sku + ')' : '')).join('\\n');
  const pick = prompt('Type the exact name to pick:\\n\\n' + options);
  if (!pick) return;
  const match = data.data.find(p => p.name === pick.split(' (')[0]);
  if (!match) { alert('Not found'); return; }
  matchExisting(i, match.id, match.name);
}

function openCreateNew(i) {
  const u = DATA.data.uncertain[i];
  const name = prompt('Product name:', u.description || '');
  if (!name) return;
  const sku = prompt('SKU (leave blank for none):', u.supplier_code || '');
  let retail = u.suggested_retail_price;
  const retailStr = prompt('Retail price:', retail != null ? retail.toFixed(2) : '');
  retail = parseFloat(retailStr);
  if (isNaN(retail)) retail = null;
  DECISIONS[i] = {
    decision: 'create_new',
    supplier_code: u.supplier_code, description: u.description,
    barcode: u.barcode, quantity: u.quantity, unit_cost: u.unit_cost,
    new_product_name: name, new_product_sku: sku || null,
    new_retail_price: retail,
  };
  render();
}

function skipUncertain(i) {
  const u = DATA.data.uncertain[i];
  DECISIONS[i] = {
    decision: 'skip',
    supplier_code: u.supplier_code, description: u.description,
    barcode: u.barcode, quantity: u.quantity, unit_cost: u.unit_cost,
  };
  render();
}
function clearDecision(i) { delete DECISIONS[i]; render(); }

function updateFinalButton() {
  const btn = document.getElementById('finalBtn');
  if (!btn) return;
  const total = (DATA.data.uncertain || []).length;
  const decided = Object.keys(DECISIONS).length;
  const pending = total - decided;
  if (pending > 0) {
    btn.disabled = true;
    btn.textContent = 'Decide ' + pending + ' more uncertain line(s)';
  } else {
    btn.disabled = false;
    btn.textContent = 'Push to Lightspeed';
  }
}

async function finalize() {
  const btn = document.getElementById('finalBtn');
  btn.disabled = true; btn.textContent = 'Pushing...';
  const body = {
    invoice_id: INVOICE_ID,
    receive_immediately: document.getElementById('receive').checked,
    update_costs_for_existing: document.getElementById('updateCosts').checked,
    decisions: Object.values(DECISIONS),
    matched_overrides: MATCHED_PRICE_OVERRIDES,
  };
  try {
    const resp = await fetch('/invoices/finalize', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    const out = document.getElementById('finalResult');
    if (resp.ok) {
      let h = '<div class="success">✓ Imported as <code>' + escape(data.consignment_id)
            + '</code> (' + data.status + ', ' + data.items_added + ' items)';
      if (data.products_created.length) h += '<br>Created ' + data.products_created.length + ' new products.';
      if (data.products_updated.length) h += '<br>Updated ' + data.products_updated.length + ' existing products.';
      if (data.errors.length) h += '<br><strong>Errors:</strong><ul>' +
        data.errors.map(e => '<li>' + escape(e) + '</li>').join('') + '</ul>';
      h += '</div>';
      out.innerHTML = h;
      setTimeout(() => location.reload(), 1500);
    } else {
      out.innerHTML = '<div class="error">' + escape(data.detail || resp.statusText) + '</div>';
      btn.disabled = false; btn.textContent = 'Retry';
    }
  } catch (err) {
    document.getElementById('finalResult').innerHTML =
      '<div class="error">Network error: ' + escape(err.message) + '</div>';
    btn.disabled = false; btn.textContent = 'Retry';
  }
}

function metaRow(label, value) {
  return '<div><label>' + label + '</label><span>' + escape(value == null ? '—' : value) + '</span></div>';
}
function fmtMoney(n) { return n == null ? '—' : '$' + n.toFixed(2); }
function sourceClass(s) {
  if (!s || s === 'none') return 'none';
  if (s === 'msrp') return 'msrp';
  if (s.startsWith('scrape')) return 'scrape';
  return 'rule';
}
function sourceLabel(s) {
  if (!s || s === 'none') return 'no source';
  if (s === 'msrp') return 'MSRP';
  if (s === 'scrape:chewy') return 'Chewy';
  if (s === 'scrape:petco') return 'Petco';
  if (s === 'scrape:petsmart') return 'PetSmart';
  if (s === 'rule') return 'Rule';
  return s;
}
function escape(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
})[c]); }
function escAttr(s) { return String(s == null ? '' : s).replace(/'/g, "\\\\'"); }
</script>
</body></html>"""


SETTINGS_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<title>Settings</title>
<style>""" + _COMMON_CSS + """
.rule-row { display: grid; grid-template-columns: 2fr 3fr 1fr 1fr 1fr auto;
            gap: 8px; align-items: center; padding: 8px 0;
            border-bottom: 1px solid var(--border); font-size: 13px; }
.rule-row.header { font-weight: 600; color: var(--muted);
                   font-size: 12px; text-transform: uppercase; }
.rule-row input { font: inherit; padding: 4px 6px; border: 1px solid var(--border);
                  border-radius: 3px; width: 100%; }
form.upload { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
</style></head><body>
<div class="container">
""" + _NAV.replace('id="nav-settings">Settings<', 'id="nav-settings" class="active">Settings<') + """
  <h1>Settings</h1>

  <div class="card">
    <h2>Pricing rules</h2>
    <p class="subtitle">Markup is cost × multiplier, applied in priority order. First match wins.
       Default rule (no keywords) catches everything else.</p>
    <div id="rules">Loading...</div>
    <div class="rule-row" style="border-top: 2px solid var(--border); margin-top: 8px">
      <input id="rname" placeholder="Rule name" />
      <input id="rkw" placeholder="keywords,comma,separated (blank = match all)" />
      <input id="rmult" type="number" step="0.01" placeholder="2.2" />
      <input id="rpri" type="number" placeholder="100" />
      <select id="rround">
        <option value="charm">.99 rounding</option>
        <option value="none">no rounding</option>
      </select>
      <button class="secondary" onclick="addRule()">Add</button>
    </div>
  </div>

  <div class="card">
    <h2>Upload MSRP list</h2>
    <p class="subtitle">CSV with columns: <code>supplier_code, barcode, msrp, notes</code>.
       supplier_code OR barcode required per row.</p>
    <form class="upload" onsubmit="uploadMsrp(event)">
      <select id="msup" required></select>
      <input type="file" id="mfile" accept=".csv" required />
      <button class="primary" type="submit">Upload</button>
    </form>
    <div id="mresult" style="margin-top:12px"></div>
  </div>
</div>
<script>
async function loadRules() {
  const resp = await fetch('/pricing/rules');
  const data = await resp.json();
  const el = document.getElementById('rules');
  let html = '<div class="rule-row header"><div>Name</div><div>Keywords</div>'
           + '<div>Multiplier</div><div>Priority</div><div>Rounding</div><div></div></div>';
  for (const r of data.data) {
    html += '<div class="rule-row">'
         + '<div>' + escape(r.name) + '</div>'
         + '<div style="color:var(--muted)">' + escape(r.keywords || '(all)') + '</div>'
         + '<div>' + r.multiplier + '×</div>'
         + '<div>' + r.priority + '</div>'
         + '<div>' + r.rounding + '</div>'
         + '<div><button class="secondary" onclick="delRule(' + r.id + ')">×</button></div>'
         + '</div>';
  }
  el.innerHTML = html;
}
async function addRule() {
  const name = document.getElementById('rname').value.trim();
  const kw = document.getElementById('rkw').value.trim() || null;
  const mult = parseFloat(document.getElementById('rmult').value);
  const pri = parseInt(document.getElementById('rpri').value || '100');
  const round = document.getElementById('rround').value;
  if (!name || isNaN(mult)) { alert('Name and multiplier required'); return; }
  await fetch('/pricing/rules', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, keywords: kw, multiplier: mult, priority: pri, rounding: round}),
  });
  document.getElementById('rname').value = '';
  document.getElementById('rkw').value = '';
  document.getElementById('rmult').value = '';
  loadRules();
}
async function delRule(id) {
  if (!confirm('Delete this rule?')) return;
  await fetch('/pricing/rules/' + id, { method: 'DELETE' });
  loadRules();
}
async function loadSuppliers() {
  const resp = await fetch('/suppliers');
  const data = await resp.json();
  const sel = document.getElementById('msup');
  sel.innerHTML = data.data.map(s =>
    '<option value="' + s.id + '">' + escape(s.name) + '</option>'
  ).join('');
}
async function uploadMsrp(e) {
  e.preventDefault();
  const form = new FormData();
  form.append('supplier_id', document.getElementById('msup').value);
  form.append('file', document.getElementById('mfile').files[0]);
  const resp = await fetch('/pricing/msrp', { method: 'POST', body: form });
  const data = await resp.json();
  const out = document.getElementById('mresult');
  if (resp.ok) {
    let h = '<div class="success">Added ' + data.added + ' MSRP entries.</div>';
    if (data.errors.length) h += '<div class="warnings">Errors:<ul>' +
      data.errors.map(e => '<li>' + escape(e) + '</li>').join('') + '</ul></div>';
    out.innerHTML = h;
  } else {
    out.innerHTML = '<div class="error">' + escape(data.detail || resp.statusText) + '</div>';
  }
}
function escape(s) { return s == null ? '' : String(s).replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
})[c]); }
loadRules(); loadSuppliers();
</script>
</body></html>"""

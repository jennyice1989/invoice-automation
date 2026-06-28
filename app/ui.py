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
<a href="/enrich" id="nav-enrich">Add products</a>
<a href="/audit" id="nav-audit">Catalog audit</a>
<a href="/history" id="nav-history">History</a>
<a href="/settings" id="nav-settings">Settings</a>
<a href="/admin" id="nav-admin">Admin</a>
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
        <th class="num">Total</th><th>Status</th><th></th>
      </tr></thead>
      <tbody><tr><td colspan="7" style="color:var(--muted)">Loading...</td></tr></tbody>
    </table>
  </div>
</div>
<script>
(async () => { await load(); })();

async function load() {
  const resp = await fetch('/invoices');
  const data = await resp.json();
  const tb = document.querySelector('#tbl tbody');
  if (!data.data.length) {
    tb.innerHTML = '<tr><td colspan="7" style="color:var(--muted)">No invoices yet.</td></tr>';
    return;
  }
  tb.innerHTML = '';
  for (const r of data.data) {
    const tr = document.createElement('tr');
    tr.className = 'row';
    tr.innerHTML =
      '<td onclick="go(' + r.id + ')">' + new Date(r.created_at).toLocaleString() + '</td>' +
      '<td onclick="go(' + r.id + ')">' + escape(r.supplier_name) + '</td>' +
      '<td onclick="go(' + r.id + ')">' + escape(r.supplier_invoice_number) + '</td>' +
      '<td onclick="go(' + r.id + ')">' + escape(r.invoice_date) + '</td>' +
      '<td class="num" onclick="go(' + r.id + ')">' +
        (r.total == null ? '—' : r.total.toFixed(2)) + '</td>' +
      '<td onclick="go(' + r.id + ')"><span class="badge ' + r.status.toLowerCase() +
        '">' + r.status + '</span></td>' +
      '<td><button class="secondary" onclick="del(' + r.id + ',\\'' +
        escAttr(r.supplier_invoice_number || ('#' + r.id)) + '\\',\\'' +
        r.status + '\\')">Delete</button></td>';
    tb.appendChild(tr);
  }
}
function go(id) { window.location = '/review/' + id; }

async function del(id, label, status) {
  let msg = 'Delete invoice ' + label + '?';
  if (status === 'IMPORTED') {
    msg += '\\n\\nNOTE: This was already pushed to Lightspeed. The '
         + 'consignment in Lightspeed will NOT be deleted — only this '
         + 'local record. You would need to remove the consignment in '
         + 'Lightspeed manually.';
  }
  if (!confirm(msg)) return;
  const resp = await fetch('/invoices/' + id, { method: 'DELETE' });
  const data = await resp.json();
  if (resp.ok) {
    if (data.warning) alert(data.warning);
    load();
  } else {
    alert('Delete failed: ' + (data.detail || resp.statusText));
  }
}
function escape(s) { return s == null ? '—' : String(s).replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
})[c]); }
function escAttr(s) { return String(s == null ? '' : s).replace(/'/g, "\\\\'"); }
</script>
</body></html>"""


AUDIT_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Catalog audit</title>
<style>""" + _COMMON_CSS + """
.toolbar { display: grid; grid-template-columns: 1fr 180px auto auto; gap: 8px;
           align-items: center; margin-bottom: 16px; }
.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
           gap: 8px; margin-bottom: 16px; }
.metric { background: white; border: 1px solid var(--border); border-radius: 6px;
          padding: 10px 12px; }
.metric strong { display: block; font-size: 18px; }
.metric span { color: var(--muted); font-size: 12px; }
.audit-row { background: white; border: 1px solid var(--border); border-radius: 8px;
             padding: 14px 16px; margin-bottom: 10px; }
.audit-head { display: grid; grid-template-columns: 1fr auto; gap: 12px;
              align-items: start; }
.audit-head h2 { margin: 0 0 4px; }
.audit-meta { color: var(--muted); font-size: 12px; }
.issue-list { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.issue { display: inline-block; padding: 2px 7px; border-radius: 4px;
         font-size: 11px; font-weight: 600; }
.issue.high { background: var(--bad-bg); color: var(--bad); }
.issue.medium { background: var(--warn-bg); color: var(--warn); }
.issue.low { background: #f3f4f6; color: #4b5563; }
.audit-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
                 margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
.audit-actions textarea { width: 100%; min-height: 110px; font: 12px/1.4 ui-monospace, monospace;
                          padding: 8px; border: 1px solid var(--border); border-radius: 4px; }
.price-box { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.price-box input { width: 100px; }
.empty { color: var(--muted); padding: 24px; text-align: center; }
@media (max-width: 760px) {
  .toolbar, .audit-actions, .audit-head { grid-template-columns: 1fr; }
}
</style></head><body>
<div class="container">
""" + _NAV.replace('id="nav-audit">Catalog audit<', 'id="nav-audit" class="active">Catalog audit<') + """
  <h1>Catalog audit</h1>
  <p class="subtitle">Review existing Lightspeed products for missing photos, weak descriptions, and pricing below target.</p>

  <div class="toolbar">
    <input type="text" id="q" placeholder="Search name, SKU, barcode, supplier code" onkeydown="if(event.key==='Enter') load()" />
    <select id="issue" onchange="load()">
      <option value="all">All issues</option>
      <option value="missing_description">Missing description</option>
      <option value="weak_description">Weak description</option>
      <option value="missing_photo">Missing photo</option>
      <option value="below_target_margin">Below target margin</option>
      <option value="missing_price">Missing price</option>
      <option value="missing_barcode">Missing barcode</option>
      <option value="missing_sku">Missing SKU</option>
      <option value="generated_sku">Generated/internal SKU</option>
      <option value="missing_barcode_sku">Missing both barcode/SKU</option>
      <option value="missing_brand">Missing brand</option>
      <option value="missing_category">Missing category</option>
    </select>
    <button class="secondary" onclick="load()">Search</button>
    <button class="primary" id="syncBtn" onclick="syncCatalog()">Sync catalog</button>
  </div>
  <div class="toolbar">
    <label class="opt"><input type="checkbox" id="selectAll" onchange="toggleSelectAll(this.checked)" /> Select all visible</label>
    <button class="secondary" id="bulkDraftBtn" onclick="bulkDraftDescriptions()">Draft selected descriptions</button>
    <button class="primary" id="bulkDescBtn" onclick="bulkApplyDescriptions()">Approve selected descriptions</button>
    <button class="primary" id="bulkPriceBtn" onclick="bulkApplyPrices()">Approve selected prices</button>
    <button class="primary" id="bulkSkuBtn" onclick="bulkApplySkus()">Assign selected SKUs</button>
    <button class="primary" id="bulkBarcodeSkuBtn" onclick="bulkApplyBarcodeSkus()">Update selected barcode/SKUs</button>
  </div>
  <div id="bulkMsg"></div>

  <div id="summary" class="summary"></div>
  <div id="content"><p style="color:var(--muted)">Loading...</p></div>
</div>
<script>
let PRODUCTS = [];
const BULK_LIMIT = 50;

(async () => { await load(); })();

async function load() {
  const q = document.getElementById('q').value.trim();
  const issue = document.getElementById('issue').value;
  const params = new URLSearchParams({ limit: '100' });
  if (issue && issue !== 'all') params.set('issue', issue);
  if (q) params.set('q', q);
  const resp = await fetch('/audit/products?' + params.toString());
  const data = await resp.json();
  if (!resp.ok) {
    document.getElementById('content').innerHTML =
      '<div class="error">' + escape(data.detail || resp.statusText) + '</div>';
    return;
  }
  PRODUCTS = data.data || [];
  document.getElementById('selectAll').checked = false;
  renderSummary(data.summary || {}, data.total || 0, issue);
  renderProducts();
}

async function syncCatalog() {
  const btn = document.getElementById('syncBtn');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Syncing';
  try {
    const resp = await fetch('/audit/sync', { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) {
      alert('Sync failed: ' + (data.detail || resp.statusText));
    }
    await load();
  } finally {
    btn.disabled = false; btn.textContent = 'Sync catalog';
  }
}

function renderSummary(s, shown, issue) {
  const items = [
    ['Products', s.products || 0],
    ['Showing', shown || 0],
    ['With issues', s.with_issues || 0],
    ['Missing photos', s.missing_photo || 0],
    ['Weak copy', (s.missing_description || 0) + (s.weak_description || 0)],
    ['Below target', s.below_target_margin || 0],
    ['Missing barcodes', s.missing_barcode || 0],
    ['Missing SKUs', s.missing_sku || 0],
    ['Generated SKUs', s.generated_sku || 0],
    ['Missing codes', s.missing_barcode_sku || 0],
  ];
  document.getElementById('summary').innerHTML = items.map(([label, value]) =>
    '<div class="metric"><strong>' + value + '</strong><span>' + label + '</span></div>'
  ).join('') + (issue && issue !== 'all'
    ? '<div class="audit-meta" style="grid-column:1/-1">Filtered by ' + escape(issue.replaceAll('_', ' ')) + '</div>'
    : '');
}

function renderProducts() {
  const el = document.getElementById('content');
  if (!PRODUCTS.length) {
    el.innerHTML = '<div class="card empty">No products match this audit filter.</div>';
    return;
  }
  el.innerHTML = PRODUCTS.map(renderProduct).join('');
}

function renderProduct(p) {
  const issues = (p.issues || []).map(i =>
    '<span class="issue ' + i.severity + '">' + escape(i.label) + '</span>'
  ).join('');
  const price = p.retail_price == null ? '—' : '$' + p.retail_price.toFixed(2);
  const target = p.target_price == null ? '—' : '$' + p.target_price.toFixed(2);
  const skuControls = p.sku ? '' : (
    '<div style="margin-top:12px"><h2>Custom SKU</h2>'
    + '<div class="price-box"><input type="text" id="sku-' + p.id + '" value="'
    + escape(p.suggested_custom_sku || '') + '" />'
    + '<button class="primary" onclick="applySku(\\'' + p.id + '\\')">Assign SKU</button></div></div>'
  );
  const barcodeSkuControls = (!p.barcode || p.is_generated_sku) ? (
    '<div style="margin-top:12px"><h2>Barcode/SKU</h2>'
    + '<div class="price-box"><input type="text" id="barcode-sku-' + p.id + '" value="'
    + escape(p.is_generated_sku ? '' : (p.barcode || p.sku || '')) + '" placeholder="Scan or type barcode" />'
    + '<button class="primary" onclick="applyBarcodeSku(\\'' + p.id + '\\')">Update barcode/SKU</button></div></div>'
  ) : '';
  return '<div class="audit-row" id="row-' + p.id + '">'
    + '<div class="audit-head"><div>'
    + '<h2><label class="opt"><input type="checkbox" class="product-select" onchange="enforceBulkLimit(this)" value="' + escape(p.id) + '" /> '
    + escape(p.name || '(unnamed product)') + '</label></h2>'
    + '<div class="audit-meta">'
    + 'SKU ' + escape(p.sku || '—') + ' · Barcode ' + escape(p.barcode || '—')
    + ' · Brand ' + escape(p.brand_name || '—') + ' · Category ' + escape(p.category_name || '—')
    + '</div><div class="issue-list">' + issues + '</div></div>'
    + '<div class="num">Retail ' + price + '<br><small>Target ' + target + '</small></div></div>'
    + '<div class="audit-actions">'
    + '<div><h2>Description</h2>'
    + '<textarea id="desc-' + p.id + '">' + escape(p.description || '') + '</textarea>'
    + '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">'
    + '<button class="secondary" onclick="draftDescription(\\'' + p.id + '\\')">Draft with OpenAI</button>'
    + '<button class="primary" onclick="applyDescription(\\'' + p.id + '\\')">Approve description</button>'
    + '</div></div>'
    + '<div><h2>Pricing & photo</h2>'
    + '<div class="price-box">$<input type="number" step="0.01" id="price-' + p.id + '" value="'
    + (p.target_price != null ? p.target_price.toFixed(2) : '') + '" />'
    + '<button class="primary" onclick="applyPrice(\\'' + p.id + '\\')">Approve price</button></div>'
    + '<div style="margin-top:12px"><input type="file" accept="image/jpeg,image/png,image/webp" '
    + 'onchange="uploadImage(\\'' + p.id + '\\', this.files[0])" />'
    + '<div class="audit-meta">Use supplier/manufacturer images or licensed files only.</div></div>'
    + skuControls
    + barcodeSkuControls
    + '<div id="msg-' + p.id + '" style="margin-top:10px"></div>'
    + '</div></div></div>';
}

function selectedProductIds() {
  return Array.from(document.querySelectorAll('.product-select:checked')).slice(0, BULK_LIMIT).map(cb => cb.value);
}

function toggleSelectAll(checked) {
  const boxes = Array.from(document.querySelectorAll('.product-select'));
  boxes.forEach((cb, idx) => { cb.checked = checked && idx < BULK_LIMIT; });
  if (checked && boxes.length > BULK_LIMIT) {
    showBulk('Selected the first ' + BULK_LIMIT + ' visible products. Bulk operations are limited to ' + BULK_LIMIT + ' at a time.', 'success');
  }
}

function enforceBulkLimit(changed) {
  const checked = Array.from(document.querySelectorAll('.product-select:checked'));
  if (checked.length <= BULK_LIMIT) return;
  changed.checked = false;
  showBulk('Bulk operations are limited to ' + BULK_LIMIT + ' products at a time.', 'error');
}

function setBulkBusy(busy) {
  ['bulkDraftBtn', 'bulkDescBtn', 'bulkPriceBtn', 'bulkSkuBtn', 'bulkBarcodeSkuBtn', 'syncBtn'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
}

function showBulk(message, kind) {
  document.getElementById('bulkMsg').innerHTML =
    '<div class="' + (kind || 'success') + '">' + escape(message) + '</div>';
}

function showItemMessage(id, message, kind) {
  const msg = document.getElementById('msg-' + id);
  if (msg) msg.innerHTML = '<div class="' + (kind || 'success') + '">' + escape(message) + '</div>';
}

async function bulkDraftDescriptions() {
  const ids = selectedProductIds();
  if (!ids.length) { showBulk('Select at least one product.', 'error'); return; }
  if (ids.length > BULK_LIMIT) { showBulk('Bulk operations are limited to ' + BULK_LIMIT + ' products at a time.', 'error'); return; }
  setBulkBusy(true);
  showBulk('Drafting ' + ids.length + ' description(s)...', 'success');
  ids.forEach(id => showItemMessage(id, 'Drafting description...', 'success'));
  try {
    const resp = await fetch('/audit/bulk/draft-descriptions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ product_ids: ids }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showBulk(data.detail || resp.statusText, 'error');
      return;
    }
    (data.results || []).forEach(r => {
      if (r.ok) {
        const textarea = document.getElementById('desc-' + r.product_id);
        if (textarea) textarea.value = r.description || '';
        showItemMessage(r.product_id, 'Draft ready. Review it, then approve.', 'success');
      } else {
        showItemMessage(r.product_id, r.error || 'Draft failed', 'error');
      }
    });
    showBulk('Drafted ' + data.succeeded + ' of ' + data.requested + ' selected product(s).', data.failed ? 'error' : 'success');
  } finally {
    setBulkBusy(false);
  }
}

async function bulkApplyDescriptions() {
  const ids = selectedProductIds();
  if (!ids.length) { showBulk('Select at least one product.', 'error'); return; }
  if (ids.length > BULK_LIMIT) { showBulk('Bulk operations are limited to ' + BULK_LIMIT + ' products at a time.', 'error'); return; }
  const updates = ids.map(id => ({
    product_id: id,
    approve_description: true,
    description: (document.getElementById('desc-' + id) || {}).value || '',
  }));
  await bulkApply(updates, 'description');
}

async function bulkApplyPrices() {
  const ids = selectedProductIds();
  if (!ids.length) { showBulk('Select at least one product.', 'error'); return; }
  if (ids.length > BULK_LIMIT) { showBulk('Bulk operations are limited to ' + BULK_LIMIT + ' products at a time.', 'error'); return; }
  const updates = [];
  for (const id of ids) {
    const price = parseFloat((document.getElementById('price-' + id) || {}).value);
    if (isNaN(price)) {
      showItemMessage(id, 'Enter an approved price.', 'error');
      continue;
    }
    updates.push({ product_id: id, approve_price: true, retail_price: price });
  }
  if (!updates.length) { showBulk('No selected products have valid prices.', 'error'); return; }
  await bulkApply(updates, 'price');
}

async function bulkApplySkus() {
  const ids = selectedProductIds();
  if (!ids.length) { showBulk('Select at least one product.', 'error'); return; }
  if (ids.length > BULK_LIMIT) { showBulk('Bulk operations are limited to ' + BULK_LIMIT + ' products at a time.', 'error'); return; }
  const updates = [];
  for (const id of ids) {
    const skuEl = document.getElementById('sku-' + id);
    if (!skuEl) {
      showItemMessage(id, 'Product already has a SKU.', 'error');
      continue;
    }
    const sku = skuEl.value.trim();
    if (!sku) {
      showItemMessage(id, 'Enter a custom SKU.', 'error');
      continue;
    }
    updates.push({ product_id: id, approve_sku: true, custom_sku: sku });
  }
  if (!updates.length) { showBulk('No selected products need custom SKUs.', 'error'); return; }
  await bulkApply(updates, 'SKU');
}

async function bulkApplyBarcodeSkus() {
  const ids = selectedProductIds();
  if (!ids.length) { showBulk('Select at least one product.', 'error'); return; }
  if (ids.length > BULK_LIMIT) { showBulk('Bulk operations are limited to ' + BULK_LIMIT + ' products at a time.', 'error'); return; }
  const updates = [];
  for (const id of ids) {
    const field = document.getElementById('barcode-sku-' + id);
    if (!field) {
      showItemMessage(id, 'No barcode/SKU update needed for this product.', 'error');
      continue;
    }
    const value = field.value.trim();
    if (!value) {
      showItemMessage(id, 'Enter the barcode/SKU.', 'error');
      continue;
    }
    updates.push({ product_id: id, approve_barcode_sku: true, barcode_sku: value });
  }
  if (!updates.length) { showBulk('No selected products have barcode/SKU values ready.', 'error'); return; }
  await bulkApply(updates, 'barcode/SKU');
}

async function bulkApply(updates, label) {
  setBulkBusy(true);
  showBulk('Applying ' + updates.length + ' ' + label + ' update(s)...', 'success');
  updates.forEach(item => showItemMessage(item.product_id, 'Applying update...', 'success'));
  try {
    const resp = await fetch('/audit/bulk/apply', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ updates }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showBulk(data.detail || resp.statusText, 'error');
      return;
    }
    (data.results || []).forEach(r => {
      if (r.ok) {
        showItemMessage(r.product_id, r.retired ? (r.detail || 'Removed from audit queue.') : 'Updated in Lightspeed.', 'success');
      } else {
        showItemMessage(r.product_id, r.error || 'Update failed', 'error');
      }
    });
    showBulk('Applied ' + data.succeeded + ' of ' + data.requested + ' selected update(s).', data.failed ? 'error' : 'success');
    await load();
  } finally {
    setBulkBusy(false);
  }
}

async function draftDescription(id) {
  const msg = document.getElementById('msg-' + id);
  msg.innerHTML = '<span class="spinner"></span>Drafting description...';
  const resp = await fetch('/audit/products/' + id + '/draft-description', { method: 'POST' });
  const data = await resp.json();
  if (resp.ok) {
    document.getElementById('desc-' + id).value = data.description || '';
    msg.innerHTML = '<div class="success">Draft ready. Review it, then approve.</div>';
  } else {
    msg.innerHTML = '<div class="error">' + escape(data.detail || resp.statusText) + '</div>';
  }
}

async function applyDescription(id) {
  const description = document.getElementById('desc-' + id).value;
  await applyUpdate(id, { approve_description: true, description });
}

async function applyPrice(id) {
  const price = parseFloat(document.getElementById('price-' + id).value);
  if (isNaN(price)) { alert('Enter an approved price.'); return; }
  await applyUpdate(id, { approve_price: true, retail_price: price });
}

async function applySku(id) {
  const sku = (document.getElementById('sku-' + id) || {}).value || '';
  if (!sku.trim()) { alert('Enter a custom SKU.'); return; }
  await applyUpdate(id, { approve_sku: true, custom_sku: sku.trim() });
}

async function applyBarcodeSku(id) {
  const value = (document.getElementById('barcode-sku-' + id) || {}).value || '';
  if (!value.trim()) { alert('Enter the barcode/SKU.'); return; }
  await applyUpdate(id, { approve_barcode_sku: true, barcode_sku: value.trim() });
}

async function applyUpdate(id, body) {
  const msg = document.getElementById('msg-' + id);
  msg.innerHTML = '<span class="spinner"></span>Applying update...';
  const resp = await fetch('/audit/products/' + id + '/apply', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (resp.ok) {
    if (data.retired) {
      msg.innerHTML = '<div class="success">' + escape(data.detail || 'Product removed from audit queue.') + '</div>';
    } else {
      msg.innerHTML = '<div class="success">Updated in Lightspeed.</div>';
    }
    await load();
  } else if (resp.status === 409) {
    msg.innerHTML = '<div class="success">' + escape(data.detail || 'Product removed from audit cache.') + '</div>';
    await load();
  } else {
    msg.innerHTML = '<div class="error">' + escape(data.detail || resp.statusText) + '</div>';
  }
}

async function uploadImage(id, file) {
  if (!file) return;
  const msg = document.getElementById('msg-' + id);
  msg.innerHTML = '<span class="spinner"></span>Uploading image...';
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch('/audit/products/' + id + '/image', { method: 'POST', body: form });
  const data = await resp.json();
  if (resp.ok) {
    msg.innerHTML = '<div class="success">Image uploaded.</div>';
    await load();
  } else {
    msg.innerHTML = '<div class="error">' + escape(data.detail || resp.statusText) + '</div>';
  }
}

function escape(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
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
.cand-btn.unmatch-skip { background: var(--bad-bg); border-color: #fecaca; color: var(--bad); }
.cand-btn.unmatch-skip:hover { border-color: var(--bad); }
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
let ORDER_COSTS = [];

(async () => {
  const resp = await fetch('/invoices/' + INVOICE_ID);
  const result = await resp.json();
  if (!resp.ok) {
    document.getElementById('content').innerHTML =
      '<div class="error">' + escape(result.detail || 'Failed to load') + '</div>';
    return;
  }
  DATA = result;
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
  html += '<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">';
  html += '<a href="/invoices/' + INVOICE_ID + '/csv" class="cand-btn">'
       + 'Download CSV backup</a>';
  if (!isImported) {
    html += '<button class="cand-btn" onclick="reprocess()">'
         + 'Re-process (re-run extraction + matching)</button>';
  }
  html += '<button class="cand-btn unmatch-skip" onclick="deleteInvoice()">'
       + 'Delete this invoice</button>';
  html += '</div>';
  html += '</div>';

  // Full extraction table — every line, before any matching decisions.
  // This is what came out of Claude; verify it against the PDF.
  const allLines = [
    ...d.matched.map(l => ({...l, _bucket: 'match'})),
    ...d.uncertain.map(l => ({...l, _bucket: 'uncertain'})),
  ];
  html += '<div class="card"><h2>Extracted lines</h2>'
       + '<p class="subtitle" style="margin-top:-4px">'
       + 'Verify these against the PDF before importing. '
       + (allLines.length + ' line' + (allLines.length !== 1 ? 's' : '')) + ' total.'
       + '</p>'
       + '<table><thead><tr>'
       + '<th>#</th><th>Code</th><th>Description</th><th>Barcode</th>'
       + '<th class="num">Qty</th><th class="num">Unit</th><th class="num">Line</th>'
       + '<th>Status</th></tr></thead><tbody>';
  allLines.forEach((l, i) => {
    const lineTotal = l.quantity * l.unit_cost;
    html += '<tr>'
         + '<td style="color:var(--muted)">' + (i + 1) + '</td>'
         + '<td>' + escape(l.supplier_code || '—') + '</td>'
         + '<td>' + escape(l.description || '—') + '</td>'
         + '<td>' + escape(l.barcode || '—') + '</td>'
         + '<td class="num">' + l.quantity + '</td>'
         + '<td class="num">' + l.unit_cost.toFixed(2) + '</td>'
         + '<td class="num">' + lineTotal.toFixed(2) + '</td>'
         + '<td><span class="badge ' + l._bucket + '">' + l._bucket + '</span></td>'
         + '</tr>';
  });
  // Computed subtotal vs claimed subtotal sanity-check row
  const computed = allLines.reduce((s, l) => s + l.quantity * l.unit_cost, 0);
  html += '<tr style="font-weight:600">'
       + '<td colspan="6" class="num">Computed line-item sum</td>'
       + '<td class="num">' + computed.toFixed(2) + '</td>'
       + '<td>' + (inv.subtotal != null
            ? (Math.abs(computed - inv.subtotal) < 0.5
                ? '<span style="color:var(--good)">✓ matches</span>'
                : '<span style="color:var(--bad)">≠ ' + inv.subtotal.toFixed(2) + '</span>')
            : '') + '</td>'
       + '</tr>';
  html += '</tbody></table>';

  // Collapsible raw JSON for debugging the extraction
  html += '<details style="margin-top:12px"><summary style="cursor:pointer;'
       + 'color:var(--muted);font-size:13px">Show raw extraction JSON</summary>'
       + '<pre style="background:#f8f8f7;padding:12px;border-radius:6px;'
       + 'font-size:12px;overflow-x:auto;margin-top:8px">'
       + escape(JSON.stringify(d, null, 2)) + '</pre></details>';
  html += '</div>';

  // Matched bucket
  if (d.matched.length) {
    html += '<div class="bucket-title"><span class="badge match">Match</span>'
         + '<h2>Existing products to update</h2>'
         + '<small>(' + d.matched.length + ' — costs update on import; retail changes require approval)</small>'
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
         + (isImported ? '' : '<button class="cand-btn" onclick="queueAllUnresolved()">Queue all new</button>')
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
         + 'Update costs on matched products</label>'
         + '<div style="flex-basis:100%;display:flex;gap:8px;align-items:end;flex-wrap:wrap">'
         + '<label style="font-size:12px;color:var(--muted)">Freight / extra cost'
         + '<input id="extraLabel" type="text" value="Freight" style="display:block;margin-top:4px" /></label>'
         + '<label style="font-size:12px;color:var(--muted)">Amount'
         + '<input id="extraAmount" type="number" step="0.01" placeholder="0.00" style="display:block;margin-top:4px;width:110px" /></label>'
         + '<button class="cand-btn" type="button" onclick="addOrderCost()">Add cost</button>'
         + '<span id="orderCostsView"></span></div>
         + '<span class="grow"></span>'
         + '<button class="primary" id="finalBtn" onclick="finalize()">'
         + 'Push to Lightspeed</button></div>'
         + '<div id="finalResult" style="margin-top:16px"></div>';
  }

  document.getElementById('content').innerHTML = html;
  updateFinalButton();
  renderOrderCosts();
}

function renderMatchedRow(m, i, locked) {
  const pricing = m.suggested_retail_price;
  const source = m.pricing_source || 'none';
  const approved = MATCHED_PRICE_OVERRIDES[i] != null;
  const current = m.current_retail_price != null ? '$' + m.current_retail_price.toFixed(2) : '—';
  const recommended = pricing != null ? '$' + pricing.toFixed(2) : '—';
  return '<div class="line-row">' +
    '<div class="from"><strong>' + escape(m.product_name) + '</strong>' +
      '<small>' + escape(m.product_sku || '') + ' · matched by ' + m.matched_by + '</small>' +
      '<small>Invoice: ' + escape(m.description || m.supplier_code || '—') + '</small></div>' +
    '<div class="qty-cost">' + m.quantity + ' × $' + m.unit_cost.toFixed(2) +
      '<br><small>= $' + (m.quantity * m.unit_cost).toFixed(2) + '</small></div>' +
    '<div class="price-cell">' +
      '<span class="pricing-pill ' + sourceClass(source) + '">' + sourceLabel(source) + '</span>' +
      '<small>current ' + current + '<br>recommended ' + recommended + '</small>' +
      (locked ? ''
              : ('<label class="opt" title="Retail price will only update if approved">'
                + '<input type="checkbox" ' + (approved ? 'checked' : '')
                + ' onchange="toggleMatchedPriceApproval(' + i + ', this.checked)" />'
                + 'Approve</label>'
                + '$<input type="number" step="0.01" value="' + (pricing != null ? pricing.toFixed(2) : '')
                + '" onchange="setMatchedApprovedPrice(' + i + ', this.value)" '
                + (approved ? '' : 'disabled') + ' />')) +
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
  } else if (dec && dec.decision === 'queue_enrich') {
    controls = '<small>✓ Will create new product after enrichment'
            + (dec.kind_hint ? ' (' + dec.kind_hint.replace('_',' ') + ')' : '')
            + '</small>'
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
    buttons += '<button class="cand-btn" onclick="queueNew(' + i + ',\\'dry_good\\')">New dry good</button>';
    buttons += '<button class="cand-btn" onclick="queueNew(' + i + ',\\'live_fish\\')">New fish</button>';
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
    retail_price_override: null,
    _name: name,
  };
  render();
}

function toggleMatchedPriceApproval(i, checked) {
  const m = DATA.data.matched[i];
  if (!m) return;
  if (checked && m.suggested_retail_price != null) {
    MATCHED_PRICE_OVERRIDES[i] = m.suggested_retail_price;
  } else {
    delete MATCHED_PRICE_OVERRIDES[i];
  }
  render();
}

function setMatchedApprovedPrice(i, value) {
  const n = parseFloat(value);
  if (isNaN(n)) {
    delete MATCHED_PRICE_OVERRIDES[i];
  } else {
    MATCHED_PRICE_OVERRIDES[i] = n;
  }
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

function queueNew(i, kindHint) {
  // Queue this line for enrichment. The actual product creation happens
  // on the enrichment review screen after finalize.
  const u = DATA.data.uncertain[i];
  DECISIONS[i] = {
    decision: 'queue_enrich',
    supplier_code: u.supplier_code, description: u.description,
    barcode: u.barcode, quantity: u.quantity, unit_cost: u.unit_cost,
    retail_price_override: u.suggested_retail_price,
    kind_hint: kindHint,
  };
  render();
}

function addOrderCost() {
  const label = document.getElementById('extraLabel').value.trim() || 'Additional cost';
  const amount = parseFloat(document.getElementById('extraAmount').value);
  if (!amount || amount < 0) return;
  ORDER_COSTS.push({ label, amount });
  document.getElementById('extraAmount').value = '';
  renderOrderCosts();
}
function removeOrderCost(i) { ORDER_COSTS.splice(i, 1); renderOrderCosts(); }
function renderOrderCosts() {
  const el = document.getElementById('orderCostsView');
  if (!el) return;
  if (!ORDER_COSTS.length) { el.innerHTML = ''; return; }
  const total = ORDER_COSTS.reduce((s, c) => s + c.amount, 0);
  el.innerHTML = ORDER_COSTS.map((c, i) =>
    '<span class="badge" style="margin-right:4px">' + escape(c.label) + ' '
    + fmtMoney(c.amount) + ' <button type="button" onclick="removeOrderCost(' + i
    + ')" style="border:0;background:transparent;cursor:pointer">×</button></span>'
  ).join('') + '<small style="color:var(--muted)"> total ' + fmtMoney(total) + '</small>';
}

function queueAllUnresolved() {
  if (!confirm('Queue every undecided line as a new product draft?')) return;
  (DATA.data.uncertain || []).forEach((u, i) => {
    if (DECISIONS[i]) return;
    DECISIONS[i] = {
      decision: 'queue_enrich',
      supplier_code: u.supplier_code, description: u.description,
      barcode: u.barcode, quantity: u.quantity, unit_cost: u.unit_cost,
      retail_price_override: u.suggested_retail_price,
      kind_hint: null,
    };
  });
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
    additional_costs: ORDER_COSTS,
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
      let h = '<div class="success">';
      if (data.consignment_id) {
        h += '✓ Pushed to Lightspeed as consignment <code>' +
             escape(data.consignment_id) + '</code> (' + data.status + ', ' +
             data.items_added + ' items)';
      } else {
        h += '✓ Invoice finalized.';
      }
      if (data.products_created.length) h += '<br>Created ' + data.products_created.length + ' new products.';
      if (data.products_updated.length) h += '<br>Updated ' + data.products_updated.length + ' existing products.';
      if (data.additional_cost_total) h += '<br>Allocated additional costs into landed item costs: '
        + fmtMoney(data.additional_cost_total) + '.';
      if (data.retail_price_report && data.retail_price_report.length) {
        const changed = data.retail_price_report.filter(r => r.changed);
        const skipped = data.retail_price_report.filter(r => !r.changed);
        h += '<br>Retail price changes: <strong>' + changed.length + '</strong> raised, '
          + '<strong>' + skipped.length + '</strong> left unchanged.';
        h += '<details style="margin-top:10px"><summary style="cursor:pointer">'
          + 'Show retail price report</summary>'
          + '<table style="margin-top:8px"><thead><tr>'
          + '<th>Product</th><th>Code</th><th class="num">Existing</th>'
          + '<th class="num">Suggested</th><th>Status</th></tr></thead><tbody>';
        data.retail_price_report.forEach(r => {
          h += '<tr>'
            + '<td>' + escape(r.name || r.description || '—') + '</td>'
            + '<td>' + escape(r.sku || r.supplier_code || '—') + '</td>'
            + '<td class="num">' + (r.existing_retail_price != null ? fmtMoney(r.existing_retail_price) : '—') + '</td>'
            + '<td class="num">' + (r.suggested_retail_price != null ? fmtMoney(r.suggested_retail_price) : '—') + '</td>'
            + '<td>' + (r.changed
                ? '<span style="color:var(--good)">raised</span>'
                : '<span style="color:var(--muted)">unchanged</span>')
              + '<br><small>' + escape(r.reason || '') + '</small></td>'
            + '</tr>';
        });
        h += '</tbody></table></details>';
      }
      if (data.queued_for_enrichment_count > 0) {
        h += '<br><strong>' + data.queued_for_enrichment_count
          + ' new product(s) queued for enrichment.</strong> '
          + 'These will be added to the consignment once you approve each draft. '
          + '<a href="' + data.enrichment_redirect + '" class="primary" '
          + 'style="display:inline-block;margin-top:8px;text-decoration:none;'
          + 'color:white;padding:6px 12px;border-radius:4px">'
          + 'Go to enrichment review →</a>';
      }
      if (data.errors.length) h += '<br><strong>Errors:</strong><ul>' +
        data.errors.map(e => '<li>' + escape(e) + '</li>').join('') + '</ul>';
      h += '</div>';
      out.innerHTML = h;
      if (data.queued_for_enrichment_count > 0) {
        // Auto-redirect after a moment if there's enrichment to do
        setTimeout(() => { location.href = data.enrichment_redirect; }, 3000);
      } else {
        setTimeout(() => location.reload(), 1500);
      }
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

async function reprocess() {
  if (!confirm('Re-process this invoice? The current extraction and '
    + 'matching will be discarded and the original PDF will be re-run '
    + 'through the pipeline. Any decisions you\\'ve made here will be lost.')) {
    return;
  }
  document.getElementById('content').innerHTML =
    '<p style="color:var(--muted)"><span class="spinner"></span>'
    + 'Re-processing... (15-45 seconds)</p>';
  try {
    const resp = await fetch('/invoices/' + INVOICE_ID + '/reprocess',
      { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) {
      document.getElementById('content').innerHTML =
        '<div class="error">' + escape(data.detail || resp.statusText) + '</div>';
      return;
    }
    if (data.duplicate) {
      // Shouldn't happen (allow_duplicate=true), but handle gracefully.
      window.location.href = '/review/' + data.existing_invoice_id;
      return;
    }
    // New invoice id — go to its review page.
    window.location.href = data.redirect;
  } catch (err) {
    document.getElementById('content').innerHTML =
      '<div class="error">Network error: ' + escape(err.message) + '</div>';
  }
}

async function deleteInvoice() {
  const isImported = DATA.status === 'IMPORTED';
  let msg = 'Delete this invoice?';
  if (isImported) {
    msg += '\\n\\nNOTE: This was already pushed to Lightspeed. The '
         + 'consignment in Lightspeed will NOT be deleted — only this '
         + 'local record.';
  }
  if (!confirm(msg)) return;
  try {
    const resp = await fetch('/invoices/' + INVOICE_ID, { method: 'DELETE' });
    const data = await resp.json();
    if (!resp.ok) {
      alert('Delete failed: ' + (data.detail || resp.statusText));
      return;
    }
    if (data.warning) alert(data.warning);
    window.location.href = '/history';
  } catch (err) {
    alert('Network error: ' + err.message);
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
    <p class="subtitle">Pricing recommendations use cost × multiplier, applied in priority order.
       Retail prices are pushed only after approval on the invoice review screen.</p>
    <div id="rules">Loading...</div>
    <div class="rule-row" style="border-top: 2px solid var(--border); margin-top: 8px">
      <input id="rname" placeholder="Rule name" />
      <input id="rkw" placeholder="keywords,comma,separated (blank = match all)" />
      <input id="rmult" type="number" step="0.01" placeholder="2.2" />
      <input id="rpri" type="number" placeholder="100" />
      <select id="rround">
        <option value="cents_49_99">.49 / .99 rounding</option>
        <option value="charm">.49 / .99 rounding</option>
        <option value="cents_99">.99 rounding</option>
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


ADMIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Admin tools</title>
<style>""" + _COMMON_CSS + """
.tools-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 800px) { .tools-grid { grid-template-columns: 1fr; } }
.toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
.toolbar input { max-width:320px; }
.danger { color: var(--bad); border-color:#fecaca; }
.muted { color: var(--muted); }
</style></head><body>
<div class="container">
""" + _NAV.replace('id="nav-admin">Admin<', 'id="nav-admin" class="active">Admin<') + """
  <h1>Admin tools</h1>
  <p class="subtitle">Catalog sync, learned-match cleanup, and failure triage.</p>

  <div class="tools-grid">
    <div class="card">
      <h2>Catalog</h2>
      <div id="catalogStatus" class="muted">Loading...</div>
      <div style="margin-top:12px">
        <button class="primary" onclick="syncCatalog()">Sync catalog now</button>
      </div>
      <div id="syncResult" style="margin-top:12px"></div>
    </div>

    <div class="card">
      <h2>Recent errors</h2>
      <div id="errorsBox" class="muted">Loading...</div>
    </div>
  </div>

  <div class="card">
    <h2>Labels to reprint</h2>
    <div class="toolbar">
      <select id="labelStatus" onchange="loadLabelReprints()">
        <option value="pending">Pending</option>
        <option value="printed">Printed</option>
        <option value="all">All</option>
      </select>
      <a class="secondary" id="labelCsv" href="/admin/label-reprints.csv">Export CSV</a>
      <button class="primary" onclick="markLabelsPrinted()">Mark selected printed</button>
    </div>
    <div id="labelBox" class="muted">Loading...</div>
  </div>

  <div class="card">
    <h2>Generated SKUs</h2>
    <div class="toolbar">
      <input id="generatedSkuQ" type="text" placeholder="Search product, generated SKU, supplier code" />
      <button class="secondary" onclick="loadGeneratedSkus()">Search</button>
      <a class="secondary" href="/admin/generated-skus.csv">Export CSV</a>
    </div>
    <div id="generatedSkuBox" class="muted">Loading...</div>
  </div>

  <div class="card">
    <h2>Supplier catalog PDFs</h2>
    <div class="toolbar">
      <select id="catalogSupplier"></select>
      <input id="catalogFiles" type="file" multiple accept="application/pdf" />
      <button class="primary" onclick="uploadCatalog()">Import PDFs</button>
    </div>
    <div id="catalogUploadResult" class="muted">
      Import Central Pet, Phillips Pet, or Reef H2O catalog PDFs to improve UPC lookup and product descriptions.
    </div>
  </div>

  <div class="card">
    <h2>Supplier item memory</h2>
    <div class="toolbar">
      <input id="memoryQ" type="text" placeholder="Search code, description, supplier, product id" />
      <button class="secondary" onclick="loadMemory()">Search</button>
    </div>
    <div id="memoryBox" class="muted">Loading...</div>
  </div>

  <div class="card">
    <h2>Supplier SKU mappings</h2>
    <div class="toolbar">
      <input id="mappingQ" type="text" placeholder="Search supplier code, product name, product id" />
      <button class="secondary" onclick="loadMappings()">Search</button>
    </div>
    <div id="mappingBox" class="muted">Loading...</div>
  </div>
</div>
<script>
async function api(url, opts) {
  const resp = await fetch(url, opts || {});
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || resp.statusText);
  return data;
}

async function loadStatus() {
  const data = await api('/admin/status');
  document.getElementById('catalogStatus').innerHTML =
    '<div class="meta">'
    + meta('Products cached', data.catalog.product_count)
    + meta('Last synced', data.catalog.last_synced_at || 'never')
    + meta('Supplier memory', data.supplier_item_count)
    + meta('Mappings', data.mapping_count)
    + meta('Failed invoices', data.failed_invoice_count)
    + '</div>';
}

async function syncCatalog() {
  const out = document.getElementById('syncResult');
  out.innerHTML = '<span class="spinner"></span>Syncing...';
  try {
    const data = await api('/catalog/sync', { method: 'POST' });
    out.innerHTML = '<div class="success">Synced ' + data.product_count + ' products.</div>';
    loadStatus();
  } catch (err) {
    out.innerHTML = '<div class="error">' + escape(err.message) + '</div>';
  }
}

async function loadSupplierOptions() {
  const resp = await fetch('/suppliers');
  const data = await resp.json();
  const sel = document.getElementById('catalogSupplier');
  const suppliers = resp.ok ? (data.data || []) : [];
  sel.innerHTML = suppliers.map(s =>
    '<option value="' + escAttr(s.id) + '">' + escape(s.name) + '</option>'
  ).join('');
}

async function uploadCatalog() {
  const out = document.getElementById('catalogUploadResult');
  const supplierId = document.getElementById('catalogSupplier').value;
  const files = document.getElementById('catalogFiles').files;
  if (!supplierId) { out.innerHTML = '<div class="error">Pick a supplier first.</div>'; return; }
  if (!files.length) { out.innerHTML = '<div class="error">Choose at least one PDF.</div>'; return; }
  const form = new FormData();
  form.append('supplier_id', supplierId);
  for (const f of files) form.append('files', f);
  out.innerHTML = '<span class="spinner"></span>Importing ' + files.length + ' PDF(s)...';
  try {
    const resp = await fetch('/admin/catalog/upload', { method: 'POST', body: form });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || resp.statusText);
    let h = '<div class="success">Imported ' + data.imported + ' supplier catalog item(s).</div>';
    if (data.files && data.files.length) {
      h += '<table><thead><tr><th>File</th><th>Items</th></tr></thead><tbody>';
      data.files.forEach(f => {
        h += '<tr><td>' + escape(f.filename) + '</td><td>' + f.items + '</td></tr>';
      });
      h += '</tbody></table>';
    }
    if (data.errors && data.errors.length) {
      h += '<div class="error">' + data.errors.map(escape).join('<br>') + '</div>';
    }
    out.innerHTML = h;
    loadStatus();
    loadMemory();
  } catch (err) {
    out.innerHTML = '<div class="error">' + escape(err.message) + '</div>';
  }
}

async function loadErrors() {
  const data = await api('/admin/errors');
  if (!data.data.length) {
    document.getElementById('errorsBox').innerHTML = '<span class="muted">No stored failures.</span>';
    return;
  }
  let h = '<table><thead><tr><th>When</th><th>Invoice</th><th>Status</th><th>Error</th></tr></thead><tbody>';
  data.data.forEach(r => {
    h += '<tr><td>' + escape(new Date(r.created_at).toLocaleString()) + '</td>'
      + '<td><a href="/review/' + r.id + '">#' + r.id + '</a><br><small>'
      + escape(r.supplier_name || '') + ' ' + escape(r.supplier_invoice_number || '') + '</small></td>'
      + '<td>' + escape(r.status) + '</td>'
      + '<td>' + escape(r.error || '') + '</td></tr>';
  });
  h += '</tbody></table>';
  document.getElementById('errorsBox').innerHTML = h;
}

async function loadLabelReprints() {
  const status = document.getElementById('labelStatus').value;
  document.getElementById('labelCsv').href = '/admin/label-reprints.csv?status=' + encodeURIComponent(status);
  const data = await api('/admin/label-reprints?status=' + encodeURIComponent(status));
  if (!data.data.length) {
    document.getElementById('labelBox').innerHTML = '<span class="muted">No label reprints.</span>';
    return;
  }
  let h = '<table><thead><tr><th></th><th>Product</th><th>SKU / Barcode</th><th>Price change</th><th>When</th></tr></thead><tbody>';
  data.data.forEach(r => {
    h += '<tr><td><input type="checkbox" class="label-select" value="' + r.id + '" /></td>'
      + '<td><strong>' + escape(r.product_name || '') + '</strong><br><small>' + escape(r.lightspeed_product_id || '') + '</small></td>'
      + '<td>' + escape(r.sku || '') + '<br><small>' + escape(r.barcode || '') + '</small></td>'
      + '<td>' + money(r.old_price) + ' → <strong>' + money(r.new_price) + '</strong><br><small>' + escape(r.status || '') + '</small></td>'
      + '<td>' + escape(r.created_at ? new Date(r.created_at).toLocaleString() : '') + '</td></tr>';
  });
  h += '</tbody></table>';
  document.getElementById('labelBox').innerHTML = h;
}

async function markLabelsPrinted() {
  const ids = Array.from(document.querySelectorAll('.label-select:checked')).map(cb => parseInt(cb.value));
  if (!ids.length) { alert('Select at least one label row.'); return; }
  await api('/admin/label-reprints/mark-printed', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ ids }),
  });
  loadLabelReprints();
}

async function loadGeneratedSkus() {
  const q = document.getElementById('generatedSkuQ').value.trim();
  const data = await api('/admin/generated-skus?q=' + encodeURIComponent(q));
  if (!data.data.length) {
    document.getElementById('generatedSkuBox').innerHTML = '<span class="muted">No generated SKUs.</span>';
    return;
  }
  let h = '<table><thead><tr><th>Product</th><th>Generated SKU</th><th>Current barcode</th><th>Real barcode/SKU</th><th></th></tr></thead><tbody>';
  data.data.forEach(r => {
    h += '<tr><td><strong>' + escape(r.name || '') + '</strong><br><small>'
      + escape(r.brand_name || '') + ' ' + escape(r.category_name || '') + '</small><br><small>'
      + escape(r.id || '') + '</small></td>'
      + '<td>' + escape(r.sku || '') + '</td>'
      + '<td>' + escape(r.barcode || '') + '</td>'
      + '<td><input id="real-sku-' + escAttr(r.id) + '" type="text" placeholder="Scan or type barcode" /></td>'
      + '<td><button class="primary" onclick="updateGeneratedSku(\\'' + escAttr(r.id) + '\\')">Update</button></td></tr>';
  });
  h += '</tbody></table>';
  document.getElementById('generatedSkuBox').innerHTML = h;
}

async function updateGeneratedSku(id) {
  const input = document.getElementById('real-sku-' + id);
  const sku = (input ? input.value : '').trim();
  if (!sku) { alert('Enter the real barcode/SKU.'); return; }
  await api('/admin/generated-skus/' + encodeURIComponent(id) + '/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ sku }),
  });
  loadGeneratedSkus();
}

async function loadMemory() {
  const q = document.getElementById('memoryQ').value.trim();
  const data = await api('/admin/supplier-items?q=' + encodeURIComponent(q));
  let h = '<table><thead><tr><th>Supplier item</th><th>Status</th><th>Linked product</th><th>Seen</th><th></th></tr></thead><tbody>';
  data.data.forEach(r => {
    h += '<tr><td><strong>' + escape(r.supplier_code) + '</strong><br><small>'
      + escape(r.supplier_name || '') + '</small><br>' + escape(r.description || '') + '</td>'
      + '<td>' + escape(r.status || '') + '</td>'
      + '<td>' + escape(r.lightspeed_product_id || 'not linked') + '</td>'
      + '<td>' + (r.seen_count || 0) + '<br><small>' + escape(r.last_seen_at || '') + '</small></td>'
      + '<td>' + (r.lightspeed_product_id
        ? '<button class="secondary danger" onclick="unlinkItem(' + r.id + ')">Unlink</button>'
        : '') + '</td></tr>';
  });
  h += '</tbody></table>';
  document.getElementById('memoryBox').innerHTML = data.data.length ? h : '<span class="muted">No rows.</span>';
}

async function unlinkItem(id) {
  if (!confirm('Unlink this supplier item from its product?')) return;
  await api('/admin/supplier-items/' + id + '/unlink', { method: 'POST' });
  loadMemory();
}

async function loadMappings() {
  const q = document.getElementById('mappingQ').value.trim();
  const data = await api('/admin/mappings?q=' + encodeURIComponent(q));
  let h = '<table><thead><tr><th>Supplier code</th><th>Product</th><th>Updated</th><th></th></tr></thead><tbody>';
  data.data.forEach(r => {
    h += '<tr><td>' + escape(r.supplier_code) + '<br><small>' + escape(r.supplier_id) + '</small></td>'
      + '<td>' + escape(r.product_name || '') + '<br><small>' + escape(r.lightspeed_product_id || '') + '</small></td>'
      + '<td>' + escape(r.updated_at || '') + '</td>'
      + '<td><button class="secondary danger" onclick="deleteMapping(' + r.id + ')">Delete</button></td></tr>';
  });
  h += '</tbody></table>';
  document.getElementById('mappingBox').innerHTML = data.data.length ? h : '<span class="muted">No rows.</span>';
}

async function deleteMapping(id) {
  if (!confirm('Delete this saved mapping?')) return;
  await api('/admin/mappings/' + id, { method: 'DELETE' });
  loadMappings();
}

function meta(label, value) {
  return '<div><label>' + escape(label) + '</label><span>' + escape(value) + '</span></div>';
}
function money(value) {
  return value == null ? '—' : '$' + Number(value).toFixed(2);
}
function escape(s) { return s == null ? '' : String(s).replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
})[c]); }
function escAttr(s) { return escape(s); }

loadStatus(); loadSupplierOptions(); loadErrors(); loadLabelReprints(); loadGeneratedSkus(); loadMemory(); loadMappings();
</script>
</body></html>"""


ENRICH_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Add products</title>
<style>""" + _COMMON_CSS + """
textarea { width: 100%; min-height: 180px; font: inherit;
           padding: 12px; border: 1px solid var(--border);
           border-radius: 6px; resize: vertical; }
.hint { font-size: 13px; color: var(--muted); margin: 8px 0; }
.kind-pick { display: flex; gap: 8px; margin: 12px 0; }
.kind-pick label { display: flex; align-items: center; gap: 6px;
                   font-size: 13px; cursor: pointer; }
.recent { margin-top: 32px; }
.recent table { width: 100%; border-collapse: collapse; font-size: 13px; }
.recent td, .recent th { padding: 8px 12px; border-bottom: 1px solid var(--border);
                         text-align: left; }
.recent tr.row:hover { background: var(--accent-soft); cursor: pointer; }
.progress-bar { height: 6px; background: var(--border); border-radius: 3px;
                overflow: hidden; width: 120px; display: inline-block;
                vertical-align: middle; }
.progress-fill { height: 100%; background: var(--good); }
</style></head><body>
<div class="container">
""" + _NAV.replace('id="nav-enrich">Add products<', 'id="nav-enrich" class="active">Add products<') + """
  <h1>Add products to catalog</h1>
  <p class="subtitle">Paste product names. We draft descriptions for dry goods
  and full care profiles for live fish, then you review before creating
  them in Lightspeed.</p>

  <div class="card">
    <h2>Product names</h2>
    <p class="hint">One product per line. For live fish, use the species name
    (common or scientific). You'll be able to fix the type per-product on the
    next screen if we guess wrong.</p>
    <textarea id="names" placeholder="API Quick Start 16oz&#10;Fluval 307 Canister Filter&#10;Electric Blue Acara&#10;Amano Shrimp&#10;Seachem Prime 500ml"></textarea>
    <div class="kind-pick">
      <span style="font-size:13px;color:var(--muted)">Type hint for all:</span>
      <label><input type="radio" name="kind" value="" checked /> Auto-detect each</label>
      <label><input type="radio" name="kind" value="dry_good" /> All dry goods</label>
      <label><input type="radio" name="kind" value="live_fish" /> All live fish</label>
    </div>
    <button class="primary" id="goBtn" onclick="submitBatch()">Draft these products</button>
    <div id="status" style="margin-top:12px"></div>
  </div>

  <div class="recent">
    <h2>Recent batches</h2>
    <div class="card">
      <table id="batches">
        <thead><tr><th>When</th><th>Products</th><th>Progress</th></tr></thead>
        <tbody><tr><td colspan="3" style="color:var(--muted)">Loading...</td></tr></tbody>
      </table>
    </div>
  </div>
</div>
<script>
async function submitBatch() {
  const raw = document.getElementById('names').value;
  const names = raw.split('\\n').map(s => s.trim()).filter(Boolean);
  if (!names.length) { showStatus('Enter at least one product name.', 'error'); return; }
  if (names.length > 100) { showStatus('Max 100 products per batch.', 'error'); return; }
  const kind = document.querySelector('input[name=kind]:checked').value || null;

  showStatus('<span class="spinner"></span>Drafting ' + names.length +
    ' product(s)... this can take a minute or two for large batches.');
  document.getElementById('goBtn').disabled = true;

  const items = names.map(n => ({ name: n, kind_hint: kind }));
  try {
    const resp = await fetch('/enrich/batch', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ items }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showStatus('Error: ' + (data.detail || resp.statusText), 'error');
      document.getElementById('goBtn').disabled = false;
      return;
    }
    window.location.href = data.redirect;
  } catch (err) {
    showStatus('Network error: ' + err.message, 'error');
    document.getElementById('goBtn').disabled = false;
  }
}
function showStatus(html, kind) {
  const el = document.getElementById('status');
  el.innerHTML = html;
  el.className = kind || '';
}
async function loadBatches() {
  const resp = await fetch('/enrich/batches');
  const data = await resp.json();
  const tb = document.querySelector('#batches tbody');
  if (!data.data.length) {
    tb.innerHTML = '<tr><td colspan="3" style="color:var(--muted)">No batches yet.</td></tr>';
    return;
  }
  tb.innerHTML = '';
  for (const b of data.data) {
    const done = b.created + b.skipped;
    const pct = b.total ? Math.round(done / b.total * 100) : 0;
    const tr = document.createElement('tr');
    tr.className = 'row';
    tr.onclick = () => window.location = '/enrich/review/' + b.batch_id;
    tr.innerHTML =
      '<td>' + new Date(b.created_at).toLocaleString() + '</td>' +
      '<td>' + b.total + ' (' + b.created + ' created, ' + b.draft + ' pending)</td>' +
      '<td><span class="progress-bar"><span class="progress-fill" style="width:' +
        pct + '%"></span></span> ' + pct + '%</td>';
    tb.appendChild(tr);
  }
}
loadBatches();
</script>
</body></html>"""


ENRICH_REVIEW_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Review products</title>
<style>""" + _COMMON_CSS + """
.draft-card { background: white; border: 1px solid var(--border);
              border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; }
.draft-card.created { opacity: 0.55; }
.draft-card.skipped { opacity: 0.4; }
.draft-head { display: flex; align-items: center; gap: 10px;
              margin-bottom: 12px; flex-wrap: wrap; }
.draft-head h3 { margin: 0; font-size: 15px; }
.draft-head .grow { flex: 1; }
.kind-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px;
              font-weight: 600; text-transform: uppercase; }
.kind-badge.dry_good { background: #dbeafe; color: #1e40af; }
.kind-badge.live_fish { background: #d1fae5; color: #065f46; }
.kind-badge.live_invert { background: #ccfbf1; color: #0f766e; }
.kind-badge.live_plant { background: #dcfce7; color: #14532d; }
.kind-badge.live_coral { background: #fce7f3; color: #9d174d; }
.kind-badge.unknown { background: #fef3c7; color: #92400e; }
.desc-preview { padding: 12px 14px; background: #fafaf9; border: 1px solid var(--border);
                border-radius: 4px; font-size: 14px; line-height: 1.5; }
.desc-preview h3 { margin: 0 0 8px; font-size: 16px; }
.desc-preview p { margin: 6px 0; }
.fields { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 16px; }
.fields .full { grid-column: 1 / -1; }
.field label { display: block; font-size: 11px; color: var(--muted);
               text-transform: uppercase; letter-spacing: 0.03em;
               margin-bottom: 2px; }
.field input, .field textarea, .field select {
  width: 100%; font: inherit; padding: 6px 8px;
  border: 1px solid var(--border); border-radius: 4px;
}
.field-action { display: flex; gap: 6px; align-items: center; }
.field-action input { min-width: 0; }
.field-action button { white-space: nowrap; padding: 6px 9px; }
.field textarea { min-height: 60px; resize: vertical; }
.field.uncertain input, .field.uncertain textarea {
  border-color: #f59e0b; background: #fffbeb;
}
.uncertain-tag { font-size: 10px; color: var(--warn); font-weight: 600; }
.draft-actions { display: flex; gap: 8px; margin-top: 12px;
                 padding-top: 12px; border-top: 1px solid var(--border); }
.draft-actions .grow { flex: 1; }
.fish-section { grid-column: 1/-1; border-top: 1px dashed var(--border);
                margin-top: 6px; padding-top: 10px; }
.fish-section h4 { margin: 0 0 8px; font-size: 12px; color: var(--muted);
                   text-transform: uppercase; }
.warn-row { background: var(--warn-bg); border: 1px solid #fde68a;
            color: var(--warn); padding: 6px 10px; border-radius: 4px;
            font-size: 12px; margin-bottom: 8px; }
.batch-bar { display: flex; align-items: center; gap: 12px;
             margin-bottom: 20px; padding: 12px 16px; background: white;
             border: 1px solid var(--border); border-radius: 8px; }
.batch-bar .grow { flex: 1; }
.save-flash { font-size: 12px; color: var(--good); margin-left: 8px; }
</style></head><body>
<div class="container">
""" + _NAV + """
  <h1>Review drafted products</h1>
  <p class="subtitle">Edit anything that's wrong. UPC lookup is available
  for Central Pet, Phillips Pet, and Reef H2O drafts. Yellow fields are ones
  OpenAI flagged as uncertain. Create pushes the product to Lightspeed.</p>
  <div id="content"><p style="color:var(--muted)">Loading...</p></div>
</div>
<script>
const BATCH_ID = "{{BATCH_ID}}";
let DRAFTS = [];
let SUPPLIERS = [];
let CATEGORIES = [];
let BRANDS = [];

(async () => {
  try {
    const [batchResp, supResp, catResp, brandResp] = await Promise.all([
      fetch('/enrich/batch/' + BATCH_ID),
      fetch('/suppliers'),
      fetch('/categories'),
      fetch('/brands'),
    ]);
    const batchData = await batchResp.json();
    if (!batchResp.ok) {
      document.getElementById('content').innerHTML =
        '<div class="error">' + escape(batchData.detail || 'Failed to load') + '</div>';
      return;
    }
    DRAFTS = batchData.drafts;
    if (supResp.ok) SUPPLIERS = (await supResp.json()).data || [];
    if (catResp.ok) CATEGORIES = (await catResp.json()).data || [];
    if (brandResp.ok) BRANDS = (await brandResp.json()).data || [];
    render();
  } catch (err) {
    document.getElementById('content').innerHTML =
      '<div class="error">Network error: ' + escape(err.message) + '</div>';
  }
})();

function render() {
  const pending = DRAFTS.filter(d => d.status === 'DRAFT').length;
  const drafting = DRAFTS.filter(d => d.status === 'PENDING_ENRICH').length;
  const created = DRAFTS.filter(d => d.status === 'CREATED').length;
  const skipped = DRAFTS.filter(d => d.status === 'SKIPPED').length;

  let html = '<div class="batch-bar">'
    + '<strong>' + DRAFTS.length + ' products</strong>'
    + '<span style="color:var(--muted)">· ' + created + ' created · '
    + skipped + ' skipped · ' + pending + ' pending'
    + (drafting > 0 ? ' · <span class="spinner"></span>' + drafting + ' drafting' : '')
    + '</span>'
    + '<span class="grow"></span>'
    + (pending > 0
        ? '<button class="primary" onclick="createAll()">Create all ' + pending + ' pending</button>'
        : drafting > 0
        ? '<span style="color:var(--muted)">Waiting for drafts...</span>'
        : '<span style="color:var(--good)">All done</span>')
    + '</div>';

  for (const d of DRAFTS) {
    html += renderDraft(d);
  }
  document.getElementById('content').innerHTML = html;

  // Auto-refresh while anything is still drafting
  if (drafting > 0) {
    clearTimeout(window._refreshTimer);
    window._refreshTimer = setTimeout(refreshDrafts, 4000);
  }
}

async function refreshDrafts() {
  try {
    const resp = await fetch('/enrich/batch/' + BATCH_ID);
    if (resp.ok) {
      const data = await resp.json();
      DRAFTS = data.drafts;
      render();
    }
  } catch (err) { console.warn('refresh failed', err); }
}

function supplierOptions(selected) {
  let opts = '<option value="">— none —</option>';
  for (const s of SUPPLIERS) {
    opts += '<option value="' + s.id + '"' +
      (s.id === selected ? ' selected' : '') + '>' + escape(s.name) + '</option>';
  }
  return opts;
}

function renderDraft(d) {
  const locked = d.status !== 'DRAFT';
  const cls = d.status === 'CREATED' ? 'created'
            : d.status === 'SKIPPED' ? 'skipped'
            : d.status === 'PENDING_ENRICH' ? 'skipped'
            : '';

  const kindLabel =
    d.kind === 'live_fish' ? 'Live fish' :
    d.kind === 'live_invert' ? 'Live invert' :
    d.kind === 'live_plant' ? 'Live plant' :
    d.kind === 'live_coral' ? 'Live coral' :
    d.kind === 'dry_good' ? 'Dry good' : 'Unknown';

  let html = '<div class="draft-card ' + cls + '" id="draft-' + d.id + '">';
  html += '<div class="draft-head">'
    + '<span class="kind-badge ' + d.kind + '">' + kindLabel + '</span>'
    + '<h3>' + escape(d.final_name || d.input_name) + '</h3>'
    + (d.source_invoice_id
        ? '<small style="color:var(--muted)">from invoice '
          + '<a href="/review/' + d.source_invoice_id + '">#' + d.source_invoice_id
          + '</a>' + (d.source_quantity ? ' · qty ' + d.source_quantity : '') + '</small>'
        : '')
    + '<span class="grow"></span>'
    + (d.status === 'PENDING_ENRICH'
        ? '<span style="color:var(--muted);font-size:13px"><span class="spinner"></span>Drafting...</span>'
        : d.status === 'CREATED'
        ? '<span style="color:var(--good);font-size:13px">✓ Created</span>'
        : d.status === 'SKIPPED'
        ? '<span style="color:var(--muted);font-size:13px">Skipped</span>'
        : '<span class="save-flash" id="flash-' + d.id + '"></span>')
    + '</div>';

  for (const w of (d.warnings || [])) {
    html += '<div class="warn-row">' + escape(w) + '</div>';
  }
  if (d.error) {
    html += '<div class="warn-row" style="background:var(--bad-bg);'
         + 'border-color:#fecaca;color:var(--bad)">' + escape(d.error) + '</div>';
  }

  const dis = locked ? ' disabled' : '';
  html += '<div class="fields">';

  // Identity row
  html += field('Product name', 'final_name', d.final_name || d.input_name, d, dis, 'full');

  // Classification + supplier
  html += '<div class="field"><label>Type</label><select onchange="upd(' + d.id +
    ',\\'kind\\',this.value)"' + dis + '>'
    + '<option value="dry_good"' + (d.kind === 'dry_good' ? ' selected':'') + '>Dry good</option>'
    + '<option value="live_fish"' + (d.kind === 'live_fish' ? ' selected':'') + '>Live fish</option>'
    + '<option value="live_invert"' + (d.kind === 'live_invert' ? ' selected':'') + '>Live invert</option>'
    + '<option value="live_plant"' + (d.kind === 'live_plant' ? ' selected':'') + '>Live plant</option>'
    + '<option value="live_coral"' + (d.kind === 'live_coral' ? ' selected':'') + '>Live coral</option>'
    + '<option value="unknown"' + (d.kind === 'unknown' ? ' selected':'') + '>Unknown</option>'
    + '</select></div>';

  html += '<div class="field"><label>Supplier</label><select onchange="upd(' + d.id +
    ',\\'supplier_id\\',this.value)"' + dis + '>' + supplierOptions(d.supplier_id) + '</select></div>';

  // Category dropdown — picks from real Lightspeed list
  html += '<div class="field"><label>Product category</label><select onchange="updCategory(' + d.id +
    ',this.value)"' + dis + '>' + categoryOptions(d.product_category) + '</select></div>';

  // Brand dropdown
  html += '<div class="field"><label>Brand</label><select onchange="updBrand(' + d.id +
    ',this.value)"' + dis + '>' + brandOptions(d.brand_name) + '</select></div>';

  // Codes
  html += field('SKU', 'sku', d.sku, d, dis);
  html += barcodeField(d, dis, locked);
  html += field('Supplier code', 'supplier_code', d.supplier_code, d, dis);

  // Pricing
  html += numField('Supply price', 'supply_price', d.supply_price, d, dis);
  html += numField('Retail price', 'retail_price', d.retail_price, d, dis);

  // Photo flag
  html += '<div class="field"><label>Photo</label><label style="font-weight:normal;'
    + 'text-transform:none;font-size:13px"><input type="checkbox"' +
    (d.has_photo ? ' checked':'') + dis + ' onchange="upd(' + d.id +
    ',\\'has_photo\\',this.checked)" /> I have a photo to add in Lightspeed</label></div>';
  if (d.lightspeed_product_id) {
    html += '<div class="field"><label>Upload photo</label>'
      + '<input type="file" accept="image/jpeg,image/png,image/webp" '
      + 'onchange="uploadImage(' + d.id + ', this.files[0])" />'
      + '<small style="color:var(--muted)">Use supplier/manufacturer images or licensed files only.</small>'
      + '</div>';
  }

  // Tags
  html += field('Tags (comma-separated)', '_tags_str',
    (d.tags || []).join(', '), d, dis, 'full');

  // Description — HTML; show a preview alongside the editor
  html += '<div class="field full"><label>Description (HTML)</label>'
    + '<textarea' + dis + ' onchange="upd(' + d.id +
    ',\\'description\\',this.value)" style="min-height:140px;font-family:monospace;font-size:12px">'
    + escape(d.description || '') + '</textarea></div>';
  if (d.description) {
    html += '<div class="field full"><label>Preview</label>'
      + '<div class="desc-preview">' + d.description + '</div></div>';
  }

  html += '</div>'; // .fields

  if (!locked) {
    html += '<div class="draft-actions">'
      + '<button class="secondary" onclick="reenrich(' + d.id + ')">Re-draft</button>'
      + '<span class="grow"></span>'
      + '<button class="secondary" onclick="skipDraft(' + d.id + ')">Skip</button>'
      + '<button class="primary" onclick="createOne(' + d.id + ')">Create in Lightspeed</button>'
      + '</div>';
  }
  html += '</div>';
  return html;
}

function categoryOptions(selectedName) {
  let opts = '<option value="">— pick a category —</option>';
  for (const c of CATEGORIES) {
    opts += '<option value="' + escAttr(c.full_name) + '"' +
      (c.full_name === selectedName ? ' selected' : '') + '>' +
      escape(c.full_name) + '</option>';
  }
  return opts;
}

function brandOptions(selectedName) {
  let opts = '<option value="">— none —</option>';
  // Include the currently-selected brand even if it's not in the list
  // (OpenAI might suggest a brand that isn't in your Lightspeed Brands)
  let seen = new Set();
  if (selectedName && !BRANDS.some(b => b.name === selectedName)) {
    opts += '<option value="' + escAttr(selectedName) + '" selected>' +
      escape(selectedName) + ' (new)</option>';
    seen.add(selectedName);
  }
  for (const b of BRANDS) {
    if (seen.has(b.name)) continue;
    opts += '<option value="' + escAttr(b.name) + '"' +
      (b.name === selectedName ? ' selected' : '') + '>' +
      escape(b.name) + '</option>';
  }
  return opts;
}

function field(label, key, val, d, dis, full) {
  return '<div class="field ' + (full || '') + '"><label>' + label + '</label>'
    + '<input type="text" value="' + escAttr(val || '') + '"' + dis
    + ' onchange="upd(' + d.id + ',\\'' + key + '\\',this.value)" /></div>';
}
function barcodeField(d, dis, locked) {
  const disabled = locked ? ' disabled' : '';
  return '<div class="field"><label>Barcode / UPC</label><div class="field-action">'
    + '<input type="text" value="' + escAttr(d.barcode || '') + '"' + dis
    + ' onchange="upd(' + d.id + ',\\'barcode\\',this.value)" />'
    + '<button class="secondary" id="upcbtn-' + d.id + '"' + disabled
    + ' onclick="lookupUpc(' + d.id + ')">Lookup</button>'
    + '</div></div>';
}
function numField(label, key, val, d, dis) {
  return '<div class="field"><label>' + label + '</label>'
    + '<input type="number" step="0.01" value="' + (val != null ? val : '') + '"' + dis
    + ' onchange="upd(' + d.id + ',\\'' + key + '\\',parseFloat(this.value))" /></div>';
}

let saveTimers = {};
function upd(id, key, value) {
  const d = DRAFTS.find(x => x.id === id);
  if (!d) return;
  // Special handling for the comma-separated tags input
  if (key === '_tags_str') {
    const tagList = String(value || '').split(',').map(s => s.trim()).filter(Boolean);
    d.tags = tagList;
    scheduleSave(id, { tags: tagList });
    return;
  }
  d[key] = value;
  scheduleSave(id, { [key]: value });
}
function updCategory(id, name) {
  const d = DRAFTS.find(x => x.id === id);
  if (!d) return;
  d.product_category = name;
  // Find the id from the list
  const cat = CATEGORIES.find(c => c.full_name === name);
  d.product_category_id = cat ? cat.id : null;
  scheduleSave(id, { product_category: name, product_category_id: d.product_category_id });
}
function updBrand(id, name) {
  const d = DRAFTS.find(x => x.id === id);
  if (!d) return;
  d.brand_name = name || null;
  const brand = BRANDS.find(b => b.name === name);
  d.brand_id = brand ? brand.id : null;
  scheduleSave(id, { brand_name: d.brand_name, brand_id: d.brand_id });
}
function scheduleSave(id, patch) {
  clearTimeout(saveTimers[id]);
  saveTimers[id] = setTimeout(() => saveDraft(id, patch), 600);
}
async function saveDraft(id, patch) {
  try {
    const resp = await fetch('/enrich/draft/' + id, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(patch),
    });
    if (resp.ok) {
      const flash = document.getElementById('flash-' + id);
      if (flash) { flash.textContent = 'saved'; setTimeout(() => flash.textContent = '', 1500); }
    }
  } catch (err) { console.warn('save failed', err); }
}

async function lookupUpc(id) {
  const btn = document.getElementById('upcbtn-' + id);
  if (btn) { btn.disabled = true; btn.textContent = 'Looking...'; }
  try {
    const resp = await fetch('/enrich/draft/' + id + '/lookup-upc', { method: 'POST' });
    const data = await resp.json();
    const i = DRAFTS.findIndex(x => x.id === id);
    if (i >= 0 && data.draft) DRAFTS[i] = data.draft;
    render();
    const flash = document.getElementById('flash-' + id);
    if (flash) {
      flash.textContent = resp.ok && data.ok ? 'UPC found' : (data.message || 'No UPC found');
      setTimeout(() => flash.textContent = '', 2200);
    }
    if (!resp.ok) alert(data.detail || data.message || 'UPC lookup failed');
  } catch (err) {
    alert('UPC lookup failed: ' + err.message);
  } finally {
    const nextBtn = document.getElementById('upcbtn-' + id);
    if (nextBtn) { nextBtn.disabled = false; nextBtn.textContent = 'Lookup'; }
  }
}

async function reenrich(id) {
  if (!confirm('Re-draft this product? Your edits to the description / '
    + 'care profile will be replaced with a fresh draft.')) return;
  const resp = await fetch('/enrich/draft/' + id + '/reenrich', { method: 'POST' });
  const data = await resp.json();
  if (resp.ok) {
    const i = DRAFTS.findIndex(x => x.id === id);
    DRAFTS[i] = data.draft;
    render();
  } else {
    alert('Re-draft failed: ' + (data.detail || resp.statusText));
  }
}
async function createOne(id) {
  const resp = await fetch('/enrich/draft/' + id + '/create', { method: 'POST' });
  const data = await resp.json();
  if (resp.ok) {
    const i = DRAFTS.findIndex(x => x.id === id);
    DRAFTS[i] = data.draft;
    render();
  } else {
    alert('Create failed: ' + (data.detail || resp.statusText));
  }
}
async function skipDraft(id) {
  const resp = await fetch('/enrich/draft/' + id + '/skip', { method: 'POST' });
  if (resp.ok) {
    const d = DRAFTS.find(x => x.id === id);
    if (d) d.status = 'SKIPPED';
    render();
  }
}
async function uploadImage(id, file) {
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  try {
    const resp = await fetch('/enrich/draft/' + id + '/image', {
      method: 'POST',
      body: form,
    });
    const data = await resp.json();
    if (resp.ok) {
      const i = DRAFTS.findIndex(x => x.id === id);
      DRAFTS[i] = data.draft;
      render();
    } else {
      alert('Image upload failed: ' + (data.detail || resp.statusText));
    }
  } catch (err) {
    alert('Image upload failed: ' + err.message);
  }
}
async function createAll() {
  const pending = DRAFTS.filter(d => d.status === 'DRAFT');
  if (!pending.length) return;
  if (!confirm('Create all ' + pending.length + ' pending products in Lightspeed?')) return;
  for (const d of pending) {
    try {
      const resp = await fetch('/enrich/draft/' + d.id + '/create', { method: 'POST' });
      const data = await resp.json();
      const i = DRAFTS.findIndex(x => x.id === d.id);
      if (resp.ok) {
        DRAFTS[i] = data.draft;
      } else {
        DRAFTS[i].error = data.detail || resp.statusText;
      }
      render();
    } catch (err) {
      const i = DRAFTS.findIndex(x => x.id === d.id);
      DRAFTS[i].error = 'Network error: ' + err.message;
      render();
    }
  }
}

function escape(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
})[c]); }
function escAttr(s) { return String(s == null ? '' : s).replace(/"/g, '&quot;'); }
</script>
</body></html>"""

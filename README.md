# Lightspeed Invoice Importer

Drop a PDF supplier invoice on a web page. The app:

1. **Extracts** structured data via Claude (supplier, invoice #, line items, totals)
2. **Resolves** the supplier name to a Lightspeed supplier ID
3. **Matches** each line item to a Lightspeed product (tiered: saved mapping → SKU → barcode → fuzzy name)
4. **Surfaces unmatched lines** with candidate suggestions for one-click resolution
5. **Imports** the finalized invoice as a `SUPPLIER` consignment in Lightspeed
6. **Remembers** every manual resolution so the same supplier next time is fully automatic

## Setup

### 1. Lightspeed Personal Token

In Lightspeed: **Setup → Personal Tokens → Generate new token**. Save it.

Your **domain prefix** is the subdomain of your Lightspeed URL
(`https://YOURSTORE.retail.lightspeed.app` → `YOURSTORE`).

### 2. Anthropic API key

Get one at <https://console.anthropic.com>. Add some credit; invoice
extraction costs roughly $0.01–0.03 per invoice with the default model.

### 3. Deploy to Render

Push this repo to GitHub, point Render at it. The `render.yaml` blueprint
provisions a free Postgres database and wires everything together. Add
these env vars in the Render dashboard:

```
LIGHTSPEED_DOMAIN_PREFIX=yourstore
LIGHTSPEED_PERSONAL_TOKEN=...
LIGHTSPEED_DEFAULT_OUTLET_ID=...
ANTHROPIC_API_KEY=sk-ant-...
```

After first deploy, hit `GET /outlets` to find your outlet UUID and set
`LIGHTSPEED_DEFAULT_OUTLET_ID`.

### 4. Use it

Open the deployed URL in a browser. Drag a PDF onto the page.

## Workflow

**First invoice from a new supplier:**

1. Drop the PDF
2. Extraction runs (~10–30 seconds)
3. Most lines probably won't match if you've never used the supplier's
   SKU scheme before — they show up under "Unmatched"
4. For each unmatched line, click the right candidate from the
   suggestions (or "Skip" if Lightspeed doesn't have that product yet)
5. Each pick saves a permanent mapping
6. Click "Import to Lightspeed"

**Second invoice from the same supplier:** should be ~100% auto-matched.
Just check totals and click Import.

## When to check "Mark as RECEIVED immediately"

Check it when the invoice represents goods you physically have in hand
right now and want inventory updated immediately. Leave it unchecked if
you want the consignment to sit in `OPEN` so you can review and receive
it manually in the Lightspeed UI. Once a consignment is `RECEIVED`,
received quantities are locked.

## API

The web UI is built on these endpoints, all available for direct use:

- `POST /invoices/process` — multipart PDF upload, returns extracted + matched data
- `POST /invoices/match` — JSON input, runs only the matching pipeline
- `POST /invoices/import` — JSON input, creates the Lightspeed consignment
- `POST /mappings` — teach a permanent supplier-code → SKU mapping
- `GET /mappings?supplier_id=...` — list saved mappings
- `GET /outlets`, `GET /suppliers`, `GET /suppliers/search?q=...`
- `GET /products/lookup?sku=...` or `?supplier_code=...`
- `GET /consignments/{id}` — check status of a created consignment
- `GET /healthz` — verify configuration

Visit `/docs` for the Swagger UI.

## Matching tiers

For each invoice line, in order, first hit wins:

1. **Saved mapping** (1.0) — supplier_id + supplier_code seen before
2. **Exact SKU** (1.0) — supplier_code on invoice matches a Lightspeed SKU
3. **Barcode** (1.0) — barcode on invoice matches product's barcode field
4. **Fuzzy name** (≥0.85) — description fuzzy-matches a product name

Below the fuzzy threshold → unmatched, with top-3 candidates. Adjust
`FUZZY_MATCH_THRESHOLD` in `app/matching.py` to tune sensitivity.

## Local development

```bash
pip install -r requirements.txt
export LIGHTSPEED_DOMAIN_PREFIX=yourstore
export LIGHTSPEED_PERSONAL_TOKEN=...
export ANTHROPIC_API_KEY=...
export DATABASE_URL=postgresql://localhost/invoice_importer
uvicorn app.main:app --reload
```

Open <http://localhost:8000>.

## Tests

```bash
pytest
```

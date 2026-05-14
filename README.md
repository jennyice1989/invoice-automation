# Lightspeed Invoice Importer

End-to-end invoice automation for Lightspeed X-Series. Drop a PDF on a web
page; the app extracts, deduplicates, matches against your inventory, prices
new items, presents a review screen, then creates products, updates costs,
and pushes a consignment to Lightspeed.

## Features

- **Private upload page** — single-password login, drag-and-drop PDF (iPad-friendly)
- **Invoice reader** — Claude extracts supplier, products, qty, cost, UPC/SKU, descriptions across multi-page PDFs
- **Duplicate detection** — refuses to re-import the same supplier invoice number twice
- **Tiered matching** — saved mappings → SKU → barcode → fuzzy name; saves resolutions permanently
- **Pricing engine** — MSRP (uploaded per-supplier price list) → web-scrape (best effort) → rules-based markup
- **Review screen** — split into "existing to update" and "uncertain"; one-click match, search, create-new, or skip
- **Pushes to Lightspeed** — creates new products, updates costs + retail on existing, pushes consignment, optionally marks RECEIVED
- **CSV export** — per-invoice backup CSV of every line decision

## Setup

### 1. Required env vars

```
LIGHTSPEED_DOMAIN_PREFIX=yourstore
LIGHTSPEED_PERSONAL_TOKEN=...        # Setup -> Personal Tokens in Lightspeed
LIGHTSPEED_DEFAULT_OUTLET_ID=...     # GET /outlets after first deploy
ANTHROPIC_API_KEY=sk-ant-...         # console.anthropic.com
APP_PASSWORD=...                     # the single password for the app
DATABASE_URL=...                     # set automatically by Render
```

### 2. Deploy to Render

Push this repo to GitHub. Render's blueprint provisions a Postgres database
and wires `DATABASE_URL`. Add the five non-DB env vars in the dashboard.
First deploy initializes the database and seeds default pricing rules.

### 3. Sign in

Visit your Render URL, enter `APP_PASSWORD`. Cookie is valid for 30 days.

## Workflow

**Drop a PDF.** Extraction + matching + pricing runs (15–45 seconds).

**Review the result.** You'll see:

- **Existing products to update** (matched). Cost + retail price update on import.
  You can override the suggested retail per-line.
- **Uncertain** lines need a decision: pick a candidate, search Lightspeed,
  create a new product, or skip.

**Push to Lightspeed.** Creates new products, updates costs/retails on
matched products, creates the consignment. Optionally mark RECEIVED to
update inventory immediately. (Once RECEIVED, received quantities are locked.)

**Subsequent invoices from the same supplier** auto-match against everything
you've taught it. The unknown rate trends toward zero.

## Pricing engine

Each line's retail price is resolved in order; first non-null wins:

1. **MSRP** — if you've uploaded an MSRP CSV for that supplier and the
   line matches by supplier_code or barcode
2. **Scrape** — best-effort search of Chewy, Petco, PetSmart. Returns
   the first non-sale price found. Often blocked from cloud IPs; see notes.
3. **Rules** — cost × multiplier, rounded. Configure rules per category
   in Settings. Default seed: 2.0× frozen, 1.8× livestock, 2.2× everything else.

### Web scraping limitations

Chewy, Petco, and PetSmart use Cloudflare and often block requests from
cloud-provider IPs. The scraper is implemented as best-effort: when blocked,
it returns null and pricing falls back to the rules engine. For reliable
retail-price data, swap the scraper functions in `app/pricing.py` to use:

- A residential-proxy service (Bright Data, Oxylabs)
- A retail-price API (SerpAPI's Google Shopping endpoint)
- A companion process running on a residential IP at your store

All three are drop-in changes to the `_scrape_*` functions.

### MSRP upload format

CSV with columns (any subset, one of `supplier_code` or `barcode` required):

```csv
supplier_code,barcode,msrp,notes
RH2-CAL-2KG,,29.99,Standard
,012345678905,9.99,
```

## API endpoints

All require authentication via the session cookie except `/healthz`.

- `GET /` — upload page
- `GET /history` — recent invoices
- `GET /review/{id}` — review/finalize screen
- `GET /settings` — pricing rules + MSRP upload
- `POST /invoices/process` (multipart PDF) — upload + extract + match + price
- `GET /invoices` — recent invoice list (JSON)
- `GET /invoices/{id}` — full invoice with extraction + match data
- `POST /invoices/finalize` — apply decisions, push to Lightspeed
- `GET /invoices/{id}/csv` — download backup CSV
- `GET /pricing/rules`, `POST /pricing/rules`, `DELETE /pricing/rules/{id}`
- `POST /pricing/msrp` (multipart CSV) — upload MSRP list per supplier
- `GET /products/search?q=...` — search Lightspeed catalog for manual picks
- `GET /outlets`, `GET /suppliers`
- `GET /consignments/{id}`

## Local development

```bash
pip install -r requirements.txt
export LIGHTSPEED_DOMAIN_PREFIX=yourstore
export LIGHTSPEED_PERSONAL_TOKEN=...
export ANTHROPIC_API_KEY=...
export APP_PASSWORD=letmein
export DATABASE_URL=postgresql://localhost/invoice_importer
uvicorn app.main:app --reload
```

Open <http://localhost:8000>.

## Architecture

```
app/
  auth.py        Single-password auth via signed cookies
  db.py          SQLAlchemy schema + queries (Postgres)
  lightspeed.py  X-Series API client (consignments, products)
  extraction.py  PDF -> structured invoice via Claude
  matching.py    Tiered product matching pipeline
  pricing.py     MSRP -> scrape -> rules pricing engine
  ui.py          HTML templates (login, upload, history, review, settings)
  main.py        FastAPI routes wiring it all together
```

## Tests

```bash
pytest
```

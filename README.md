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
- **OpenAI product descriptions** — drafts catalog-ready product names, HTML descriptions, categories, brands, and tags for review
- **Pricing review** — target margin, MSRP, and first-party retailer comparison notes; retail changes require approval before upload
- **Product image upload** — after a drafted product is created, upload an approved JPG/PNG/WebP directly to Lightspeed
- **Catalog audit** — reviews existing Lightspeed products for missing photos, weak descriptions, missing codes, and pricing below target
- **Review screen** — split into "existing to update" and "uncertain"; one-click match, search, create-new, or skip
- **Pushes to Lightspeed** — creates new products, updates costs, uploads approved retail changes, pushes consignment, optionally marks RECEIVED
- **CSV export** — per-invoice backup CSV of every line decision

## Setup

### 1. Required env vars

```
LIGHTSPEED_DOMAIN_PREFIX=yourstore
LIGHTSPEED_PERSONAL_TOKEN=...        # Setup -> Personal Tokens in Lightspeed
LIGHTSPEED_DEFAULT_OUTLET_ID=...     # GET /outlets after first deploy
ANTHROPIC_API_KEY=sk-ant-...         # console.anthropic.com
OPENAI_API_KEY=sk-...                # platform.openai.com, used for product descriptions
OPENAI_MODEL=gpt-5.5                 # optional; defaults to gpt-5.5
APP_PASSWORD=...                     # the single password for the app
DATABASE_URL=...                     # set automatically by Render
```

### 2. Deploy to Render

Push this repo to GitHub. Render's blueprint provisions a Postgres database
and wires `DATABASE_URL`. Add the non-DB env vars in the dashboard.
First deploy initializes the database and seeds default pricing rules.

### 3. Sign in

Visit your Render URL, enter `APP_PASSWORD`. Cookie is valid for 30 days.

## Workflow

**Drop a PDF.** Extraction + matching + pricing runs (15–45 seconds).

**Review the result.** You'll see:

- **Existing products to update** (matched). Cost updates on import.
  Suggested retail is shown per-line and uploads only when approved.
- **Uncertain** lines need a decision: pick a candidate, search Lightspeed,
  create a new product, or skip.

**Push to Lightspeed.** Creates new products, flags imported products for
inventory tracking, updates costs on matched products, and creates the
consignment. Retail price recommendations are uploaded only for lines you
approve on the review screen. Receiving is separate: optionally mark RECEIVED
only when you want Lightspeed to add stock immediately. (Once RECEIVED,
received quantities are locked; queued enrichment products are received later
on follow-up consignments.)

**Add product photos.** On the enrichment review screen, create the product
first, then upload an approved supplier/manufacturer image or another licensed
JPG, PNG, or WebP file. Do not use random web images unless usage rights are
confirmed.

**Audit existing products.** Use Catalog audit to sync existing Lightspeed
products, flag missing photos, weak descriptions, missing codes, and retail
prices below the `1.5x cost` target. You can draft copy with OpenAI, approve a
price change, or upload an approved image. Nothing is changed in Lightspeed
until you approve the specific action.

**Subsequent invoices from the same supplier** auto-match against everything
you've taught it. The unknown rate trends toward zero.

## Pricing engine

Each line's retail price is recommended from:

1. **Target margin** — default recommendation is cost × 1.5, rounded up
   to the next `.49` or `.99`
2. **MSRP** — if you've uploaded an MSRP CSV for that supplier and the
   line matches by supplier_code or barcode
3. **Retailer comparison** — SerpApi Google Shopping when
   `PRICING_PROVIDER=serpapi` and `SERPAPI_API_KEY` are configured. The app
   searches barcode/UPC first, then product description, filters sale,
   second-hand, and marketplace results, and uses the median accepted retailer
   price as a market-aligned candidate. Direct Chewy/Petco/PetSmart checks
   remain as a fallback when `ENABLE_SCRAPING=1`.

The app recommends the highest safe candidate from target margin, MSRP,
retailer comparison, and current retail. It will not recommend lowering an
existing retail price. If the computed recommendation is below current retail,
the recommendation is held at the current price. Matched-product retail changes
are pushed to Lightspeed only after per-line approval in the review screen.

### Pricing provider setup

For reliable market-aware pricing, set these environment variables:

```env
PRICING_PROVIDER=serpapi
SERPAPI_API_KEY=your_serpapi_key
```

SerpApi returns structured Google Shopping results, which are more reliable
than direct retailer scraping from a cloud host. If SerpApi is not configured,
the app can still try the direct Chewy/Petco/PetSmart fallback when
`ENABLE_SCRAPING=1`, but those retailers often block cloud-provider IPs.

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
- `GET /audit` — existing catalog audit queue
- `GET /settings` — pricing rules + MSRP upload
- `POST /invoices/process` (multipart PDF) — upload + extract + match + price
- `GET /invoices` — recent invoice list (JSON)
- `GET /invoices/{id}` — full invoice with extraction + match data
- `POST /invoices/finalize` — apply decisions, push to Lightspeed
- `GET /invoices/{id}/csv` — download backup CSV
- `GET /pricing/rules`, `POST /pricing/rules`, `DELETE /pricing/rules/{id}`
- `POST /pricing/msrp` (multipart CSV) — upload MSRP list per supplier
- `GET /products/search?q=...` — search Lightspeed catalog for manual picks
- `GET /audit/products` — audit existing products from the local catalog cache
- `POST /audit/sync` — refresh catalog cache before audit review
- `POST /audit/products/{id}/draft-description` — draft catalog copy with OpenAI
- `POST /audit/products/{id}/apply` — upload approved audit changes
- `POST /audit/products/{id}/image` — upload an approved product image
- `GET /outlets`, `GET /suppliers`
- `GET /consignments/{id}`

## Local development

```bash
pip install -r requirements.txt
export LIGHTSPEED_DOMAIN_PREFIX=yourstore
export LIGHTSPEED_PERSONAL_TOKEN=...
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-5.5
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
  pricing.py     target margin + MSRP + retailer comparison recommendations
  ui.py          HTML templates (login, upload, history, review, settings)
  main.py        FastAPI routes wiring it all together
```

## Tests

```bash
pytest
```

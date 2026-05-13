# Lightspeed Invoice Importer

FastAPI service that takes structured invoice data and pushes it into
Lightspeed Retail (X-Series) as `SUPPLIER` consignments — with a smart
matching layer that learns supplier-code-to-SKU mappings over time.

## What it does

Three stages:

1. **Match** — `POST /invoices/match` takes raw invoice lines (supplier
   code, description, barcode, qty, cost) and resolves them to Lightspeed
   products via a tiered strategy: saved mapping → exact SKU → barcode →
   fuzzy name match. Anything that doesn't resolve confidently lands in
   an "unmatched" list with candidate suggestions.

2. **Teach** — `POST /mappings` saves a resolution permanently. Once you
   tell it that ReefH2O's `RH2-CAL-2KG` is your `CAL2KG`, that mapping
   fires automatically forever. The unknown rate trends toward zero.

3. **Import** — `POST /invoices/import` takes fully-matched line items
   and creates a `SUPPLIER` consignment in Lightspeed. Optionally walks
   it through `DISPATCHED → RECEIVED`, which is what actually adjusts
   inventory.

## Setup

### 1. Lightspeed Personal Token

In Lightspeed: **Setup → Personal Tokens → Generate new token**. Save it.

Your **domain prefix** is the subdomain of your Lightspeed URL
(`https://YOURSTORE.retail.lightspeed.app` → `YOURSTORE`).

### 2. Deploy to Render

Push to GitHub, point Render at the repo, accept the blueprint. Render
creates the Postgres database and wires `DATABASE_URL` automatically.

Add these env vars in the Render dashboard:

```
LIGHTSPEED_DOMAIN_PREFIX=yourstore
LIGHTSPEED_PERSONAL_TOKEN=...
LIGHTSPEED_DEFAULT_OUTLET_ID=...   # find via GET /outlets after first deploy
```

### 3. Verify

```
GET /healthz              -> {"ok": true, "lightspeed_configured": true, "db_configured": true}
GET /outlets              -> list of your outlets
GET /suppliers            -> list of your suppliers
```

## Endpoints

### Matching

`POST /invoices/match`

```json
{
  "supplier_id": "54e3df0b-b969-4626-8ad7-1cbc4f347d3c",
  "lines": [
    {
      "supplier_code": "RH2-CAL-2KG",
      "description": "Reef Calcium Supplement 2kg",
      "barcode": "0123456789012",
      "quantity": 6,
      "unit_cost": 12.50
    }
  ]
}
```

Response:

```json
{
  "matched": [{
    "supplier_code": "RH2-CAL-2KG",
    "product_id": "...",
    "product_sku": "CAL2KG",
    "product_name": "Reef Calcium 2kg",
    "matched_by": "sku",
    "confidence": 1.0,
    "quantity": 6,
    "unit_cost": 12.50
  }],
  "unmatched": [],
  "summary": {"total_lines": 1, "matched_count": 1, "unmatched_count": 0,
              "by_method": {"sku": 1}}
}
```

For unmatched lines, you get up to 3 fuzzy-match candidates so a UI can
suggest options. Pick one, teach the system via `POST /mappings`, and the
next invoice from that supplier will hit instantly.

### Teaching mappings

`POST /mappings`

```json
{
  "supplier_id": "54e3df0b-b969-4626-8ad7-1cbc4f347d3c",
  "supplier_code": "RH2-CAL-2KG",
  "lightspeed_product_id": "0683d884-602d-...",
  "lightspeed_sku": "CAL2KG",
  "product_name": "Reef Calcium 2kg"
}
```

Idempotent: re-posting the same `supplier_id + supplier_code` updates the
existing mapping.

`GET /mappings?supplier_id=...` lists what you've taught it.

### Importing

`POST /invoices/import`

```json
{
  "supplier_invoice_number": "INV-2026-0481",
  "supplier_id": "54e3df0b-...",
  "receive_immediately": true,
  "items": [
    {"product_id": "...", "count": 6, "cost": 12.50}
  ]
}
```

`receive_immediately: false` leaves the consignment in `OPEN` for review
in the Lightspeed UI before receiving. **Once `RECEIVED`, quantities are
locked**, so default to `false` until you trust your extraction.

### Discovery / debugging

- `GET /outlets` — list outlets (run once during setup)
- `GET /suppliers` — list all suppliers
- `GET /suppliers/search?q=reef` — substring search, space-insensitive
- `GET /suppliers/lookup?name=ReefH2O` — exact-match lookup
- `GET /products/lookup?sku=CAL2KG` — exact SKU lookup
- `GET /consignments/{id}` — check status of a consignment you created

## Matching strategy

Lines flow through tiers in order; first hit wins:

1. **Saved mapping** (confidence 1.0) — supplier_id + supplier_code seen before
2. **Exact SKU** (confidence 1.0) — supplier_code matches a Lightspeed SKU
3. **Barcode** (confidence 1.0) — barcode matches a product's barcode field
4. **Fuzzy name** (confidence 0.85+) — description matches product name above threshold

Anything below the fuzzy threshold returns `unmatched` with top-3 candidates.

The fuzzy threshold (`FUZZY_MATCH_THRESHOLD` in `app/matching.py`) defaults
to 0.85. Lower it for noisier invoices; raise it for stricter matching.

## Workflow for a new supplier

First invoice from a new supplier will mostly land in `unmatched`. For each:

1. Look at the candidates returned with the unmatched line
2. If one is right, `POST /mappings` to save it
3. Re-run `POST /invoices/match` — it'll now resolve via the saved mapping
4. Repeat until everything matches, then `POST /invoices/import`

Second invoice from the same supplier should be ~100% auto-matched if
their SKU scheme is consistent.

## Local development

```bash
pip install -r requirements.txt
export LIGHTSPEED_DOMAIN_PREFIX=yourstore
export LIGHTSPEED_PERSONAL_TOKEN=...
export DATABASE_URL=postgresql://localhost/invoice_importer
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for the interactive API explorer.

## Tests

```bash
pytest
```

Tests mock the Lightspeed HTTP layer; they run offline.

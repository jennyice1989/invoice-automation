# Lightspeed Invoice Importer

FastAPI service that pushes structured invoices into Lightspeed Retail
(X-Series) as `SUPPLIER` consignments. This is the back half of an invoice
processing pipeline — bring your own extraction layer.

## What it does

Given an invoice that's already been parsed into line items + matched to
Lightspeed product IDs, this service:

1. Creates a `SUPPLIER` consignment for the supplier
2. Adds each line item with quantity and unit cost
3. Optionally walks the consignment through `DISPATCHED` → `RECEIVED`,
   which is what actually adjusts inventory in Lightspeed

It also exposes lookup endpoints (`/suppliers/lookup`, `/products/lookup`)
so your extraction layer can resolve supplier names and supplier SKUs to
Lightspeed IDs before posting an invoice.

## Setup

### 1. Get a Personal Token

In Lightspeed: **Setup → Personal Tokens → Generate new token**. Save it —
you can't see it again.

You also need your **domain prefix** — it's the subdomain part of your
Lightspeed URL (`https://YOURSTORE.retail.lightspeed.app` → `YOURSTORE`).

### 2. Find your default outlet ID

Once deployed, hit `GET /outlets` to list outlets and grab the UUID of the
one invoices default to.

### 3. Environment variables

```
LIGHTSPEED_DOMAIN_PREFIX=yourstore
LIGHTSPEED_PERSONAL_TOKEN=...
LIGHTSPEED_DEFAULT_OUTLET_ID=...   # optional but convenient
```

### 4. Deploy to Render

Push this repo to GitHub and connect it as a Render web service — the
`render.yaml` blueprint handles the rest. Add the three env vars in the
Render dashboard (they're marked `sync: false` so they don't get committed).

## Local development

```bash
pip install -r requirements.txt
export LIGHTSPEED_DOMAIN_PREFIX=yourstore
export LIGHTSPEED_PERSONAL_TOKEN=...
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for the interactive API explorer.

## API

### `POST /invoices/import`

The main endpoint. Pass a fully-matched invoice; get back a consignment.

```json
{
  "supplier_invoice_number": "INV-2026-0481",
  "supplier_id": "0242ac11-0002-11eb-...",
  "outlet_id": "0242ac12-0002-11e9-...",
  "receive_immediately": true,
  "items": [
    {"product_id": "0242ac12-...", "count": 6, "cost": 12.50},
    {"product_id": "0242ac13-...", "count": 2, "cost": 45.00}
  ]
}
```

Response:

```json
{
  "consignment_id": "0242ac17-...",
  "status": "RECEIVED",
  "items_added": 2,
  "items_failed": 0,
  "errors": []
}
```

Set `receive_immediately: false` if you want to leave the consignment in
`OPEN` for manual review in the Lightspeed UI before receiving.

### `GET /products/lookup?supplier_code=ABC-123`

Resolve a supplier's SKU (what's on the invoice) to a Lightspeed product.
Prefers `supplier_code`; falls back to `sku` if you pass that instead.

### `GET /suppliers/lookup?name=Acme%20Distribution`

Find a supplier by exact name (case-insensitive).

### `GET /outlets`

List outlets (run once during setup to find your outlet_id).

## Design notes

- **Why supplier_code is the primary match key**: invoices show the
  supplier's SKU, not yours. Lightspeed products have a `supplier_code`
  field exactly for this. Make sure your products have it populated.

- **Why we go straight OPEN → DISPATCHED → RECEIVED**: the `SENT` status
  only exists to mirror "we sent the PO to the supplier" — irrelevant
  when you're importing an invoice that represents goods already received.
  Skipping `SENT` is officially supported.

- **Why `cost` matters**: Lightspeed uses the per-unit cost to update the
  product's supply price and weighted average cost. Getting this right is
  the whole point of importing invoices via API instead of by hand.

- **Once RECEIVED, quantities are locked.** If you might need to amend a
  delivery, leave it at `OPEN` and finalize in the UI.

## What's next

The extraction layer. Once you're ready, the flow will be:

1. PDF / email / scan lands in a watched location
2. Vision LLM extracts supplier name, invoice number, line items
3. For each line, call `/products/lookup` to resolve supplier_code → product_id
4. Show unmatched lines in a review UI; save resolved mappings
5. Once everything is resolved, `POST /invoices/import`

## Tests

```bash
pytest
```

Tests mock the Lightspeed HTTP layer so they run offline.

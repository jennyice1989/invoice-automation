# A2Z Lightspeed Invoice Backend

Render-ready FastAPI backend for uploading supplier invoice PDFs and generating a reviewable Lightspeed import CSV.

## What v1 does

- Runs on Render as a Python FastAPI web service
- Provides `/health` for Render health checks
- Provides `/docs` interactive API docs
- Accepts PDF invoice uploads at `/invoices/upload`
- Extracts text from PDFs with `pypdf`
- Creates starter Lightspeed CSV output
- Protects admin endpoints with `X-API-Key`
- Includes placeholders for Lightspeed Retail X-Series API, UPC lookup, pricing lookup, Dropbox, and OpenAI descriptions

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate  # Mac/Linux
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

## Render setup

Render settings:

- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

Environment variables:

```text
ADMIN_API_KEY=make-this-long-and-random
CORS_ORIGINS=*
LIGHTSPEED_RETAILER_ID=
LIGHTSPEED_ACCESS_TOKEN=
OPENAI_API_KEY=
DROPBOX_TOKEN=
STORAGE_DIR=storage
```

## Test upload with curl

```bash
curl -X POST "https://YOUR-RENDER-APP.onrender.com/invoices/upload" \
  -H "X-API-Key: YOUR_ADMIN_API_KEY" \
  -F "file=@invoice.pdf"
```

## Next build steps

1. Add supplier-specific parsers:
   - ReefH2O
   - Central Pet
   - Live fish invoices
2. Add existing inventory upload/matching.
3. Add real Lightspeed Retail X-Series product push after review approval.
4. Add a private GoDaddy admin page that calls this backend.
5. Add persistent database storage. Render's normal filesystem is not permanent between deploys, so production should use Postgres or object storage for invoices/exports.

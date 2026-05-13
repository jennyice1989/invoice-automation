from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.auth import require_api_key
from app.config import settings
from app.models import InvoiceProcessResult
from app.services.invoice_parser import extract_pdf_text, simple_line_item_parse
from app.services.csv_export import write_lightspeed_csv

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.post("/upload", response_model=InvoiceProcessResult, dependencies=[Depends(require_api_key)])
async def upload_invoice(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF invoices are supported in this starter backend")

    invoice_id = str(uuid4())
    storage_root = Path(settings.storage_dir)
    upload_path = storage_root / "uploads" / f"{invoice_id}.pdf"
    export_path = storage_root / "exports" / f"{invoice_id}_lightspeed_import.csv"
    upload_path.parent.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    upload_path.write_bytes(content)

    text = extract_pdf_text(upload_path)
    products = simple_line_item_parse(text, file.filename)
    write_lightspeed_csv(products, export_path)

    return InvoiceProcessResult(
        invoice_id=invoice_id,
        filename=file.filename,
        products=products,
        csv_download_url=f"/invoices/{invoice_id}/csv",
        message=f"Processed {len(products)} possible product rows. Review before importing to Lightspeed.",
    )


@router.get("/{invoice_id}/csv", dependencies=[Depends(require_api_key)])
def download_csv(invoice_id: str):
    path = Path(settings.storage_dir) / "exports" / f"{invoice_id}_lightspeed_import.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="CSV not found")
    return FileResponse(path, filename=path.name, media_type="text/csv")

import re
from pathlib import Path
from pypdf import PdfReader
from app.models import ProductCandidate


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return "\n".join(chunks).strip()


def detect_supplier(text: str, filename: str) -> str:
    haystack = f"{filename}\n{text}".lower()
    if "reefh2o" in haystack or "reef h2o" in haystack:
        return "ReefH2O"
    if "central pet" in haystack or "central garden" in haystack:
        return "Central Pet"
    return "Unknown"


def simple_line_item_parse(text: str, filename: str) -> list[ProductCandidate]:
    """Starter parser: extracts likely item lines from invoice text.

    This is intentionally conservative. Vendor-specific parsers can replace this
    for ReefH2O, Central Pet, live fish invoices, etc.
    """
    supplier = detect_supplier(text, filename)
    products: list[ProductCandidate] = []

    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if len(line) < 8:
            continue
        if any(skip in line.lower() for skip in ["subtotal", "total", "invoice", "balance", "shipping", "tax"]):
            continue

        # Try to catch UPC/SKU-like number and a price on the same line.
        upc_match = re.search(r"\b\d{8,14}\b", line)
        price_match = re.search(r"(?:\$)?(\d+\.\d{2})\b", line)
        if not upc_match and not price_match:
            continue

        sku = upc_match.group(0) if upc_match else None
        name = line
        if sku:
            name = line.replace(sku, "").strip(" -|:")
        cost = float(price_match.group(1)) if price_match else None

        products.append(
            ProductCandidate(
                supplier=supplier,
                source_filename=filename,
                name=name[:120],
                sku=sku,
                upc=sku,
                cost=cost,
                recommended_retail=round(cost * 2.0, 2) if cost else None,
                description=None,
            )
        )

    # Keep first 100 to avoid noisy PDFs overwhelming review screen.
    return products[:100]

import csv
from pathlib import Path
from app.models import ProductCandidate

LIGHTSPEED_COLUMNS = [
    "name",
    "sku",
    "upc",
    "description",
    "category",
    "supplier",
    "supply_price",
    "retail_price",
    "quantity",
    "source_filename",
    "status",
]


def write_lightspeed_csv(products: list[ProductCandidate], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LIGHTSPEED_COLUMNS)
        writer.writeheader()
        for p in products:
            writer.writerow(
                {
                    "name": p.name,
                    "sku": p.sku or "",
                    "upc": p.upc or "",
                    "description": p.description or "",
                    "category": p.category or "",
                    "supplier": p.supplier,
                    "supply_price": p.cost or "",
                    "retail_price": p.recommended_retail or "",
                    "quantity": p.quantity or "",
                    "source_filename": p.source_filename,
                    "status": p.status,
                }
            )

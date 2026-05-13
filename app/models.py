from pydantic import BaseModel, Field
from typing import Optional


class ProductCandidate(BaseModel):
    supplier: str = "Unknown"
    source_filename: str
    name: str
    sku: Optional[str] = None
    upc: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[float] = None
    cost: Optional[float] = None
    recommended_retail: Optional[float] = None
    description: Optional[str] = None
    status: str = Field(default="needs_review")


class InvoiceProcessResult(BaseModel):
    invoice_id: str
    filename: str
    products: list[ProductCandidate]
    csv_download_url: str
    message: str

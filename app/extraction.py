"""
Invoice extraction via Claude.

Takes a PDF (or image) and returns structured invoice data:
supplier name, invoice number, date, line items (code, description,
qty, unit cost), totals. We then run sanity checks (do line items
sum to invoice total?) before handing off to the matching layer.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7")
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1/messages"


EXTRACTION_PROMPT = """You are extracting data from a supplier invoice for a retail business.

Return a single JSON object with this exact schema and no other text:

{
  "supplier_name": "the supplier/vendor name as it appears at the top",
  "invoice_number": "the supplier's invoice number",
  "invoice_date": "YYYY-MM-DD or null if unclear",
  "currency": "ISO code if shown, else null",
  "subtotal": number or null,
  "tax": number or null,
  "total": number or null,
  "lines": [
    {
      "supplier_code": "the SKU/part-number/code for this line item, or null",
      "description": "the product description as printed",
      "barcode": "UPC/EAN/barcode if shown, else null",
      "quantity": number,
      "unit_cost": number,
      "line_total": number or null
    }
  ]
}

Rules:
- unit_cost is the per-unit price BEFORE tax, after any line-item discount.
- If quantity and line_total are shown but unit_cost is not, compute it.
- Skip non-product lines (shipping, tax, discounts at invoice level) — they
  belong in `tax` or are implicit in `total`, not in `lines`.
- For numbers, never include currency symbols or thousands separators.
- If a field is genuinely missing from the invoice, use null. Don't guess.
- Return ONLY the JSON object. No prose, no markdown fences."""


class ExtractionError(Exception):
    """Raised when extraction fails or returns unparseable output."""


@dataclass
class ExtractedLine:
    supplier_code: str | None
    description: str | None
    barcode: str | None
    quantity: float
    unit_cost: float
    line_total: float | None


@dataclass
class ExtractedInvoice:
    supplier_name: str | None
    invoice_number: str | None
    invoice_date: str | None
    currency: str | None
    subtotal: float | None
    tax: float | None
    total: float | None
    lines: list[ExtractedLine]
    warnings: list[str]


async def extract_invoice_from_pdf(pdf_bytes: bytes) -> ExtractedInvoice:
    """
    Send PDF to Claude, parse the structured response, validate totals.
    """
    if not ANTHROPIC_API_KEY:
        raise ExtractionError("ANTHROPIC_API_KEY not configured")

    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            ANTHROPIC_BASE_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )

    if not resp.is_success:
        raise ExtractionError(
            f"Anthropic API error {resp.status_code}: {resp.text[:500]}"
        )

    data = resp.json()
    text = _extract_text(data)
    parsed = _parse_json(text)
    return _to_invoice(parsed)


def _extract_text(api_response: dict) -> str:
    """Pull the text content out of the Anthropic response."""
    blocks = api_response.get("content", [])
    parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    return "\n".join(parts).strip()


def _parse_json(text: str) -> dict:
    """Parse the model's JSON response, tolerating common deviations."""
    # The prompt asks for raw JSON but models sometimes wrap in fences.
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Last-ditch: find the first { and last } and try that slice.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ExtractionError(
            f"Could not parse JSON from model response: {exc}\n"
            f"Response was: {text[:500]}"
        )


def _to_invoice(parsed: dict) -> ExtractedInvoice:
    """Convert the parsed dict to our dataclass, validating along the way."""
    warnings: list[str] = []

    raw_lines = parsed.get("lines") or []
    lines: list[ExtractedLine] = []
    for i, rl in enumerate(raw_lines):
        try:
            qty = _num(rl.get("quantity"), required=True)
            cost = _num(rl.get("unit_cost"), required=True)
        except (TypeError, ValueError) as exc:
            warnings.append(f"Skipped line {i + 1}: {exc}")
            continue
        lines.append(
            ExtractedLine(
                supplier_code=_str_or_none(rl.get("supplier_code")),
                description=_str_or_none(rl.get("description")),
                barcode=_str_or_none(rl.get("barcode")),
                quantity=qty,
                unit_cost=cost,
                line_total=_num(rl.get("line_total")),
            )
        )

    invoice = ExtractedInvoice(
        supplier_name=_str_or_none(parsed.get("supplier_name")),
        invoice_number=_str_or_none(parsed.get("invoice_number")),
        invoice_date=_str_or_none(parsed.get("invoice_date")),
        currency=_str_or_none(parsed.get("currency")),
        subtotal=_num(parsed.get("subtotal")),
        tax=_num(parsed.get("tax")),
        total=_num(parsed.get("total")),
        lines=lines,
        warnings=warnings,
    )

    # Sanity check: do line totals add up to subtotal? This catches the
    # majority of OCR/extraction errors before they hit Lightspeed.
    if invoice.lines and invoice.subtotal is not None:
        computed = sum(l.quantity * l.unit_cost for l in invoice.lines)
        # Allow 1% slack for rounding / per-line discounts.
        if abs(computed - invoice.subtotal) > max(0.5, invoice.subtotal * 0.01):
            invoice.warnings.append(
                f"Line items sum to {computed:.2f} but invoice subtotal "
                f"is {invoice.subtotal:.2f} — review before importing."
            )

    if not invoice.lines:
        invoice.warnings.append(
            "No line items extracted. PDF may be unreadable or have an "
            "unusual layout."
        )

    if not invoice.invoice_number:
        invoice.warnings.append("No invoice number found.")

    return invoice


def _num(v, required: bool = False) -> float | None:
    if v is None or v == "":
        if required:
            raise ValueError("missing required number")
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        # Strip currency, commas, whitespace.
        cleaned = re.sub(r"[^\d.\-]", "", v)
        if not cleaned:
            if required:
                raise ValueError(f"unparseable number: {v!r}")
            return None
        return float(cleaned)
    raise ValueError(f"unexpected type for number: {type(v).__name__}")


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None

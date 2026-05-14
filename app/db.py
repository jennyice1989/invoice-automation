"""
Database schema.

Tables:
  - supplier_sku_mappings   : remembered supplier_code -> product_id resolutions
  - invoices                : every invoice ever uploaded (success or failure)
  - invoice_lines           : per-line decisions for audit
  - pricing_rules           : markup rules by category/keyword
  - supplier_msrp           : MSRP price lists you've uploaded per supplier
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------- #
# Existing: supplier code mappings                                      #
# --------------------------------------------------------------------- #

class SupplierSkuMapping(Base):
    __tablename__ = "supplier_sku_mappings"
    __table_args__ = (
        UniqueConstraint(
            "supplier_id", "supplier_code", name="uq_supplier_code"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(64), index=True)
    supplier_code: Mapped[str] = mapped_column(String(255), index=True)
    lightspeed_product_id: Mapped[str] = mapped_column(String(64))
    lightspeed_sku: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# --------------------------------------------------------------------- #
# New: invoice history                                                  #
# --------------------------------------------------------------------- #

class Invoice(Base):
    """One row per upload. The primary key for duplicate detection is
    (supplier_id, supplier_invoice_number) — we reject re-imports of the
    same invoice number from the same supplier."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint(
            "supplier_id", "supplier_invoice_number",
            name="uq_invoice_supplier_number",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    supplier_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    supplier_invoice_number: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    invoice_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subtotal: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Lifecycle: EXTRACTED -> REVIEWED -> IMPORTED | FAILED | DUPLICATE
    status: Mapped[str] = mapped_column(String(32), default="EXTRACTED")
    consignment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Cached extraction + match results so the review page survives a refresh.
    extraction_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )

    supplier_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Float)

    # Resolution
    bucket: Mapped[str] = mapped_column(String(32))  # match | new | update | uncertain | skipped
    lightspeed_product_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_retail_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_retail_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pricing_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # match_meta holds match_method, confidence, candidates, scraped_data
    match_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


# --------------------------------------------------------------------- #
# Pricing                                                               #
# --------------------------------------------------------------------- #

class PricingRule(Base):
    """Configurable markup by category or keyword. Rules are evaluated
    in priority order (lower = first). First matching rule wins."""

    __tablename__ = "pricing_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # Match logic: any of these tokens (case-insensitive) in product
    # category or description will match. Empty = matches anything.
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    multiplier: Mapped[float] = mapped_column(Float)  # 2.2 means 2.2x cost
    rounding: Mapped[str] = mapped_column(String(16), default="charm")
    # rounding: 'none' | 'cents_99' (round up to .99) | 'charm' (.99/.49)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(default=True)


class SupplierMsrp(Base):
    """MSRP entries you've uploaded for a supplier. Lookup is by
    (supplier_id, supplier_code) or (supplier_id, barcode)."""

    __tablename__ = "supplier_msrp"
    __table_args__ = (
        UniqueConstraint(
            "supplier_id", "supplier_code", "barcode",
            name="uq_supplier_msrp",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(64), index=True)
    supplier_code: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    msrp: Mapped[float] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )


# --------------------------------------------------------------------- #
# Engine                                                                #
# --------------------------------------------------------------------- #

def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return ""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        url = _database_url()
        if not url:
            raise RuntimeError("DATABASE_URL not set")
        _engine = create_async_engine(url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables. Idempotent."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed default pricing rules if the table is empty.
    async with session_scope() as session:
        existing = (await session.execute(select(PricingRule))).first()
        if not existing:
            session.add_all([
                PricingRule(name="Frozen / refrigerated", keywords="frozen,refrig,cold",
                            multiplier=2.0, rounding="charm", priority=10),
                PricingRule(name="Livestock", keywords="live,fish,coral,invert,plant",
                            multiplier=1.8, rounding="none", priority=20),
                PricingRule(name="Dry goods (default)", keywords=None,
                            multiplier=2.2, rounding="charm", priority=1000),
            ])


# --------------------------------------------------------------------- #
# Query helpers                                                         #
# --------------------------------------------------------------------- #

async def find_mapping(
    session: AsyncSession, *, supplier_id: str, supplier_code: str,
) -> SupplierSkuMapping | None:
    stmt = select(SupplierSkuMapping).where(
        SupplierSkuMapping.supplier_id == supplier_id,
        SupplierSkuMapping.supplier_code == supplier_code,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_mapping(
    session: AsyncSession, *, supplier_id: str, supplier_code: str,
    lightspeed_product_id: str, lightspeed_sku: str | None,
    product_name: str | None,
) -> SupplierSkuMapping:
    existing = await find_mapping(
        session, supplier_id=supplier_id, supplier_code=supplier_code,
    )
    if existing:
        existing.lightspeed_product_id = lightspeed_product_id
        existing.lightspeed_sku = lightspeed_sku
        existing.product_name = product_name
        return existing
    mapping = SupplierSkuMapping(
        supplier_id=supplier_id, supplier_code=supplier_code,
        lightspeed_product_id=lightspeed_product_id,
        lightspeed_sku=lightspeed_sku, product_name=product_name,
    )
    session.add(mapping)
    return mapping


async def find_existing_invoice(
    session: AsyncSession, *, supplier_id: str, supplier_invoice_number: str,
) -> Invoice | None:
    stmt = select(Invoice).where(
        Invoice.supplier_id == supplier_id,
        Invoice.supplier_invoice_number == supplier_invoice_number,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_msrp(
    session: AsyncSession, *, supplier_id: str,
    supplier_code: str | None, barcode: str | None,
) -> SupplierMsrp | None:
    if supplier_code:
        row = (await session.execute(
            select(SupplierMsrp).where(
                SupplierMsrp.supplier_id == supplier_id,
                SupplierMsrp.supplier_code == supplier_code,
            )
        )).scalar_one_or_none()
        if row:
            return row
    if barcode:
        row = (await session.execute(
            select(SupplierMsrp).where(
                SupplierMsrp.supplier_id == supplier_id,
                SupplierMsrp.barcode == barcode,
            )
        )).scalar_one_or_none()
        if row:
            return row
    return None

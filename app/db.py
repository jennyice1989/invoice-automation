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
from typing import AsyncIterator, Optional

from sqlalchemy import (
    Boolean,
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
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
    lightspeed_sku: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# --------------------------------------------------------------------- #
# Local catalog cache                                                   #
# --------------------------------------------------------------------- #

class CatalogProduct(Base):
    """Local copy of a Lightspeed product.

    Invoice matching and manual search should read from this table first.
    Lightspeed remains the source of truth, but a local cache makes matching
    deterministic, fast, and easier to debug.
    """

    __tablename__ = "catalog_products"
    __table_args__ = (
        UniqueConstraint(
            "lightspeed_product_id", name="uq_catalog_lightspeed_product_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lightspeed_product_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    normalized_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    supplier_code: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    brand_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    supply_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    retail_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    deleted_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SupplierCatalogItem(Base):
    """Supplier item memory, including products that are not in Lightspeed yet."""

    __tablename__ = "supplier_catalog_items"
    __table_args__ = (
        UniqueConstraint(
            "supplier_id", "supplier_code", name="uq_supplier_catalog_code"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(64), index=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    supplier_code: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    mfg_part: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    list_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    catalog_source: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    catalog_page: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    facts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    lightspeed_product_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="needs_product", index=True)
    last_unit_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    seen_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
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
    filename: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    supplier_invoice_number: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    invoice_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    subtotal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tax: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Lifecycle: EXTRACTED -> REVIEWED -> IMPORTED | FAILED | DUPLICATE
    status: Mapped[str] = mapped_column(String(32), default="EXTRACTED")
    consignment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Cached extraction + match results so the review page survives a refresh.
    extraction_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Original PDF bytes — kept so the invoice can be re-processed later
    # after pipeline improvements. Invoices are small; this is fine at
    # single-store volume. Nullable so old rows without it still load.
    pdf_bytes: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

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

    supplier_code: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Float)

    # Resolution
    bucket: Mapped[str] = mapped_column(String(32))  # match | new | update | uncertain | skipped
    lightspeed_product_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    suggested_retail_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    final_retail_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pricing_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # match_meta holds match_method, confidence, candidates, scraped_data
    match_meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

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
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    multiplier: Mapped[float] = mapped_column(Float)  # 1.5 means 1.5x cost
    rounding: Mapped[str] = mapped_column(String(16), default="charm")
    # rounding: 'none' | 'cents_99' | 'charm' / 'cents_49_99'
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
    supplier_code: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    msrp: Mapped[float] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )


class EnrichmentDraft(Base):
    """A product being enriched for catalog entry. Holds the draft content
    (description or fish profile) plus the manually-entered fields (UPC,
    pricing, photo status). Lifecycle: DRAFT -> CREATED | SKIPPED."""

    __tablename__ = "enrichment_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # A batch groups products entered together (one bulk-paste, one import).
    batch_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    input_name: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(16), default="unknown")  # dry_good | live_fish | unknown

    # Drafted by Claude
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fish_profile: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    detected_brand: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Manually entered / from invoice
    final_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    supplier_code: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    supply_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    retail_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    has_photo: Mapped[bool] = mapped_column(default=False)

    # New: catalog organization
    product_category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    product_category_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    brand_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    brand_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # {"list": [...]}

    status: Mapped[str] = mapped_column(String(16), default="DRAFT")  # DRAFT | CREATED | SKIPPED
    lightspeed_product_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    warnings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # If this draft was queued from an invoice, hold enough context to
    # add the resulting product to that invoice's consignment after
    # the user approves it.
    source_invoice_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    source_consignment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_receive_immediately: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
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
        # Lightweight migration: add columns that may not exist on
        # databases created by earlier versions. create_all() never
        # alters existing tables, so we do it explicitly. Postgres
        # supports IF NOT EXISTS on ADD COLUMN.
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS pdf_bytes BYTEA"
        ))
        # Enrichment draft source columns (added when invoice integration shipped)
        for stmt in (
            "CREATE TABLE IF NOT EXISTS catalog_products (id SERIAL PRIMARY KEY)",
            "CREATE TABLE IF NOT EXISTS supplier_catalog_items (id SERIAL PRIMARY KEY)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS lightspeed_product_id VARCHAR(64)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS name VARCHAR(500)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS normalized_name VARCHAR(500)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS sku VARCHAR(255)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS barcode VARCHAR(255)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS supplier_code VARCHAR(255)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS supplier_id VARCHAR(64)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS brand_name VARCHAR(255)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS category_name VARCHAR(255)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS supply_price DOUBLE PRECISION",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS retail_price DOUBLE PRECISION",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS deleted_at VARCHAR(64)",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS raw JSONB",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS synced_at TIMESTAMP",
            "ALTER TABLE catalog_products ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_lightspeed_product_id_idx ON catalog_products(lightspeed_product_id)",
            "CREATE INDEX IF NOT EXISTS ix_catalog_products_sku ON catalog_products(sku)",
            "CREATE INDEX IF NOT EXISTS ix_catalog_products_barcode ON catalog_products(barcode)",
            "CREATE INDEX IF NOT EXISTS ix_catalog_products_supplier_code ON catalog_products(supplier_code)",
            "CREATE INDEX IF NOT EXISTS ix_catalog_products_normalized_name ON catalog_products(normalized_name)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS supplier_id VARCHAR(64)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS supplier_name VARCHAR(500)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS supplier_code VARCHAR(255)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS description VARCHAR(500)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS barcode VARCHAR(64)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS mfg_part VARCHAR(255)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS list_price DOUBLE PRECISION",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS catalog_source VARCHAR(500)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS catalog_page VARCHAR(64)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS facts JSONB",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS lightspeed_product_id VARCHAR(64)",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS status VARCHAR(32) DEFAULT 'needs_product'",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS last_unit_cost DOUBLE PRECISION",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS seen_count INTEGER DEFAULT 0",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
            "ALTER TABLE supplier_catalog_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_catalog_code_idx ON supplier_catalog_items(supplier_id, supplier_code)",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS source_invoice_id INTEGER",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS source_consignment_id VARCHAR(64)",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS source_quantity DOUBLE PRECISION",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS source_cost DOUBLE PRECISION",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS source_receive_immediately BOOLEAN DEFAULT FALSE",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS product_category VARCHAR(255)",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS product_category_id VARCHAR(64)",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS brand_name VARCHAR(255)",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS brand_id VARCHAR(64)",
            "ALTER TABLE enrichment_drafts ADD COLUMN IF NOT EXISTS tags JSONB",
        ):
            try:
                await conn.execute(text(stmt))
            except Exception:
                # Table may not exist yet on first deploy; create_all
                # ran above and made it with the column, so we're fine.
                pass

    # Seed default pricing rules if the table is empty.
    async with session_scope() as session:
        existing_rules = (await session.execute(select(PricingRule))).scalars().all()
        if not existing_rules:
            session.add_all([
                PricingRule(name="Default target margin", keywords=None,
                            multiplier=1.5, rounding="cents_49_99", priority=1000),
            ])
        elif not any(r.name == "Default target margin" for r in existing_rules):
            old_seed_names = {
                "Frozen / refrigerated", "Livestock", "Dry goods (default)",
            }
            for rule in existing_rules:
                if rule.name in old_seed_names:
                    rule.enabled = False
            session.add(PricingRule(
                name="Default target margin",
                keywords=None,
                multiplier=1.5,
                rounding="cents_49_99",
                priority=1000,
            ))


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

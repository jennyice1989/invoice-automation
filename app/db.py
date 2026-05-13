"""
Database setup. Uses SQLAlchemy 2.x async with Postgres.

One table for now: supplier_sku_mappings — the "memory" of every
supplier-code-to-Lightspeed-SKU resolution you've ever made. Once you
teach it that ReefH2O's "RH2-CAL-2KG" is your "CAL2KG", that mapping
fires automatically forever after.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import DateTime, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SupplierSkuMapping(Base):
    """
    One row per (supplier, code-on-invoice) pair.

    `supplier_id` is the Lightspeed supplier UUID — we key on it so the
    same code from two different suppliers doesn't collide.
    `supplier_code` is whatever literal string appeared on the invoice
    line (could be a SKU, a part number, even a normalized product name).
    `lightspeed_product_id` is what it resolves to.
    """

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
# Engine & session                                                      #
# --------------------------------------------------------------------- #

def _database_url() -> str:
    """
    Render provides DATABASE_URL with the `postgres://` scheme; SQLAlchemy
    async wants `postgresql+asyncpg://`. Normalize it here.
    """
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
            raise RuntimeError(
                "DATABASE_URL not set; cannot initialize database"
            )
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
    """Context manager for a single transactional unit of work."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create tables if they don't exist. Idempotent — safe on every boot."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# --------------------------------------------------------------------- #
# Convenience queries                                                   #
# --------------------------------------------------------------------- #

async def find_mapping(
    session: AsyncSession,
    *,
    supplier_id: str,
    supplier_code: str,
) -> SupplierSkuMapping | None:
    stmt = select(SupplierSkuMapping).where(
        SupplierSkuMapping.supplier_id == supplier_id,
        SupplierSkuMapping.supplier_code == supplier_code,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_mapping(
    session: AsyncSession,
    *,
    supplier_id: str,
    supplier_code: str,
    lightspeed_product_id: str,
    lightspeed_sku: str | None,
    product_name: str | None,
) -> SupplierSkuMapping:
    existing = await find_mapping(
        session,
        supplier_id=supplier_id,
        supplier_code=supplier_code,
    )
    if existing:
        existing.lightspeed_product_id = lightspeed_product_id
        existing.lightspeed_sku = lightspeed_sku
        existing.product_name = product_name
        return existing
    mapping = SupplierSkuMapping(
        supplier_id=supplier_id,
        supplier_code=supplier_code,
        lightspeed_product_id=lightspeed_product_id,
        lightspeed_sku=lightspeed_sku,
        product_name=product_name,
    )
    session.add(mapping)
    return mapping

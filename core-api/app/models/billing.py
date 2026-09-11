import uuid
from sqlalchemy import Column, String, Numeric, Integer, Date, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class BillingUnitPrice(Base):
    __tablename__ = "billing_unit_prices"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    item_key = Column(String(50), nullable=False)
    unit_price = Column(Numeric(12, 4), nullable=False)
    effective_from = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BillingInvoice(Base):
    __tablename__ = "billing_invoices"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    target_year_month = Column(String(7), nullable=False)
    status = Column(String(20), nullable=False, default="draft")
    subtotal = Column(Integer, nullable=False, default=0)
    tax_amount = Column(Integer, nullable=False, default=0)
    total_amount = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    finalized_at = Column(DateTime(timezone=True), nullable=True)
    bill_shock_notified_at = Column(DateTime(timezone=True), nullable=True)


class BillingLineItem(Base):
    __tablename__ = "billing_line_items"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("billing_invoices.id", ondelete="CASCADE"), nullable=False)
    item_key = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(12, 4), nullable=False)
    amount = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BillingDefaultUnitPrice(Base):
    __tablename__ = "billing_default_unit_prices"
    item_key = Column(String(50), primary_key=True)
    unit_price = Column(Numeric(12, 4), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BillingSettings(Base):
    __tablename__ = "billing_settings"
    id = Column(Integer, primary_key=True, default=1)
    tax_rate = Column(Numeric(5, 4), nullable=False)
    default_bill_shock_threshold_amount = Column(Integer, nullable=True)

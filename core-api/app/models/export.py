import uuid
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class ExportJob(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", name="uq_export_jobs_tenant_kind"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(10), nullable=False)
    status = Column(String(20), nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    device_ids = Column(JSONB, nullable=True)
    group_id = Column(UUID(as_uuid=True), nullable=True)
    minio_key = Column(String(255), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    error_message = Column(String(500), nullable=True)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class ExportSchedule(Base):
    __tablename__ = "export_schedules"
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    frequency = Column(String(10), nullable=False)
    device_ids = Column(JSONB, nullable=True)
    group_id = Column(UUID(as_uuid=True), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)

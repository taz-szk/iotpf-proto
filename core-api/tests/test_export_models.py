from app.models.export import ExportJob, ExportSchedule

def test_export_job_table_name_and_unique_constraint_columns():
    assert ExportJob.__tablename__ == "export_jobs"
    row = ExportJob(
        tenant_id="11111111-1111-1111-1111-111111111111",
        kind="adhoc", status="queued",
        period_start="2026-10-01T00:00:00+00:00",
        period_end="2026-10-19T00:00:00+00:00",
    )
    assert row.kind == "adhoc"
    assert row.status == "queued"
    names = {uc.name for uc in ExportJob.__table__.constraints}
    assert "uq_export_jobs_tenant_kind" in names

def test_export_schedule_table_name_and_defaults():
    assert ExportSchedule.__tablename__ == "export_schedules"
    row = ExportSchedule(tenant_id="11111111-1111-1111-1111-111111111111", frequency="weekly")
    assert row.frequency == "weekly"

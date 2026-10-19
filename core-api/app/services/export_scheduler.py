"""定期(periodic)CSVエクスポートのスケジュール判定・実行。

テナントごとに(tenant_id, kind="periodic")のExportJob行は1つだけ(UNIQUE制約)。
アドホック経路(request_adhoc_export)とは異なり、定期実行にはロック・サイズ上限の
チェックは適用しない(要件はアドホックのみ)。そのため`request_adhoc_export`は
再利用せず、ここで直接ExportJob行をupsertして`run_export_job`を呼ぶ。
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.export import ExportJob, ExportSchedule
from app.services.export_jobs import run_export_job

_INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7), "monthly": timedelta(days=30)}


def run_due_periodic_exports() -> None:
    """有効な全スケジュールを確認し、間隔が経過しているものについて
    periodicなExportJobをupsertしてrun_export_jobを同期的に実行する。
    呼び出し元はスレッドループ本体であり、既にリクエストパス外のため
    追加のスレッド起動は不要(run_export_jobは同期呼び出し)。"""
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        schedules = db.query(ExportSchedule).filter(ExportSchedule.enabled == True).all()  # noqa: E712

        due_job_ids: list[str] = []
        for schedule in schedules:
            interval = _INTERVALS[schedule.frequency]
            if schedule.last_run_at is not None and (now - schedule.last_run_at) < interval:
                continue

            period_end = now
            period_start = now - interval

            job = db.query(ExportJob).filter(
                ExportJob.tenant_id == schedule.tenant_id, ExportJob.kind == "periodic"
            ).first()
            if job is None:
                job = ExportJob(tenant_id=schedule.tenant_id, kind="periodic")

            job.status = "queued"
            job.period_start = period_start
            job.period_end = period_end
            job.device_ids = schedule.device_ids
            job.group_id = schedule.group_id
            job.requested_at = now
            job.minio_key = None
            job.file_size_bytes = None
            job.error_message = None
            job.started_at = None
            job.completed_at = None

            db.add(job)
            db.commit()
            db.refresh(job)

            schedule.last_run_at = now
            db.commit()

            due_job_ids.append(str(job.id))

    for job_id in due_job_ids:
        run_export_job(job_id)


def start_export_scheduler_worker() -> None:
    """1時間おきに定期エクスポートの実行対象を確認するバックグラウンドスレッドを起動する。"""
    def _loop() -> None:
        while True:
            try:
                run_due_periodic_exports()
            except Exception as e:
                print(f"[export_scheduler] run failed: {e}")
            time.sleep(3600)
    threading.Thread(target=_loop, daemon=True).start()

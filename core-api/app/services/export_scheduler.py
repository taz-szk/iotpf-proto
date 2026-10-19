"""定期(periodic)CSVエクスポートのスケジュール判定・実行。

テナントごとに(tenant_id, kind="periodic")のExportJob行は1つだけ(UNIQUE制約)。
アドホック経路(request_adhoc_export)とは異なり、定期実行にはアドホック用のロックは
適用しない。そのため`request_adhoc_export`は再利用せず、ここで直接ExportJob行を
upsertして`run_export_job`を呼ぶ。

サイズ上限は定期実行にも適用する。ただし無人実行でリクエストを拒否する相手がいないため、
上限超過時はCSVを生成せず、ジョブ行に失敗(error_message)を記録して次の周期を待つ。

期間は前回実行時刻(last_run_at)から今回実行時刻までとし、連続する定期ファイル同士の
期間に隙間ができないようにする(初回のみ[now - interval, now])。
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.database import SessionLocal
from app.models.export import ExportJob, ExportSchedule
from app.models.public import Tenant
from app.services.device_groups import GroupNotFoundError
from app.services.export_csv import estimate_export_row_count, InfluxUnavailableError
from app.services.export_jobs import (
    BYTES_PER_ROW_ESTIMATE, record_failed_attempt, resolve_target_device_ids, run_export_job,
)

_INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7), "monthly": timedelta(days=30)}

PERIODIC_TOO_LARGE_MESSAGE = (
    "定期エクスポート対象のデータ量が上限を超えています。デバイス/グループの絞り込みを見直してください"
)
PERIODIC_GROUP_NOT_FOUND_MESSAGE = "定期エクスポートの対象グループが見つかりません。設定を見直してください"


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
            last_run_at = schedule.last_run_at
            if last_run_at is not None and last_run_at.tzinfo is None:
                last_run_at = last_run_at.replace(tzinfo=timezone.utc)
            if last_run_at is not None and (now - last_run_at) < interval:
                continue

            period_end = now
            # 前回の期間の終端(=前回のlast_run_at)から続けることで期間の隙間を作らない
            period_start = last_run_at if last_run_at is not None else now - interval

            job = db.query(ExportJob).filter(
                ExportJob.tenant_id == schedule.tenant_id, ExportJob.kind == "periodic"
            ).first()
            if job is None:
                job = ExportJob(tenant_id=schedule.tenant_id, kind="periodic")

            job.period_start = period_start
            job.period_end = period_end
            job.device_ids = schedule.device_ids
            job.group_id = schedule.group_id
            job.requested_at = now
            job.started_at = None
            # minio_key / file_size_bytes / completed_at は前回のファイル情報として保持する。

            # サイズ上限チェック(無人実行のため、超過時は生成せず失敗として記録する)
            failure_message = None
            try:
                tenant = db.query(Tenant).filter(Tenant.id == schedule.tenant_id).first()
                target_device_ids = resolve_target_device_ids(
                    schedule.tenant_id, schedule.device_ids, schedule.group_id)
                estimated_bytes = (
                    estimate_export_row_count(tenant, period_start, period_end, target_device_ids)
                    * BYTES_PER_ROW_ESTIMATE
                )
                if estimated_bytes > settings.export_max_size_bytes:
                    failure_message = PERIODIC_TOO_LARGE_MESSAGE
            except GroupNotFoundError:
                failure_message = PERIODIC_GROUP_NOT_FOUND_MESSAGE
            except InfluxUnavailableError as e:
                # 一時的な障害の可能性が高いので、last_run_atは進めずに次回ポーリングで再試行する
                # (期間はlast_run_atから続くので、再試行しても隙間はできない)。
                print(f"[export_scheduler] size estimate failed for tenant={schedule.tenant_id}: {e}")
                record_failed_attempt(job, f"データ量の見積もりに失敗しました(次回再試行します): {e}")
                db.add(job)
                db.commit()
                continue

            if failure_message is not None:
                record_failed_attempt(job, failure_message)
                db.add(job)
                schedule.last_run_at = now
                db.commit()
                continue

            job.status = "queued"
            job.error_message = None
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

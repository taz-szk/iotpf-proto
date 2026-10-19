"""アドホックCSVエクスポートジョブのオーケストレーション。

テナントごとに(tenant_id, kind="adhoc")のExportJob行は1つだけ(UNIQUE制約)。
request_adhoc_export()は実行中ジョブの重複を防ぎ(ただしロックのタイムアウトによる
復旧付き)、サイズ概算によるキャップ超過を事前にチェックしてからジョブ行を作成/更新する。
run_export_job()はバックグラウンドスレッドから呼ばれる実ワーカーで、例外を外に出さない
(呼び出し元が無いため)。
"""
import threading
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models.export import ExportJob
from app.models.public import Tenant
from app.services.export_csv import estimate_export_row_count, generate_export_csv, InfluxUnavailableError
from app.services.minio_client import upload_export_file, delete_export_file

BYTES_PER_ROW_ESTIMATE = 180
_LOCK_TIMEOUT_SECONDS = 600


class JobAlreadyRunningError(Exception):
    pass


class ExportTooLargeError(Exception):
    def __init__(self, estimated_bytes: int, limit_bytes: int):
        self.estimated_bytes = estimated_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"Estimated export size {estimated_bytes} bytes exceeds limit {limit_bytes} bytes"
        )


def request_adhoc_export(db, tenant: Tenant, start: datetime, end: datetime,
                          device_ids: list[str] | None, group_id: str | None) -> ExportJob:
    existing = db.query(ExportJob).filter(
        ExportJob.tenant_id == tenant.id, ExportJob.kind == "adhoc"
    ).first()

    if existing is not None and existing.status in ("queued", "processing"):
        now = datetime.now(timezone.utc)
        is_stale = (
            existing.started_at is not None
            and (now - existing.started_at) > timedelta(seconds=_LOCK_TIMEOUT_SECONDS)
        )
        if not is_stale:
            raise JobAlreadyRunningError()
        # else: クラッシュしたジョブとみなして復旧し、新規リクエスト処理へフォールスルーする
        # (既存行への書き込みは、サイズゲート通過後にまとめて行う。ここでは何も変更しない)。

    # サイズゲート: InfluxUnavailableErrorはここで伝播させる(フェイルセーフのため黙殺しない)。
    # 既存行はここまで一切変更していないため、ここで拒否すれば既存行は無変更のまま残る。
    estimated_rows = estimate_export_row_count(tenant, start, end, device_ids)
    estimated_bytes = estimated_rows * BYTES_PER_ROW_ESTIMATE
    if estimated_bytes > settings.export_max_size_bytes:
        raise ExportTooLargeError(estimated_bytes, settings.export_max_size_bytes)

    job = existing if existing is not None else ExportJob(tenant_id=tenant.id, kind="adhoc")
    job.status = "queued"
    job.period_start = start
    job.period_end = end
    job.device_ids = device_ids
    job.group_id = group_id
    job.requested_at = datetime.now(timezone.utc)
    job.minio_key = None
    job.file_size_bytes = None
    job.error_message = None
    job.started_at = None
    job.completed_at = None

    db.add(job)
    db.commit()

    threading.Thread(target=run_export_job, args=(str(job.id),), daemon=True).start()

    return job


def run_export_job(job_id: str) -> None:
    """バックグラウンドスレッドから呼ばれるワーカー本体。呼び出し元が無いため、
    どのような例外が起きても外へ伝播させてはならない(全経路をtry/exceptで囲む)。"""
    from app.database import SessionLocal

    try:
        with SessionLocal() as db:
            job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
            if job is None:
                return

            try:
                job.status = "processing"
                job.started_at = datetime.now(timezone.utc)
                db.commit()

                tenant = db.query(Tenant).filter(Tenant.id == job.tenant_id).first()

                resolved_device_ids = job.device_ids
                if job.group_id is not None and resolved_device_ids is None:
                    from app.services.device_groups import list_group_device_ids
                    schema = f"tenant_{str(job.tenant_id).replace('-', '_')}"
                    resolved_device_ids = list_group_device_ids(schema, str(job.group_id))

                csv_bytes = generate_export_csv(tenant, job.period_start, job.period_end, resolved_device_ids)

                # 上書き対象スロットの既存ファイルを先に削除してから新しいファイルをアップロードする。
                old_minio_key = job.minio_key
                if old_minio_key:
                    delete_export_file(old_minio_key)

                minio_key = upload_export_file(str(job.tenant_id), str(job.id), csv_bytes)

                job.status = "done"
                job.minio_key = minio_key
                job.file_size_bytes = len(csv_bytes)
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
            except Exception as exc:
                job.status = "failed"
                job.error_message = str(exc)[:500]
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
    except Exception as exc:
        # SessionLocal()自体が失敗するなど、ジョブ行の更新すら不可能な想定外の事態。
        # スレッドの呼び出し元が存在しないため、ここで握りつぶすしかない。
        print(f"[export_jobs] run_export_job({job_id}) failed outside recoverable path: {exc}")

"""アドホックCSVエクスポートジョブのオーケストレーション。

テナントごとに(tenant_id, kind="adhoc")のExportJob行は1つだけ(UNIQUE制約)。
request_adhoc_export()は実行中ジョブの重複を防ぎ(ただしロックのタイムアウトによる
復旧付き)、対象デバイスの検証・グループ解決とサイズ概算によるキャップ超過を事前に
チェックしてからジョブ行を作成/更新する。run_export_job()はバックグラウンドスレッド
(または定期スケジューラ)から呼ばれる実ワーカーで、例外を外に出さない(呼び出し元が無いため)。

ファイルの差し替え方針: 新しい生成が「成功した時点で」古いファイルを置き換える。
リクエスト受付・生成中・生成失敗の各時点では、既存のminio_key/file_size_bytesを保持し、
前回の正常なファイルを引き続きダウンロード可能にする。
"""
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.models.export import ExportJob
from app.models.public import Tenant
from app.services.device_groups import list_group_device_ids, GroupNotFoundError  # noqa: F401 (再エクスポート)
from app.services.export_csv import estimate_export_row_count, generate_export_csv, InfluxUnavailableError  # noqa: F401
from app.services.minio_client import upload_export_file, delete_export_file

BYTES_PER_ROW_ESTIMATE = 180
_LOCK_TIMEOUT_SECONDS = 600

_RETAINED_FILE_NOTE = "直近の生成に失敗しました(前回のファイルは引き続きダウンロードできます)"


class JobAlreadyRunningError(Exception):
    pass


class ExportTooLargeError(Exception):
    def __init__(self, estimated_bytes: int, limit_bytes: int):
        self.estimated_bytes = estimated_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"Estimated export size {estimated_bytes} bytes exceeds limit {limit_bytes} bytes"
        )


class UnknownDeviceError(Exception):
    """リクエストされたdevice_idの一部が自テナントのデバイスとして存在しない。"""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"Unknown device_id(s): {', '.join(missing)}")


def _tenant_schema(tenant_id) -> str:
    return f"tenant_{str(tenant_id).replace('-', '_')}"


def _fetch_existing_device_ids(schema: str, device_ids: list[str]) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'SELECT device_id FROM "{schema}".devices WHERE device_id = ANY(:ids)'),
            {"ids": list(device_ids)},
        ).fetchall()
    return {r.device_id for r in rows}


def validate_export_device_ids(tenant_id, device_ids: list[str] | None) -> None:
    """device_idsが全て自テナントの実在デバイスであることを検証する。Fluxクエリへ
    埋め込まれる値をテナントの実デバイスIDに限定し、クエリ注入を防ぐ。"""
    if not device_ids:
        return
    existing = _fetch_existing_device_ids(_tenant_schema(tenant_id), device_ids)
    missing = sorted({d for d in device_ids if d not in existing})
    if missing:
        raise UnknownDeviceError(missing)


def resolve_target_device_ids(tenant_id, device_ids: list[str] | None, group_id) -> list[str] | None:
    """エクスポート対象のdevice_idリストを決める。device_ids指定があればそれを優先し、
    無ければgroup_idの所属デバイスに解決する(GroupNotFoundErrorは伝播させる)。
    どちらも無ければNone(=全デバイス)。"""
    if device_ids is not None:
        return device_ids
    if group_id is not None:
        return list_group_device_ids(_tenant_schema(tenant_id), str(group_id))
    return None


def estimate_export_bytes(tenant: Tenant, start: datetime, end: datetime,
                          device_ids: list[str] | None) -> int:
    return estimate_export_row_count(tenant, start, end, device_ids) * BYTES_PER_ROW_ESTIMATE


def record_failed_attempt(job: ExportJob, message: str) -> None:
    """生成の試行失敗をジョブ行に記録する。前回のファイルが残っていれば、それは引き続き
    有効なのでstatus="done"のまま(ファイル情報もcompleted_atも保持)error_messageに失敗を
    記録する。ファイルが無ければstatus="failed"とする。"""
    if job.minio_key:
        job.status = "done"
        job.error_message = f"{_RETAINED_FILE_NOTE}: {message}"[:500]
    else:
        job.status = "failed"
        job.error_message = message[:500]
        job.completed_at = datetime.now(timezone.utc)


def _is_stale(job: ExportJob, now: datetime) -> bool:
    # 処理開始前(started_at=None)のままスレッドが起動しなかった/落ちたジョブも
    # 復旧できるよう、started_atが無ければrequested_atを基準にする。
    reference = job.started_at or job.requested_at
    if reference is None:
        return True
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (now - reference) > timedelta(seconds=_LOCK_TIMEOUT_SECONDS)


def request_adhoc_export(db, tenant: Tenant, start: datetime, end: datetime,
                          device_ids: list[str] | None, group_id: str | None) -> ExportJob:
    existing = db.query(ExportJob).filter(
        ExportJob.tenant_id == tenant.id, ExportJob.kind == "adhoc"
    ).first()

    if existing is not None and existing.status in ("queued", "processing"):
        if not _is_stale(existing, datetime.now(timezone.utc)):
            raise JobAlreadyRunningError()
        # else: クラッシュしたジョブとみなして復旧し、新規リクエスト処理へフォールスルーする
        # (既存行への書き込みは、各ゲート通過後にまとめて行う。ここでは何も変更しない)。

    # 対象デバイスの検証・グループ解決(UnknownDeviceError / GroupNotFoundErrorは伝播)。
    # サイズ概算をグループ所属デバイスに絞るため、概算より前に解決する。
    validate_export_device_ids(tenant.id, device_ids)
    target_device_ids = resolve_target_device_ids(tenant.id, device_ids, group_id)

    # サイズゲート: InfluxUnavailableErrorはここで伝播させる(フェイルセーフのため黙殺しない)。
    # 既存行はここまで一切変更していないため、ここで拒否すれば既存行は無変更のまま残る。
    estimated_bytes = estimate_export_bytes(tenant, start, end, target_device_ids)
    if estimated_bytes > settings.export_max_size_bytes:
        raise ExportTooLargeError(estimated_bytes, settings.export_max_size_bytes)

    job = existing if existing is not None else ExportJob(tenant_id=tenant.id, kind="adhoc")
    job.status = "queued"
    job.period_start = start
    job.period_end = end
    job.device_ids = device_ids
    job.group_id = group_id
    job.requested_at = datetime.now(timezone.utc)
    job.error_message = None
    job.started_at = None
    # minio_key / file_size_bytes / completed_at は前回のファイルの情報として保持する
    # (新しいファイルの生成が成功した時点で置き換える)。

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

            new_minio_key = None
            try:
                job.status = "processing"
                job.started_at = datetime.now(timezone.utc)
                db.commit()

                tenant = db.query(Tenant).filter(Tenant.id == job.tenant_id).first()
                resolved_device_ids = resolve_target_device_ids(job.tenant_id, job.device_ids, job.group_id)

                csv_bytes = generate_export_csv(tenant, job.period_start, job.period_end, resolved_device_ids)

                # 新しいファイルを(生成ごとに一意な)別キーへアップロードし、行を更新してから
                # 古いファイルを削除する。途中で失敗しても前回のファイルは無傷で残る。
                old_minio_key = job.minio_key
                new_minio_key = upload_export_file(str(job.tenant_id), str(job.id), csv_bytes)

                job.status = "done"
                job.minio_key = new_minio_key
                job.file_size_bytes = len(csv_bytes)
                job.error_message = None
                job.completed_at = datetime.now(timezone.utc)
                db.commit()

                if old_minio_key and old_minio_key != new_minio_key:
                    try:
                        delete_export_file(old_minio_key)
                    except Exception as exc:
                        print(f"[export_jobs] failed to delete old export file {old_minio_key}: {exc}")
            except Exception as exc:
                db.rollback()
                job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
                if job is None:
                    return
                if new_minio_key and new_minio_key != job.minio_key:
                    # アップロード後の行更新に失敗した場合、新ファイルは参照されないので消す
                    try:
                        delete_export_file(new_minio_key)
                    except Exception:
                        pass
                record_failed_attempt(job, str(exc))
                db.commit()
    except Exception as exc:
        # SessionLocal()自体が失敗するなど、ジョブ行の更新すら不可能な想定外の事態。
        # スレッドの呼び出し元が存在しないため、ここで握りつぶすしかない。
        # (行はqueued/processingのまま残るが、request_adhoc_exportのstale判定で復旧できる)
        print(f"[export_jobs] run_export_job({job_id}) failed outside recoverable path: {exc}")

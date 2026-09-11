import threading
import time
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.public import Tenant
from app.models.billing import BillingInvoice, BillingLineItem
from app.services.billing import calculate_invoice, get_effective_unit_prices, get_tax_rate
from app.services.billing_usage import aggregate_monthly_usage


def _current_target_year_month() -> tuple[int, int, str]:
    now = datetime.now(timezone.utc)
    return now.year, now.month, f"{now.year:04d}-{now.month:02d}"


def _months_between_exclusive(start_ym: str, end_ym: str) -> list[str]:
    """start_ym（除く）からend_ym（除く）までの"YYYY-MM"を昇順で返す。startがend以上なら空。"""
    year, month = (int(p) for p in start_ym.split("-"))
    end_year, end_month = (int(p) for p in end_ym.split("-"))
    result = []
    while True:
        month += 1
        if month > 12:
            month = 1
            year += 1
        if (year, month) >= (end_year, end_month):
            break
        result.append(f"{year:04d}-{month:02d}")
    return result


def _backfill_missing_months(
    db, tenant_id: str, schema: str, influxdb_org_id: str, influxdb_token: str,
    current_target_year_month: str,
) -> None:
    """既存の請求書と現在の対象月の間に抜けている月（バッチが複数月停止していた場合等）
    があれば、finalizedとして遡って生成する。テナントにまだ請求書が1件も無い場合
    （開通直後）は対象なし。provisionable_devicesは「現在時点」のスナップショットで
    過去を再現できないため、直近の既存請求書が持っていた値をそのまま引き継ぐ。"""
    existing = db.query(BillingInvoice).filter(BillingInvoice.tenant_id == tenant_id).all()
    if not existing:
        return
    latest_existing_ym = max(row.target_year_month for row in existing)

    missing_months = _months_between_exclusive(latest_existing_ym, current_target_year_month)
    if not missing_months:
        return

    latest_invoice = db.query(BillingInvoice).filter(
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.target_year_month == latest_existing_ym,
    ).first()
    latest_items = db.query(BillingLineItem).filter(BillingLineItem.invoice_id == latest_invoice.id).all()
    preserved_provisionable = next(
        (li.quantity for li in latest_items if li.item_key == "provisionable_devices"),
        0,
    )

    for ym in missing_months:
        year, month = (int(p) for p in ym.split("-"))
        usage = aggregate_monthly_usage(db, tenant_id, schema, influxdb_org_id, influxdb_token, year, month)
        usage["provisionable_devices"] = preserved_provisionable
        prices = get_effective_unit_prices(db, tenant_id, year, month)
        calc = calculate_invoice(usage, prices, get_tax_rate(db))

        invoice = BillingInvoice(
            tenant_id=tenant_id, target_year_month=ym, status="finalized",
            subtotal=calc["subtotal"], tax_amount=calc["tax_amount"], total_amount=calc["total_amount"],
            finalized_at=datetime.now(timezone.utc),
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        _replace_line_items(db, invoice.id, calc["line_items"])


class InvoiceNotFinalizedError(Exception):
    pass


def correct_invoice(
    db, tenant_id: str, schema: str, influxdb_org_id: str, influxdb_token: str,
    target_year_month: str,
) -> BillingInvoice | None:
    """finalized済み請求書を手動で再集計し、差額(アジャストメント)のみを保持する
    status='corrected'行を追加する。差額(小計・税・合計いずれも)が0なら何も作成せずNoneを返す。
    その月にfinalized行が無ければInvoiceNotFinalizedErrorを投げる（draftや開通直後は対象外）。
    running（現時点の確定金額）はfinalized行＋既存のcorrected行すべての合計。
    provisionable_devicesは「現在時点」のスナップショットのため再計算せず、
    finalized行が持っていた値をそのまま引き継ぐ（過去月の値を偽らないため）。"""
    existing = db.query(BillingInvoice).filter(
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.target_year_month == target_year_month,
    ).order_by(BillingInvoice.created_at).all()

    if not any(r.status == "finalized" for r in existing):
        raise InvoiceNotFinalizedError(
            f"No finalized invoice for tenant {tenant_id} in {target_year_month}"
        )

    running_subtotal = sum(r.subtotal for r in existing)
    running_tax = sum(r.tax_amount for r in existing)
    running_total = sum(r.total_amount for r in existing)

    running_items: dict[str, dict] = {}
    for r in existing:
        for li in db.query(BillingLineItem).filter(BillingLineItem.invoice_id == r.id).all():
            acc = running_items.setdefault(li.item_key, {"quantity": 0, "amount": 0})
            acc["quantity"] += li.quantity
            acc["amount"] += li.amount

    preserved_provisionable = running_items.get("provisionable_devices", {"quantity": 0})["quantity"]

    year, month = (int(p) for p in target_year_month.split("-"))
    usage = aggregate_monthly_usage(db, tenant_id, schema, influxdb_org_id, influxdb_token, year, month)
    usage["provisionable_devices"] = preserved_provisionable
    prices = get_effective_unit_prices(db, tenant_id, year, month)
    calc = calculate_invoice(usage, prices, get_tax_rate(db))

    delta_subtotal = calc["subtotal"] - running_subtotal
    delta_tax = calc["tax_amount"] - running_tax
    delta_total = calc["total_amount"] - running_total

    if delta_subtotal == 0 and delta_tax == 0 and delta_total == 0:
        return None

    invoice = BillingInvoice(
        tenant_id=tenant_id, target_year_month=target_year_month, status="corrected",
        subtotal=delta_subtotal, tax_amount=delta_tax, total_amount=delta_total,
        finalized_at=datetime.now(timezone.utc),
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    for li in calc["line_items"]:
        running = running_items.get(li["item_key"], {"quantity": 0, "amount": 0})
        db.add(BillingLineItem(
            invoice_id=invoice.id, item_key=li["item_key"],
            quantity=li["quantity"] - running["quantity"],
            unit_price=li["unit_price"],
            amount=li["amount"] - running["amount"],
        ))
    db.commit()

    return invoice


def _finalize_stale_drafts(
    db, tenant_id: str, schema: str, influxdb_org_id: str, influxdb_token: str,
    current_target_year_month: str,
) -> None:
    """対象月が変わったdraft請求書を、最後にもう一度再集計してからfinalizedに確定する。
    月末までのデータを取りこぼさないための最終確定パス。finalized遷移はこの1経路のみ。
    provisionable_devicesは「現在時点」のスナップショットのため再計算せず、
    そのdraftが最後に持っていた明細行の値をそのまま引き継ぐ（過去月の値を偽らないため）。"""
    stale = db.query(BillingInvoice).filter(
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.status == "draft",
        BillingInvoice.target_year_month < current_target_year_month,
    ).all()
    for invoice in stale:
        year, month = (int(p) for p in invoice.target_year_month.split("-"))

        existing_items = db.query(BillingLineItem).filter(
            BillingLineItem.invoice_id == invoice.id
        ).all()
        preserved_provisionable = next(
            (li.quantity for li in existing_items if li.item_key == "provisionable_devices"),
            0,
        )

        usage = aggregate_monthly_usage(db, tenant_id, schema, influxdb_org_id, influxdb_token, year, month)
        usage["provisionable_devices"] = preserved_provisionable
        prices = get_effective_unit_prices(db, tenant_id, year, month)
        calc = calculate_invoice(usage, prices, get_tax_rate(db))

        invoice.subtotal = calc["subtotal"]
        invoice.tax_amount = calc["tax_amount"]
        invoice.total_amount = calc["total_amount"]
        invoice.status = "finalized"
        invoice.finalized_at = datetime.now(timezone.utc)
        db.commit()

        _replace_line_items(db, invoice.id, calc["line_items"])


def _get_or_create_draft_invoice(db, tenant_id: str, target_year_month: str) -> BillingInvoice:
    invoice = db.query(BillingInvoice).filter(
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.target_year_month == target_year_month,
    ).first()
    if invoice is None:
        invoice = BillingInvoice(tenant_id=tenant_id, target_year_month=target_year_month, status="draft")
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
    return invoice


def _replace_line_items(db, invoice_id, line_items: list[dict]) -> None:
    db.query(BillingLineItem).filter(BillingLineItem.invoice_id == invoice_id).delete()
    for li in line_items:
        db.add(BillingLineItem(
            invoice_id=invoice_id, item_key=li["item_key"], quantity=li["quantity"],
            unit_price=li["unit_price"], amount=li["amount"],
        ))
    db.commit()


def run_monthly_billing_batch() -> list[dict]:
    """全アクティブテナントについて、対象月draft請求書を再集計する。
    対象月が変わったdraftは先にfinalizedへ確定する。戻り値は各テナントの処理結果。"""
    results: list[dict] = []
    year, month, target_year_month = _current_target_year_month()

    with SessionLocal() as db:
        tenants = db.query(Tenant).filter(Tenant.status == "active").all()
        tenant_infos = [
            (str(t.id), f"tenant_{str(t.id).lower().replace('-', '_')}", t.influxdb_org_id or "", t.influxdb_token or "")
            for t in tenants
        ]

    for tenant_id, schema, influxdb_org_id, influxdb_token in tenant_infos:
        try:
            with SessionLocal() as db:
                _backfill_missing_months(db, tenant_id, schema, influxdb_org_id, influxdb_token, target_year_month)
                _finalize_stale_drafts(db, tenant_id, schema, influxdb_org_id, influxdb_token, target_year_month)

                invoice = _get_or_create_draft_invoice(db, tenant_id, target_year_month)
                if invoice.status != "draft":
                    results.append({"tenant_id": tenant_id, "status": "skipped_not_draft"})
                    continue

                usage = aggregate_monthly_usage(db, tenant_id, schema, influxdb_org_id, influxdb_token, year, month)
                prices = get_effective_unit_prices(db, tenant_id, year, month)
                calc = calculate_invoice(usage, prices, get_tax_rate(db))

                invoice.subtotal = calc["subtotal"]
                invoice.tax_amount = calc["tax_amount"]
                invoice.total_amount = calc["total_amount"]
                db.commit()

                _replace_line_items(db, invoice.id, calc["line_items"])
            results.append({"tenant_id": tenant_id, "status": "ok", "total_amount": calc["total_amount"]})
        except Exception as e:
            results.append({"tenant_id": tenant_id, "status": "error", "detail": str(e)})

    return results


def start_billing_batch_worker() -> None:
    """毎日1回、月次請求書生成バッチを実行するバックグラウンドスレッドを起動する。"""
    def _loop() -> None:
        while True:
            try:
                results = run_monthly_billing_batch()
                for r in results:
                    if r.get("status") != "ok":
                        print(f"[billing_batch] tenant {r.get('tenant_id')}: {r}")
            except Exception as e:
                print(f"[billing_batch] run failed: {e}")
            time.sleep(86400)
    threading.Thread(target=_loop, daemon=True).start()

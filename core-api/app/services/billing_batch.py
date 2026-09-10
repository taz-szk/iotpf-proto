import threading
import time
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.public import Tenant
from app.models.billing import BillingInvoice, BillingLineItem
from app.services.billing import calculate_invoice, get_effective_unit_prices
from app.services.billing_usage import aggregate_monthly_usage


def _current_target_year_month() -> tuple[int, int, str]:
    now = datetime.now(timezone.utc)
    return now.year, now.month, f"{now.year:04d}-{now.month:02d}"


def _finalize_stale_drafts(db, tenant_id: str, current_target_year_month: str) -> None:
    """対象月が変わったdraft請求書をfinalizedに確定する。finalized遷移はこの1経路のみ。"""
    stale = db.query(BillingInvoice).filter(
        BillingInvoice.tenant_id == tenant_id,
        BillingInvoice.status == "draft",
        BillingInvoice.target_year_month != current_target_year_month,
    ).all()
    for invoice in stale:
        invoice.status = "finalized"
        invoice.finalized_at = datetime.now(timezone.utc)
    if stale:
        db.commit()


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
            (str(t.id), f"tenant_{str(t.id).replace('-', '_')}", t.influxdb_org_id or "", t.influxdb_token or "")
            for t in tenants
        ]

    for tenant_id, schema, influxdb_org_id, influxdb_token in tenant_infos:
        try:
            with SessionLocal() as db:
                _finalize_stale_drafts(db, tenant_id, target_year_month)

                invoice = _get_or_create_draft_invoice(db, tenant_id, target_year_month)
                if invoice.status != "draft":
                    results.append({"tenant_id": tenant_id, "status": "skipped_not_draft"})
                    continue

                usage = aggregate_monthly_usage(db, tenant_id, schema, influxdb_org_id, influxdb_token, year, month)
                prices = get_effective_unit_prices(db, tenant_id, year, month)
                calc = calculate_invoice(usage, prices)

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
                run_monthly_billing_batch()
            except Exception as e:
                print(f"[billing_batch] run failed: {e}")
            time.sleep(86400)
    threading.Thread(target=_loop, daemon=True).start()

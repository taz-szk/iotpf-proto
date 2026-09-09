from app.config import settings
from app.db import (
    get_all_tenants, get_active_alert_rules, get_group_device_ids,
    get_unresolved_event, create_alert_event, resolve_alert_event, mark_event_notified,
    get_offline_devices, mark_device_offline,
)
from app.evaluator import evaluate_rule
from app.notifier import send_alert_email

def evaluate_all_tenants() -> None:
    try:
        tenants = get_all_tenants()
    except Exception as e:
        print(f"Failed to get tenants: {e}")
        return
    for tenant in tenants:
        tenant_id = tenant["id"]
        org_id = tenant["influxdb_org_id"]
        token = tenant.get("influxdb_token") or ""
        if not org_id or not token:
            continue
        try:
            _evaluate_tenant(tenant_id, org_id, token)
            _check_dead_devices(tenant_id)
        except Exception as e:
            print(f"Error processing tenant {tenant_id}: {e}")

def _make_group_device_resolver(tenant_id: str):
    cache: dict[str, list[str]] = {}

    def resolve(group_id: str) -> list[str]:
        if group_id not in cache:
            try:
                cache[group_id] = get_group_device_ids(tenant_id, group_id)
            except Exception as e:
                print(f"Failed to resolve group {group_id} devices: {e}")
                cache[group_id] = []
        return cache[group_id]

    return resolve

def _evaluate_tenant(tenant_id: str, org_id: str, token: str) -> None:
    rules = get_active_alert_rules(tenant_id)
    resolve_group = _make_group_device_resolver(tenant_id)
    for rule in rules:
        rule_id = rule["id"]
        device_id = rule.get("device_id")
        group_id = rule.get("group_id")
        if rule["condition"] == "device_offline":
            continue
        try:
            group_device_ids = resolve_group(group_id) if group_id else None
            should_alert, last_value = evaluate_rule(rule, org_id, token, group_device_ids)
        except Exception as e:
            print(f"Eval error rule {rule_id}: {e}")
            continue

        existing = get_unresolved_event(tenant_id, rule_id, device_id)

        if should_alert and not existing:
            event_id = create_alert_event(tenant_id, rule_id, device_id, last_value)
            send_alert_email(
                to_emails=list(rule["notify_emails"] or []),
                tenant_id=tenant_id, device_id=device_id,
                sensor_key=rule["sensor_key"], condition=rule["condition"],
                threshold=float(rule["threshold"]) if rule["threshold"] else None,
                current_value=last_value, severity=rule["severity"],
            )
            mark_event_notified(tenant_id, event_id)
        elif not should_alert and existing:
            resolve_alert_event(tenant_id, existing["id"])
            send_alert_email(
                to_emails=list(rule["notify_emails"] or []),
                tenant_id=tenant_id, device_id=device_id,
                sensor_key=rule["sensor_key"], condition=rule["condition"],
                threshold=float(rule["threshold"]) if rule["threshold"] else None,
                current_value=last_value, severity=rule["severity"], resolved=True,
            )

def _check_dead_devices(tenant_id: str) -> None:
    offline_devices = get_offline_devices(tenant_id, settings.device_offline_threshold_sec)
    if not offline_devices:
        return
    rules = get_active_alert_rules(tenant_id)
    resolve_group = _make_group_device_resolver(tenant_id)
    for row in offline_devices:
        device_id = row["device_id"]
        mark_device_offline(tenant_id, device_id)
        for rule in rules:
            if rule["condition"] != "device_offline":
                continue
            if rule.get("device_id") and rule["device_id"] != device_id:
                continue
            if rule.get("group_id") and device_id not in resolve_group(rule["group_id"]):
                continue
            existing = get_unresolved_event(tenant_id, rule["id"], device_id)
            if existing:
                continue
            event_id = create_alert_event(tenant_id, rule["id"], device_id, None)
            send_alert_email(
                to_emails=list(rule["notify_emails"] or []),
                tenant_id=tenant_id, device_id=device_id,
                sensor_key="device", condition="device_offline",
                threshold=None, current_value=None, severity=rule["severity"],
            )
            mark_event_notified(tenant_id, event_id)

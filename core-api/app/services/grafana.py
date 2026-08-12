import httpx
import secrets
from app.config import settings

_FLUX_TELEMETRY = (
    'from(bucket: "telemetry")\n'
    '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
    '  |> filter(fn: (r) => r._measurement == "telemetry")\n'
    '  |> filter(fn: (r) => r.device_name =~ /^${device_name:regex}$/)\n'
    '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n'
    '  |> yield(name: "mean")'
)

_FLUX_DEVICE_VAR = (
    'import "influxdata/influxdb/schema"\n'
    'schema.tagValues(bucket: "telemetry", tag: "device_name", start: -30d)'
)

_FLUX_DELETED = (
    'from(bucket: "telemetry")\n'
    '  |> range(start: 0)\n'
    '  |> filter(fn: (r) => r._measurement == "device_deleted")\n'
    '  |> filter(fn: (r) => r.device_name =~ /^${device_name:regex}$/)\n'
    '  |> last()'
)

_DEFAULT_DASHBOARD = {
    "dashboard": {
        "title": "テレメトリ監視",
        "templating": {
            "list": [
                {
                    "name": "device_name",
                    "label": "デバイス",
                    "type": "query",
                    "multi": True,
                    "includeAll": True,
                    "allValue": ".*",
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "query": {
                        "query": _FLUX_DEVICE_VAR,
                        "refId": "StandardVariableQuery",
                    },
                    "datasource": {"type": "influxdb"},
                    "refresh": 2,
                    "sort": 1,
                }
            ]
        },
        "panels": [
            # Row — device_name でリピート。この行より後にあるパネルが一緒にリピートされる
            {
                "id": 1,
                "type": "row",
                "title": "${device_name}",
                "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1},
                "repeat": "device_name",
                "repeatDirection": "v",
                "collapsed": False,
            },
            # 削除済みステータス Stat パネル
            {
                "id": 2,
                "type": "stat",
                "title": "状態",
                "gridPos": {"x": 0, "y": 1, "w": 3, "h": 4},
                "targets": [
                    {
                        "refId": "A",
                        "datasource": {"type": "influxdb"},
                        "query": _FLUX_DELETED,
                    }
                ],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "orientation": "auto",
                    "textMode": "auto",
                    "colorMode": "background",
                    "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "noValue": "稼働中",
                        "mappings": [
                            {
                                "type": "value",
                                "options": {
                                    "1": {"text": "削除済み", "index": 0},
                                },
                            }
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"value": None, "color": "green"},
                                {"value": 1, "color": "red"},
                            ],
                        },
                        "color": {"mode": "thresholds"},
                    }
                },
            },
            # テレメトリ Timeseries パネル
            {
                "id": 3,
                "type": "timeseries",
                "title": "テレメトリ",
                "gridPos": {"x": 3, "y": 1, "w": 21, "h": 8},
                "targets": [
                    {
                        "refId": "A",
                        "datasource": {"type": "influxdb"},
                        "query": _FLUX_TELEMETRY,
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "custom": {"lineWidth": 2},
                        "displayName": "${__field.name}",
                    },
                },
                "options": {
                    "tooltip": {"mode": "multi"},
                    "legend": {"displayMode": "list", "placement": "bottom"},
                },
            },
        ],
        "time": {"from": "now-1h", "to": "now"},
        "refresh": "30s",
        "schemaVersion": 38,
        "version": 0,
        "editable": False,
    },
    "overwrite": True,
}


def mark_device_deleted(influxdb_org_id: str, device_name: str) -> None:
    """デバイス削除マーカーを InfluxDB に書き込む。Grafana の削除済み表示に使用。"""
    import time
    # device_name に特殊文字が含まれる場合は line protocol エスケープが必要
    escaped = device_name.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")
    line = f'device_deleted,device_name={escaped} deleted=1i {int(time.time())}000000000'
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/write"
            f"?orgID={influxdb_org_id}&bucket=telemetry&precision=ns",
            headers={
                "Authorization": f"Token {settings.influxdb_admin_token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            content=line.encode(),
            timeout=5.0,
        )
        resp.raise_for_status()
        print(f"[mark_device_deleted] OK: {device_name} org={influxdb_org_id}")
    except Exception as e:
        print(f"[mark_device_deleted] FAILED: {device_name} org={influxdb_org_id} err={e}")


def retire_device_in_influxdb(influxdb_org_id: str, device_name: str) -> None:
    """デバイスを引退させる。
    InfluxDB 上の全データを Del_{device_name} にリネームし元データを削除する。
    再登録時に同じ device_id で新規スタートできるようにするため。
    """
    import time
    new_name = f"Del_{device_name}"

    # Flux 文字列リテラル用エスケープ
    esc_old = device_name.replace("\\", "\\\\").replace('"', '\\"')
    esc_new = new_name.replace("\\", "\\\\").replace('"', '\\"')

    # 1. 全データを新しい device_name でコピー（Flux to() 経由）
    copy_flux = (
        'from(bucket: "telemetry")\n'
        '  |> range(start: 0)\n'
        f'  |> filter(fn: (r) => r.device_name == "{esc_old}")\n'
        f'  |> map(fn: (r) => ({{r with device_name: "{esc_new}"}}))\n'
        f'  |> to(bucket: "telemetry", orgID: "{influxdb_org_id}")'
    )
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/query?orgID={influxdb_org_id}",
            headers={
                "Authorization": f"Token {settings.influxdb_admin_token}",
                "Content-Type": "application/json",
                "Accept": "application/csv",
            },
            json={"query": copy_flux, "type": "flux"},
            timeout=60.0,
        )
        resp.raise_for_status()
        print(f"[retire_device] copied: {device_name} -> {new_name}")
    except Exception as e:
        print(f"[retire_device] copy FAILED: {device_name} err={e}")

    # 2. 削除済みマーカーを新しい名前で書き込む
    esc_lp = new_name.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")
    line = f'device_deleted,device_name={esc_lp} deleted=1i {int(time.time())}000000000'
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/write"
            f"?orgID={influxdb_org_id}&bucket=telemetry&precision=ns",
            headers={
                "Authorization": f"Token {settings.influxdb_admin_token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            content=line.encode(),
            timeout=5.0,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[retire_device] write marker FAILED: {new_name} err={e}")

    # 3. 元の device_name のデータを削除
    esc_pred = device_name.replace("\\", "\\\\").replace('"', '\\"')
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/delete"
            f"?orgID={influxdb_org_id}&bucket=telemetry",
            headers={
                "Authorization": f"Token {settings.influxdb_admin_token}",
                "Content-Type": "application/json",
            },
            json={
                "start": "1970-01-01T00:00:00Z",
                "stop": "2099-12-31T00:00:00Z",
                "predicate": f'device_name="{esc_pred}"',
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        print(f"[retire_device] deleted old data: {device_name}")
    except Exception as e:
        print(f"[retire_device] delete FAILED: {device_name} err={e}")

def _admin_auth() -> tuple[str, str]:
    return (settings.grafana_admin_user, settings.grafana_admin_password)

def create_grafana_org(name: str) -> int:
    resp = httpx.post(
        f"{settings.grafana_url}/api/orgs",
        auth=_admin_auth(),
        json={"name": name},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()["orgId"]

def setup_grafana_datasource(org_id: int, tenant_name: str, influxdb_org_id: str, influxdb_token: str) -> None:
    # influxdb_org_id は内部 ID。Grafana Flux データソースは org 名が必要なので
    # InfluxDB API で org 名を取得する
    influxdb_org_name = _get_influxdb_org_name(influxdb_org_id) or tenant_name
    resp = httpx.post(
        f"{settings.grafana_url}/api/datasources",
        auth=_admin_auth(),
        headers={"X-Grafana-Org-Id": str(org_id)},
        json={
            "name": f"InfluxDB-{tenant_name}",
            "type": "influxdb",
            "url": "http://influxdb:8086",
            "access": "proxy",
            "isDefault": True,
            "jsonData": {
                "version": "Flux",
                "organization": influxdb_org_name,
                "defaultBucket": "telemetry",
            },
            "secureJsonData": {"token": influxdb_token},
        },
        timeout=10.0,
    )
    resp.raise_for_status()


def _get_influxdb_org_name(org_id: str) -> str | None:
    try:
        resp = httpx.get(
            f"{settings.influxdb_url}/api/v2/orgs/{org_id}",
            headers={"Authorization": f"Token {settings.influxdb_admin_token}"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json().get("name")
    except Exception:
        pass
    return None

def create_default_dashboard(org_id: int, tenant_name: str) -> str:
    """ダッシュボードを作成し、org のホームに設定する。ダッシュボード UID を返す。"""
    dashboard = dict(_DEFAULT_DASHBOARD)
    dashboard["dashboard"] = dict(dashboard["dashboard"])
    dashboard["dashboard"]["title"] = f"テレメトリ監視 - {tenant_name}"
    resp = httpx.post(
        f"{settings.grafana_url}/api/dashboards/db",
        auth=_admin_auth(),
        headers={"X-Grafana-Org-Id": str(org_id)},
        json=dashboard,
        timeout=10.0,
    )
    resp.raise_for_status()
    uid = resp.json()["uid"]
    # org のホームダッシュボードに設定 — ログイン後 /grafana/?orgId=N で直接着地
    httpx.put(
        f"{settings.grafana_url}/api/org/preferences",
        auth=_admin_auth(),
        headers={"X-Grafana-Org-Id": str(org_id)},
        json={"homeDashboardUID": uid},
        timeout=10.0,
    ).raise_for_status()
    return uid

def ensure_grafana_user_in_org(org_id: int, email: str, grafana_role: str, tenant_id_str: str) -> None:
    """Grafana org にユーザーを追加する。存在しなければ作成する。"""
    login = f"{tenant_id_str}:{email}"
    # 1. ユーザー存在確認（テナント名前空間付きloginで検索）
    lookup = httpx.get(
        f"{settings.grafana_url}/api/users/lookup",
        params={"loginOrEmail": login},
        auth=_admin_auth(), timeout=10.0,
    )
    if lookup.status_code == 404:
        # 2. ユーザー作成（パスワードは使わない — Auth Proxy が認証するため）
        # email フィールドには login（テナント名前空間付き）を使う。
        # Grafana は email をグローバル一意制約で管理するため、実際のメール
        # アドレスをそのまま使うと他テナントで同一アドレスが登録済みの場合 500 になる。
        create = httpx.post(
            f"{settings.grafana_url}/api/admin/users",
            auth=_admin_auth(),
            json={"name": email, "email": login, "login": login,
                  "password": secrets.token_hex(16)},
            timeout=10.0,
        )
        create.raise_for_status()
    else:
        lookup.raise_for_status()

    # 3. org に追加（409 = すでにメンバー → 無視。ロールの更新は別途 PATCH が必要だが
    #    現時点でロール変更エンドポイントがないため未実装）
    add = httpx.post(
        f"{settings.grafana_url}/api/orgs/{org_id}/users",
        auth=_admin_auth(),
        json={"loginOrEmail": login, "role": grafana_role},
        timeout=10.0,
    )
    if add.status_code not in (200, 409):
        add.raise_for_status()

def add_user_to_grafana_org(org_id: int, login_or_email: str, role: str = "Admin") -> None:
    """ユーザーを Grafana org に追加する（409 = 既存メンバー → 無視）"""
    resp = httpx.post(
        f"{settings.grafana_url}/api/orgs/{org_id}/users",
        auth=_admin_auth(),
        json={"loginOrEmail": login_or_email, "role": role},
        timeout=10.0,
    )
    if resp.status_code not in (200, 409):
        resp.raise_for_status()


def get_org_home_dashboard_url(org_id: int) -> str | None:
    """org のホームダッシュボード URL を返す。未設定なら None。"""
    prefs = httpx.get(
        f"{settings.grafana_url}/api/org/preferences",
        auth=_admin_auth(),
        headers={"X-Grafana-Org-Id": str(org_id)},
        timeout=10.0,
    )
    prefs.raise_for_status()
    uid = prefs.json().get("homeDashboardUID")
    if not uid:
        return None
    dash = httpx.get(
        f"{settings.grafana_url}/api/dashboards/uid/{uid}",
        auth=_admin_auth(),
        headers={"X-Grafana-Org-Id": str(org_id)},
        timeout=10.0,
    )
    if dash.status_code != 200:
        return None
    url = dash.json().get("meta", {}).get("url", "")
    return url if url else None


def set_user_default_org_via_proxy(email: str, org_id: int) -> None:
    """Auth Proxy ヘッダーを使ってユーザーのデフォルト org を変更する。
    GF_AUTH_PROXY_WHITELIST に core-api のネットワーク (grafana-net) が含まれる前提。
    """
    try:
        httpx.post(
            f"{settings.grafana_url}/api/user/using/{org_id}",
            headers={"X-WEBAUTH-USER": email, "X-WEBAUTH-EMAIL": email},
            timeout=5.0,
        )
    except Exception as e:
        print(f"[set_user_default_org] failed for {email}: {e}")


def ensure_platform_admin_in_grafana(email: str) -> None:
    """プラットフォーム管理者を Grafana に server admin として登録する。
    Auth Proxy は login = email で検索するため、同名ユーザーを先に作成して権限を付与する。
    ユーザーが既に server admin であれば何もしない。"""
    lookup = httpx.get(
        f"{settings.grafana_url}/api/users/lookup",
        params={"loginOrEmail": email},
        auth=_admin_auth(), timeout=10.0,
    )
    if lookup.status_code == 404:
        create = httpx.post(
            f"{settings.grafana_url}/api/admin/users",
            auth=_admin_auth(),
            json={"name": email, "email": email, "login": email,
                  "password": secrets.token_hex(16)},
            timeout=10.0,
        )
        create.raise_for_status()
        user_id = create.json()["id"]
        is_admin = False
    else:
        lookup.raise_for_status()
        user_id = lookup.json()["id"]
        is_admin = lookup.json().get("isGrafanaAdmin", False)
    if not is_admin:
        httpx.put(
            f"{settings.grafana_url}/api/admin/users/{user_id}/permissions",
            auth=_admin_auth(),
            json={"isGrafanaAdmin": True},
            timeout=10.0,
        ).raise_for_status()


def provision_tenant_grafana(tenant_name: str, influxdb_org_id: str, influxdb_token: str, org_name: str | None = None) -> int:
    """テナント用Grafana Orgを作成しDataSource・ダッシュボードを設定する。Org IDを返す。
    org_name: InfluxDB org 名（省略時は tenant_name）。tenant_id UUID を渡して InfluxDB 側の一意性を確保する。
              Grafana org 名は常に tenant_name（表示名）を使用する。
    """
    # Grafana org は tenant_name（表示名）で作成 — UUID は表示が崩れるため使わない
    org_id = create_grafana_org(tenant_name)
    setup_grafana_datasource(org_id, tenant_name, influxdb_org_id, influxdb_token)
    create_default_dashboard(org_id, tenant_name)

    # プラットフォーム管理 org にも当テナントのデータソースを追加する
    try:
        platform_org_id = get_or_create_platform_org()
        # InfluxDB org 名は org_name (= tenant_id UUID) で InfluxDB の一意性を保つ
        add_tenant_datasource_to_platform_org(platform_org_id, tenant_name, org_name or tenant_name)
    except Exception as e:
        print(f"[provision] platform org datasource add failed: {e}")

    return org_id


# ─── プラットフォーム管理 Grafana org ─────────────────────────────────────────

_PLATFORM_ORG_NAME = "platform-admin"

# Grafana 13 対応: datasource 変数 (${ds}) でテナントを選択するクロステナントダッシュボード
# - pluginId フィールドを追加（Grafana 10+ 必須）
# - パネルレベルにも datasource を設定（Grafana 13 での変数解決に必要）
_DS_REF = {"type": "influxdb", "uid": "${ds}"}

_PLATFORM_DASHBOARD = {
    "dashboard": {
        "title": "プラットフォーム全テナント監視",
        "templating": {
            "list": [
                {
                    "name": "ds",
                    "label": "テナント",
                    "type": "datasource",
                    "pluginId": "influxdb",
                    "query": "influxdb",
                    "multi": False,
                    "includeAll": False,
                    "refresh": 1,
                    "sort": 1,
                    "hide": 0,
                    "options": [],
                    "current": {},
                },
                {
                    "name": "device_name",
                    "label": "デバイス",
                    "type": "query",
                    "multi": True,
                    "includeAll": True,
                    "allValue": ".*",
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "query": {"query": _FLUX_DEVICE_VAR, "refId": "StandardVariableQuery"},
                    "datasource": _DS_REF,
                    "refresh": 2,
                    "sort": 1,
                    "hide": 0,
                    "options": [],
                },
            ]
        },
        "panels": [
            {
                "id": 1, "type": "row", "title": "${device_name}",
                "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1},
                "repeat": "device_name", "repeatDirection": "v", "collapsed": False,
                "datasource": _DS_REF,
            },
            {
                "id": 2, "type": "stat", "title": "状態",
                "gridPos": {"x": 0, "y": 1, "w": 3, "h": 4},
                "datasource": _DS_REF,
                "targets": [{"refId": "A", "datasource": _DS_REF, "query": _FLUX_DELETED}],
                "options": {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "orientation": "auto", "textMode": "auto",
                    "colorMode": "background", "graphMode": "none",
                },
                "fieldConfig": {
                    "defaults": {
                        "noValue": "稼働中",
                        "mappings": [{"type": "value", "options": {"1": {"text": "削除済み", "index": 0}}}],
                        "thresholds": {"mode": "absolute", "steps": [
                            {"value": None, "color": "green"}, {"value": 1, "color": "red"}]},
                        "color": {"mode": "thresholds"},
                    }
                },
            },
            {
                "id": 3, "type": "timeseries", "title": "テレメトリ",
                "gridPos": {"x": 3, "y": 1, "w": 21, "h": 8},
                "datasource": _DS_REF,
                "targets": [{"refId": "A", "datasource": _DS_REF, "query": _FLUX_TELEMETRY}],
                "fieldConfig": {
                    "defaults": {
                        "custom": {"lineWidth": 2},
                        "displayName": "${__field.name}",
                    }
                },
                "options": {
                    "tooltip": {"mode": "multi"},
                    "legend": {"displayMode": "list", "placement": "bottom"},
                },
            },
        ],
        "time": {"from": "now-1h", "to": "now"},
        "refresh": "30s",
        "schemaVersion": 40,
        "version": 0,
        "editable": True,
    },
    "overwrite": True,
}


def get_or_create_platform_org() -> int:
    """プラットフォーム管理 Grafana org を取得または作成し org_id を返す。"""
    resp = httpx.get(
        f"{settings.grafana_url}/api/orgs/name/{_PLATFORM_ORG_NAME}",
        auth=_admin_auth(), timeout=10.0,
    )
    if resp.status_code == 200:
        return resp.json()["id"]

    # 存在しなければ作成してダッシュボードを初期化
    org_id = create_grafana_org(_PLATFORM_ORG_NAME)
    try:
        import copy
        dashboard = copy.deepcopy(_PLATFORM_DASHBOARD)
        r = httpx.post(
            f"{settings.grafana_url}/api/dashboards/db",
            auth=_admin_auth(),
            headers={"X-Grafana-Org-Id": str(org_id)},
            json=dashboard, timeout=10.0,
        )
        r.raise_for_status()
        uid = r.json()["uid"]
        httpx.put(
            f"{settings.grafana_url}/api/org/preferences",
            auth=_admin_auth(),
            headers={"X-Grafana-Org-Id": str(org_id)},
            json={"homeDashboardUID": uid}, timeout=10.0,
        ).raise_for_status()
    except Exception as e:
        print(f"[platform_org] dashboard init failed: {e}")

    return org_id


def add_tenant_datasource_to_platform_org(platform_org_id: int, tenant_name: str, influxdb_org_name: str) -> None:
    """プラットフォーム管理 org にテナントのデータソースを追加する。
    influxdb_org_name は InfluxDB org 名（= tenant_id UUID）。
    管理者トークンを使用して全テナントにクロスアクセスする。

    Grafana の secureJsonData（token）を保存するには admin ユーザーが
    対象 org に属している必要があるため、一時的に org を切り替える。
    """
    auth = _admin_auth()
    # admin を platform org に切り替え（secureJsonData 保存に必要）
    httpx.post(f"{settings.grafana_url}/api/user/using/{platform_org_id}", auth=auth, timeout=5.0)
    try:
        resp = httpx.post(
            f"{settings.grafana_url}/api/datasources",
            auth=auth,
            json={
                "name": f"InfluxDB-{tenant_name}",
                "type": "influxdb",
                "url": "http://influxdb:8086",
                "access": "proxy",
                "isDefault": False,
                "jsonData": {
                    "version": "Flux",
                    "organization": influxdb_org_name,
                    "defaultBucket": "telemetry",
                },
                "secureJsonData": {"token": settings.influxdb_admin_token},
            },
            timeout=10.0,
        )
        if resp.status_code not in (200, 201, 409):
            resp.raise_for_status()
    finally:
        # admin を Main Org に戻す
        httpx.post(f"{settings.grafana_url}/api/user/using/1", auth=auth, timeout=5.0)


def sync_all_tenants_to_platform_org(platform_org_id: int, tenants: list[dict]) -> None:
    """全テナントのデータソースを platform-admin org に同期する。
    tenants: [{"name": str, "influxdb_org_id": str}, ...]
    既存エントリは 409 で無視されるため冪等に動作する。
    """
    for t in tenants:
        try:
            # InfluxDB org 名を実際に問い合わせる（新旧どちらの命名でも対応）
            influxdb_org_name = _get_influxdb_org_name(t["influxdb_org_id"]) or t.get("tenant_id", t["name"])
            add_tenant_datasource_to_platform_org(platform_org_id, t["name"], influxdb_org_name)
        except Exception as e:
            print(f"[sync] tenant '{t['name']}' datasource sync failed: {e}")


def remove_tenant_datasource_from_platform_org(platform_org_id: int, influxdb_org_name: str) -> None:
    """プラットフォーム管理 org からテナントのデータソースを削除する。
    influxdb_org_name (= tenant_id UUID) で一致するデータソースを探して削除する。
    """
    try:
        list_resp = httpx.get(
            f"{settings.grafana_url}/api/datasources",
            auth=_admin_auth(),
            headers={"X-Grafana-Org-Id": str(platform_org_id)},
            timeout=10.0,
        )
        if list_resp.status_code != 200:
            return
        for ds in list_resp.json():
            if (ds.get("jsonData") or {}).get("organization") == influxdb_org_name:
                httpx.delete(
                    f"{settings.grafana_url}/api/datasources/{ds['id']}",
                    auth=_admin_auth(),
                    headers={"X-Grafana-Org-Id": str(platform_org_id)},
                    timeout=10.0,
                )
                break
    except Exception as e:
        print(f"[platform_org] datasource remove failed: {e}")

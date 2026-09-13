# テナント管理者向けAIアシスタント展開 設計仕様書

**Goal:** Phase 1でPF管理者限定として実装したAIアシスタント機能（RAG+エージェント）を、テナント管理者（admin/operatorロール）にも展開する。自テナントのスコープ内でのドキュメントQ&A・確認フロー付き操作代行（プロビジョニングトークン発行・ダッシュボード設定・アラートルール作成）を提供する。

**背景:** [[project_rag_assistant_feature]]でPF管理者向けPhase 1を実装・AWS実機デプロイまで完了（2026-09-13）。その直後、ユーザーから「テナント管理者向けにも展開したい」との要望が出た。UIについても「タブ選択方式ではなく常時表示のフローティングウィジェット＋全画面展開」という新しい方向性の要望が出ている。

**参照:**
- `docs/superpowers/specs/2026-09-12-rag-assistant-design.md`（Phase 1・PF管理者向けの元設計）
- `core-api/app/routers/tenant_portal.py`（テナント自己サービスAPIの既存実装スタイル）
- `core-api/app/services/rag.py`・`app/services/rag_tools/`（Phase 1で実装済みの共通ロジック）

**スコープ外（本機能ではやらない。将来検討）:**
- ドキュメント再インデックス機能のテナント向け提供（PF管理者専用のまま）
- テナント向け専用のドキュメントインデックス分離（PF管理者向けと同じ`doc_chunks`を共有）
- PF管理者向け既存UI（`platform-ui/tenant.html`のタブ形式）のフローティングウィジェット化（今回はテナント向けのみ）
- ウィジェット開閉状態のlocalStorage等での永続化

---

## 1. 決定事項（ユーザーとのQ&Aで確定）

- **対象ロール:** テナントの`admin`・`operator`両ロール（既存の`_require_admin_or_operator`と同じ基準）。
- **操作代行ツールのラップ先:** PF管理者向けAPI（`provisioning_tokens.py`等）ではなく、**テナント自己サービスAPI**（`tenant_portal.py`の`create_token`/`create_alert_rule`/`put_panel_configs`）を直接ラップする。実際のテナントJWTペイロードをそのまま使うため、合成payloadは不要（PF管理者向けダッシュボードツールで使っていた合成payload方式より安全）。
- **APIエンドポイント構成:** `tenant_portal.py`に専用の新規エンドポイント（`/me/assistant/chat`・`/me/assistant/confirm-action`）を追加する。既存のPF管理者向け`/tenants/{tenant_id}/assistant/*`（`app/routers/rag.py`）は一切変更しない。
- **ナレッジベース:** PF管理者向けと同じ`doc_chunks`インデックスをそのまま共有する（YAGNI、公開情報のため機密性の懸念なし）。
- **UI適用範囲:** 今回は`admin-ui/tenant-portal.html`のみに新UI（フローティングウィジェット＋全画面展開）を導入する。PF管理者向け`platform-ui/tenant.html`の既存タブUIは変更しない。
- **ペイロード伝搬:** `answer_question`/`execute_pending_action`に`payload`（認証済みテナントJWTペイロード）を新規オプション引数として追加する。PF管理者フローでは`payload=None`のままハンドラへは一切渡さない（後述の理由により、PF管理者向けツールファイルには変更を加えない）。

---

## 2. 全体アーキテクチャ

```
[PF管理者]                          [テナント管理者(admin/operator)]
platform-ui/tenant.html             admin-ui/tenant-portal.html
  ↓ Platform JWT                      ↓ Tenant JWT(Cookie)
app/routers/rag.py（無変更）        app/routers/tenant_portal.py（新規エンドポイント追加）
  /tenants/{id}/assistant/*           /me/assistant/chat
                                       /me/assistant/confirm-action
  ↓                                    ↓
app/services/rag.py（共通・拡張）
  answer_question(db, tenant_id, message, requested_by, tools=TOOLS, payload=None)
  execute_pending_action(db, tenant_id, pending_action_id, tools=TOOLS, payload=None)
  ↓ toolsパラメータでレジストリを切替
  ↓
TOOLS（既存、PF管理者向け・無変更）   TENANT_TOOLS（新規）
  provisioning.py / dashboard.py /     rag_tools/tenant/provisioning.py
  alerts.py / general.py               rag_tools/tenant/dashboard.py
                                        rag_tools/tenant/alerts.py
                                        （tenant_list相当は含めない）
```

`doc_chunks`（ドキュメント検索）・`agent_pending_actions`（確認待ちアクション）は両フローで共通のテーブルをそのまま使う。テナント越境防止は既存通り`tenant_id`条件で担保する。

---

## 3. ツールレジストリ設計

`app/services/rag_tools/tenant/`ディレクトリを新設し、PF管理者向け（`app/services/rag_tools/`直下）と対称的な構造にする。

```python
# app/services/rag_tools/tenant/__init__.py
from app.services.rag_tools import AgentTool  # データクラスは共通流用

from . import alerts, dashboard, provisioning

TENANT_TOOLS: list[AgentTool] = [
    *provisioning.TOOLS,
    *dashboard.TOOLS,
    *alerts.TOOLS,
]
# 注意: tenant_list相当（全テナント一覧）は絶対に追加しないこと。
# テナント自身が他テナントの情報を取得できてしまう越境漏洩になる。
```

各ツールハンドラは`(tenant_id: str, payload: dict, **kwargs) -> dict`という統一シグネチャとし、`payload`（実際のテナントJWTペイロード）をそのまま`tenant_portal.py`の関数に渡す。

```python
# app/services/rag_tools/tenant/provisioning.py
from app.routers.tenant_portal import TokenCreate, create_token, list_tokens
from app.services.rag_tools import AgentTool


def issue_provisioning_token(tenant_id: str, payload: dict, max_devices: int | None = None, expires_days: int | None = None) -> dict:
    # TokenCreateはNoneを明示的に渡すと番兵値(無制限)を踏み抜く（PF管理者向けC-2と同じ罠）。
    # 省略時はフィールド自体を渡さず、TokenCreate自身の既定値(100台/365日)を使う。
    body_kwargs: dict = {}
    if max_devices is not None:
        body_kwargs["max_devices"] = max_devices
    if expires_days is not None:
        body_kwargs["expires_days"] = expires_days
    return create_token(body=TokenCreate(**body_kwargs), payload=payload)


def list_my_provisioning_tokens(tenant_id: str, payload: dict) -> list[dict]:
    return list_tokens(payload=payload)  # tenant_portal.pyの既存関数


TOOLS = [
    AgentTool(
        name="tenant_provisioning_token_issue",
        description="自テナントの新しいプロビジョニングトークンを発行する。",
        input_schema={
            "type": "object",
            "properties": {
                "max_devices": {"type": "integer", "description": "登録可能な最大デバイス数（省略時は100）"},
                "expires_days": {"type": "integer", "description": "有効期限（日数、省略時は365日）"},
            },
        },
        read_only=False,
        handler=issue_provisioning_token,
    ),
    AgentTool(
        name="tenant_provisioning_token_list",
        description="自テナントの発行済みプロビジョニングトークン一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_my_provisioning_tokens,
    ),
]
```

`dashboard.py`は`tenant_portal.py`の`get_panel_configs`/`put_panel_configs`（既に実JWTペイロードベース）を、`alerts.py`は`tenant_portal.py`の`create_alert_rule`/`list_alert_rules`（テナント自己サービス版）をラップする。いずれもツール名にPF管理者向けと区別するため`tenant_`プレフィックスを付ける。

**ツール名の命名規則（拡張):** `tenant_<ドメイン>_<動詞>`（例: `tenant_alert_rule_create`）。PF管理者向けの`<ドメイン>_<動詞>`と衝突しないようにする。

---

## 4. `app/services/rag.py`の拡張

`answer_question`/`execute_pending_action`に`tools`（デフォルト`TOOLS`）・`payload`（デフォルト`None`）を追加する。

```python
def answer_question(db, tenant_id, message, requested_by, tools=None, payload=None) -> dict:
    tools = list(tools if tools is not None else TOOLS)
    ...
    for _ in range(_MAX_TOOL_ROUNDS):
        ...
        if tool.read_only:
            handler_kwargs = {"tenant_id": tenant_id, **tool_args}
            if payload is not None:
                handler_kwargs["payload"] = payload
            try:
                result = tool.handler(**handler_kwargs)
            except Exception as e:
                ...
```

- `payload`が`None`（PF管理者フロー）の場合、ハンドラ呼び出しに`payload`キーワード自体を含めない。**PF管理者向けの既存ツールファイル（`provisioning.py`等）は一切変更不要**——「使われない引数」という将来の混乱の種を生まない。
- テナント向けツールは`payload`を必須引数（デフォルト値なし）にする。呼び出し漏れがあれば`TypeError: missing required argument`で即座に気づける（`None`のまま内部に渡って分かりにくいエラーになるより安全）。
- `execute_pending_action`も同様の`tools`/`payload`拡張を行う。
- `_sanitize_tool_args`は、`tenant_id`に加えて`payload`キーもモデル由来の`tool_args`から除去するよう拡張する（ツールのJSON Schemaに`payload`プロパティは定義しないため通常発生しないが、多重キーワード引数エラーすら起きないよう事前に防御する）。

---

## 5. API設計

`app/routers/tenant_portal.py`に新規エンドポイントを追加する（既存の`/me/tokens`等と同じ自己サービス系）。

```
POST /me/assistant/chat            ← { "message": "..." }
POST /me/assistant/confirm-action  ← { "pending_action_id": "..." }
```

- 認証は`_require_admin_or_operator`（Tenant JWT Cookie）。
- `tenant_id`は`payload["tenant_id"]`から取得（既存の自己サービスエンドポイントと同じ流儀、URLパスにtenant_idを含めない）。
- 内部で`answer_question(db, tenant_id=payload["tenant_id"], message=body.message, requested_by=payload["email"], tools=TENANT_TOOLS, payload=payload)`のように呼び出す。
- 再インデックスエンドポイントは追加しない（PF管理者専用のまま）。
- `ollama_url`未設定時は既存の`is_assistant_configured`をそのまま再利用し503を返す。

---

## 6. UI設計

`admin-ui/tenant-portal.html`に、既存のタブ構造とは独立した常時表示のフローティングウィジェットを追加する。Alpine.jsの状態は3段階。

```
collapsed（既定）→ panel（小窓）→ fullscreen（全画面）
```

- **collapsed**: 画面右下に固定表示される丸いチャットアイコン。
- **panel**: アイコンクリックで開く、右下固定の小さいチャットウィンドウ（幅約360px×高さ約480px目安）。CSSの`resize: both; overflow: auto`をコンテナに付与し、ブラウザ標準のドラッグリサイズに対応させる（JS実装不要）。ウィンドウ内に「全画面へ」ボタンを配置。
- **fullscreen**: 画面全体を覆うオーバーレイ表示。「小さくする」ボタンでpanelに戻る。

いずれの状態でもメッセージ一覧・入力欄・確認カードのロジックは、PF管理者向け（`platform-ui/tenant.html`）と共通のパターンを踏襲する（表示サイズのみ異なる）。ウィジェットの開閉状態はページリロードで初期状態（collapsed）に戻る（localStorage等での永続化はしない、YAGNI）。

**注記（実装後に確認）:** ユーザーからは「実際に見てみないと最終判断できない」との留保あり。実装後に必ずブラウザで動作確認し、微調整の余地を残すこと。

---

## 7. セキュリティ

- **テナント越境防止:** `payload["tenant_id"]`は常にサーバー側のJWT検証結果由来。モデルが`tool_args`に混入させた`tenant_id`/`payload`は`_sanitize_tool_args`で除去する（PF管理者向けと同じ仕組みを拡張）。
- **ロール伝搬:** `payload`をそのまま`tenant_portal.py`の関数に渡すため、operatorユーザーがAIアシスタント経由でadmin限定操作を実行することはない（既存の権限チェックロジックがそのまま効く）。
- **`tenant_list`除外:** `TENANT_TOOLS`には含めない。§3のコメントで明記済み。
- **確認フロー:** `agent_pending_actions`はPF管理者向けと同じテーブル・TTL（10分）・確認UIパターンをそのまま使用する。

---

## 8. テスト方針

- `TENANT_TOOLS`の各ツールハンドラ: 対応する`tenant_portal.py`関数呼び出しの単体テスト（PF管理者向け`test_rag_tools.py`と同じ形式）。
- `/me/assistant/chat`・`/me/assistant/confirm-action`: 認証必須・admin/operator両方許可・503分岐のテスト。
- `answer_question`/`execute_pending_action`への`tools`/`payload`パラメータ追加に伴う既存テスト（PF管理者向け）の回帰確認——`payload=None`のデフォルトのままで従来通り動作すること。
- `_sanitize_tool_args`が`payload`キーも除去することのテスト追加。
- UI: 自動テストなし（既存踏襲、手動確認。フローティングウィジェットの3状態遷移を目視確認する）。

---

## 9. 拡張基盤への影響

Phase 1で確立した拡張レシピ（元設計書§8）に、「テナント向けドメイン追加時は`app/services/rag_tools/tenant/<domain>.py`に1ファイル追加し、PF管理者向けAPIではなくテナント自己サービスAPIをラップする」という分岐を追記する（実装時に元設計書へ追記する）。

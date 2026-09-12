# ローカルLLM RAG+エージェント機能 設計仕様書（Phase 1）

**Goal:** IoTプラットフォームの操作・仕様に関する質問にローカルLLM（クラウドAPI不使用）で答え、さらに「使い始め」に必要な定型操作（プロビジョニングトークン発行・ダッシュボード設定・アラートルール作成）をユーザーの確認を経て代行実行できる、PF管理者向けアシスタント機能を実装する。将来的にはプラットフォームのほぼ全機能をカバーする拡張を見据え、Phase 1ではその拡張基盤ごと確立する。

**背景:** [[project_tenant_billing_future]]の完了を受け、ユーザーが次に着手したい3つの課題の1つとして提起（2026-09-12）。「操作や仕様面を支援するRAGとエージェント機能」。

**参照:**
- `docs/design.html`・`docs/device-guide.html`・`docs/install-guide.html`・`docs/superpowers/specs/*.md`・`CLAUDE.md`（インデックス対象の既存ドキュメント群）
- `core-api/app/services/tenant.py`・`billing.py`・`billing_batch.py`（既存の「サービス層を薄くラップする」設計スタイルの参照実装）
- `docs/superpowers/specs/2026-09-12-data-retention-design.md`（「PF管理者代理設定＋テナント管理者セルフサービスの2窓口・共通サービス層」パターンの前例、Phase 2で参照）

**スコープ外（Phase 1ではやらない。将来のPhase 2以降で検討）:**
- 本節「9. ロードマップ」記載のドメイン（テナント管理・単価課金設定・請求書修正・デバイス管理・デバイスグループ・ファームウェア/OTA・ユーザー管理・公開ダッシュボード・MFA設定）のアクションツール化
- テナント管理者向けのセルフサービス提供（Phase 1はPF管理者限定）
- 会話履歴の永続化（毎回ステートレス。保存するのは未確認アクションのみ、短命）
- GPU前提の大規模モデルへの切り替え（CPUのみでの運用を前提に設計する）

---

## 1. 決定事項（ユーザーとのQ&Aで確定）

- **ローカルLLM必須（クラウドAPI不使用）。** 実行基盤はOllama（docker-composeへのサービス追加のみで導入可能、生成モデル・埋め込みモデル双方に対応、ツール呼び出し対応モデルも豊富）。
- **検索方式は本格的なRAG（埋め込み+ベクトル検索）を採用。** 当初「全文コンテキスト投入+prompt caching」も検討したが、これはClaude固有の機能（1Mトークンコンテキスト・prompt caching）に依存するため、ローカルLLM前提に転換した時点で不採用に変更。ローカルモデルはコンテキスト窓が小さく、同種のキャッシュ機構も持たないため、素直な埋め込みベース検索が妥当。
- **ベクトルストアはpgvector。** 新規サービスを増やさず、既存postgresコンテナに拡張として追加。コーパス規模が小さいうちは専用ベクトルDBは過剰。
- **実行ハードウェアはCPUのみ（GPUなし）。** モデルサイズは応答速度優先で3B前後（`qwen2.5:3b`想定）。日本語・ツール呼び出しの精度は中規模モデルに劣るが、まずここから始める。
- **利用者はPF管理者（テナントを管理する立場）。** Phase 1ではテナント管理者への展開はしない（②の拡張候補として明記のみ）。
- **エージェントの実行範囲:** 質問応答＋プラットフォームの現在情報参照（読み取り専用ツール）に加え、ユーザーが依頼すれば**確認を経て**実際の操作を代行実行できる（提案→確認→実行のフロー）。
- **Phase 1で対象とする操作ドメインは「使い始め」の3本柱**：ブートストラップ（プロビジョニングトークン発行）、データ可視化（ダッシュボードパネル設定）、アラート設定（アラートルール作成）。運用管理系（テナント停止・請求書修正等）はPhase 2以降。
- **最終ゴールはプラットフォームのほぼ全機能をエージェント経由でも操作可能にすること。** Phase 1では3ドメインの実装と同時に、Phase 2以降が同じ流儀で迷わず積み上げられる拡張基盤（ツールレジストリ・命名規則・追加手順）を確立する（9節・10節）。

---

## 2. 全体アーキテクチャ

### 2.1 新規インフラ（docker-compose追加）

```yaml
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    restart: unless-stopped
    networks: [internal]
    volumes:
      - ollama_data:/root/.ollama
    expose:
      - "11434"
```

- 外部公開しない（`internal`ネットワークのみ、core-apiからのみアクセス）。
- モデルはコンテナ起動後に`docker compose exec ollama ollama pull qwen2.5:3b`・`ollama pull nomic-embed-text`で取得する（初回セットアップ手順として`docs/install-guide.html`に追記、または起動時自動pullスクリプトをPhase 1のタスクに含める）。
- 通信はOllamaのOpenAI互換エンドポイント（`POST /v1/chat/completions`、`POST /v1/embeddings`）を使う。Ollama固有のSDK/独自形式には依存しない——将来vLLM等に切り替える際も同じインターフェースで差し替えられるようにするため（7節参照）。既存の`httpx`直呼び出しスタイル（`tenant.py`等）をそのまま踏襲し、新規Pythonライブラリは追加しない。

### 2.2 新規PostgreSQL拡張（pgvector）

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS doc_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_path VARCHAR(500) NOT NULL,
    heading VARCHAR(500),
    content TEXT NOT NULL,
    embedding VECTOR(768),  -- nomic-embed-textの次元数
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- 既存の`migrate_*`関数と同じ冪等パターンで追加する新規マイグレーション関数。
- コーパスが小規模（数百チャンク程度）なうちは、`embedding <=> query_embedding`によるシーケンシャルスキャンで十分な速度が出る想定。ivfflat/hnsw等のインデックスはコーパス増大時の検討事項として9節に記録し、Phase 1では作らない（YAGNI）。

### 2.3 新規バックエンドモジュール（`core-api`に追加。新規マイクロサービスは作らない）

- `app/services/rag.py` — チャンク化・埋め込み生成・ベクトル検索・Ollamaへのチャット呼び出しをまとめたオーケストレーション層
- `app/services/rag_tools/__init__.py` — `AgentTool`データクラスと`TOOLS`レジストリ（全ドメイン共通の集約点）
- `app/services/rag_tools/provisioning.py` / `dashboard.py` / `alerts.py` — Phase 1の3ドメインのツールハンドラ（8節の命名規則・分割方針に従う）
- `app/routers/rag.py` — チャット・確認実行・再インデックスのHTTPエンドポイント（プレフィックスは使わず、各エンドポイントに完全パスを明示する。理由: テナントスコープのエンドポイントとPF全体スコープのエンドポイントが混在するため）

---

## 3. インデックス作成パイプライン

### 3.1 対象ドキュメント（スコープ）

- `docs/design.html`、`docs/device-guide.html`、`docs/install-guide.html`
- `docs/superpowers/specs/*.md`
- `CLAUDE.md`
- `docs/superpowers/plans/*.md`は**対象外**（開発時の実装手順書であり、運用・仕様の質問には不向き。ノイズになるため除外する）

対象ファイルのリストは`app/services/rag.py`内で明示的なglobパターンのリストとして管理し、将来の対象拡大は設定変更のみで済むようにする（7節③）。

### 3.2 チャンク化方針

- Markdownは見出し（`#`〜`###`）単位で分割。HTMLは`<h2>`/`<h3>`セクション単位で分割し、タグを剥がしてプレーンテキスト化する。
- 1チャンクあたり目安300〜800トークン。
- 各チャンクに出典（`source_path`＋`heading`）を保持し、回答時に参照元を提示できるようにする。

### 3.3 再インデックスのタイミング

- ファイル監視・CI連携はしない。ドキュメントは開発者が手動でコミットするものであり、リアルタイム反映は不要。
- `POST /platform/assistant/reindex`（PF管理者限定）が全対象ファイルを読み直し、`doc_chunks`を全削除→再構築する（既存の「単価テーブル全置換」等と同じ「全削除→再挿入」パターン）。
- UIには`platform-settings.html`に「ドキュメント再インデックス」ボタンを配置する。

---

## 4. クエリ時のエージェントフロー

### 4.1 基本フロー

1. PF管理者がテナント詳細画面のチャットUIから質問を入力する。
2. 質問文を`nomic-embed-text`で埋め込み、`doc_chunks`をコサイン類似度検索（pgvectorの`<=>`演算子）で上位3〜5件取得する。
3. 取得したチャンク（出典付き）＋質問文＋`TOOLS`レジストリのツール定義をOllamaの`/v1/chat/completions`にOpenAI互換のtools形式で送信する。
4. モデルが「ドキュメントの記述だけで答えられる」と判断すればそのまま回答する。「プラットフォームの現在情報が要る」「操作を実行したい」と判断すればツール呼び出し（`tool_calls`）を返す。
5. **読み取り専用ツール**（`read_only=True`）は即座に実行し、結果を`tool`ロールのメッセージとして追加、再度Ollamaへ送って最終回答を生成する（往復は1〜2回を上限とする。上限に達した時点でそれ以上のツール呼び出しは無視し、そこまでに得られた情報だけで最終回答を生成させる）。
6. **アクション実行ツール**（`read_only=False`）は即座に実行せず、4.2節の確認フローに入る。
7. 回答＋参照した出典（ファイル名・見出し）をUIに返す。

### 4.2 アクション実行の確認フロー

```sql
CREATE TABLE IF NOT EXISTS agent_pending_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tool_name VARCHAR(100) NOT NULL,
    tool_args JSONB NOT NULL,
    requested_by VARCHAR(255) NOT NULL,  -- PF管理者のemail
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
```

1. モデルがアクション実行ツールの呼び出しを返したら、core-api側では**実行せず**、提案内容を`agent_pending_actions`に保存する（`expires_at`は作成から10分後）。
2. チャットのレスポンスとして、通常の回答とは見た目を変えた「確認カード」をUIに返す（提案内容の要約＋`pending_action_id`）。
3. ユーザーが「実行する」を押すと`POST /tenants/{tenant_id}/assistant/confirm-action`（`{pending_action_id}`）を呼び、そこで初めて対象ツールのハンドラを実行する。実行後は当該レコードを削除する。**セキュリティ上の注意:** ハンドラに渡す`tenant_id`は必ずURLパス（認証済み・`agent_pending_actions.tenant_id`との一致を検証済み）由来の値を使う。モデルが返した`tool_args`に`tenant_id`らしき値が含まれていても信用しない（他テナントへの誤爆・悪用を防ぐため）。
4. 実行時は既存の`write_audit_log`に加え、`detail`に`{"via": "ai_assistant", "confirmed_by": <email>}`を含めて監査ログに残す（追跡性の担保）。
5. `expires_at`を過ぎた提案は`confirm-action`側で404を返し、無効化する（期限切れレコードの掃除は既存の`audit purge worker`と同じ日次バッチパターンで別途削除してもよいが、Phase 1では毎回の`confirm-action`呼び出し時に期限チェックすれば十分——専用の掃除ジョブはYAGNI）。

会話履歴自体はDBに保存しない（フロントエンドの状態のみ、ページリロードで消える）。保存するのは未確認アクションのみで、これは短命な提案データであり「会話履歴」ではない。

---

## 5. Phase 1 ツールセット

### 5.1 読み取り専用ツール（`read_only=True`、共通/汎用）

| ツール名 | 説明 | ラップする既存関数 |
|---|---|---|
| `tenant_list` | テナント名からIDを解決する | 既存のテナント一覧取得 |
| `tenant_stats_get` | テナントの統計情報を取得 | `stats.py`の統計取得ロジック |
| `tenant_invoice_get` | テナントの請求書詳細を取得 | `get_invoice_detail_aggregated` |
| `provisioning_token_list` | 発行済みトークン一覧を取得 | 既存のトークン一覧API |
| `dashboard_panel_config_list` | 現在のダッシュボード設定を取得 | 既存のパネル設定取得API |
| `alert_rule_list` | 現在のアラートルール一覧を取得 | 既存のアラートルール一覧API |

### 5.2 アクション実行ツール（`read_only=False`、確認フロー必須）

| ツール名 | 説明 | ラップする既存関数 |
|---|---|---|
| `provisioning_token_issue` | プロビジョニングトークンを発行する | 既存のトークン発行API |
| `dashboard_panel_config_set` | ダッシュボードのセンサー別チャートタイプ等を設定する | 既存のパネル設定更新API |
| `alert_rule_create` | アラートルールを作成する | 既存のアラートルール作成API |

各ツールのJSON Schema・具体的なパラメータ名は実装計画（`writing-plans`）で確定する。

---

## 6. UI

### 6.1 チャット画面（`platform-ui/tenant.html`に新規タブ「AIアシスタント」）

- テナント個別画面に配置し、そのテナントの`tenant_id`が自動的にツール呼び出しのコンテキストに乗る（質問のたびにテナントを指定させない）。
- メッセージ一覧（ユーザー発言は右寄せ、アシスタント発言は左寄せの吹き出し）、入力欄＋送信ボタン。
- 回答生成中は「考え中...」表示（CPU推論のため数秒〜数十秒かかる想定）。
- 回答には参照元ドキュメント（ファイル名・見出し）を小さく併記する。
- アクション提案時は、通常の吹き出しと見た目を変えた確認カード（黄色枠等）＋「実行する」「キャンセル」ボタンを表示する。

### 6.2 再インデックス（`platform-ui/platform-settings.html`）

既存の各種設定カードと同じ形式で、「ドキュメント再インデックス」ボタンを1つ持つカードを追加する（最終インデックス日時の表示程度で十分、詳細な進捗UIは不要）。

---

## 7. API

```
POST /tenants/{tenant_id}/assistant/chat            ← { "message": "..." }
POST /tenants/{tenant_id}/assistant/confirm-action  ← { "pending_action_id": "..." }
POST /platform/assistant/reindex
```

いずれもPF管理者認証必須（`_require_platform`）。`app/routers/rag.py`に集約する（プレフィックスなしで各エンドポイントに完全パスを記述する）。

---

## 8. 機能拡張の進め方（将来の担当者向けレシピ）

**ツールレジストリの構造:**

```python
# app/services/rag_tools/__init__.py
from dataclasses import dataclass
from typing import Callable

@dataclass
class AgentTool:
    name: str            # 命名規則: <ドメイン>_<動詞>（例: alert_rule_create）
    description: str     # モデルに渡す日本語の説明
    input_schema: dict   # JSON Schema
    read_only: bool      # False なら4.2節の確認フロー必須
    handler: Callable    # (tenant_id: str, **kwargs) -> dict

from . import provisioning, dashboard, alerts
# Phase 2以降: from . import tenants, pricing, devices, device_groups, firmware, users

TOOLS: list[AgentTool] = [
    *provisioning.TOOLS,
    *dashboard.TOOLS,
    *alerts.TOOLS,
]
```

**新しいドメインを追加する手順:**

1. 9節の一覧表から実装したい操作を選ぶ。
2. その操作を担う既存のサービス関数を確認する。**新規ビジネスロジックは書かない**——既存API/サービス層をそのまま呼ぶ薄いラッパーに徹する。Phase 1の`rag_tools/provisioning.py`等を参照実装とする。
3. `app/services/rag_tools/<domain>.py`を新規作成し、ハンドラ関数＋`AgentTool`定義を書く（1ドメイン1ファイル。既存の`billing.py`/`billing_batch.py`/`bill_shock.py`の機能分割方針を踏襲）。
4. 状態変更を伴うなら`read_only=False`（確認フローは共通基盤がそのまま効くため追加実装不要）。参照のみなら`read_only=True`。
5. `TOOLS`レジストリ（`app/services/rag_tools/__init__.py`）に追記する。
6. ハンドラの単体テストを追加する（ラップ元のサービス関数の既存テストと同じモックの流儀を揃える）。
7. 実装計画は「Task: ○○ドメインのツール追加」という1〜2タスク程度の小さい単位で切り出せる規模。Phase 1のような大掛かりなブレスト・設計フェーズは不要——本仕様書の8節・9節が設計の代わりになる。新規ブレストは、確認フローの仕組み自体を変える必要が生じた場合のみ行う。

---

## 9. ロードマップ（Phase 2以降の対象ドメイン、未実装）

| ドメイン | 対象操作の例 | 優先度の目安 |
|---|---|---|
| テナント管理 | 作成・名称変更・停止/再開・削除 | 中 |
| 単価・課金設定 | 単価設定、デフォルト単価、消費税率、ビルショックしきい値、データ保持期間 | 中 |
| 請求書 | 手動修正（再集計） | 低 |
| デバイス管理 | デバイス削除、グループ割当 | 中 |
| デバイスグループ | 作成・更新・削除 | 中 |
| ファームウェア/OTA | アップロード・削除・配信（単体/グループ一括） | 低 |
| ユーザー管理 | テナントユーザーの作成・更新・削除・パスワードリセット | 低 |
| 公開ダッシュボード | 有効化/無効化 | 低 |
| MFA設定 | 必須化トグル | 低 |

優先度は現時点の目安であり、着手時にユーザーと再確認する。

**その他の拡張方針（技術的拡張性、コードで即対応できないため設計原則として明記）:**
- **利用者の拡張:** テナント管理者へのセルフサービス展開時は、[[project_data_retention_feature]]と同じ「PF管理者代理設定＋テナント管理者セルフサービスの2窓口・共通サービス層」パターンを踏襲する。
- **ナレッジベースの拡張:** 対象ファイルはglobリストの追加のみ。**重要な原則:** 動的なプラットフォームデータ（利用状況等）は絶対にベクトルストアへ事前投入しない。鮮度が保てないため、常にツール呼び出しでその場取得する（静的ドキュメント＝RAG、動的データ＝ツール、という役割分担を維持する）。
- **テナント横断の汎用アシスタントへの拡張:** チャット本体のロジックとツール一覧は独立させて設計しているため、テナント固有ツールを外した「一般的な仕様質問専用」の入口を後から追加できる。
- **モデル・インフラのスケールアップ:** OpenAI互換エンドポイントに統一しているため、より大きいモデルへの変更はOllama側のモデル設定変更のみ、将来vLLM等への移行もアプリ側の変更は最小限で済む想定。
- **ベクトルストアのスケールアップ（既知の限界）:** pgvectorはYAGNI優先の選択。将来コーパスが大規模化し、ハイブリッド検索・リランキング等が必要になった場合は専用ベクトルDBへの移行が必要になり、データ移行コストが発生する。

---

## 10. テスト方針

- チャンク化ロジック（Markdown/HTML見出し分割）: 単体テスト（既知の入力に対する分割結果を検証）。
- `rag.py`のベクトル検索・Ollama呼び出し: httpxをモックしてリクエスト形状（embeddings/chat completions）を検証。
- `AgentTool`レジストリ: 各ツールの`read_only`フラグ・スキーマの妥当性を検証する簡易テスト。
- 各ツールハンドラ（Phase 1の3ドメイン）: ラップ元サービス関数の既存テストと同じモックスタイルで、正常系・異常系を検証。
- 確認フロー（`agent_pending_actions`）: 提案作成→confirm-actionでの実行→レコード削除、期限切れ時の404、をモックで検証。
- API（`chat`/`confirm-action`/`reindex`）: 既存の`test_billing_api.py`等と同じ形式で認証・404系を検証。
- UI: 自動テストなし（既存のUI機能と同様、手動確認）。

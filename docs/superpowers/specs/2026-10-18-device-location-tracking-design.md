# デバイス位置情報(GPS)表示機能 設計スペック

## 意図・背景

テナント内のデバイスが、全世界に散らばって動く場合（配送車両など）と、工場・拠点に集約されている場合の両方があり得る。現状、デバイスの位置情報を表示する手段が無い。

Grafanaの地図パネル(Geomap)への組み込みはInfluxDBデータの受け渡しが煩雑になりがちなため、テナントポータル内に独立した位置情報モジュールとして作る。

## ゴール

- テナント内の全デバイスの最新位置を1枚の地図上にピン表示する「全体表示」画面
- ピンをクリックすると、その1台の移動履歴(軌跡)を期間指定で表示する「履歴」画面に遷移する
- 地図の初期表示位置・初期ズームを、テナントごとに保存・変更できる(「この表示をデフォルトにする」ボタン)
- 動作確認用に、シミュレータが移動ルートをシミュレートして送信できる

## 非ゴール・アンチゴール

- Grafanaダッシュボードへの地図パネル組み込み(今回は見送り)
- ジオフェンシングなどのアラート機能との連携(将来の拡張候補、今回はスコープ外)
- 地図タイルの自前ホスティング(全世界をカバーするのは非現実的)
- 手入力で緯度経度・ズームを数値指定するUI(「現在の表示を保存」ボタンで十分とする)

## 決定した設計

### データの送り方(デバイス→バックエンド)

既存のテレメトリの仕組みに乗せる。デバイスは`latitude`・`longitude`という名前のセンサー値を、既存のMQTT経由のテレメトリ送信にそのまま含めて送る。ingestion-service・InfluxDBの保存経路は変更不要（既存のセンサーキーと同じ型・同じ保存先）。

### テナントポータルUI

`admin-ui/tenant-portal.html`に新規タブ「位置情報」を追加する。編集操作を伴わない閲覧機能なので、全ロール(admin/operator/viewer)が閲覧可能とする(**既存の「デバイス」タブ等と同じ閲覧権限の考え方**)。

**全体表示画面**
- テナント内の全デバイスの最新の`latitude`/`longitude`を取得し、OpenStreetMapの地図上にピン表示する
- 位置情報を送っていないデバイスはピンを出さない(リストには出すが地図には出さない、程度の扱い。詳細は実装プランで決める)
- 地図の初期中心・初期ズームは、テナントごとに保存された値を使う(未設定なら実装側で決める適当な既定値、例: 日本全体が入る程度)
- 「この表示をデフォルトにする」ボタン(admin/operatorのみ、既存の`canEdit`相当の権限)。押した時点の地図の中心・ズームを保存する
- ピンをクリックすると履歴画面に遷移する

**履歴画面**
- 1台のデバイスの`latitude`/`longitude`の時系列データを、期間を指定して取得し、地図上に線(軌跡)で表示する
- 期間指定のUIは既存のダッシュボード・統計画面の期間選択パターンに合わせる(**ビルダーの判断に委ねる**: 具体的なプリセット・UIコンポーネント)

### 地図描画ライブラリ

Leaflet.js（軽量・OpenStreetMapタイル前提の定番ライブラリ）を静的ファイルとしてローカルに配置し、既存のTailwind/Alpine/QRCodeと同様にCDN経由にしない。

**ただし地図タイル画像そのものは例外的に外部(OpenStreetMapのタイルサーバー)から読み込む。** これまでの「外部CDN不使用」方針からの明示的な例外（ユーザー承認済み）。タイルはズームレベル18〜19程度(建物単位)まで無料で利用でき、GNSS相当の精度での表示に十分。

この例外に伴い、位置情報タブが使われるnginxロケーション(`/admin/`配下)のCSPに、OpenStreetMapタイルサーバーのホスト(`*.tile.openstreetmap.org`)を`img-src`に追加する。Leaflet.js自体はローカル配置のため`script-src`の変更は不要。

### データモデル(新規)

`core-api/app/models/public.py`に、テナントごとの地図デフォルト表示設定を追加する。`Tenant`テーブルに直接カラムを追加する(既存の`bill_shock_threshold_amount`等と同じ、テナント単位設定カラムを増やすパターン):

```python
# Tenant クラスに追加
map_default_lat = Column(Float, nullable=True)
map_default_lng = Column(Float, nullable=True)
map_default_zoom = Column(Integer, nullable=True)
```

`core-api/app/database.py`に`migrate_add_map_default_view()`を追加し、既存の`migrate_*`と同じ`ADD COLUMN IF NOT EXISTS`パターンで`app/main.py`の起動時マイグレーションタプルに登録する。

### バックエンドAPI(新規、`tenant_portal.py`に追加)

- `GET /tenant-portal/me/device-locations` — 全デバイスの最新`latitude`/`longitude`を返す(InfluxDBから、既存の`get_tenant_sensor_keys`等と同様のFlux/InfluxQLクエリパターンで実装)
- `GET /tenant-portal/me/devices/{device_id}/location-history?start=...&end=...` — 指定デバイス・期間の`latitude`/`longitude`の時系列を返す
- `PUT /tenant-portal/me/map-default-view` — `{lat, lng, zoom}`を受け取り`Tenant`のカラムを更新(admin/operatorのみ)

具体的なInfluxDB/Fluxクエリの実装（既存のセンサー値取得パターンのどれを再利用するか）は**ビルダーの判断に委ねる**。

### シミュレータの変更

`simulator/`に移動ルートのシミュレーション機能を追加する。`config.json`に基準となる緯度経度(`base_lat`/`base_lon`)を設定でき、有効にすると一定間隔ごとに小さくランダムに位置がずれた`latitude`/`longitude`を、既存のテレメトリ送信と同じタイミングで送る(簡易なランダムウォーク。経路の事前定義などは行わない)。既存の`device_worker.py`のテレメトリ生成ロジックに追加する形になる想定（**ビルダーの判断に委ねる**: 具体的な移動幅・頻度の数値）。

## ビルダーに委ねる部分

- 履歴画面の期間選択UIの具体的なプリセット・コンポーネント
- 位置情報を持たないデバイスの一覧上の扱い
- 地図のデフォルト値(保存されていない場合の初期中心・ズーム)
- InfluxDB/Fluxクエリの具体的な実装
- シミュレータのランダムウォークの移動幅・送信頻度の数値
- `map_default_lat`/`lng`/`zoom`のバリデーション範囲(緯度-90〜90等)

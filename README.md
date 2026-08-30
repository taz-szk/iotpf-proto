<img width="2816" height="1536" alt="IoTAirx_logo (1)" src="https://github.com/user-attachments/assets/b4c44f28-f51e-4233-8659-5e8c78afe2b2" />
# IoTAir-X — マルチテナント型 IoT デバイス管理基盤

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

MQTT mTLS 接続・リアルタイム可視化・OTA 配信・アラート通知・ゼロタッチプロビジョニングを一体化した、マルチテナント対応の IoT プラットフォームです。
工場・病院・インフラなど複数拠点の IoT デバイスを、テナントごとに完全分離した環境で管理できます。

## 主な機能

- **MQTT mTLS 接続** — EMQX 5.x による相互 TLS 認証でデバイスを安全接続
- **リアルタイム可視化** — Grafana + InfluxDB 2 によるライブダッシュボード
- **アラート・通知** — 閾値監視・メール/Webhook 通知（1 分評価）
- **OTA ファームウェア配信** — MinIO 経由の安全なリモート配信・進捗追跡
- **マルチテナント管理** — InfluxDB Org / Grafana Org を完全分離
- **ゼロタッチプロビジョニング** — Step-CA による証明書自動発行・デバイス登録
- **公開ダッシュボード** — 認証なしで閲覧できるパブリックビュー（デジタルサイネージ等）

## ドキュメント

| | |
|---|---|
| トップ（ナビ） | https://taz-szk.github.io/iotpf-proto/ |
| 紹介スライド | https://taz-szk.github.io/iotpf-proto/promo.html |
| インストール手順 | https://taz-szk.github.io/iotpf-proto/install-guide.html |
| システム設計 | https://taz-szk.github.io/iotpf-proto/design.html |

## Tech Stack

| レイヤー | 技術 |
|---------|------|
| API | FastAPI + PostgreSQL 16 |
| 時系列DB | InfluxDB 2 |
| MQTT | EMQX 5（mTLS） |
| 可視化 | Grafana OSS |
| ストレージ | MinIO |
| 内部 CA | Step-CA |
| プロキシ | nginx 1.25 |
| インフラ | Docker Compose |

## ライセンス

[MIT License](LICENSE) — Copyright (c) 2026 taz-szk

> **依存サービスについて:** MinIO および Grafana OSS は AGPL-3.0 です。
> 変更なしで使用する範囲では本プロジェクトへの影響はありませんが、
> これらをベースに商用 SaaS を構築する場合は各ベンダーの商用ライセンスを検討してください。

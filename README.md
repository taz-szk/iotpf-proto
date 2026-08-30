# MQTTによるIoTデータ収集・簡易可視化アプリケーション
本アプリケーションは、IoTデバイスから送信されるデータをローカル環境またはクラウド環境で簡易的に保存・可視化するためのアプリケーションです。

主な機能として、データ収集・可視化に加えて、ブートストラップトークンを用いたゼロタッチプロビジョニングやOTA（Over-The-Air）アップデートなどのデバイス管理機能も備えています。マルチテナント環境での利用を想定しており、ローカルでのMQTTデータ通信や、Grafanaを活用したダッシュボード構築に関心のある方におすすめです。
また、認証不要でアクセス可能な公開用ページ（パブリックビュー）の設定も行えるため、デジタルサイネージ等への応用も可能です。

#ドキュメント
インストール手順や詳細な仕様については、以下の各ページをご参照ください。

トップ（ナビ）  : https://taz-szk.github.io/iotpf-proto/

紹介動画        : https://taz-szk.github.io/iotpf-proto/promo.html

インストール手順  : https://taz-szk.github.io/iotpf-proto/install-guide.html

システム設計/ドキュメント  : https://taz-szk.github.io/iotpf-proto/design.html

#ライセンス・利用条件
本リポジトリのコードおよびデータは、オープンソースとして公開していますが、著作権は作者に帰属します。
ライセンス（例：MIT License）の範囲内において、個人の責任で自由にご利用・改変いただけますが、一定のセキュリティ実装チェックを行っているものの、作者は一切の責任を負いません。より良い形への改修やフォークは歓迎いたします。

#免責事項 (Disclaimer)
このソフトウェアや提供物は「現状有姿 (AS-IS)」で提供され、明示・黙示を問わず、いかなる保証もありません。

本リポジトリの利用によって生じた、いかなる損害（データの消失、システムの破損、利益の損失、その他の金銭的・精神的被害を含む）についても、作者は一切の責任を負いません。

すべて自己責任 (Use at your own risk) でご利用ください。

The software and materials in this repository are provided "AS IS", without warranty of any kind, express or implied.
In no event shall the author be liable for any claim, damages, or other liability arising from, out of, or in connection with the software or the use or other dealings in the software.
Use at your own risk.

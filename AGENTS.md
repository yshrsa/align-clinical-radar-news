# AGENTS.md — Align Clinical Operations Radar

このリポジトリは、Align TechnologyのClinical / Treatment Planning Operationsを
題材にした独立ポートフォリオです。

- `bi-evidence-radar*`、`asset-a`、`shd-platform`を実行時依存にしない。
- 既存Radarを変更せず、必要な契約だけをこのリポジトリ内へ複製する。
- PubMedの`PublicationTypeList`はXMLから決定論的に取得し、LLMに分類させない。
- 抄録にない内容・数値・症例数を補完しない。
- 有料API、外部公開、Cloudflare/GitHub設定変更、通知送信は明示承認なしに行わない。
- コード変更には比例したテストを追加し、収集・品質ゲート・静的公開を個別に確認する。

# Architecture

## 目的

歯科知識を装うためのデモではなく、公開エビデンスを品質管理・手戻り削減・育成・
安全性・Global連携へ変換するClinical Operationsの実例として示す。

## データフロー

```text
PubMed E-utilities
  -> XML抽出（PublicationType / MeSHを決定論的に取得）
  -> pack.yaml分類（製品・企業・Japan・運用テーマ）
  -> 7項目日本語要約（抄録外の推測を禁止）
  -> 品質ゲート（完全性・数値根拠・重複・鮮度）
  -> 静的JSON / GitHub Pages
  -> D1文献テーブル（初回全件、以後は同期ハッシュ差分のみupsert）

公開業界ソース
  -> Align公式Newsroom（公開HTML）
  -> Orthodontic Products / Dental Tribune / MedTech Dive（公開RSS）
  -> FDA MedWatch / Press Releases（公開RSS）
  -> 決定論的な領域語フィルタ・URL重複排除
  -> Industry News静的JSON / GitHub Pages

GitHub Actions（毎日 06:15 JST）
  -> Cloudflare Workerの要約専用endpoint
  -> Workers AI Qwen 3.8 27B（日次予算をD1で事前予約）
  -> 品質ゲート通過後だけGitHub Pagesを更新
  -> 業界ニュースを差分マージ（記事・媒体・取得元URLを保持）

Cloudflare Access
  -> Pages Function（認証メールを同一Originで引き渡し）
  -> Worker（利用者別レート制限・D1文献検索・D1履歴）
  -> D1障害時はNotion（任意）/ 公開JSONへ退避
  -> 根拠番号付きQwen回答
```

## 面接での説明軸

| Radarの機能 | Clinical Managerへの接続 |
|---|---|
| planned vs achieved / refinement | 品質・手戻り・エスカレーションの監視 |
| PublicationTypeの決定論的取得 | 再現性のある品質基準 |
| 抄録外数値の拒否 | AI自動化に対する品質ガバナンス |
| 鮮度・件数・失敗の可視化 | KPI運営と異常の早期検知 |
| Japan / Global / 競合の分類 | 本社連携と日本固有課題の説明 |

## 境界

- これは臨床判断支援ではなく、公開文献の情報整理プロトタイプ。
- ClinCheckの社内品質指標や患者データには接続しない。
- 企業・製品との関連はタイトルと抄録の文字列一致であり、資金提供・COIを意味しない。
- Newsは公開情報のみをGitHub Pagesで配信する。AI ChatはCloudflare Accessの許可利用者だけが利用できる。
- OpenAI API、Vectorize、有料フォールバックは使用しない。

# Align Clinical Operations Radar

- News: https://yshrsa.github.io/align-clinical-radar-news/
- AI Chat: https://align-clinical-radar-chat.pages.dev/

Align Technology JapanのClinical Manager / Treatment Planning Operationsを題材に、
クリアアライナー領域の公開PubMed文献を「臨床品質と運用判断」へつなぐ独立Radarです。
既存のStructural Heart RadarとBI Evidence Radarは変更しません。

## 何を見るか

- Invisalign / ClinCheck / iTeroなどのAlign関連エビデンス
- planned vs achieved、refinement、治療計画、デジタル工程
- 安全性・患者アウトカム、AI/自動化、保定・後戻り
- RCT / Meta-Analysis、日本関連、競合製品

## 信頼性の境界

- PubMed XMLの`PublicationTypeList`を直接取得し、エビデンス階層をLLMに分類させません。
- 日本語要約は7フィールドを1回で生成します。
- 抄録にない項目は必ず`抄録に記載なし`とします。
- 結果・症例数の数値が抄録に存在しない場合、公開前に同じ文言へ戻します。
- 不完全な要約や`[要約エラー]`を公開しません。
- データ件数、要約待ち、検証エラー、収集鮮度を品質ステータスに出します。

## ローカル検証

```powershell
python -m unittest discover -s tests -v
python -m src.collect_pubmed --max-results 20
python -m src.seed_pmids 34993617 40394411 29957318 36989750
python -m src.summarize_ja --backend ollama --limit 4
python -m src.publish
```

初期データはローカルOllama（既定`qwen3.8:27b`）で無課金生成できます。定期更新時は
専用Cloudflare Workerへ接続し、品質ゲート通過済みデータだけをGitHub Pagesへ公開します。
OpenAI APIや有料フォールバックは使用しません。

全件の文献メタデータはD1の検索専用テーブルにも保存します。初回同期は全件、以後は
文献・分類・日英要約を含む同期ハッシュが変わったレコードだけを毎日06:15 JSTにupsertします。
D1同期が失敗しても静的JSONの公開は継続し、チャット検索も公開JSONへ自動退避します。

## 構成計画

1. **News（このリポジトリ）**: PubMed収集、7項目要約、品質ゲート、静的サイト
2. **AI Chat（別リポジトリ）**: Access保護、同一Origin Gateway、利用者別履歴・制限
3. **面接コンテンツ**: Radarの根拠をPeople / Quality / Process / Globalの回答へ接続

詳細は[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)を参照してください。
現時点の実行制約は[`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md)に記録しています。

# 面接での見せ方

## 30秒版（日本語）

私は、公開PubMed文献を収集するだけでなく、Clinical Operationsの判断に使える形へ
変換するRadarを作りました。Publication TypeはXMLから決定論的に取得し、AI要約は
抄録外の推測と数値補完を禁止しています。Align向けには、planned vs achieved、
refinement、治療計画、患者アウトカムを分類し、品質・手戻り・育成・安全性の観点で
見られるようにしました。これは歯科医の臨床判断を代替するものではなく、専門家が
判断に集中できるよう、データ品質と運用の仕組みを整える私の仕事の実例です。

## 30-second version (English)

I built an evidence radar that turns public PubMed literature into operationally useful,
quality-controlled information. Publication types are extracted deterministically from PubMed XML,
while the AI summary is not allowed to invent missing facts or numbers. For Align, the radar focuses
on planned-versus-achieved outcomes, refinements, treatment planning, patient outcomes, and digital
workflow. It does not replace clinical judgment. It demonstrates how I enable clinical specialists
to focus on judgment by improving data quality, process visibility, and governance.

## デモ順序

1. 品質ステータスで、件数・要約待ち・検証エラー・鮮度を示す。
2. `計画と実績の差`または`Refinement・手戻り`で絞り込む。
3. 1件を開き、試験デザイン・エンドポイント・症例数が抄録根拠に限定されることを示す。
4. 「Clinical Operations視点」が品質・教育・安全性のどこに効くか説明する。
5. 歯科の専門判断はクリニカルスタッフが担う、と境界を明言する。

## 言わないこと

- 「ClinCheckの品質を評価できる」とは言わない。公開抄録から見える研究上の論点だけ。
- 製品名の一致を、Alignが資金提供した研究や企業公式見解だと扱わない。
- このRadarを実患者・社内データに接続したとは言わない。
- AI Chatが未公開の段階で、クラウド完結済みとは言わない。

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import json

import src.publish as publish_module
import src.summarize_ja as summarize_module
from src.collect_pubmed import classify, extract_records
from datetime import datetime, timezone

from src.publish import freshness, public_config, validate_document
from src.summarize_ja import guard_summary, normalize_missing_abstracts
from src.common import NOT_REPORTED, SUMMARY_FIELDS
from src.sync_d1 import changed_documents, evidence_document
from src.sync_industry_d1 import changed_documents as changed_industry_documents, industry_news_document
from src.collect_industry_news import merge_items, parse_align_newsroom, parse_rss, prepare_item, relevant
from src.summarize_industry_news import MISSING as NEWS_MISSING, guard_summary as guard_news_summary, prompt_for as news_prompt


class PipelineTests(unittest.TestCase):
    def test_industry_rss_preserves_article_and_source_metadata(self) -> None:
        source = {"id": "x", "name": "Source", "source_url": "https://source.test/", "feed_url": "https://source.test/feed", "category": "業界"}
        xml = b"""<rss><channel><item><title>Invisalign update</title><link>https://source.test/a</link><description>Clear aligner news</description><pubDate>Fri, 09 Oct 2026 11:26:48 +0000</pubDate><category>Aligners</category></item></channel></rss>"""
        item = parse_rss(xml, source)[0]
        self.assertEqual(item["url"], "https://source.test/a")
        self.assertEqual(item["published_date"], "2026-10-09")
        self.assertEqual(item["source_categories"], ["Aligners"])

    def test_align_newsroom_parser_uses_official_article_url(self) -> None:
        source = {"source_url": "https://www.aligntech.com/newsroom"}
        html = '<div class="news-item"><h4>10/01/2026</h4><h4 class="light-text"><a href="https://investor.aligntech.com/release">Align release</a></h4></div>'
        item = parse_align_newsroom(html, source)[0]
        self.assertEqual(item["published_date"], "2026-10-01")
        self.assertEqual(item["url"], "https://investor.aligntech.com/release")

    def test_industry_relevance_and_merge_are_deterministic(self) -> None:
        self.assertTrue(relevant({"title": "Clear aligner launch"}, {}, ["clear aligner"]))
        self.assertFalse(relevant({"title": "Cardiac catheter"}, {}, ["clear aligner"]))
        old = [{"id": "old", "published_date": "2026-10-01"}]
        new = [{"id": "new", "published_date": "2026-10-02"}]
        merged, added = merge_items(old, new, 10)
        self.assertEqual([item["id"] for item in merged], ["new", "old"])
        self.assertEqual(added, 1)

    def test_industry_merge_preserves_summary_only_for_unchanged_source_text(self) -> None:
        old = [{"id": "same", "title": "T", "snippet_en": "S", "summary_ja": "要約", "key_takeaway_ja": "要点"}]
        unchanged, _ = merge_items(old, [{"id": "same", "title": "T", "snippet_en": "S"}], 10)
        changed, _ = merge_items(old, [{"id": "same", "title": "T", "snippet_en": "New"}], 10)
        self.assertEqual(unchanged[0]["summary_ja"], "要約")
        self.assertNotIn("summary_ja", changed[0])

    def test_industry_merge_tracks_first_and_last_seen(self) -> None:
        old = [{"id": "same", "title": "T", "snippet_en": "S", "retrieved_at_utc": "2026-10-09T00:00:00Z"}]
        merged, _ = merge_items(old, [{"id": "same", "title": "T", "snippet_en": "S", "retrieved_at_utc": "2026-10-10T00:00:00Z"}], 10)
        self.assertEqual(merged[0]["first_seen_at_utc"], "2026-10-09T00:00:00Z")
        self.assertEqual(merged[0]["last_seen_at_utc"], "2026-10-10T00:00:00Z")

    def test_industry_d1_delta_ignores_observation_timestamp_only_changes(self) -> None:
        row = {"id": "a", "title": "T", "url": "https://example.test/a", "retrieved_at_utc": "2026-10-09T00:00:00Z"}
        first = industry_news_document(row)
        row["retrieved_at_utc"] = "2026-10-10T00:00:00Z"
        second = industry_news_document(row)
        self.assertEqual(first["article_sha256"], second["article_sha256"])
        self.assertEqual(changed_industry_documents([second], {"a": first["article_sha256"]}), [])

    def test_industry_d1_hash_changes_when_summary_changes(self) -> None:
        row = {"id": "a", "title": "T", "url": "https://example.test/a", "retrieved_at_utc": "2026-10-09T00:00:00Z"}
        first = industry_news_document(row)
        row["summary_ja"] = "日本語要約"
        second = industry_news_document(row)
        self.assertNotEqual(first["article_sha256"], second["article_sha256"])

    def test_align_official_news_is_always_focal_related(self) -> None:
        pack = {
            "entities": {"focal_company": {"name": "Align Technology"}, "assets": []},
            "scope": {"topics": []}, "sources": {"japan_relevance_terms": []},
        }
        item = prepare_item(
            {"title": "Quarterly results", "url": "https://example.test/release"},
            {"id": "align-official", "name": "Align", "source_url": "https://example.test", "feed_url": "https://example.test", "category": "Align公式"},
            pack, "2026-10-10T00:00:00Z",
        )
        self.assertTrue(item["focal_related"])
        self.assertEqual(item["matched_companies"], ["Align Technology"])

    def test_news_summary_guard_and_prompt_forbid_unsupported_claims(self) -> None:
        guarded = guard_news_summary({"summary_ja": "", "key_takeaway_ja": "要点"})
        self.assertEqual(guarded["summary_ja"], NEWS_MISSING)
        prompt = news_prompt({"source_name": "Source", "title": "Launch", "snippet_en": "Company announcement"})
        self.assertIn("推測、外部知識、数値の補完は禁止", prompt)
        self.assertIn("Company announcement", prompt)

    def test_d1_evidence_hash_changes_when_summary_changes(self) -> None:
        row = {"pmid": "1", "title": "Trial", "abstract": "English abstract", "content_sha256": "source"}
        first = evidence_document(row)
        row["summary_ja"] = "日本語要約"
        second = evidence_document(row)
        self.assertEqual(first["summary_en"], "English abstract")
        self.assertNotEqual(first["evidence_sha256"], second["evidence_sha256"])

    def test_d1_evidence_normalizes_pubmed_author_string(self) -> None:
        document = evidence_document({"pmid": "1", "title": "Trial", "authors": "A One, B Two"})
        self.assertEqual(document["authors"], ["A One", "B Two"])

    def test_d1_delta_excludes_unchanged_documents(self) -> None:
        document = evidence_document({"pmid": "1", "title": "Trial"})
        self.assertEqual(changed_documents([document], {"1": document["evidence_sha256"]}), [])
        self.assertEqual(changed_documents([document], {}), [document])

    def test_pubmed_publication_type_is_extracted_from_xml(self) -> None:
        xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
        <ArticleTitle>Trial</ArticleTitle><Abstract><AbstractText>N=100 patients.</AbstractText></Abstract>
        <PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
        </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
        record = extract_records(xml)[0]
        self.assertEqual(record["publication_types"], ["Randomized Controlled Trial"])

    def test_company_and_topic_are_derived_from_pack(self) -> None:
        pack = {
            "entities": {"focal_company": {"name": "Align Technology"}, "assets": [
                {"name": "clincheck", "label": "ClinCheck", "brand": [], "company": "Align Technology"}
            ]},
            "scope": {"topics": [
                {"id": "refinement", "label": "Refinement", "synonyms": ["additional aligner"]}
            ]},
            "sources": {"japan_relevance_terms": ["Japan"]},
        }
        record = {"title": "ClinCheck refinement in Japan", "abstract": "Additional aligners were evaluated."}
        classify(record, pack)
        self.assertTrue(record["focal_related"])
        self.assertEqual(record["matched_assets"], ["clincheck"])
        self.assertEqual(record["matched_topics"], ["refinement"])
        self.assertTrue(record["japan_relevant"])

    def test_missing_abstract_forces_all_fields_to_not_reported(self) -> None:
        guarded = guard_summary({field: "invented" for field in SUMMARY_FIELDS}, "")
        self.assertEqual(set(guarded.values()), {NOT_REPORTED})

    def test_numbers_not_in_abstract_are_removed(self) -> None:
        result = {field: "text" for field in SUMMARY_FIELDS}
        result["endpoint_results_ja"] = "HR 0.75"
        result["sample_size_ja"] = "N=999"
        guarded = guard_summary(result, "A randomized trial enrolled 100 participants.")
        self.assertEqual(guarded["endpoint_results_ja"], NOT_REPORTED)
        self.assertEqual(guarded["sample_size_ja"], NOT_REPORTED)

    def test_partial_summary_fails_quality_gate(self) -> None:
        row = {"pmid": "1", "title": "x", "url": "https://example.test", "content_sha256": "a", "publication_types": [], "summary_ja": "x"}
        self.assertIn("summary fields must be all present or all absent", validate_document(row))

    def test_abstract_free_invented_summary_fails_quality_gate(self) -> None:
        row = {"pmid": "1", "title": "x", "abstract": "", "url": "https://example.test", "content_sha256": "a", "publication_types": []}
        row.update({field: "invented" for field in SUMMARY_FIELDS})
        self.assertIn("abstract-free records must use the missing-information sentinel in all summary fields", validate_document(row))

    def test_missing_abstract_normalization_is_deterministic(self) -> None:
        corpus = {"documents": [{"abstract": "", **{field: "invented" for field in SUMMARY_FIELDS}}]}
        self.assertEqual(normalize_missing_abstracts(corpus), 1)
        self.assertEqual({corpus["documents"][0][field] for field in SUMMARY_FIELDS}, {NOT_REPORTED})

    def test_freshness_reports_stale_data(self) -> None:
        checked = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        result = freshness("2026-10-07T11:00:00Z", 48, checked)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["age_hours"], 73.0)

    def test_public_config_contains_labels_not_query_terms(self) -> None:
        pack = {
            "display_name": "x",
            "entities": {
                "focal_company": {"name": "Align Technology", "label": "Align関連"},
                "assets": [{"name": "clincheck", "label": "ClinCheck", "company": "Align Technology"}],
            },
            "scope": {"topics": [{"id": "refinement", "label": "Refinement・手戻り"}]},
            "delivery": {"chat_url": ""},
        }
        config = public_config(pack)
        self.assertEqual(config["assets"][0]["label"], "ClinCheck")
        self.assertEqual(config["topics"][0]["id"], "refinement")

    def test_daily_cloud_budget_exhaustion_stops_without_marking_failure(self) -> None:
        with TemporaryDirectory() as temp:
            data_dir = Path(temp)
            corpus_path = data_dir / "corpus.json"
            usage_path = data_dir / "ai-usage.json"
            corpus_path.write_text(json.dumps({
                "documents": [{
                    "pmid": "1", "title": "pending", "journal": "J", "abstract": "N=10",
                    "publication_types": [], "content_sha256": "abc",
                }]
            }), encoding="utf-8")
            with (
                patch.object(summarize_module, "CORPUS_PATH", corpus_path),
                patch.object(summarize_module, "USAGE_PATH", usage_path),
                patch.object(summarize_module, "call_qwen", side_effect=summarize_module.DailyBudgetExhausted()),
            ):
                result = summarize_module.summarize(limit=1, backend="cloudflare")
            self.assertEqual(result["completed"], 0)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["stopped_reason"], "daily_free_summary_budget_exhausted")

    def test_no_publishable_documents_preserves_previous_feed(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "data"
            site_data_dir = root / "site" / "data"
            data_dir.mkdir()
            site_data_dir.mkdir(parents=True)
            (root / "pack.yaml").write_text(
                "display_name: Test\n"
                "entities:\n"
                "  focal_company: {name: Align Technology}\n"
                "  assets: []\n"
                "scope: {topics: []}\n"
                "delivery: {max_items: 10, stale_after_hours: 48, chat_url: ''}\n",
                encoding="utf-8",
            )
            corpus = {
                "generated_at_utc": "2026-10-10T00:00:00Z",
                "documents": [{
                    "pmid": "1", "title": "pending", "url": "https://example.test", "content_sha256": "x",
                    "publication_types": [], "abstract": "text",
                }],
            }
            (data_dir / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
            old_feed = '{"items":[{"pmid":"old"}]}'
            (site_data_dir / "news-latest.json").write_text(old_feed, encoding="utf-8")
            with (
                patch.object(publish_module, "ROOT", root),
                patch.object(publish_module, "DATA_DIR", data_dir),
                patch.object(publish_module, "SITE_DATA_DIR", site_data_dir),
                patch.object(publish_module, "CORPUS_PATH", data_dir / "corpus.json"),
            ):
                with self.assertRaisesRegex(ValueError, "no fully summarized"):
                    publish_module.publish()
            self.assertEqual((site_data_dir / "news-latest.json").read_text(encoding="utf-8"), old_feed)
            status = json.loads((site_data_dir / "quality-status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["blocking_reasons"], ["no_publishable_documents"])


if __name__ == "__main__":
    unittest.main()

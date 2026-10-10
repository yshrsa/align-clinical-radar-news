from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import json

import src.publish as publish_module
from src.collect_pubmed import classify, extract_records
from datetime import datetime, timezone

from src.publish import freshness, public_config, validate_document
from src.summarize_ja import guard_summary, normalize_missing_abstracts
from src.common import NOT_REPORTED, SUMMARY_FIELDS


class PipelineTests(unittest.TestCase):
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

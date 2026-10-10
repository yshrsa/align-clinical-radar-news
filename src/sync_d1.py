from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from typing import Any

import requests

from .common import DATA_DIR, SUMMARY_FIELDS, read_json

def evidence_document(row: dict[str, Any]) -> dict[str, Any]:
    authors = row.get("authors") or []
    if isinstance(authors, str):
        authors = [value.strip() for value in authors.split(",") if value.strip()]
    payload = {
        "pmid": str(row.get("pmid") or ""),
        "title": row.get("title") or "",
        "abstract_en": row.get("abstract") or "",
        # PubMed abstract is the authoritative English-language summary. Re-generating it would add drift and cost.
        "summary_en": row.get("abstract") or "",
        "summary_ja": row.get("summary_ja") or "",
        "key_takeaway_ja": row.get("key_takeaway_ja") or "",
        "why_matters_ja": row.get("why_matters_ja") or "",
        "study_type_ja": row.get("study_type_ja") or "",
        "endpoints_ja": row.get("endpoints_ja") or "",
        "endpoint_results_ja": row.get("endpoint_results_ja") or "",
        "sample_size_ja": row.get("sample_size_ja") or "",
        "journal": row.get("journal") or "",
        "authors": authors,
        "publication_date": row.get("pub_date") or "",
        "doi": row.get("doi") or "",
        "url": row.get("url") or "",
        "publication_types": row.get("publication_types") or [],
        "mesh_terms": row.get("mesh_terms") or [],
        "assets": row.get("matched_assets") or [],
        "companies": row.get("matched_companies") or [],
        "topics": row.get("matched_topics") or [],
        "focal_related": bool(row.get("focal_related")),
        "japan_relevant": bool(row.get("japan_relevant")),
        "source_content_sha256": row.get("content_sha256") or "",
        "fetched_at_utc": row.get("fetched_at_utc") or "",
        "source_updated_at_utc": row.get("updated_at_utc") or "",
        "summary_model": row.get("summary_model") or "",
        "summary_generated_at_utc": row.get("summary_generated_at_utc") or "",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["evidence_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def changed_documents(documents: list[dict[str, Any]], hashes: dict[str, str]) -> list[dict[str, Any]]:
    return [document for document in documents if hashes.get(document["pmid"]) != document["evidence_sha256"]]


class EvidenceClient:
    def __init__(self, base_url: str, secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        delay = 1.0
        last_error: Exception | None = None
        for _ in range(5):
            try:
                response = requests.request(method, f"{self.base_url}{path}", headers=self.headers, timeout=90, **kwargs)
                if response.status_code == 429 or response.status_code in {500, 502, 503, 504}:
                    retry = response.headers.get("Retry-After")
                    time.sleep(float(retry) if retry and retry.isdigit() else delay)
                    delay = min(delay * 2, 20)
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(delay)
                delay = min(delay * 2, 20)
        raise RuntimeError(f"D1 evidence sync failed for {path}") from last_error

    def state(self) -> dict[str, Any]:
        return self.request("GET", "/api/admin/evidence/state").json()

    def sync_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/admin/evidence/sync", json=payload).json()


def sync(batch_size: int = 25) -> dict[str, Any]:
    base_url = os.environ.get("ALIGN_RADAR_AI_URL", "").strip()
    secret = os.environ.get("ALIGN_RADAR_INGEST_SECRET", "").strip()
    if not base_url or not secret:
        raise RuntimeError("ALIGN_RADAR_AI_URL and ALIGN_RADAR_INGEST_SECRET are required")
    corpus = read_json(DATA_DIR / "corpus.json", {"documents": []})
    documents = [evidence_document(row) for row in corpus.get("documents", [])]
    client = EvidenceClient(base_url, secret)
    state = client.state()
    hashes = state.get("hashes") or {}
    changed = changed_documents(documents, hashes)
    mode = "full" if not hashes else "delta"
    upserted = 0
    for offset in range(0, len(changed), batch_size):
        result = client.sync_batch({
            "mode": mode,
            "source_generated_at_utc": corpus.get("generated_at_utc") or "",
            "source_document_count": len(documents),
            "documents": changed[offset:offset + batch_size],
        })
        upserted += int(result.get("upserted") or 0)
    if not changed:
        client.sync_batch({
            "mode": mode,
            "source_generated_at_utc": corpus.get("generated_at_utc") or "",
            "source_document_count": len(documents),
            "documents": [],
        })
    return {
        "mode": mode,
        "source_documents": len(documents),
        "remote_documents_before": len(hashes),
        "changed": len(changed),
        "upserted": upserted,
        "unchanged": len(documents) - len(changed),
        "summarized": sum(all(str(document.get(field) or "").strip() for field in SUMMARY_FIELDS) for document in documents),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 25:
        raise SystemExit("--batch-size must be between 1 and 25")
    print(json.dumps(sync(args.batch_size), ensure_ascii=False, indent=2))

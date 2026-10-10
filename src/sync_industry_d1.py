from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from typing import Any

import requests

from .common import DATA_DIR, read_json


def industry_news_document(row: dict[str, Any]) -> dict[str, Any]:
    last_seen = row.get("last_seen_at_utc") or row.get("retrieved_at_utc") or ""
    payload = {
        "article_id": str(row.get("id") or ""),
        "title": row.get("title") or "",
        "url": row.get("url") or "",
        "published_date": row.get("published_date") or "",
        "published_at_utc": row.get("published_at_utc") or "",
        "source_id": row.get("source_id") or "",
        "source_name": row.get("source_name") or "",
        "source_url": row.get("source_url") or "",
        "feed_url": row.get("feed_url") or "",
        "source_category": row.get("source_category") or "",
        "source_categories": row.get("source_categories") or [],
        "snippet_en": row.get("snippet_en") or "",
        "summary_ja": row.get("summary_ja") or "",
        "key_takeaway_ja": row.get("key_takeaway_ja") or "",
        "assets": row.get("matched_assets") or [],
        "companies": row.get("matched_companies") or [],
        "topics": row.get("matched_topics") or [],
        "focal_related": bool(row.get("focal_related")),
        "japan_relevant": bool(row.get("japan_relevant")),
        "summary_model": row.get("summary_model") or "",
        "summary_generated_at_utc": row.get("summary_generated_at_utc") or "",
        "first_seen_at_utc": row.get("first_seen_at_utc") or last_seen,
        "last_seen_at_utc": last_seen,
    }
    canonical = json.dumps(
        {key: value for key, value in payload.items() if key not in {"first_seen_at_utc", "last_seen_at_utc"}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["article_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def changed_documents(documents: list[dict[str, Any]], hashes: dict[str, str]) -> list[dict[str, Any]]:
    return [document for document in documents if hashes.get(document["article_id"]) != document["article_sha256"]]


class IndustryNewsClient:
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
        raise RuntimeError(f"D1 industry news sync failed for {path}") from last_error

    def state(self) -> dict[str, Any]:
        return self.request("GET", "/api/admin/industry-news/state").json()

    def sync_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/admin/industry-news/sync", json=payload).json()


def sync(batch_size: int = 25) -> dict[str, Any]:
    base_url = os.environ.get("ALIGN_RADAR_AI_URL", "").strip()
    secret = os.environ.get("ALIGN_RADAR_INGEST_SECRET", "").strip()
    if not base_url or not secret:
        raise RuntimeError("ALIGN_RADAR_AI_URL and ALIGN_RADAR_INGEST_SECRET are required")
    news = read_json(DATA_DIR / "industry-news.json", {"items": []})
    documents = [industry_news_document(row) for row in news.get("items", [])]
    client = IndustryNewsClient(base_url, secret)
    state = client.state()
    hashes = state.get("hashes") or {}
    changed = changed_documents(documents, hashes)
    mode = "full" if not hashes else "delta"
    upserted = 0
    archived = len(hashes)
    batches = [changed[offset:offset + batch_size] for offset in range(0, len(changed), batch_size)] or [[]]
    for batch in batches:
        result = client.sync_batch({
            "mode": mode,
            "source_generated_at_utc": news.get("generated_at_utc") or "",
            "source_document_count": len(documents),
            "documents": batch,
        })
        upserted += int(result.get("upserted") or 0)
        archived = int(result.get("archived_documents") or archived)
    return {
        "mode": mode,
        "source_documents": len(documents),
        "remote_documents_before": len(hashes),
        "changed": len(changed),
        "upserted": upserted,
        "unchanged": len(documents) - len(changed),
        "archived_documents": archived,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 25:
        raise SystemExit("--batch-size must be between 1 and 25")
    print(json.dumps(sync(args.batch_size), ensure_ascii=False, indent=2))

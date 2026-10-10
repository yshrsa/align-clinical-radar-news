from __future__ import annotations

import argparse
import json
import os
from typing import Any

import yaml

from .collect_pubmed import EFETCH_URL, classify, content_hash, extract_records, request_with_backoff
from .common import DATA_DIR, ROOT, atomic_write_json, now_utc, read_json

CORPUS_PATH = DATA_DIR / "corpus.json"


def seed_pmids(pmids: list[str]) -> dict[str, int]:
    cleaned = list(dict.fromkeys(value.strip() for value in pmids if value.strip()))
    if not cleaned:
        return {"requested": 0, "fetched": 0, "added": 0, "updated": 0}
    params = {"db": "pubmed", "id": ",".join(cleaned), "retmode": "xml"}
    api_key = os.environ.get("NCBI_API_KEY", "")
    if api_key:
        params["api_key"] = api_key
    fetched = extract_records(request_with_backoff(EFETCH_URL, params).text)
    pack = yaml.safe_load((ROOT / "pack.yaml").read_text(encoding="utf-8"))
    corpus = read_json(CORPUS_PATH, {"schema_version": "1.0", "documents": []})
    existing: dict[str, dict[str, Any]] = {str(row["pmid"]): row for row in corpus.get("documents", [])}
    timestamp = now_utc()
    added = updated = 0
    for record in fetched:
        classify(record, pack)
        record["content_sha256"] = content_hash(record)
        previous = existing.get(record["pmid"])
        if previous:
            summaries = {key: value for key, value in previous.items() if key.endswith("_ja") or key.startswith("summary_")}
            record.update(summaries)
            record["fetched_at_utc"] = previous.get("fetched_at_utc") or timestamp
            updated += 1
        else:
            record["fetched_at_utc"] = timestamp
            added += 1
        record["updated_at_utc"] = timestamp
        existing[record["pmid"]] = record
    documents = sorted(existing.values(), key=lambda row: (row.get("pub_date", ""), row["pmid"]), reverse=True)
    atomic_write_json(CORPUS_PATH, {"schema_version": "1.0", "generated_at_utc": timestamp, "documents": documents})
    return {"requested": len(cleaned), "fetched": len(fetched), "added": added, "updated": updated}


def main() -> int:
    parser = argparse.ArgumentParser(description="Add explicitly reviewed PubMed records to the corpus.")
    parser.add_argument("pmid", nargs="+")
    args = parser.parse_args()
    print(json.dumps(seed_pmids(args.pmid), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

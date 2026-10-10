from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .common import DATA_DIR, NOT_REPORTED, ROOT, SITE_DATA_DIR, SUMMARY_FIELDS, atomic_write_json, now_utc, read_json

CORPUS_PATH = DATA_DIR / "corpus.json"


def freshness(generated_at_utc: Any, stale_after_hours: float, checked_at: datetime | None = None) -> dict[str, Any]:
    if not generated_at_utc:
        return {"status": "unknown", "age_hours": None}
    try:
        value = datetime.fromisoformat(str(generated_at_utc).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
    except ValueError:
        return {"status": "invalid", "age_hours": None}
    now = checked_at or datetime.now(timezone.utc)
    age_hours = max(0.0, (now - value.astimezone(timezone.utc)).total_seconds() / 3600)
    return {"status": "current" if age_hours <= stale_after_hours else "stale", "age_hours": round(age_hours, 1)}


def public_config(pack: dict[str, Any]) -> dict[str, Any]:
    focal = pack["entities"]["focal_company"]
    return {
        "display_name": pack["display_name"],
        "focal_label": focal.get("label") or f"{focal['name']}関連",
        "assets": [
            {"id": asset["name"], "label": asset.get("label") or asset["name"], "company": asset["company"]}
            for asset in pack["entities"]["assets"]
        ],
        "topics": [
            {"id": topic["id"], "label": topic.get("label") or topic["id"]}
            for topic in pack.get("scope", {}).get("topics", [])
        ],
        "chat_url": pack.get("delivery", {}).get("chat_url", ""),
    }


def validate_document(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("pmid", "title", "url", "content_sha256"):
        if not str(row.get(field) or "").strip():
            errors.append(f"{field} is blank")
    if not isinstance(row.get("publication_types"), list):
        errors.append("publication_types must be a list")
    present = [bool(str(row.get(field) or "").strip()) for field in SUMMARY_FIELDS]
    if any(present) and not all(present):
        errors.append("summary fields must be all present or all absent")
    if any("[要約エラー]" in str(row.get(field) or "") for field in SUMMARY_FIELDS):
        errors.append("error placeholder must not be published")
    if not str(row.get("abstract") or "").strip() and all(present):
        if any(str(row.get(field) or "").strip() != NOT_REPORTED for field in SUMMARY_FIELDS):
            errors.append("abstract-free records must use the missing-information sentinel in all summary fields")
    return errors


def publish() -> dict[str, Any]:
    corpus = read_json(CORPUS_PATH, {"documents": []})
    pack = yaml.safe_load((ROOT / "pack.yaml").read_text(encoding="utf-8"))
    documents = corpus.get("documents", [])
    seen: set[str] = set()
    problems: list[dict[str, Any]] = []
    for row in documents:
        pmid = str(row.get("pmid") or "")
        if pmid in seen:
            problems.append({"pmid": pmid, "errors": ["duplicate PMID"]})
        seen.add(pmid)
        errors = validate_document(row)
        if errors:
            problems.append({"pmid": pmid, "errors": errors})
    summarized = [row for row in documents if all(str(row.get(field) or "").strip() for field in SUMMARY_FIELDS)]
    max_items = int(pack.get("delivery", {}).get("max_items", 150))
    latest = sorted(summarized, key=lambda row: (row.get("pub_date", ""), row.get("pmid", "")), reverse=True)[:max_items]
    timestamp = now_utc()
    freshness_result = freshness(
        corpus.get("generated_at_utc"), float(pack.get("delivery", {}).get("stale_after_hours", 48))
    )
    complete = bool(documents) and len(summarized) == len(documents)
    status = {
        "schema_version": "1.0",
        "checked_at_utc": timestamp,
        "overall_status": "healthy" if not problems and complete and freshness_result["status"] == "current" else "degraded",
        "freshness": freshness_result,
        "counts": {
            "documents": len(documents),
            "summarized": len(summarized),
            "pending": len(documents) - len(summarized),
            "publishable": len(latest),
            "validation_problems": len(problems),
        },
        "blocking_reasons": ["no_publishable_documents"] if not latest else [],
        "problems": problems[:20],
    }
    config = public_config(pack)
    atomic_write_json(DATA_DIR / "quality-status.json", status)
    atomic_write_json(DATA_DIR / "radar-config.json", config)
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("quality-status.json", "radar-config.json"):
        shutil.copy2(DATA_DIR / name, SITE_DATA_DIR / name)
    if problems:
        raise ValueError(f"quality gate failed for {len(problems)} document(s)")
    if not latest:
        raise ValueError("quality gate failed: no fully summarized document is publishable")
    feed = {"schema_version": "1.0", "generated_at_utc": timestamp, "items": latest}
    atomic_write_json(DATA_DIR / "news-latest.json", feed)
    shutil.copy2(DATA_DIR / "news-latest.json", SITE_DATA_DIR / "news-latest.json")
    return status


if __name__ == "__main__":
    print(json.dumps(publish(), ensure_ascii=False, indent=2))

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import requests
import yaml

from .collect_pubmed import classify
from .common import DATA_DIR, ROOT, SITE_DATA_DIR, atomic_write_json, now_utc, read_json

NEWS_PATH = DATA_DIR / "industry-news.json"
STATUS_PATH = DATA_DIR / "industry-news-status.json"
USER_AGENT = "AlignClinicalRadar/1.0 (+https://github.com/yshrsa/align-clinical-radar-news)"


def clean_text(value: str, limit: int = 700) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value or "", flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def published_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return ""


def request_with_backoff(url: str) -> requests.Response:
    delay = 1.0
    last_error: Exception | None = None
    for _ in range(4):
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=45)
            if response.status_code == 429 or response.status_code in {500, 502, 503, 504}:
                retry = response.headers.get("Retry-After")
                time.sleep(float(retry) if retry and retry.isdigit() else delay)
                delay = min(delay * 2, 12)
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(delay)
            delay = min(delay * 2, 12)
    raise RuntimeError(f"source request failed: {url}") from last_error


def child_text(element: ET.Element, *names: str) -> str:
    for name in names:
        child = element.find(name)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def parse_rss(content: bytes, source: dict[str, Any]) -> list[dict[str, Any]]:
    root = ET.fromstring(content)
    items: list[dict[str, Any]] = []
    entries = root.findall(".//item")
    for entry in entries:
        title = clean_text(child_text(entry, "title"), 300)
        url = child_text(entry, "link", "guid")
        description = child_text(
            entry,
            "description",
            "{http://purl.org/rss/1.0/modules/content/}encoded",
            "{http://www.w3.org/2005/Atom}summary",
        )
        date = child_text(entry, "pubDate", "{http://purl.org/dc/elements/1.1/}date")
        categories = [clean_text(node.text or "", 80) for node in entry.findall("category") if clean_text(node.text or "", 80)]
        if title and url:
            items.append({"title": title, "url": url, "published_date": published_date(date), "snippet_en": clean_text(description), "source_categories": categories[:8]})
    return items


def parse_align_newsroom(content: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    pattern = re.compile(
        r'<div class="news-item">\s*<h4>(?P<date>.*?)</h4>\s*<h4[^>]*>\s*<a href="(?P<url>[^"]+)">(?P<title>.*?)</a>',
        flags=re.I | re.S,
    )
    return [
        {
            "title": clean_text(match.group("title"), 300),
            "url": urljoin(source["source_url"], html.unescape(match.group("url"))),
            "published_date": published_date(match.group("date")) or datetime.strptime(clean_text(match.group("date")), "%m/%d/%Y").date().isoformat(),
            "snippet_en": "",
            "source_categories": ["Press Release"],
        }
        for match in pattern.finditer(content)
    ]


def item_id(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def prepare_item(raw: dict[str, Any], source: dict[str, Any], pack: dict[str, Any], retrieved_at: str) -> dict[str, Any]:
    row = {"title": raw["title"], "abstract": raw.get("snippet_en") or ""}
    classify(row, pack)
    if source["id"] == "align-official":
        row["focal_related"] = True
        row["matched_companies"] = sorted(set([*row["matched_companies"], pack["entities"]["focal_company"]["name"]]))
    return {
        "id": item_id(raw["url"]),
        "title": raw["title"],
        "url": raw["url"],
        "published_date": raw.get("published_date") or "",
        "snippet_en": raw.get("snippet_en") or "",
        "source_id": source["id"],
        "source_name": source["name"],
        "source_url": source["source_url"],
        "feed_url": source["feed_url"],
        "source_category": source["category"],
        "source_categories": raw.get("source_categories") or [],
        "matched_assets": row["matched_assets"],
        "matched_companies": row["matched_companies"],
        "matched_topics": row["matched_topics"],
        "focal_related": row["focal_related"],
        "japan_relevant": row["japan_relevant"],
        "retrieved_at_utc": retrieved_at,
    }


def relevant(item: dict[str, Any], source: dict[str, Any], terms: list[str]) -> bool:
    if source.get("include_all"):
        return True
    searchable = f"{item.get('title', '')}\n{item.get('snippet_en', '')}".lower()
    return any(term.lower() in searchable for term in terms)


def merge_items(existing: list[dict[str, Any]], fetched: list[dict[str, Any]], max_items: int) -> tuple[list[dict[str, Any]], int]:
    rows = {str(item["id"]): item for item in existing if item.get("id")}
    new_count = sum(1 for item in fetched if item["id"] not in rows)
    rows.update({item["id"]: item for item in fetched})
    merged = sorted(rows.values(), key=lambda item: (item.get("published_date") or "", item.get("retrieved_at_utc") or ""), reverse=True)
    return merged[:max_items], new_count


def collect() -> dict[str, Any]:
    pack = yaml.safe_load((ROOT / "pack.yaml").read_text(encoding="utf-8"))
    settings = pack["sources"]["industry_news"]
    timestamp = now_utc()
    accepted: list[dict[str, Any]] = []
    source_status: list[dict[str, Any]] = []
    for source in settings["feeds"]:
        try:
            response = request_with_backoff(source["feed_url"])
            raw_items = parse_align_newsroom(response.text, source) if source["kind"] == "html-align-newsroom" else parse_rss(response.content, source)
            selected = [raw for raw in raw_items if relevant(raw, source, settings["relevance_terms"])]
            accepted.extend(prepare_item(raw, source, pack, timestamp) for raw in selected)
            source_status.append({
                "id": source["id"], "name": source["name"], "source_url": source["source_url"], "feed_url": source["feed_url"],
                "status": "ok", "fetched": len(raw_items), "accepted": len(selected),
            })
        except Exception as exc:
            source_status.append({
                "id": source["id"], "name": source["name"], "source_url": source["source_url"], "feed_url": source["feed_url"],
                "status": "error", "fetched": 0, "accepted": 0, "error": str(exc)[:240],
            })
    previous = read_json(NEWS_PATH, {"items": []})
    merged, new_count = merge_items(previous.get("items") or [], accepted, int(settings["max_items"]))
    payload = {"schema_version": "1.0", "generated_at_utc": timestamp, "items": merged}
    status = {
        "schema_version": "1.0", "checked_at_utc": timestamp,
        "overall_status": "healthy" if all(row["status"] == "ok" for row in source_status) else "degraded",
        "counts": {"sources": len(source_status), "healthy_sources": sum(row["status"] == "ok" for row in source_status), "items": len(merged), "new": new_count},
        "sources": source_status,
    }
    atomic_write_json(NEWS_PATH, payload)
    atomic_write_json(STATUS_PATH, status)
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(SITE_DATA_DIR / NEWS_PATH.name, payload)
    atomic_write_json(SITE_DATA_DIR / STATUS_PATH.name, status)
    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect public orthodontic and medtech industry news")
    parser.parse_args()
    print(json.dumps(collect(), ensure_ascii=False, indent=2))

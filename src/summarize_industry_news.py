from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from .common import DATA_DIR, SITE_DATA_DIR, atomic_write_json, now_utc, read_json

NEWS_PATH = DATA_DIR / "industry-news.json"
MODEL = "@cf/qwen/qwen3.8-27b"
OLLAMA_MODEL = os.environ.get("ALIGN_RADAR_OLLAMA_MODEL", "qwen3.8:27b")
OLLAMA_URL = os.environ.get("ALIGN_RADAR_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
SUMMARY_FIELDS = ("summary_ja", "key_takeaway_ja")
MISSING = "配信元抜粋に記載なし"

PROMPT = """あなたは歯科・医療機器業界ニュースの編集者です。
以下の配信元タイトルと抜粋だけを根拠に、JSONオブジェクトだけを返してください。
推測、外部知識、数値の補完は禁止です。広告・プレスリリース・ホワイトペーパーの場合は断定的な臨床事実として扱わず、その性質が分かる表現にしてください。
summary_jaは自然な日本語で2文以内、key_takeaway_jaはClinical Operations・治療計画・業務運用への要点を1文で記載してください。
根拠がない場合は「配信元抜粋に記載なし」としてください。

フィールド: summary_ja, key_takeaway_ja
Source: {source_name}
Title: {title}
Excerpt: {snippet_en}
"""


class DailyBudgetExhausted(RuntimeError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("summary response did not contain a JSON object")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("summary response must be a JSON object")
    return value


def guard_summary(value: dict[str, Any]) -> dict[str, str]:
    result = {}
    for field in SUMMARY_FIELDS:
        text = re.sub(r"\s+", " ", str(value.get(field) or "")).strip()
        result[field] = text[:900] if text else MISSING
    return result


def prompt_for(item: dict[str, Any]) -> str:
    return PROMPT.format(
        source_name=item.get("source_name") or "",
        title=item.get("title") or "",
        snippet_en=item.get("snippet_en") or "[no excerpt provided]",
    )


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def call_cloudflare(item: dict[str, Any]) -> tuple[dict[str, Any], str]:
    base_url = os.environ.get("ALIGN_RADAR_AI_URL", "").strip().rstrip("/")
    token = os.environ.get("ALIGN_RADAR_INGEST_SECRET", "").strip()
    if not base_url or not token:
        raise RuntimeError("ALIGN_RADAR_AI_URL and ALIGN_RADAR_INGEST_SECRET are required")
    content_hash = hashlib.sha256(f"{item.get('title','')}\n{item.get('snippet_en','')}".encode("utf-8")).hexdigest()
    request_key = hashlib.sha256(f"news:{item.get('id','')}:{content_hash}:{utc_day()}".encode("utf-8")).hexdigest()
    delay = 2.0
    last_error: Exception | None = None
    for _ in range(3):
        try:
            response = requests.post(
                f"{base_url}/api/admin/summarize-news",
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": request_key},
                json={"source_name": item.get("source_name"), "title": item.get("title"), "snippet_en": item.get("snippet_en")},
                timeout=180,
            )
            if response.status_code == 429:
                if response.headers.get("Content-Type", "").startswith("application/json") and response.json().get("budget_exhausted"):
                    raise DailyBudgetExhausted("daily free summary budget exhausted")
                retry = response.headers.get("Retry-After")
                time.sleep(float(retry) if retry and retry.isdigit() else delay)
                delay = min(delay * 2, 30)
                continue
            response.raise_for_status()
            payload = response.json()
            output = payload.get("response")
            if not isinstance(output, str) or not output.strip():
                raise ValueError("Cloudflare Qwen returned an empty response")
            return extract_json(output), str(payload.get("model") or MODEL)
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(delay)
            delay = min(delay * 2, 30)
    raise RuntimeError("Cloudflare news summary request failed after retries") from last_error


def call_ollama(item: dict[str, Any], timeout: int = 300) -> tuple[dict[str, Any], str]:
    response = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "messages": [{"role": "user", "content": prompt_for(item)}], "stream": False, "format": "json", "options": {"temperature": 0.1}},
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text[:500]}")
    output = response.json().get("message", {}).get("content")
    if not isinstance(output, str) or not output.strip():
        raise ValueError("Ollama returned an empty response")
    return extract_json(output), f"ollama/{OLLAMA_MODEL}"


def summarize(limit: int = 4, backend: str = "cloudflare") -> dict[str, Any]:
    payload = read_json(NEWS_PATH, {"schema_version": "1.0", "items": []})
    pending = [item for item in payload.get("items", []) if not all(str(item.get(field) or "").strip() for field in SUMMARY_FIELDS)]
    if limit:
        pending = pending[:limit]
    completed = 0
    failures: list[dict[str, str]] = []
    stopped_reason: str | None = None
    for index, item in enumerate(pending, start=1):
        try:
            result, model = call_ollama(item) if backend == "ollama" else call_cloudflare(item)
            item.update(guard_summary(result))
            item["summary_model"] = model
            item["summary_generated_at_utc"] = now_utc()
            completed += 1
            atomic_write_json(NEWS_PATH, payload)
            atomic_write_json(SITE_DATA_DIR / NEWS_PATH.name, payload)
            print(f"[{index}/{len(pending)}] summarized {item.get('source_name')}: {item.get('title','')[:70]}", flush=True)
        except DailyBudgetExhausted:
            stopped_reason = "daily_free_summary_budget_exhausted"
            break
        except Exception as exc:
            failures.append({"id": str(item.get("id") or ""), "error": f"{type(exc).__name__}: {exc}"})
    return {
        "backend": backend, "pending_at_start": len(pending), "completed": completed, "failed": len(failures),
        "stopped_reason": stopped_reason,
        "remaining": sum(not all(str(item.get(field) or "").strip() for field in SUMMARY_FIELDS) for item in payload.get("items", [])),
        "failures": failures,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate source-grounded Japanese summaries for industry news")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--backend", choices=("cloudflare", "ollama"), default="cloudflare")
    args = parser.parse_args()
    print(json.dumps(summarize(args.limit, args.backend), ensure_ascii=False, indent=2))

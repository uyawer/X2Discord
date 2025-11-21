import html
import re
from typing import Any, Dict, List, Optional

import httpx
import feedparser


class RssHubClient:
    MINIMUM_RESULTS = 1
    MAXIMUM_RESULTS = 100
    _HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

    def __init__(self, base_url: str = "http://localhost:1200", refresh_seconds: int | None = None):  # pragma: no cover - HTTP client wrapper
        normalized = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=normalized,
            headers={"User-Agent": "x2discord/1.0"},
            timeout=30,
        )
        self._refresh_seconds = refresh_seconds

    @property
    def base_url(self) -> str:
        return str(self._client.base_url).rstrip("/")

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_latest_posts(self, account: str, max_results: int = 1) -> List[Dict[str, str]]:
        normalized = account.strip().lstrip("@")
        limit = max(self.MINIMUM_RESULTS, min(max_results, self.MAXIMUM_RESULTS))
        params = {"refresh": self._refresh_seconds} if self._refresh_seconds is not None else None
        response = await self._client.get(f"/twitter/user/{normalized}", params=params)
        response.raise_for_status()
        payload = response.text or ""
        parsed = feedparser.parse(payload)
        entries = parsed.entries or []
        processed: List[Dict[str, str]] = []
        for idx, entry in enumerate(entries[:limit]):
            text = entry.get("description") or entry.get("summary") or entry.get("title") or ""
            raw = {
                "guid": entry.get("id") or entry.get("guid") or entry.get("link"),
                "description": text,
                "link": entry.get("link"),
            }
            processed.append(
                {
                    "id": self._entry_id(raw, normalized, idx),
                    "text": self._strip_html(text),
                    "link": raw.get("link") or f"https://x.com/{normalized}",
                }
            )
        return processed

    @classmethod
    def _entry_id(cls, raw: Dict[str, Any], normalized: str, index: int) -> str:
        guid = raw.get("guid")
        if isinstance(guid, dict):
            candidate = guid.get("#text") or guid.get("value")
        else:
            candidate = guid
        if not candidate:
            candidate = raw.get("id") or raw.get("link") or f"{normalized}-{index}"
        return str(candidate)

    @classmethod
    def _strip_html(cls, value: str) -> str:
        if not value:
            return ""
        text = html.unescape(value)
        return cls._HTML_TAG_PATTERN.sub("", text).strip()
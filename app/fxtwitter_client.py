import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


class FxTwitterClient:
    """api.fxtwitter.com を使って X のポストを取得するクライアント。"""

    BASE_URL = "https://api.fxtwitter.com"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"User-Agent": "x2discord/1.0"},
            timeout=30,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_posts(
        self,
        account: str,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """ポストを取得する。

        Returns:
            (posts, top_cursor)
            - posts: ポスト辞書のリスト（新しい順）
            - top_cursor: 次回呼び出し時に渡すカーソル（より新しいポストを取得するため）
        """
        normalized = account.strip().lstrip("@")
        params: Dict[str, str] = {}
        if cursor:
            params["cursor"] = cursor

        response = await self._client.get(
            f"/2/profile/{normalized}/media",
            params=params or None,
        )
        response.raise_for_status()
        data = response.json()

        results = data.get("results") or []
        cursor_data = data.get("cursor") or {}
        top_cursor: Optional[str] = cursor_data.get("top")

        posts: List[Dict[str, Any]] = []
        for item in results:
            if item.get("type") != "status":
                continue
            post_id: str = item.get("id", "")
            url: str = item.get("url", "")
            text: str = item.get("text", "")
            # fxtwitter では reposted_by が非 null ならリポスト
            is_repost: bool = item.get("reposted_by") is not None
            # quote フィールドが存在すれば引用ツイート
            is_quote: bool = item.get("quote") is not None

            posts.append(
                {
                    "id": post_id,
                    "text": text,
                    "link": url,
                    "raw_text": text,
                    "is_repost": is_repost,
                    "is_quote": is_quote,
                }
            )

        return posts, top_cursor

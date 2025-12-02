import logging
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisLinkStore:
    """Redisを使用して送信済みリンクを永続化するストア"""

    def __init__(self, redis_url: str, max_links_per_channel: int = 1000):
        self.redis_url = redis_url
        self.max_links_per_channel = max_links_per_channel
        self._client: Optional[redis.Redis] = None

    async def connect(self) -> None:
        """Redisに接続"""
        try:
            self._client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await self._client.ping()
            logger.info("Connected to Redis at %s", self.redis_url)
        except Exception as exc:
            logger.error("Failed to connect to Redis: %s", exc)
            # Redis接続失敗時はNoneのままにして、後でフォールバック処理
            self._client = None

    async def close(self) -> None:
        """Redis接続を閉じる"""
        if self._client:
            await self._client.aclose()
            logger.info("Closed Redis connection")

    def _get_key(self, channel_id: int) -> str:
        """チャンネルIDからRedisキーを生成"""
        return f"x2discord:sent_links:{channel_id}"

    async def add_link(self, channel_id: int, link: str) -> bool:
        """送信済みリンクを追加（重複チェック付き）"""
        if not self._client:
            return False

        try:
            key = self._get_key(channel_id)
            # リストの左側に追加
            await self._client.lpush(key, link)
            # サイズ制限を適用（古いものを削除）
            await self._client.ltrim(key, 0, self.max_links_per_channel - 1)
            return True
        except Exception as exc:
            logger.warning("Failed to add link to Redis: %s", exc)
            return False

    async def has_link(self, channel_id: int, link: str) -> bool:
        """リンクが既に送信済みかチェック"""
        if not self._client:
            return False

        try:
            key = self._get_key(channel_id)
            # リスト内を検索（O(n)だが、サイズが限定されているので許容範囲）
            links = await self._client.lrange(key, 0, -1)
            return link in links
        except Exception as exc:
            logger.warning("Failed to check link in Redis: %s", exc)
            return False

    async def get_all_links(self, channel_id: int) -> list[str]:
        """チャンネルの全送信済みリンクを取得（デバッグ用）"""
        if not self._client:
            return []

        try:
            key = self._get_key(channel_id)
            return await self._client.lrange(key, 0, -1)
        except Exception as exc:
            logger.warning("Failed to get links from Redis: %s", exc)
            return []

    async def clear_channel(self, channel_id: int) -> bool:
        """チャンネルの送信履歴をクリア"""
        if not self._client:
            return False

        try:
            key = self._get_key(channel_id)
            await self._client.delete(key)
            return True
        except Exception as exc:
            logger.warning("Failed to clear channel links in Redis: %s", exc)
            return False

    @property
    def is_connected(self) -> bool:
        """Redis接続が有効かどうか"""
        return self._client is not None


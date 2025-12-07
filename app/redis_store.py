import logging
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisLinkStore:
    """Redisを使用して送信済みリンクを永続化するストア"""

    def __init__(self, redis_url: str, max_links_per_channel: int = 1000, ttl_days: int = 30):
        self.redis_url = redis_url
        self.max_links_per_channel = max_links_per_channel
        self.ttl_seconds = ttl_days * 24 * 60 * 60  # 日数を秒に変換
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
            # SETに追加（O(1)）
            await self._client.sadd(key, link)
            # TTLを設定して古いデータを自動削除
            await self._client.expire(key, self.ttl_seconds)
            
            # サイズ制限を確認（必要に応じて古いメンバーを削除）
            size = await self._client.scard(key)
            if size > self.max_links_per_channel:
                # 超過分をランダムに削除（SETなので順序がないため）
                excess = size - self.max_links_per_channel
                members = await self._client.srandmember(key, excess)
                if members:
                    await self._client.srem(key, *members)
                    logger.info("Removed %d excess links from channel %d", excess, channel_id)
            return True
        except Exception as exc:
            logger.warning("Failed to add link to Redis: %s", exc)
            return False

    async def has_link(self, channel_id: int, link: str) -> bool:
        """リンクが既に送信済みかチェック（O(1)）"""
        if not self._client:
            return False

        try:
            key = self._get_key(channel_id)
            # SETのメンバーシップチェック（O(1)）
            return await self._client.sismember(key, link)
        except Exception as exc:
            logger.warning("Failed to check link in Redis: %s", exc)
            return False

    async def get_all_links(self, channel_id: int) -> list[str]:
        """チャンネルの全送信済みリンクを取得（デバッグ用）"""
        if not self._client:
            return []

        try:
            key = self._get_key(channel_id)
            members = await self._client.smembers(key)
            return list(members) if members else []
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

    def _get_last_tweet_key(self, channel_id: int, account: str) -> str:
        """last_tweet_id用のRedisキーを生成"""
        return f"x2discord:last_tweet:{channel_id}:{account}"

    async def get_last_tweet_id(self, channel_id: int, account: str) -> str | None:
        """最後に処理したツイートIDを取得"""
        if not self._client:
            return None

        try:
            key = self._get_last_tweet_key(channel_id, account)
            return await self._client.get(key)
        except Exception as exc:
            logger.warning("Failed to get last_tweet_id from Redis: %s", exc)
            return None

    async def set_last_tweet_id(self, channel_id: int, account: str, tweet_id: str) -> bool:
        """最後に処理したツイートIDを保存"""
        if not self._client:
            return False

        try:
            key = self._get_last_tweet_key(channel_id, account)
            await self._client.set(key, tweet_id)
            return True
        except Exception as exc:
            logger.warning("Failed to set last_tweet_id in Redis: %s", exc)
            return False

    @property
    def is_connected(self) -> bool:
        """Redis接続が有効かどうか"""
        return self._client is not None


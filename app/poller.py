import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple

import httpx

from .discord_bot import DiscordNotifier
from .store import Subscription, SubscriptionStore
from .rsshub_client import RssHubClient
from .redis_store import RedisLinkStore
from .utils import normalize_keyword_text

logger = logging.getLogger(__name__)


class TweetPoller:
    ACCOUNT_MIN_INTERVAL_SECONDS = 30
    MAX_SENT_LINKS_PER_CHANNEL = 1000  # 各チャンネルで記憶する最大リンク数
    _HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

    def __init__(
        self,
        notifier: DiscordNotifier,
        store: SubscriptionStore,
        rsshub_client: RssHubClient,
        redis_store: Optional[RedisLinkStore] = None,
    ):
        self.notifier = notifier
        self.store = store
        self.rsshub_client = rsshub_client
        self.redis_store = redis_store
        self._stop_event = asyncio.Event()
        self._state: Dict[Tuple[int, str], Dict[str, object]] = {}
        self._last_seen: Dict[str, str] = {}
        self._account_last_call: Dict[str, float] = {}
        # メモリキャッシュ（高速アクセス用）+ Redisへのフォールバック
        self._sent_links_cache: Dict[int, deque] = defaultdict(lambda: deque(maxlen=self.MAX_SENT_LINKS_PER_CHANNEL))

    async def start(self) -> None:
        while not self._stop_event.is_set():
            subscriptions = await self.store.get_subscriptions()
            if not subscriptions:
                await asyncio.sleep(5)
                continue
            now = time.monotonic()
            for subscription in subscriptions:
                await self._maybe_poll_subscription(subscription, now)
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._stop_event.set()

    async def _maybe_poll_subscription(self, subscription: Subscription, now: float) -> None:
        key = (subscription.channel_id, subscription.account)
        state = self._state.setdefault(
            key,
            {
                "next_run": 0.0,
                "last_id": None,
                "backoff_multiplier": 1,
            },
        )
        # subscriptionオブジェクトから直接last_tweet_idを取得
        if state.get("last_id") is None:
            if subscription.last_tweet_id:
                state["last_id"] = subscription.last_tweet_id
                self._last_seen[subscription.account] = subscription.last_tweet_id
                logger.info(
                    "Initialized last_tweet_id for %s in channel %s: %s",
                    subscription.account,
                    subscription.channel_id,
                    subscription.last_tweet_id,
                )
            else:
                persisted = await self.store.get_last_tweet_id(subscription.channel_id, subscription.account)
                if persisted:
                    state["last_id"] = persisted
                    self._last_seen[subscription.account] = persisted
                    logger.info(
                        "Loaded persisted last_tweet_id for %s in channel %s: %s",
                        subscription.account,
                        subscription.channel_id,
                        persisted,
                    )
        if not self._ensure_account_interval(subscription, state, now):
            return
        if now < state["next_run"]:
            return
        logger.info(
            "Polling %s for channel %s (interval %s sec, next_run %.1f)",
            subscription.account,
            subscription.channel_id,
            subscription.interval_seconds,
            state["next_run"],
        )
        await self._poll_subscription(subscription, state)

    async def _poll_subscription(self, subscription: Subscription, state: Dict[str, object]) -> None:
        seen_id = state.get("last_id")
        max_results = 1 if not seen_id else 5
        try:
            request_time = time.monotonic()
            self._account_last_call[subscription.account] = request_time
            posts = await self.rsshub_client.fetch_latest_posts(subscription.account, max_results=max_results)
        except httpx.HTTPStatusError as exc:
            self._handle_http_error(subscription, state, exc)
            return
        except Exception as exc:
            logger.warning("Failed to fetch posts for %s: %s", subscription.account, exc)
            self._schedule_next(subscription, state)
            return
        self._schedule_next(subscription, state)
        state["backoff_multiplier"] = 1
        if not posts:
            return
        latest_id = posts[0]["id"]
        if not seen_id:
            state["last_id"] = latest_id
            self._last_seen[subscription.account] = latest_id
            await self.store.set_last_tweet_id(
                subscription.channel_id, subscription.account, latest_id
            )
            logger.info(
                "First poll for %s in channel %s, initialized last_tweet_id to %s (no posts sent)",
                subscription.account,
                subscription.channel_id,
                latest_id,
            )
            return
        new_posts: list[tuple[dict, str]] = []
        for entry in posts:
            # 既に見たIDに到達したら、それ以降は古いポストなのでスキップ
            if seen_id and entry["id"] == seen_id:
                logger.debug("Reached already seen post %s for %s", seen_id, subscription.account)
                break

            # 重複チェック用のキーを複数生成（IDとリンクの両方）
            entry_id = entry.get("id")
            entry_link = entry.get("link")

            # IDまたはリンクが既に送信済みならスキップ（Redisとメモリキャッシュをチェック）
            already_sent = await self._is_already_sent(subscription.channel_id, entry_id, entry_link)
            if already_sent:
                continue

            # 送信用のキーはリンクを優先、なければID
            send_key = entry_link or entry_id
            if not send_key:
                logger.warning("Post has no id or link, skipping: %s", entry)
                continue

            if not self._should_include(entry, subscription):
                continue
            new_posts.append((entry, send_key))
        if not new_posts:
            state["last_id"] = latest_id
            self._last_seen[subscription.account] = latest_id
            await self.store.set_last_tweet_id(
                subscription.channel_id, subscription.account, latest_id
            )
            logger.debug(
                "No new posts to send for %s in channel %s, updated last_tweet_id to %s",
                subscription.account,
                subscription.channel_id,
                latest_id,
            )
            return
        for entry, send_key in reversed(new_posts):
            await self.notifier.send_message(
                subscription.channel_id,
                subscription.account,
                entry.get("text", ""),
                entry.get("link", ""),
                thread_id=subscription.thread_id,
            )
            # IDとリンクの両方を記録して重複を防ぐ（RedisとメモリキャッシュへI）
            await self._record_sent_link(subscription.channel_id, send_key)
            if entry.get("id") and entry.get("id") != send_key:
                await self._record_sent_link(subscription.channel_id, entry.get("id"))
            if entry.get("link") and entry.get("link") != send_key:
                await self._record_sent_link(subscription.channel_id, entry.get("link"))
            logger.debug("Sent and recorded post %s for %s", send_key, subscription.account)

        state["last_id"] = latest_id
        self._last_seen[subscription.account] = latest_id
        await self.store.set_last_tweet_id(
            subscription.channel_id, subscription.account, latest_id
        )
        logger.info(
            "Updated last_tweet_id for %s in channel %s to %s",
            subscription.account,
            subscription.channel_id,
            latest_id,
        )

    def _schedule_next(self, subscription: Subscription, state: Dict[str, object]) -> None:
        state["next_run"] = time.monotonic() + subscription.interval_seconds

    def _handle_http_error(
        self, subscription: Subscription, state: Dict[str, object], exc: httpx.HTTPStatusError
    ) -> None:
        status = exc.response.status_code
        if status == 429:
            backoff = self._compute_backoff(subscription, state, exc.response)
            state["next_run"] = max(state.get("next_run", 0.0), time.monotonic() + backoff)
            logger.warning("Rate limited for %s: backing off %s seconds", subscription.account, backoff)
            state["backoff_multiplier"] = min(state.get("backoff_multiplier", 1) * 2, 16)
            return
        if status == 403:
            backoff = max(subscription.interval_seconds, 60)
            state["next_run"] = max(state.get("next_run", 0.0), time.monotonic() + backoff)
            logger.warning("Access denied for %s: deferring %s seconds", subscription.account, backoff)
            return
        logger.warning("Failed to fetch posts for %s: %s", subscription.account, exc)
        self._schedule_next(subscription, state)

    def _ensure_account_interval(self, subscription: Subscription, state: Dict[str, object], now: float) -> bool:
        last_call = self._account_last_call.get(subscription.account)
        if last_call and now < last_call + self.ACCOUNT_MIN_INTERVAL_SECONDS:
            delay = last_call + self.ACCOUNT_MIN_INTERVAL_SECONDS
            state["next_run"] = max(state.get("next_run", 0.0), delay)
            return False
        return True

    def _compute_backoff(
        self, subscription: Subscription, state: Dict[str, object], response: httpx.Response
    ) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return max(int(retry_after), subscription.interval_seconds)
        multiplier = state.get("backoff_multiplier", 1)
        base = max(subscription.interval_seconds, 60)
        return base * multiplier

    def _should_include(self, entry: dict, subscription: Subscription) -> bool:
        text = entry.get("text", "")
        raw_text = entry.get("raw_text", "")
        if not subscription.include_reposts and self._is_repost(text):
            return False
        if not subscription.include_quotes and self._is_quote(text, raw_text):
            return False
        combined = self._normalize_entry_text(text, raw_text)
        exclude_keywords = subscription.exclude_keywords or ()
        if exclude_keywords and any(keyword in combined for keyword in exclude_keywords):
            return False
        include_keywords = subscription.include_keywords or ()
        if include_keywords and not any(keyword in combined for keyword in include_keywords):
            return False
        return True

    def _normalize_entry_text(self, text: str, raw_text: str) -> str:
        parts: list[str] = []
        if text:
            parts.append(normalize_keyword_text(text))
        if raw_text:
            cleaned = self._HTML_TAG_PATTERN.sub(" ", raw_text)
            parts.append(normalize_keyword_text(cleaned))
        return " ".join(part for part in parts if part)

    @staticmethod
    def _is_repost(text: str) -> bool:
        for line in text.splitlines():
            candidate = line.strip().lower()
            if not candidate:
                continue
            if candidate.startswith("リツイート"):
                return True
            if candidate.startswith("rt"):
                rest = candidate[2:]
                if not rest or not rest[0].isalnum():
                    return True
        return False

    @staticmethod
    def _is_quote(text: str, raw_text: str | None = None) -> bool:
        lower = text.lower()
        raw_lower = (raw_text or "").lower()
        return (
            "quote tweet" in lower
            or "引用" in lower
            or "quoted tweet" in lower
            or "rsshub-quote" in raw_lower
        )

    async def _is_already_sent(self, channel_id: int, entry_id: str | None, entry_link: str | None) -> bool:
        """IDまたはリンクが既に送信済みかチェック（Redisのみ）"""
        if not self.redis_store or not self.redis_store.is_connected:
            logger.warning("Redis is not available, cannot check for duplicates")
            return False

        # IDとリンクの両方をチェック
        if entry_id and await self.redis_store.has_link(channel_id, entry_id):
            logger.debug("Found duplicate in Redis (by ID): %s", entry_id)
            return True
        if entry_link and await self.redis_store.has_link(channel_id, entry_link):
            logger.debug("Found duplicate in Redis (by link): %s", entry_link)
            return True

        return False

    async def _record_sent_link(self, channel_id: int, link: str) -> None:
        """送信済みリンクをRedisに記録"""
        if not self.redis_store or not self.redis_store.is_connected:
            logger.warning("Redis is not available, cannot persist sent link: %s", link)
            return

        await self.redis_store.add_link(channel_id, link)


import asyncio
import logging
import time
from typing import Dict, Tuple

import httpx

from .discord_bot import DiscordNotifier
from .store import Subscription, SubscriptionStore
from .rsshub_client import RssHubClient

logger = logging.getLogger(__name__)


class TweetPoller:
    ACCOUNT_MIN_INTERVAL_SECONDS = 30

    def __init__(self, notifier: DiscordNotifier, store: SubscriptionStore, rsshub_client: RssHubClient):
        self.notifier = notifier
        self.store = store
        self.rsshub_client = rsshub_client
        self._stop_event = asyncio.Event()
        self._state: Dict[Tuple[int, str], Dict[str, object]] = {}
        self._last_seen: Dict[str, str] = {}
        self._account_last_call: Dict[str, float] = {}

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
                "last_id": self._last_seen.get(subscription.account),
                "backoff_multiplier": 1,
            },
        )
        if state.get("last_id") is None:
            persisted = await self.store.get_last_tweet_id(subscription.channel_id, subscription.account)
            if persisted:
                state["last_id"] = persisted
                self._last_seen[subscription.account] = persisted
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
            return
        new_posts = []
        for entry in posts:
            if seen_id and entry["id"] == seen_id:
                break
            if not self._should_include(entry.get("text", ""), subscription):
                continue
            new_posts.append(entry)
        if not new_posts:
            state["last_id"] = latest_id
            self._last_seen[subscription.account] = latest_id
            await self.store.set_last_tweet_id(
                subscription.channel_id, subscription.account, latest_id
            )
            return
        for entry in reversed(new_posts):
            await self.notifier.send_message(
                subscription.channel_id,
                subscription.account,
                entry.get("text", ""),
                entry.get("link", ""),
                thread_id=subscription.thread_id,
            )
        state["last_id"] = latest_id
        self._last_seen[subscription.account] = latest_id
        await self.store.set_last_tweet_id(
            subscription.channel_id, subscription.account, latest_id
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

    def _should_include(self, text: str, subscription: Subscription) -> bool:
        if not subscription.include_reposts and self._is_repost(text):
            return False
        if not subscription.include_quotes and self._is_quote(text):
            return False
        return True

    @staticmethod
    def _is_repost(text: str) -> bool:
        candidate = text.strip().lower()
        return candidate.startswith("rt @") or candidate.startswith("rt ") or candidate.startswith("リツイート")

    @staticmethod
    def _is_quote(text: str) -> bool:
        lower = text.lower()
        return "quote tweet" in lower or "引用" in text or "quoted tweet" in lower

from __future__ import annotations

import pytest

from app.poller import TweetPoller
from app.store import Subscription


class DummyNotifier:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, str, str, int | None]] = []

    async def send_message(
        self,
        channel_id: int,
        account: str,
        text: str,
        link: str,
        thread_id: int | None = None,
    ) -> None:
        self.sent_messages.append((channel_id, account, text, link, thread_id))


class DummyStore:
    def __init__(self) -> None:
        self.last_ids: dict[tuple[int, str], str] = {}

    async def get_subscriptions(self) -> list[Subscription]:
        return []

    async def get_last_tweet_id(self, channel_id: int, account: str) -> str | None:
        return self.last_ids.get((channel_id, account))

    async def set_last_tweet_id(self, channel_id: int, account: str, tweet_id: str) -> None:
        self.last_ids[(channel_id, account)] = tweet_id


class DummyRssHubClient:
    def __init__(self, batches: list[list[dict[str, str]]]) -> None:
        self.batches = batches
        self.calls = 0

    async def fetch_latest_posts(self, account: str, max_results: int | None = None) -> list[dict[str, str]]:
        batch = self.batches[min(self.calls, len(self.batches) - 1)]
        self.calls += 1
        return batch


@pytest.mark.asyncio
async def test_duplicate_link_only_sent_once() -> None:
    notifier = DummyNotifier()
    store = DummyStore()
    rsshub = DummyRssHubClient(
        [
            [{"id": "first", "link": "https://x.com/post/1", "text": "First"}],
            [
                {"id": "second", "link": "https://x.com/post/1", "text": "Duplicate"},
                {"id": "first", "link": "https://x.com/post/1", "text": "First"},
            ],
        ]
    )
    poller = TweetPoller(notifier, store, rsshub)
    subscription = Subscription(channel_id=123, account="foo", interval_seconds=60)
    state = {"next_run": 0.0, "last_id": "initial", "backoff_multiplier": 1}

    await poller._poll_subscription(subscription, state)
    assert len(notifier.sent_messages) == 1
    assert notifier.sent_messages[0][3] == "https://x.com/post/1"
    assert "https://x.com/post/1" in poller._sent_links[subscription.channel_id]

    await poller._poll_subscription(subscription, state)
    assert len(notifier.sent_messages) == 1
    assert poller._sent_links[subscription.channel_id] == {"https://x.com/post/1"}


def test_is_repost_detection() -> None:
    repost_samples = [
        "RT @foo retweeted text",
        "rt @foo 内容",
        "RT Test",  # has non-breaking space
        "rt",  # bare prefix
        "リツイート テスト",
    ]
    for text in repost_samples:
        assert TweetPoller._is_repost(text)

    assert not TweetPoller._is_repost("普通の投稿")
    assert not TweetPoller._is_repost("This is a quote tweet")


def test_is_quote_detection() -> None:
    entry = {
        "text": "明日の夜９時から！",
        "raw_text": "<div class=\"rsshub-quote\">引用本文</div>",
    }
    assert TweetPoller._is_quote(entry["text"], entry["raw_text"])
    assert not TweetPoller._is_quote("ノーマル投稿", "<div>本文</div>")

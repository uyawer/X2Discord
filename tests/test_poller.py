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
    async def get_subscriptions(self) -> list[Subscription]:
        return []



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
            [{"id": "https://x.com/post/1", "link": "https://x.com/post/1", "text": "First"}],
            [
                {"id": "https://x.com/post/2", "link": "https://x.com/post/2", "text": "Second"},
                {"id": "https://x.com/post/1", "link": "https://x.com/post/1", "text": "First duplicate"},
            ],
        ]
    )
    poller = TweetPoller(notifier, store, rsshub, redis_store=None)
    subscription = Subscription(channel_id=123, account="foo", interval_seconds=60)
    state = {"next_run": 0.0, "last_id": "initial", "backoff_multiplier": 1}

    # 1回目のポーリング - 初回は1件送信
    await poller._poll_subscription(subscription, state)
    assert len(notifier.sent_messages) == 1
    assert notifier.sent_messages[0][3] == "https://x.com/post/1"

    # 2回目のポーリング - Redisがないため重複チェックなし、last_idでのみフィルタリング
    await poller._poll_subscription(subscription, state)
    # post/2は新規として検出される（last_idより新しいため）
    assert len(notifier.sent_messages) == 2
    assert notifier.sent_messages[1][3] == "https://x.com/post/2"


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


def test_keyword_filters() -> None:
    poller = TweetPoller(DummyNotifier(), DummyStore(), DummyRssHubClient([[{}]]), redis_store=None)
    entry = {"text": "New feature release", "raw_text": "<div>Feature</div>"}
    sub = Subscription(
        channel_id=123,
        account="foo",
        interval_seconds=60,
        include_keywords=("feature",),
        exclude_keywords=("spam",),
    )
    assert poller._should_include(entry, sub)

    entry_spam = {"text": "Spammy feature", "raw_text": "<div>spam</div>"}
    assert not poller._should_include(entry_spam, sub)

    sub_only_include = Subscription(
        channel_id=123,
        account="foo",
        interval_seconds=60,
        include_keywords=("release",),
    )
    assert poller._should_include(entry, sub_only_include)
    entry_other = {"text": "something else", "raw_text": "<div></div>"}
    assert not poller._should_include(entry_other, sub_only_include)

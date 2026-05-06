import asyncio

import pytest

from app.rsshub_client import RssHubClient


SAMPLE_FEED = """<?xml version='1.0' encoding='UTF-8'?>
<rss version='2.0'>
    <channel>
        <item>
            <guid>feed-guid-123</guid>
            <title>Test post</title>
            <description>Hello <strong>RSS</strong></description>
            <link>https://x.com/test/123</link>
        </item>
    </channel>
</rss>"""

SAMPLE_FEED_WITH_STATUS_ID = """<?xml version='1.0' encoding='UTF-8'?>
<rss version='2.0'>
    <channel>
        <item>
            <guid>https://fxtwitter.com/testuser/status/2043887905324822783</guid>
            <title>Status post</title>
            <description>Hello status</description>
            <link>https://fxtwitter.com/testuser/status/2043887905324822783</link>
        </item>
    </channel>
</rss>"""

SAMPLE_FEED_WITH_UNDEFINED_USER = """<?xml version='1.0' encoding='UTF-8'?>
<rss version='2.0'>
    <channel>
        <item>
            <guid>https://fxtwitter.com/undefined/status/2043887905324822783</guid>
            <title>Undefined user post</title>
            <description>Hello undefined</description>
            <link>https://fxtwitter.com/undefined/status/2043887905324822783</link>
        </item>
    </channel>
</rss>"""


class DummyResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class DummyAsyncClient:
    def __init__(self, base_url: str, headers: dict[str, str], timeout: object) -> None:
        self.base_url = base_url
        self.headers = headers
        self.timeout = timeout

    async def get(self, path: str, params: dict[str, int] | None = None) -> DummyResponse:
        return DummyResponse(SAMPLE_FEED)

    async def aclose(self) -> None:
        pass


class DummyAsyncClientWithFeed:
    def __init__(self, feed: str) -> None:
        self._feed = feed

    async def get(self, path: str, params: dict[str, int] | None = None) -> DummyResponse:
        return DummyResponse(self._feed)

    async def aclose(self) -> None:
        pass


def test_fetch_latest_posts_parses_xml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rsshub_client.httpx.AsyncClient", DummyAsyncClient)

    async def run_test() -> None:
        client = RssHubClient("http://rsshub.local")
        posts = await client.fetch_latest_posts("test")
        await client.close()
        # /status/ パターンがないURLはリンクがそのままIDとして使用される
        assert posts[0]["id"] == "https://x.com/test/123"
        assert posts[0]["text"] == "Hello RSS"
        assert posts[0]["link"] == "https://x.com/test/123"

    asyncio.run(run_test())


def test_fetch_latest_posts_uses_status_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """/status/<id> 形式のURLからポストIDを数値文字列として抽出することを確認"""
    def make_client(base_url: str, headers: dict, timeout: object) -> DummyAsyncClientWithFeed:
        return DummyAsyncClientWithFeed(SAMPLE_FEED_WITH_STATUS_ID)

    monkeypatch.setattr("app.rsshub_client.httpx.AsyncClient", make_client)

    async def run_test() -> None:
        client = RssHubClient("http://rsshub.local")
        posts = await client.fetch_latest_posts("testuser")
        await client.close()
        # ステータスIDのみが id として使用される
        assert posts[0]["id"] == "2043887905324822783"
        # link はそのまま保持される
        assert posts[0]["link"] == "https://fxtwitter.com/testuser/status/2043887905324822783"

    asyncio.run(run_test())


def test_fetch_latest_posts_fixes_undefined_username(monkeypatch: pytest.MonkeyPatch) -> None:
    """RSSHubが'undefined'をユーザー名として返した場合、正しいアカウント名に修正されることを確認"""
    def make_client(base_url: str, headers: dict, timeout: object) -> DummyAsyncClientWithFeed:
        return DummyAsyncClientWithFeed(SAMPLE_FEED_WITH_UNDEFINED_USER)

    monkeypatch.setattr("app.rsshub_client.httpx.AsyncClient", make_client)

    async def run_test() -> None:
        client = RssHubClient("http://rsshub.local")
        posts = await client.fetch_latest_posts("paimon_genshin7")
        await client.close()
        # undefined がアカウント名に置換される
        assert posts[0]["link"] == "https://fxtwitter.com/paimon_genshin7/status/2043887905324822783"
        # IDはステータスIDの数値文字列
        assert posts[0]["id"] == "2043887905324822783"

    asyncio.run(run_test())

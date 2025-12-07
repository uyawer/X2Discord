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


def test_fetch_latest_posts_parses_xml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rsshub_client.httpx.AsyncClient", DummyAsyncClient)

    async def run_test() -> None:
        client = RssHubClient("http://rsshub.local")
        posts = await client.fetch_latest_posts("test")
        await client.close()
        # リンクが優先されてIDとして使用される
        assert posts[0]["id"] == "https://x.com/test/123"
        assert posts[0]["text"] == "Hello RSS"
        assert posts[0]["link"] == "https://x.com/test/123"

    asyncio.run(run_test())
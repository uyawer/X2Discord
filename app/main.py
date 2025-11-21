import asyncio
import logging

from fastapi import FastAPI

from .config import Settings
from .discord_bot import DiscordNotifier
from .poller import TweetPoller
from .rsshub_client import RssHubClient
from .store import SubscriptionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

settings = Settings()
store = SubscriptionStore(
    settings.subscriptions_path,
    default_interval_seconds=settings.default_poll_interval_seconds,
    min_interval_seconds=settings.min_poll_interval_seconds,
)
rsshub_client = RssHubClient(settings.rsshub_base_url)
notifier = DiscordNotifier(
    settings.discord_bot_token,
    store,
    rsshub_client,
    guild_ids=settings.guild_ids,
)
poller = TweetPoller(notifier, store, rsshub_client)
app = FastAPI(title="Twitter to Discord Bridge")


@app.on_event("startup")
async def startup_event() -> None:
    logger.info("Starting Discord bot and tweet poller")
    await notifier.login(settings.discord_bot_token)
    bot_task = asyncio.create_task(notifier.connect())
    app.state.bot_task = bot_task
    await notifier.wait_until_ready()
    app.state.poller_task = asyncio.create_task(poller.start())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    logger.info("Stopping poller and Discord bot")
    await poller.stop()
    task = getattr(app.state, "poller_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    bot_task = getattr(app.state, "bot_task", None)
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
    await notifier.close()
    await rsshub_client.close()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

import asyncio
import logging
from typing import Optional, Sequence

import httpx
import discord
from discord import AllowedMentions, app_commands
from discord.abc import Messageable

from .rsshub_client import RssHubClient
from .store import SubscriptionStore
from .utils import parse_keyword_input


logger = logging.getLogger(__name__)


class DiscordNotifier(discord.Client):
    def __init__(
        self,
        token: str,
        store: SubscriptionStore,
        rsshub_client: RssHubClient,
        guild_ids: Sequence[int] | None = None,
    ):
        intents = discord.Intents.none()
        super().__init__(intents=intents)
        self.store = store
        self.rsshub_client = rsshub_client
        self.tree = app_commands.CommandTree(self)
        self._channel_cache: dict[int, Messageable] = {}
        self._ready_event = asyncio.Event()
        self._guild_ids = tuple(guild_ids or ())

    async def setup_hook(self) -> None:
        self.tree.clear_commands(guild=None)
        await self.tree.sync(guild=None)
        commands = [
            self._build_add_command(),
            self._build_edit_command(),
            self._build_remove_command(),
            self._build_list_command(),
            self._build_refresh_command(),
        ]
        if self._guild_ids:
            for guild_id in self._guild_ids:
                logger.info("Registering slash commands for guild %s", guild_id)
                guild = discord.Object(id=guild_id)
                for command in commands:
                    self.tree.add_command(command, guild=guild)
                logger.info("Syncing commands to guild %s", guild_id)
                await self.tree.sync(guild=guild)
        else:
            for command in commands:
                self.tree.add_command(command)
            logger.info("Syncing global slash commands")
            await self.tree.sync(guild=None)

    async def on_ready(self) -> None:
        logger.info("Discord bot ready (%s)", self.user)
        self._ready_event.set()

    async def send_message(
        self,
        channel_id: int,
        username: str,
        text: str,
        tweet_url: str,
        thread_id: Optional[int] = None,
    ) -> None:
        await self.wait_until_ready()
        target_id = thread_id or channel_id
        channel = await self._resolve_channel(target_id)
        message = self._rewrite_tweet_url(tweet_url)
        await channel.send(content=message, allowed_mentions=AllowedMentions.none())

    async def _resolve_channel(self, channel_id: int) -> Messageable:
        if channel_id in self._channel_cache:
            return self._channel_cache[channel_id]
        channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, Messageable):
            raise TypeError("Resolved channel is not sendable")
        self._channel_cache[channel_id] = channel
        return channel

    @staticmethod
    def _rewrite_tweet_url(tweet_url: str) -> str:
        if not tweet_url:
            return tweet_url
        return tweet_url.replace("https://x.com/", "https://fxtwitter.com/")

    @staticmethod
    def _format_interval(interval_seconds: int) -> str:
        if interval_seconds % 60 == 0:
            minutes = interval_seconds // 60
            return f"{minutes}分"
        return f"{interval_seconds}秒"

    def _build_add_command(self) -> app_commands.Command:
        @app_commands.command(
            name="add",
            description="チャンネルにXアカウントの監視を追加するで",
        )
        @app_commands.describe(
            account="アカウントID（@を含めない）",
            polling="監視間隔（秒単位。省略時は60秒）",
            include_reposts="リポストの有無（default:false）",
            include_quotes="引用リポストの有無（default:false）",
            include_keywords="投稿に含まれると連携されるキーワード（カンマ区切り）",
            exclude_keywords="投稿に含まれると連携を除外するキーワード（カンマ区切り）",
        )
        async def add(
            interaction: discord.Interaction,
            account: str,
            polling: Optional[int] = None,
            include_reposts: bool = False,
            include_quotes: bool = False,
            include_keywords: str | None = None,
            exclude_keywords: str | None = None,
        ) -> None:
            channel_id = interaction.channel_id
            if channel_id is None:
                await interaction.response.send_message(
                    "このコマンドはテキストチャンネルで実行するんやで。",
                    ephemeral=True,
                )
                return
            interval = polling or self.store.default_interval_seconds
            if interval < self.store.min_interval_seconds:
                await interaction.response.send_message(
                    f"監視間隔は{self.store.min_interval_seconds}秒以上にせなアカンやで。",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            try:
                subscription = await self.store.add_subscription(
                    channel_id,
                    account,
                    interval_seconds=interval,
                    include_reposts=include_reposts,
                    include_quotes=include_quotes,
                    include_keywords=parse_keyword_input(include_keywords),
                    exclude_keywords=parse_keyword_input(exclude_keywords),
                )
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            latest_post = await self._fetch_latest_post(subscription.account)
            await self._announce_subscription_channel(
                subscription.channel_id,
                subscription.account,
                latest_post,
                interaction.user.mention,
            )
            await interaction.followup.send(
                f"{subscription.account} を監視設定に追加したで。",
                ephemeral=True,
            )

        return add

    def _build_edit_command(self) -> app_commands.Command:
        @app_commands.command(
            name="edit",
            description="既存の監視設定を更新するで",
        )
        @app_commands.describe(
            account="アカウントID（@を含めない）",
            polling="監視間隔（秒単位。省略すると維持）",
            include_reposts="リポストの有無（省略すると維持）",
            include_quotes="引用リポストの有無（省略すると維持）",
            include_keywords="投稿に含まれると連携されるキーワード（カンマ区切り）",
            exclude_keywords="投稿に含まれると連携を除外するキーワード（カンマ区切り）",
        )
        async def edit(
            interaction: discord.Interaction,
            account: str,
            polling: Optional[int] = None,
            include_reposts: Optional[bool] = None,
            include_quotes: Optional[bool] = None,
            include_keywords: str | None = None,
            exclude_keywords: str | None = None,
        ) -> None:
            channel_id = interaction.channel_id
            if channel_id is None:
                await interaction.response.send_message(
                    "このコマンドはテキストチャンネルで実行するんやで。",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            try:
                subscription = await self.store.update_subscription(
                    channel_id,
                    account,
                    interval_seconds=polling,
                    include_reposts=include_reposts,
                    include_quotes=include_quotes,
                    include_keywords=parse_keyword_input(include_keywords) if include_keywords is not None else None,
                    exclude_keywords=parse_keyword_input(exclude_keywords) if exclude_keywords is not None else None,
                )
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await interaction.followup.send(
                f"{subscription.account} の設定を更新したで: {self._format_interval(subscription.interval_seconds)} / リポスト:{'あり' if subscription.include_reposts else 'なし'} / 引用:{'あり' if subscription.include_quotes else 'なし'}",
                ephemeral=True,
            )

        return edit

    def _build_remove_command(self) -> app_commands.Command:
        @app_commands.command(
            name="remove",
            description="チャンネルからXアカウントの監視を解除するで",
        )
        @app_commands.describe(account="アカウントID（@を含めない）")
        async def remove(interaction: discord.Interaction, account: str) -> None:
            channel_id = interaction.channel_id
            if channel_id is None:
                await interaction.response.send_message(
                    "このコマンドはテキストチャンネルで実行するんやで。",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            removed = await self.store.remove_subscription(channel_id, account)
            if removed:
                await self._announce_removal(channel_id, account, interaction.user.mention)
                await interaction.followup.send("監視を解除したで。", ephemeral=True)
            else:
                await interaction.followup.send(
                    f"{account} はこのチャンネルで監視してないやん。",
                    ephemeral=True,
                )

        return remove

    def _build_list_command(self) -> app_commands.Command:
        @app_commands.command(
            name="list",
            description="このチャンネルの監視アカウント一覧を表示するで",
        )
        async def list_cmd(interaction: discord.Interaction) -> None:
            channel_id = interaction.channel_id
            if channel_id is None:
                await interaction.response.send_message(
                    "このコマンドはテキストチャンネルで実行するんやで。",
                    ephemeral=True,
                )
                return
            subscriptions = await self.store.get_channel_subscriptions(channel_id)
            if not subscriptions:
                await interaction.response.send_message(
                    "今のチャンネルでは何も監視してへんやで。",
                    ephemeral=True,
                )
                return
            lines = []
            for sub in subscriptions:
                line = (
                    f"・{sub.account} / {self._format_interval(sub.interval_seconds)}"
                    f" / リポスト:{'あり' if sub.include_reposts else 'なし'}"
                    f" / 引用:{'あり' if sub.include_quotes else 'なし'}"
                    f" / キーワード:{', '.join(sub.include_keywords) if sub.include_keywords else 'なし'}"
                    f" / 除外キーワード:{', '.join(sub.exclude_keywords) if sub.exclude_keywords else 'なし'}"
                )
                lines.append(line)
            text = "このチャンネルで監視してるアカウント一覧やで:\n" + "\n".join(lines)
            await interaction.response.send_message(text, ephemeral=True)

        return list_cmd

    def _build_refresh_command(self) -> app_commands.Command:
        @app_commands.command(
            name="refresh",
            description="手動で最新の投稿を取りに行くで",
        )
        @app_commands.describe(account="アカウントID（@を含めない）",)
        async def refresh(interaction: discord.Interaction, account: str) -> None:
            channel_id = interaction.channel_id
            if channel_id is None:
                await interaction.response.send_message(
                    "このコマンドはテキストチャンネルで実行するんやで。",
                    ephemeral=True,
                )
                return
            normalized = self.store.normalize_account(account)
            subscriptions = await self.store.get_channel_subscriptions(channel_id)
            if not any(sub.account == normalized for sub in subscriptions):
                await interaction.response.send_message(
                    f"{account} はこのチャンネルで監視してないやん。",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            latest_post = await self._fetch_latest_post(normalized)
            if not latest_post:
                await interaction.followup.send(
                    "今はまだ取れへんやで。しばらく待ちや。",
                    ephemeral=True,
                )
                return
            await self.send_message(
                channel_id,
                normalized,
                latest_post["text"],
                latest_post["link"],
            )
            await interaction.followup.send(
                f"{normalized} の最新投稿拾ったで。",
                ephemeral=True,
            )

        return refresh

    async def _fetch_latest_post(self, username: str) -> Optional[dict]:
        try:
            tweets = await self.rsshub_client.fetch_latest_posts(username)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                logger.warning("初回取得はレート制限されました: %s", username)
                await asyncio.sleep(5)
                return None
            logger.warning("初回取得に失敗しました: %s (%s)", username, exc)
            return None
        except httpx.HTTPError as exc:
            logger.warning("初回取得に失敗しました: %s (%s)", username, exc)
            return None
        return tweets[0] if tweets else None

    async def _announce_subscription_channel(
        self,
        channel_id: int,
        username: str,
        latest_post: Optional[dict],
        actor: str,
    ) -> None:
        try:
            channel = await self._resolve_channel(channel_id)
        except Exception as exc:  # pragma: no cover - runtime errors
            logger.warning("通知先チャンネルを取得できませんでした: %s", exc)
            return
        text = f"{actor} が {username} を監視リストに追加したで。"
        if latest_post:
            text += f"\n最新の投稿はこれやで: {self._rewrite_tweet_url(latest_post['link'])}"
        await channel.send(content=text, allowed_mentions=AllowedMentions.none())

    async def _announce_removal(self, channel_id: int, username: str, actor: str) -> None:
        try:
            channel = await self._resolve_channel(channel_id)
        except Exception as exc:  # pragma: no cover - runtime errors
            logger.warning("通知先チャンネルを取得できませんでした: %s", exc)
            return
        text = f"{actor} が {username} の監視を解除したで。"
        await channel.send(content=text, allowed_mentions=AllowedMentions.none())

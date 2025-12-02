from pathlib import Path
from typing import Optional

from pydantic import Field, PositiveInt, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_bot_token: str
    rsshub_base_url: str = Field(
        "http://localhost:1200",
        description="RSSHubのベースURL、末尾スラッシュ不要",
    )
    rsshub_refresh_seconds: Optional[PositiveInt] = Field(
        None,
        description="RSSHubに `refresh` クエリを渡す秒数。省略するとデフォルトキャッシュを使う",
    )
    default_poll_interval_seconds: PositiveInt = Field(
        60,
        description="/add で polling を省略したときのデフォルト間隔（秒）",
        ge=1,
    )
    min_poll_interval_seconds: PositiveInt = Field(
        60,
        description="許容する最小間隔（秒）",
    )
    subscriptions_path: Path = Field(Path("subscriptions.json"), description="サブスクリプション永続化ファイル")
    redis_url: str = Field(
        "redis://localhost:6379/0",
        description="Redis接続URL（送信済みリンクの永続化に使用）",
    )
    guild_ids_str: str | None = Field(
        None,
        alias="GUILD_IDS",
        description="カンマ区切りで指定する Slash コマンド対象ギルド",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def guild_ids(self) -> tuple[int, ...]:
        raw = self.guild_ids_str or ""
        if not raw:
            return ()
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        try:
            return tuple(int(part) for part in parts)
        except ValueError as exc:
            raise ValueError("GUILD_IDS must contain only integers") from exc

    @model_validator(mode="after")
    def validate_intervals(self) -> "Settings":
        if self.default_poll_interval_seconds < self.min_poll_interval_seconds:
            raise ValueError("default_poll_interval_seconds must be >= min_poll_interval_seconds")
        return self

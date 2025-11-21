from pathlib import Path

from app.config import Settings


def test_settings_defaults() -> None:
    settings = Settings(
        _env_file=None,
        discord_bot_token="dummy-token",
        rsshub_base_url="http://rsshub.local",
    )
    assert settings.default_poll_interval_seconds == 60
    assert settings.min_poll_interval_seconds == 60
    assert settings.subscriptions_path == Path("subscriptions.json")

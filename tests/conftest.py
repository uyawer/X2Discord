from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# Ensure the repository root is on sys.path so tests can import `app.*`.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# discord.py は ctypes に依存しており Python 3.14 環境でロードに失敗する場合があるため
# テスト実行時はスタブモジュールで置き換える
if "discord" not in sys.modules:
    _discord_stub = types.ModuleType("discord")

    class _IntentsStub:
        @staticmethod
        def none() -> "_IntentsStub":
            return _IntentsStub()
        @staticmethod
        def default() -> "_IntentsStub":
            return _IntentsStub()

    class _ClientStub:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    class _AllowedMentionStub:
        pass

    class _CommandTreeStub:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass
        def command(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            def decorator(func):  # type: ignore[no-untyped-def]
                return func
            return decorator

    class _AppCommandsStub:
        CommandTree = _CommandTreeStub

    _discord_stub.Client = _ClientStub  # type: ignore[attr-defined]
    _discord_stub.Intents = _IntentsStub  # type: ignore[attr-defined]
    _discord_stub.AllowedMentions = _AllowedMentionStub  # type: ignore[attr-defined]
    _discord_stub.app_commands = _AppCommandsStub()  # type: ignore[attr-defined]
    sys.modules["discord"] = _discord_stub

    _abc_stub = types.ModuleType("discord.abc")
    _abc_stub.Messageable = object  # type: ignore[attr-defined]
    sys.modules["discord.abc"] = _abc_stub

    _ext_stub = types.ModuleType("discord.ext")
    sys.modules["discord.ext"] = _ext_stub

    _commands_stub = types.ModuleType("discord.ext.commands")
    _commands_stub.Bot = object  # type: ignore[attr-defined]
    sys.modules["discord.ext.commands"] = _commands_stub

# pytest-asyncio configuration
pytest_plugins = ('pytest_asyncio',)


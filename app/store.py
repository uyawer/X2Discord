import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Subscription:
    channel_id: int
    account: str
    interval_seconds: int
    include_reposts: bool = False
    include_quotes: bool = False
    thread_id: Optional[int] = None
    start_offset_minutes: int = 0
    last_tweet_id: Optional[str] = None


class SubscriptionStore:
    def __init__(
        self,
        path: Path,
        default_interval_seconds: int = 60,
        min_interval_seconds: int = 60,
    ):
        self.path = path
        self.default_interval_seconds = default_interval_seconds
        self.min_interval_seconds = min_interval_seconds
        self._lock = asyncio.Lock()
        self._data: Dict[str, List[Dict[str, Any]]] = self._load()

    def _load(self) -> Dict[str, List[Dict[str, Any]]]:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"subscriptions": {}}))
        try:
            raw = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            raw = {}
        return raw.get("subscriptions", {})

    def _save(self) -> None:
        payload = {"subscriptions": self._data}
        self.path.write_text(json.dumps(payload, indent=2))

    def _derive_interval_seconds(self, entry: Dict[str, Any]) -> int:
        seconds = entry.get("interval_seconds")
        if isinstance(seconds, (int, float)) and seconds > 0:
            return int(seconds)
        legacy_minutes = entry.get("interval_minutes")
        if isinstance(legacy_minutes, (int, float)) and legacy_minutes > 0:
            return int(legacy_minutes * 60)
        return self.default_interval_seconds

    @staticmethod
    def _normalize_account(value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("account name required")
        while candidate.endswith("/"):
            candidate = candidate[:-1]
        if candidate.startswith("https://") or candidate.startswith("http://"):
            candidate = candidate.split("/")[-1]
        if candidate.startswith("@"):
            candidate = candidate[1:]
        return candidate

    async def add_subscription(
        self,
        channel_id: int,
        account: str,
        interval_seconds: Optional[int] = None,
        include_reposts: bool = False,
        include_quotes: bool = False,
        start_offset_minutes: int = 0,
    ) -> Subscription:
        normalized = self._normalize_account(account)
        interval = interval_seconds or self.default_interval_seconds
        if interval < self.min_interval_seconds:
            raise ValueError(
                f"interval must be at least {self.min_interval_seconds} seconds"
            )
        if start_offset_minutes < 0 or start_offset_minutes >= interval:
            raise ValueError("start_offset_minutes must be in [0, interval)")

        async with self._lock:
            bucket = self._data.setdefault(str(channel_id), [])
            existing = next((entry for entry in bucket if entry.get("account") == normalized), None)
            entry = {
                "account": normalized,
                "interval_seconds": interval,
                "include_reposts": include_reposts,
                "include_quotes": include_quotes,
                "start_offset_minutes": start_offset_minutes,
                "last_tweet_id": existing.get("last_tweet_id") if existing else None,
            }
            if existing:
                existing.update(entry)
            else:
                bucket.append(entry)
            self._save()
        return Subscription(
            channel_id=channel_id,
            account=normalized,
            interval_seconds=interval,
            include_reposts=include_reposts,
            include_quotes=include_quotes,
            start_offset_minutes=start_offset_minutes,
        )

    async def update_subscription(
        self,
        channel_id: int,
        account: str,
        interval_seconds: Optional[int] = None,
        include_reposts: Optional[bool] = None,
        include_quotes: Optional[bool] = None,
    ) -> Subscription:
        normalized = self._normalize_account(account)
        async with self._lock:
            bucket = self._data.setdefault(str(channel_id), [])
            existing = next((entry for entry in bucket if entry.get("account") == normalized), None)
            if existing is None:
                raise ValueError(f"{normalized} is not being watched in this channel")
            interval = interval_seconds if interval_seconds is not None else self._derive_interval_seconds(existing)
            if interval < self.min_interval_seconds:
                raise ValueError(
                    f"interval must be at least {self.min_interval_seconds} seconds"
                )
            updated = {
                "interval_seconds": interval,
            }
            if include_reposts is not None:
                updated["include_reposts"] = include_reposts
            if include_quotes is not None:
                updated["include_quotes"] = include_quotes
            existing.update(updated)
            self._save()
        return Subscription(
            channel_id=channel_id,
            account=normalized,
            interval_seconds=interval,
            include_reposts=existing.get("include_reposts", False),
            include_quotes=existing.get("include_quotes", False),
            start_offset_minutes=existing.get("start_offset_minutes", 0),
            thread_id=existing.get("thread_id"),
            last_tweet_id=existing.get("last_tweet_id"),
        )

    def normalize_account(self, account: str) -> str:
        return self._normalize_account(account)

    async def remove_subscription(self, channel_id: int, account: str) -> bool:
        normalized = self._normalize_account(account)
        async with self._lock:
            bucket = self._data.get(str(channel_id), [])
            before = len(bucket)
            bucket[:] = [entry for entry in bucket if entry.get("account") != normalized]
            if not bucket and str(channel_id) in self._data:
                del self._data[str(channel_id)]
            if len(bucket) != before:
                self._save()
                return True
        return False

    async def get_subscriptions(self) -> List[Subscription]:
        async with self._lock:
            result: List[Subscription] = []
            for channel_key, entries in self._data.items():
                try:
                    channel_id = int(channel_key)
                except ValueError:
                    continue
                for entry in entries:
                    account = entry.get("account")
                    interval_seconds = self._derive_interval_seconds(entry)
                    if account:
                        result.append(
                            Subscription(
                                channel_id=channel_id,
                                account=account,
                                interval_seconds=interval_seconds,
                                include_reposts=entry.get("include_reposts", False),
                                include_quotes=entry.get("include_quotes", False),
                                start_offset_minutes=entry.get("start_offset_minutes", 0),
                                thread_id=entry.get("thread_id"),
                                last_tweet_id=entry.get("last_tweet_id"),
                            )
                        )
            return result

    async def get_channel_subscriptions(self, channel_id: int) -> List[Subscription]:
        async with self._lock:
            result: List[Subscription] = []
            entries = self._data.get(str(channel_id), [])
            for entry in entries:
                account = entry.get("account")
                interval_seconds = self._derive_interval_seconds(entry)
                if account:
                    result.append(
                        Subscription(
                            channel_id=channel_id,
                            account=account,
                            interval_seconds=interval_seconds,
                            include_reposts=entry.get("include_reposts", False),
                            include_quotes=entry.get("include_quotes", False),
                            start_offset_minutes=entry.get("start_offset_minutes", 0),
                            thread_id=entry.get("thread_id"),
                            last_tweet_id=entry.get("last_tweet_id"),
                        )
                    )
            return result

    async def get_last_tweet_id(self, channel_id: int, account: str) -> Optional[str]:
        normalized = self._normalize_account(account)
        async with self._lock:
            bucket = self._data.get(str(channel_id), [])
            for entry in bucket:
                if entry.get("account") == normalized:
                    return entry.get("last_tweet_id")
        return None

    async def set_last_tweet_id(self, channel_id: int, account: str, tweet_id: str) -> None:
        normalized = self._normalize_account(account)
        async with self._lock:
            bucket = self._data.get(str(channel_id), [])
            for entry in bucket:
                if entry.get("account") == normalized:
                    entry["last_tweet_id"] = tweet_id
                    self._save()
                    return

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Tuple


def normalize_keyword_text(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    return normalized.casefold().strip()


def normalize_keywords(values: Iterable[str] | None) -> Tuple[str, ...]:
    if not values:
        return ()
    normalized: list[str] = []
    for value in values:
        if not value:
            continue
        candidate = normalize_keyword_text(value)
        if candidate:
            normalized.append(candidate)
    return tuple(normalized)


def parse_keyword_input(value: str | None) -> Tuple[str, ...]:
    if not value:
        return ()
    parts = re.split(r"[\n,]+", value)
    return normalize_keywords(parts)

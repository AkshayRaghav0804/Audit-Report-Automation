"""Validated display preferences stored with the current browser session."""

from collections.abc import MutableMapping
from typing import Any

PAGE_SIZE_KEY = "report_page_size"
PAGE_SIZES = (10, 25, 50)


def report_page_size(storage: MutableMapping[str, Any]) -> int:
    value = storage.get(PAGE_SIZE_KEY)
    return value if isinstance(value, int) and value in PAGE_SIZES else 25


def save_display_preferences(
    storage: MutableMapping[str, Any], *, page_size: int, collapsed: bool
) -> None:
    if page_size not in PAGE_SIZES:
        raise ValueError("Choose 10, 25 or 50 reports per page.")
    storage[PAGE_SIZE_KEY] = page_size
    storage["sidebar_collapsed"] = collapsed

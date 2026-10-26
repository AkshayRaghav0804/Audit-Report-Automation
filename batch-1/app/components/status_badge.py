"""Compact status and priority badges."""

from __future__ import annotations

import re

from nicegui import ui


def status_badge(text: str) -> None:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    ui.label(text).classes(f"status-badge status-badge--{slug}")

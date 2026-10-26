"""Controlled errors raised when report-definition sources cannot be used."""

from __future__ import annotations


class SourceDataError(RuntimeError):
    """A source file is missing, malformed, or does not meet app invariants."""

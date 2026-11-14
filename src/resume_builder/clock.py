"""Timestamps used across storage backends and services (UTC, ISO 8601)."""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def now_iso_precise() -> str:
    """Microsecond timestamps, so snapshots made in the same second still sort in order."""
    return datetime.now(timezone.utc).isoformat()

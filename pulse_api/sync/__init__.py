"""Sync pipeline — fetches events + social posts, dedups, persists.

Public entry point: `run_daily_sync` (used by /sync route and CLI).
"""

from pulse_api.sync.orchestrator import run_daily_sync

__all__ = ["run_daily_sync"]

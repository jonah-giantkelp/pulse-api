"""In-process counters for AI calls during a sync run.

Reset at the start of `run_daily_sync` and dumped at the end. Captures
per-purpose call counts and rough input/output sizes (chars) so we can
spot which call types dominate cost.

Char counts are intentionally rough — 1 token ≈ 4 chars of English text
is good enough to compare relative sizes between call types.
"""

import json
from collections import defaultdict
from typing import Any, Literal

Tier = Literal["smart", "fast", "image", "search"]


def response_chars(response: Any) -> int:
    """Best-effort length of an AI response — handles str, dict, list."""
    if response is None:
        return 0
    if isinstance(response, str):
        return len(response)
    try:
        return len(json.dumps(response))
    except (TypeError, ValueError):
        return len(str(response))


def _empty_bucket() -> dict:
    return {
        "count": 0,
        "tier": "",
        "in_chars": 0,
        "out_chars": 0,
    }


_stats: dict[str, dict] = defaultdict(_empty_bucket)


def reset() -> None:
    _stats.clear()


def record(
    purpose: str,
    tier: Tier,
    input_chars: int = 0,
    output_chars: int = 0,
) -> None:
    bucket = _stats[purpose]
    bucket["tier"] = tier
    bucket["count"] += 1
    bucket["in_chars"] += input_chars
    bucket["out_chars"] += output_chars


def _fmt_n(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def summary_lines() -> list[str]:
    if not _stats:
        return ["🤖 AI usage: no calls recorded"]

    # Sort by total input chars desc — biggest cost sinks first
    rows = sorted(
        _stats.items(),
        key=lambda kv: kv[1]["in_chars"],
        reverse=True,
    )

    lines = ["🤖 AI usage:"]
    lines.append(
        "    %-20s %-7s %5s   %12s   %10s"
        % ("purpose", "tier", "n", "avg in / out", "total in")
    )
    total_calls = 0
    total_in = 0
    total_out = 0
    for purpose, b in rows:
        n = b["count"]
        total_calls += n
        total_in += b["in_chars"]
        total_out += b["out_chars"]
        avg_in = b["in_chars"] // n if n else 0
        avg_out = b["out_chars"] // n if n else 0
        avg_str = (
            f"{_fmt_n(avg_in)} / {_fmt_n(avg_out)}"
            if b["tier"] != "search"
            else f"--- / {_fmt_n(avg_out)}"
        )
        lines.append(
            "    %-20s %-7s %5d   %12s   %10s"
            % (purpose, b["tier"], n, avg_str, _fmt_n(b["in_chars"]))
        )
    lines.append(
        "    %-20s %-7s %5d   %12s   %10s"
        % (
            "TOTAL",
            "",
            total_calls,
            f"{_fmt_n(total_in // total_calls if total_calls else 0)} / "
            f"{_fmt_n(total_out // total_calls if total_calls else 0)}",
            _fmt_n(total_in),
        )
    )
    return lines

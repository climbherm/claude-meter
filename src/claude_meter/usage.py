"""Fetch + format Claude usage via Anthropic's OAuth usage endpoint."""
from __future__ import annotations

import datetime
import email.utils
import json
import urllib.error
import urllib.request

from claude_meter.auth import get_access_token, invalidate_cached_token

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA      = "oauth-2025-04-20"


class RateLimited(Exception):
    """Raised on HTTP 429. Carries a retry_after hint in seconds."""
    def __init__(self, retry_after: int):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


def _parse_retry_after(value: str | None) -> int:
    """Retry-After is seconds (int) or HTTP-date. Fall back to 60s."""
    if not value:
        return 60
    try:
        return max(1, int(value))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
        delta = (dt - datetime.datetime.now(tz=dt.tzinfo)).total_seconds()
        return max(1, int(delta))
    except Exception:
        return 60


def _get(token: str) -> dict:
    req = urllib.request.Request(
        USAGE_URL,
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": BETA},
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def fetch_usage() -> dict:
    """GET /api/oauth/usage.

    On 401/403: re-mint a token and retry once.
    On 429: drop the cached token, mint a fresh one, retry once. If the
    retry is still 429, surface RateLimited so the caller honors
    Retry-After.
    """
    token, _org = get_access_token()
    try:
        return _get(token)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            invalidate_cached_token()
            try:
                old_token = token
                token, _org = get_access_token()
                if token == old_token:
                    # 2026-11-19: on the Keychain-fallback token a "re-mint"
                    # hands back the same token, so the retry was a second
                    # rate-limited call every cycle. Honor Retry-After instead.
                    raise RateLimited(_parse_retry_after(e.headers.get("Retry-After"))) from e
                return _get(token)
            except urllib.error.HTTPError as e2:
                if e2.code == 429:
                    raise RateLimited(_parse_retry_after(e2.headers.get("Retry-After"))) from e2
                raise
        # 400 joined 401/403 on 2026-11-13: after a Keychain credential swap the
        # usage endpoint answered 400 Bad Request for hours with a token the CLI
        # itself still accepted; only a re-mint cleared it. Treat it as "token
        # is wrong", not as a transient error.
        if e.code not in (400, 401, 403):
            raise
        invalidate_cached_token()
        token, _org = get_access_token()
        try:
            return _get(token)
        except urllib.error.HTTPError as e2:
            if e2.code == 429:
                raise RateLimited(_parse_retry_after(e2.headers.get("Retry-After"))) from e2
            raise


def format_reset(resets_at_str: str) -> str:
    """Human-readable countdown like 'in 2h 15m' or 'Mon 9:00 AM'."""
    if not resets_at_str:
        return "unknown"
    try:
        dt  = datetime.datetime.fromisoformat(resets_at_str)
        now = datetime.datetime.now(tz=dt.tzinfo)
        secs = int((dt - now).total_seconds())
        if secs <= 0:
            return "soon"
        if secs < 3600:
            return f"in {secs // 60}m"
        if secs < 86400:
            h, m = divmod(secs // 60, 60)
            return f"in {h}h {m}m" if m else f"in {h}h"
        return dt.astimezone().strftime("%a %-I:%M %p")
    except Exception:
        return "unknown"


def extract(data: dict) -> tuple[float, str, float, str]:
    """(five_pct, five_reset, week_pct, week_reset) from usage response."""
    five = data.get("five_hour") or {}
    week = data.get("seven_day") or {}
    return (
        float(five.get("utilization") or 0),
        format_reset(five.get("resets_at", "")),
        float(week.get("utilization") or 0),
        format_reset(week.get("resets_at", "")),
    )


def extract_scoped(data: dict):
    """First weekly model-scoped limit as (display_name, pct, reset) or None."""
    for lim in data.get("limits") or []:
        if lim.get("kind") != "weekly_scoped":
            continue
        model = ((lim.get("scope") or {}).get("model") or {})
        name = model.get("display_name")
        if name:
            return (name, float(lim.get("percent") or 0),
                    format_reset(lim.get("resets_at") or ""))
    return None

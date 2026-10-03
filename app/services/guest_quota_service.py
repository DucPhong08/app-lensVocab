from __future__ import annotations

import hashlib
import ipaddress
import os
from datetime import UTC, datetime, timedelta

from fastapi import Request
from redis.asyncio import Redis

from app.config import settings
from app.services.system_setting_service import fetch_system_settings


class GuestScanLimitError(Exception):
    pass


# Validate all counters and charge the scan in one Redis operation. A Redis outage
# fails closed; no AWS request should run without a budget check.
_GUEST_BUDGET_SCRIPT = """
local per_ip = tonumber(redis.call('GET', KEYS[1]) or '0')
local global_count = tonumber(redis.call('GET', KEYS[2]) or '0')
if per_ip >= tonumber(ARGV[1]) then return 1 end
if global_count >= tonumber(ARGV[2]) then return 2 end
if redis.call('EXISTS', KEYS[3]) == 1 then return 3 end
if redis.call('INCR', KEYS[1]) == 1 then redis.call('EXPIREAT', KEYS[1], ARGV[3]) end
if redis.call('INCR', KEYS[2]) == 1 then redis.call('EXPIREAT', KEYS[2], ARGV[3]) end
redis.call('SET', KEYS[3], '1', 'EX', ARGV[4])
return 0
"""


def client_ip(request: Request) -> str:
    # Render appends the actual peer to X-Forwarded-For. Never trust a caller-
    # supplied XFF outside Render (local tests and direct deployments).
    forwarded = request.headers.get("x-forwarded-for") if os.getenv("RENDER") == "true" else None
    address = forwarded.rsplit(",", 1)[-1].strip() if forwarded else request.client.host if request.client else ""
    try:
        return str(ipaddress.ip_address(address))
    except ValueError as exc:
        raise GuestScanLimitError("CLIENT_IP_UNAVAILABLE") from exc


async def consume_guest_quota(request: Request, redis: Redis) -> None:
    system_settings = await fetch_system_settings()
    if system_settings.maintenance_mode:
        raise GuestScanLimitError("MAINTENANCE_MODE")
    address_hash = hashlib.sha256(client_ip(request).encode("ascii")).hexdigest()
    now = datetime.now(UTC)
    day = now.strftime("%Y-%m-%d")
    expires_at = int((now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    outcome = await redis.eval(
        _GUEST_BUDGET_SCRIPT,
        3,
        f"guest:scan:{day}:{address_hash}",
        f"guest:scan:global:{day}",
        f"guest:scan:burst:{address_hash}",
        settings.GUEST_DAILY_QUOTA,
        settings.GUEST_GLOBAL_DAILY_QUOTA,
        expires_at,
        settings.GUEST_SCAN_INTERVAL_SECONDS,
    )
    if outcome:
        raise GuestScanLimitError({1: "GUEST_QUOTA_EXCEEDED", 2: "GUEST_CAP_REACHED", 3: "GUEST_SLOW_DOWN"}.get(outcome, "GUEST_SCAN_UNAVAILABLE"))

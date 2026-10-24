from __future__ import annotations

import hashlib
import ipaddress
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import Request
from redis.asyncio import Redis

from app.config import settings
from app.models.setting import SystemSetting
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


def is_trusted_proxy(peer_host: str) -> bool:
    """A flag never grants trust to peers outside the explicit IP/CIDR allowlist."""
    if not settings.TRUST_PROXY_HEADERS or not peer_host:
        return False
    try:
        peer = ipaddress.ip_address(peer_host)
    except ValueError:
        return False
    for value in settings.TRUSTED_PROXIES.split(","):
        try:
            if peer in ipaddress.ip_network(value.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    peer_host = request.client.host if request.client else ""
    try:
        peer = str(ipaddress.ip_address(peer_host))
    except ValueError as exc:
        raise GuestScanLimitError("CLIENT_IP_UNAVAILABLE") from exc
    if not is_trusted_proxy(peer):
        return peer
    raw = request.headers.get(settings.PROXY_CLIENT_IP_HEADER, "")
    if not raw:
        return peer
    parts = raw.split(",")
    if len(raw) > 4096 or len(parts) > 20:
        raise GuestScanLimitError("CLIENT_IP_UNAVAILABLE")
    if settings.PROXY_CLIENT_IP_HEADER != "x-forwarded-for" and len(parts) != 1:
        raise GuestScanLimitError("CLIENT_IP_UNAVAILABLE")
    try:
        chain = [str(ipaddress.ip_address(value.strip())) for value in parts]
    except ValueError as exc:
        raise GuestScanLimitError("CLIENT_IP_UNAVAILABLE") from exc
    # Walk only the verified proxy suffix; attacker-supplied left entries are ignored.
    for candidate in reversed(chain):
        if not is_trusted_proxy(peer):
            break
        peer = candidate
    return peer


async def consume_guest_quota(
    request: Request, redis: Redis, *, system_settings: SystemSetting | None = None
) -> None:
    system_settings = (
        system_settings if system_settings is not None else await fetch_system_settings()
    )
    if system_settings.maintenance_mode:
        raise GuestScanLimitError("MAINTENANCE_MODE")
    address_hash = hashlib.sha256(client_ip(request).encode("ascii")).hexdigest()
    now = datetime.now(UTC)
    day = now.strftime("%Y-%m-%d")
    expires_at = int(
        (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    outcome = await cast(Any, redis).eval(
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
        raise GuestScanLimitError(
            {1: "GUEST_QUOTA_EXCEEDED", 2: "GUEST_CAP_REACHED", 3: "GUEST_SLOW_DOWN"}.get(
                outcome, "GUEST_SCAN_UNAVAILABLE"
            )
        )

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
POLL_INTERVAL = 0.5
DEFAULT_ATTEMPTS = 3
NO_SESSION_GRACE = 2.0
GROUP_WAIT = 8.0

def connection(vc: Any) -> Any | None:
    return getattr(vc, "_connection", None)

def session(vc: Any) -> Any | None:
    conn = connection(vc)
    return getattr(conn, "dave_session", None) if conn is not None else None

def can_encrypt(vc: Any) -> bool:
    conn = connection(vc)
    return bool(conn is not None and getattr(conn, "can_encrypt", False))

def is_dave_session_active(session_: Any) -> bool:
    return str(getattr(session_, "status", "")).rsplit(".", 1)[-1].lower() == "active"

def group_members(vc: Any) -> set[int]:
    sess = session(vc)
    if sess is None:
        return set()
    try:
        return {int(uid) for uid in sess.get_user_ids()}
    except Exception as exc:  # noqa: BLE001 - a broken session must not raise
        log.debug("Could not read E2EE group members: %s", exc)
        return set()

def channel_listeners(vc: Any) -> list[int]:
    channel = getattr(vc, "channel", None)
    return [int(m.id) for m in getattr(channel, "members", ()) if not m.bot]

def missing_from_group(vc: Any, member_ids: Any) -> list[int]:
    wanted = {int(uid) for uid in member_ids}
    return sorted(wanted - group_members(vc))

def is_audible(vc: Any, member_ids: Any = None) -> bool:
    if not can_encrypt(vc):
        return False
    sess = session(vc)
    if sess is None or not is_dave_session_active(sess):
        return False
    wanted = channel_listeners(vc) if member_ids is None else [int(u) for u in member_ids]
    return not missing_from_group(vc, wanted)

LISTENER_WAIT_SECONDS = 15 * 60.0

async def wait_for_listener(vc: Any, timeout: float = LISTENER_WAIT_SECONDS, poll_interval: float = 1.0) -> list[int]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not (listeners := channel_listeners(vc)):
        if loop.time() >= deadline:
            return []
        await asyncio.sleep(poll_interval)
    return listeners

async def wait_until_audible(
    vc: Any,
    member_ids: Any,
    timeout: float = GROUP_WAIT,
    poll_interval: float = 0.25,
) -> bool:
    wanted = [int(uid) for uid in member_ids]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not is_audible(vc, wanted):
        if loop.time() >= deadline:
            log.warning(
                "Voice cannot reach %s after %.1fs; they may hear nothing",
                missing_from_group(vc, wanted),
                timeout,
            )
            return False
        await asyncio.sleep(poll_interval)
    return True

async def _wait_until_encrypting(vc: Any, timeout: float, poll_interval: float) -> bool:
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + timeout
    no_session_deadline = min(deadline, started + NO_SESSION_GRACE)

    while True:
        sess = session(vc)
        if can_encrypt(vc):
            log.info(
                "Voice E2EE ready (privacy code %s, protocol=%s)",
                getattr(sess, "voice_privacy_code", None),
                getattr(connection(vc), "dave_protocol_version", None),
            )
            return True
        if sess is None:
            if loop.time() >= no_session_deadline:
                log.warning(
                    "Voice connection has no E2EE session (protocol=%s); a fresh "
                    "connection is required",
                    getattr(connection(vc), "dave_protocol_version", 0),
                )
                return False
        elif loop.time() >= deadline:
            log.warning(
                "Voice E2EE session not ready after %.1fs (status=%s, epoch=%s)",
                timeout,
                getattr(sess, "status", None),
                getattr(sess, "epoch", None),
            )
            return False
        await asyncio.sleep(poll_interval)

async def ensure_voice_encryption_ready(
    vc: Any,
    timeout: float = DEFAULT_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
    *,
    reconnect: Any = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> bool:
    total = max(1, attempts)
    for attempt in range(1, total + 1):
        if await _wait_until_encrypting(vc, timeout, poll_interval):
            return True
        if reconnect is None or attempt >= total:
            break
        log.warning("Voice E2EE unusable — forcing a reconnect (%d/%d)", attempt, total)
        try:
            fresh = await reconnect()
        except Exception as exc:  # noqa: BLE001 - a failed reconnect must not raise
            log.error("Voice reconnect failed: %s", exc)
            break
        if fresh is not None:
            vc = fresh

    log.error("Voice E2EE session could not be established; listeners may hear nothing")
    return False

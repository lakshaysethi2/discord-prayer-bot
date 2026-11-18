"""End-to-end encrypted (DAVE) voice readiness for PrayerBot.

Discord now requires the DAVE E2EE protocol for bot voice connections.  With
discord.py 2.7.1 + davey 0.2.0 the MLS session can sit in ``status=pending``
(the join commit never arrives), or reach ``active`` without ever being
``ready``: discord.py ignores the return value of ``DaveSession.process_commit``
and never calls ``prepare_transition`` / ``execute_transition`` for transition
id 0.  Either way ``can_encrypt`` stays ``False``, every outgoing frame is sent
in plaintext, listeners' clients drop them, and the bot is silent while its
logs still say it is playing.

A brand new voice connection always completes the handshake — that is why
kicking the bot and letting it rejoin "fixes" it — so when a session does not
reach a usable state we escalate: force a reconnect and try again.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
POLL_INTERVAL = 0.5
DEFAULT_ATTEMPTS = 3


def _session_status(session: Any) -> str:
    return str(getattr(session, "status", ""))


def is_dave_session_active(session: Any) -> bool:
    """True once the MLS group exists and the join commit has been applied."""
    return _session_status(session).rsplit(".", 1)[-1].lower() == "active"


def complete_dave_transition(conn: Any, session: Any) -> None:
    """Finish the DAVE epoch transition discord.py leaves pending."""
    protocol_version = getattr(conn, "dave_protocol_version", 1)
    try:
        session.prepare_transition(0, protocol_version)
    except Exception as exc:  # noqa: BLE001 - best effort, never break playback
        log.debug("DAVE prepare_transition failed: %s", exc)
        return
    try:
        session.execute_transition(0)
    except Exception as exc:  # noqa: BLE001 - best effort
        log.debug("DAVE execute_transition failed: %s", exc)
    try:
        session.set_passthrough_mode(False)
    except Exception as exc:  # noqa: BLE001 - best effort
        log.debug("DAVE set_passthrough_mode failed: %s", exc)


async def _wait_until_ready(vc: Any, timeout: float, poll_interval: float) -> bool:
    """Poll one connection until it can encrypt."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    transition_attempted = False

    while True:
        # Read the live state on every pass: discord.py swaps the connection
        # object when it reconnects on its own.
        conn = getattr(vc, "_connection", None)
        if conn is None:
            return True
        session = getattr(conn, "dave_session", None)
        if session is None:
            # No E2EE negotiated on this connection (older protocol/downgrade).
            return True
        if getattr(conn, "can_encrypt", False):
            log.info(
                "Voice E2EE ready (privacy code %s)",
                getattr(session, "voice_privacy_code", None),
            )
            return True
        if not transition_attempted and is_dave_session_active(session):
            transition_attempted = True
            complete_dave_transition(conn, session)
            continue
        if loop.time() >= deadline:
            log.warning(
                "Voice E2EE session not ready after %.1fs (status=%s, epoch=%s)",
                timeout,
                _session_status(session),
                getattr(session, "epoch", None),
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
    """Make sure outgoing audio will actually be decodable by listeners.

    Returns True when the connection does not use E2EE or is already able to
    encrypt.  A session stuck in ``pending`` is retried, and — when an async
    ``reconnect`` callback is supplied — escalated to a fresh voice connection,
    which is the only reliable way to get Discord to hand out a working MLS
    group.  Callers keep playing regardless: a possibly silent prayer is still
    better than an exception mid-service.
    """
    total = max(1, attempts)
    for attempt in range(1, total + 1):
        if await _wait_until_ready(vc, timeout, poll_interval):
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

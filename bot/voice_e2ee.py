"""End-to-end encrypted (DAVE) voice readiness for PrayerBot.

Discord now requires the DAVE E2EE protocol for bot voice connections.  With
discord.py 2.7.1 + davey 0.2.0 the MLS session reaches ``status=active`` after
the join commit is applied, but discord.py ignores the return value of
``DaveSession.process_commit`` and never calls ``prepare_transition`` /
``execute_transition`` for transition id 0.  The session therefore stays
``ready=False`` / ``can_encrypt=False`` and every outgoing frame is sent in
plaintext, which listeners' clients drop: the bot connects, reports playing,
sends packets, and nobody hears anything.

Finishing the transition ourselves flips the session to ``ready`` with a voice
privacy code, so audio is encrypted properly and audible again.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
POLL_INTERVAL = 0.5


def _session_status(session: Any) -> str:
    return str(getattr(session, "status", ""))


def _is_active(session: Any) -> bool:
    """True once the MLS group exists and the join commit has been applied."""
    return _session_status(session).rsplit(".", 1)[-1].lower() == "active"


def _complete_transition(conn: Any, session: Any) -> None:
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


async def ensure_voice_encryption_ready(
    vc: Any,
    timeout: float = DEFAULT_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> bool:
    """Make sure outgoing audio will actually be decodable by listeners.

    Returns True when the connection does not use E2EE or is already able to
    encrypt.  Returns False if the session stayed unusable; callers keep
    playing regardless, since a plain connection is still better than silence
    plus an exception.
    """
    conn = getattr(vc, "_connection", None)
    if conn is None:
        return True
    session = getattr(conn, "dave_session", None)
    if session is None:
        # No E2EE negotiated on this connection (older protocol or downgrade).
        return True
    if getattr(conn, "can_encrypt", False):
        return True

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    transition_attempted = False

    while True:
        if getattr(conn, "can_encrypt", False):
            log.info(
                "Voice E2EE ready (privacy code %s)",
                getattr(session, "voice_privacy_code", None),
            )
            return True
        if not transition_attempted and _is_active(session):
            transition_attempted = True
            _complete_transition(conn, session)
            continue
        if loop.time() >= deadline:
            log.error(
                "Voice E2EE session not ready after %.1fs (status=%s, epoch=%s); "
                "listeners may not hear the bot",
                timeout,
                _session_status(session),
                getattr(session, "epoch", None),
            )
            return False
        await asyncio.sleep(poll_interval)

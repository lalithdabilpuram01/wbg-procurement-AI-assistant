"""Shared offline fixtures: no live-call opt-in, no real httpx/requests traffic and no non-local socket connections."""
from __future__ import annotations

import socket
from typing import Any

import pytest

from conversation.models import ActiveContext, Owner, Turn, TurnKind, TurnMetadata

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
ALICE = Owner(tenant_id="tenant-a", user_id="alice")
BOB = Owner(tenant_id="tenant-a", user_id="bob")
MALLORY = Owner(tenant_id="tenant-b", user_id="alice")       # same user id, different tenant


@pytest.fixture(autouse=True)
def offline(monkeypatch) -> list[str]:
    """Blocks external connections (local PostgreSQL over a Unix socket or localhost stays allowed) and fails the test
    if any external connection was attempted."""
    from tests.structured_agent.nl2sql_fakes import block_live_network

    block_live_network(monkeypatch)
    attempts: list[str] = []
    real_connect = socket.socket.connect

    def guarded(self, address: Any):
        if self.family == socket.AF_UNIX or (isinstance(address, tuple) and address[0] in LOCAL_HOSTS):
            return real_connect(self, address)
        attempts.append(str(address))
        raise ConnectionRefusedError("external network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield attempts
    assert attempts == [], f"unexpected network attempts: {attempts}"


def make_turn(thread_id: str, seq: int, role: str, text: str, context: ActiveContext | None = None,
              kind: TurnKind = TurnKind.QUESTION, epoch: int = 0, **metadata: Any) -> Turn:
    return Turn(thread_id=thread_id, seq=seq, role=role, kind=kind if role == "user" else TurnKind.ANSWER, text=text,
                topic_epoch=epoch, context=context or ActiveContext(), metadata=TurnMetadata(**metadata))

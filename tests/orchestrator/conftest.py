"""Offline safety net for every orchestrator test.

* No live LLM: the live-call opt-in is removed and any real httpx / requests transport fails (existing helper).
* No external sockets: only loopback / unix-socket connections (local registry PostgreSQL) are allowed.
* No persistent audit writes: WBG_AUDIT_LOG points to a temporary file.
* Hugging Face offline.
"""
from __future__ import annotations

import socket

import pytest

from tests.structured_agent.nl2sql_fakes import block_live_network

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    block_live_network(monkeypatch)
    attempts: list[str] = []
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if host is None or host in LOCAL_HOSTS:
            return real_connect(self, address)
        attempts.append(str(host))
        raise ConnectionRefusedError("network disabled in orchestrator tests")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setenv("WBG_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    yield attempts
    assert attempts == [], f"external connection attempted: {attempts}"

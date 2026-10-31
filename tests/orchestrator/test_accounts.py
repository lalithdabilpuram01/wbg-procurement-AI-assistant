"""Accounts: sign-up, sign-in, recovery-code reset, many users at once, and the auth endpoints."""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from conversation.models import Owner
from wbg_orchestrator.accounts import AccountError, JsonFileAccountStore, UserDirectory, UsernameTaken, normalise_code
from wbg_orchestrator.api import create_app
from wbg_orchestrator.rate_limit import RateLimiter

from tests.orchestrator.test_api import Stack


@pytest.fixture
def users(tmp_path) -> UserDirectory:
    return UserDirectory.load(tmp_path / "users.json")


def test_a_new_account_is_a_signed_in_normal_user_with_a_recovery_code(users, tmp_path):
    token, code = users.sign_up("  Maria.Lopez ", "a long passphrase")
    owner = users.authenticate(token)
    assert owner == Owner(tenant_id="local", user_id="maria.lopez") and not users.is_reviewer(owner)
    assert len(normalise_code(code)) == 16
    stored = (tmp_path / "users.json").read_text()
    assert "a long passphrase" not in stored and normalise_code(code) not in stored and code not in stored


@pytest.mark.parametrize("username, password", [("ab", "long enough 1"), ("Bad Name", "long enough 1"),
                                                ("-dash", "long enough 1"), ("maria", "short"), ("maria", "maria")])
def test_weak_or_malformed_credentials_are_refused_with_a_reason(users, username, password):
    with pytest.raises(AccountError) as refused:
        users.sign_up(username, password)
    assert str(refused.value)


def test_a_username_can_only_be_taken_once(users):
    users.sign_up("maria", "first password")
    with pytest.raises(UsernameTaken):
        users.sign_up("MARIA", "second password")


def test_the_recovery_code_resets_the_password_once_and_ends_old_sessions(users):
    old_session, code = users.sign_up("maria", "first password")
    assert users.reset_password("maria", "AAAA-BBBB-CCCC-DDDD", "second password") is None
    assert users.reset_password("nobody", code, "second password") is None
    session, new_code = users.reset_password("maria", code.lower().replace("-", " "), "second password")
    assert new_code != code and users.authenticate(session).user_id == "maria"
    assert users.authenticate(old_session) is None                                      # signed out everywhere else
    assert users.sign_in("maria", "first password") is None and users.sign_in("maria", "second password")
    assert users.reset_password("maria", code, "third password") is None                # a code works once


def test_many_people_can_sign_up_at_the_same_time(users):
    errors: list[Exception] = []

    def sign_up(n: int) -> None:
        try:
            users.sign_up(f"user{n:02d}", f"password number {n}")
        except Exception as error:                                                      # collected, checked below
            errors.append(error)
    threads = [threading.Thread(target=sign_up, args=(n,)) for n in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors and len(users.store.all()) == 20
    assert all(users.sign_in(f"user{n:02d}", f"password number {n}") for n in range(20))


def test_the_operator_sets_roles_scripts_and_lost_recovery_codes(users):
    token, code = users.set_user("etl-bot")
    assert code is None and users.authenticate(token).user_id == "etl-bot"
    _, first = users.set_user("rita", "demo", reviewer=True)                           # short demo passwords allowed here
    assert users.is_reviewer(Owner(tenant_id="local", user_id="rita"))
    second = users.issue_recovery_code("rita")
    assert users.reset_password("rita", first, "new password") is None
    assert users.reset_password("rita", second, "new password") is not None
    users.set_user("rita", "demo")                                                      # without --reviewer: role removed
    assert not users.is_reviewer(Owner(tenant_id="local", user_id="rita"))
    with pytest.raises(AccountError):
        users.issue_recovery_code("nobody")


def test_old_user_files_still_sign_in(tmp_path):
    path = tmp_path / "users.json"
    token = UserDirectory.add_user(path, "user1", password="user1")
    entry = json.loads(path.read_text())["users"][0]
    assert set(entry) == {"tenant_id", "user_id", "roles", "password", "token_sha256"}
    users = UserDirectory(JsonFileAccountStore(path))
    assert users.authenticate(token).user_id == "user1" and users.sign_in("user1", "user1")


# ------------------------------------------------------------------------------------------------------- API
def auth_client(tmp_path, per_minute: int = 50) -> TestClient:
    stack = Stack(tmp_path)
    return TestClient(create_app(stack.service, UserDirectory.load(tmp_path / "users.json"),
                                 auth_limiter=RateLimiter(per_minute=per_minute)))


def test_sign_up_and_reset_through_the_api(tmp_path):
    client = auth_client(tmp_path)
    created = client.post("/auth/signup", json={"username": "maria", "password": "first password"})
    assert created.status_code == 201
    body = created.json()
    me = client.get("/me", headers={"Authorization": f"Bearer {body['token']}"}).json()
    assert me["user_id"] == "maria" and me["reviewer"] is False
    assert client.post("/auth/signup", json={"username": "maria", "password": "other password"}).status_code == 409
    weak = client.post("/auth/signup", json={"username": "jo", "password": "first password"})
    assert weak.status_code == 422 and "username" in weak.json()["detail"]
    wrong = client.post("/auth/reset", json={"username": "maria", "recovery_code": "AAAA-BBBB-CCCC-DDDD",
                                             "new_password": "second password"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "wrong username or recovery code"
    reset = client.post("/auth/reset", json={"username": "maria", "recovery_code": body["recovery_code"],
                                             "new_password": "second password"})
    assert reset.status_code == 200 and reset.json()["recovery_code"] != body["recovery_code"]
    assert client.post("/auth/login", json={"username": "maria", "password": "second password"}).status_code == 200


def test_auth_attempts_are_limited_per_address(tmp_path):
    client = auth_client(tmp_path, per_minute=3)
    for _ in range(3):
        assert client.post("/auth/login", json={"username": "maria", "password": "guess"}).status_code == 401
    limited = client.post("/auth/login", json={"username": "maria", "password": "guess"})
    assert limited.status_code == 429 and "Retry-After" in limited.headers

"""User accounts: sign-up, password sign-in, recovery codes and roles, over one store.

    JsonFileAccountStore   a JSON file (tests, and the source of `wbg-user migrate`; the old ~/.wbg/api_users.json)
    PostgresAccountStore   the Lakebase table wbg_auth.app_user, which the app uses (accounts_db.py creates it)

No secret is stored: passwords and recovery codes are kept as salted scrypt hashes, script tokens as SHA-256.

Accounts:
* anyone may sign up; a new account is a normal user (the reviewer role is only given with `wbg-user add --reviewer`);
* a recovery code is shown once (at sign-up, after a reset, or when issued with `wbg-user recovery-code`) and works
  once: "forgot password" takes the username, the recovery code and a new password, and hands out a new code;
* a password sign-in gives a session token kept in memory for SESSION_HOURS (a restart signs everyone out); a reset
  ends every session of that user;
* every session also has a session id (`ses_...`), which is not a secret: the logs and traces carry it, so all the
  work of one sign-in can be found together. A script token has one fixed id (`tok_` + part of its hash).
Every failed sign-in or reset gives the same answer and does the same work for a known and an unknown username, so
the answers do not reveal which usernames exist.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterator, Protocol

from conversation.models import Owner

from .contracts import OwnerRef
from .logs import security_event

SCRYPT = {"n": 2**14, "r": 8, "p": 1}                     # the same parameters as the hashes already stored
SESSION_HOURS = 12
TENANT = "local"
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD, MAX_PASSWORD = 8, 200
REVIEWER = "reviewer"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O or 1/I: easy to copy by hand
CODE_GROUPS, CODE_GROUP_LENGTH = 4, 4
_DUMMY = {"salt": "00" * 16, "scrypt": "00" * 32}         # hashed against for unknown users, so timing reveals nothing


class AccountError(ValueError):
    """A sign-up or reset that cannot be accepted; the message is safe to show to the user."""


class UsernameTaken(AccountError):
    pass


# --------------------------------------------------------------------------------------------------- secrets
def hash_secret(value: str) -> dict[str, str]:
    """A salted scrypt hash of a password or recovery code, as hex strings {"salt", "scrypt"}."""
    salt = secrets.token_bytes(16)
    return {"salt": salt.hex(), "scrypt": hashlib.scrypt(value.encode(), salt=salt, **SCRYPT).hex()}


def secret_matches(value: str, stored: dict[str, str] | None) -> bool:
    """Constant-time check; an absent secret is checked against a dummy hash and never matches."""
    reference = stored or _DUMMY
    attempt = hashlib.scrypt(value.encode(), salt=bytes.fromhex(reference["salt"]), **SCRYPT).hex()
    return stored is not None and hmac.compare_digest(attempt, reference["scrypt"])


def new_recovery_code() -> str:
    """Four groups of four characters, e.g. K7QM-3XRA-PZ4T-HW9C (80 bits)."""
    chars = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUPS * CODE_GROUP_LENGTH))
    return "-".join(chars[i:i + CODE_GROUP_LENGTH] for i in range(0, len(chars), CODE_GROUP_LENGTH))


def normalise_code(code: str) -> str:
    """The code as typed, without spaces or dashes and in capitals (so 'k7qm 3xra ...' works too)."""
    return re.sub(r"[\s-]", "", code).upper()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_username(username: str) -> str:
    return username.strip().lower()


def user_hash(owner: Owner) -> str:
    """The pseudonymous id that logs and traces use for a user (never the username itself)."""
    return OwnerRef.from_identity(owner.tenant_id, owner.user_id).user_hash


def check_new_credentials(username: str, password: str) -> None:
    """Raise AccountError when a new username or password breaks the sign-up rules."""
    if not USERNAME.match(username):
        raise AccountError("A username has 3 to 32 characters: lowercase letters, digits, dots, dashes or underscores, "
                           "starting with a letter or digit.")
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AccountError(f"A password needs at least {MIN_PASSWORD} characters.")
    if password.strip().lower() == username:
        raise AccountError("The password cannot be the same as the username.")


# --------------------------------------------------------------------------------------------------- records
@dataclass(frozen=True)
class Account:
    """One stored account: roles and the hashes of its password, recovery code and script token (never the secrets)."""
    user_id: str
    tenant_id: str = TENANT
    roles: tuple[str, ...] = ()
    password: dict[str, str] | None = None        # {"salt", "scrypt"}
    recovery: dict[str, str] | None = None        # {"salt", "scrypt"} of the current recovery code
    token_sha256: str | None = None               # a script token (`wbg-user add` without --password)

    @property
    def owner(self) -> Owner:
        return Owner(tenant_id=self.tenant_id, user_id=self.user_id)

    def to_json(self) -> dict[str, Any]:
        """The account as a JSON file entry; absent secrets are left out rather than written as null."""
        out: dict[str, Any] = {"tenant_id": self.tenant_id, "user_id": self.user_id, "roles": list(self.roles)}
        out.update({k: v for k, v in (("password", self.password), ("recovery", self.recovery),
                                      ("token_sha256", self.token_sha256)) if v})
        return out

    @classmethod
    def from_json(cls, entry: dict[str, Any]) -> "Account":
        return cls(user_id=entry["user_id"], tenant_id=entry.get("tenant_id", TENANT), roles=tuple(entry.get("roles") or ()),
                   password=entry.get("password"), recovery=entry.get("recovery"), token_sha256=entry.get("token_sha256"))


class AccountStore(Protocol):
    """What the user directory needs from a store: lookups, insert-if-new, insert-or-replace and a full listing."""

    def get(self, tenant_id: str, user_id: str) -> Account | None: ...
    def by_token(self, digest: str) -> Account | None: ...
    def insert(self, account: Account) -> bool: ...          # False when the username is taken
    def save(self, account: Account) -> None: ...            # insert or replace
    def all(self) -> list[Account]: ...


# ---------------------------------------------------------------------------------------------------- stores
class JsonFileAccountStore:
    """Accounts in one JSON file (mode 600). Every change holds a file lock and replaces the file atomically, so
    several API workers and the `wbg-user` command can change it at the same time."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[list[dict[str, Any]]]:
        """Hold the file lock, yield the users list for changing, then write it back atomically with mode 600."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_name(self.path.name + ".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            users = self._read()
            yield users
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps({"users": users}, indent=2))
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary, self.path)

    def _read(self) -> list[dict[str, Any]]:
        return json.loads(self.path.read_text())["users"] if self.path.is_file() else []

    def get(self, tenant_id: str, user_id: str) -> Account | None:
        return next((Account.from_json(e) for e in self._read() if e["tenant_id"] == tenant_id and e["user_id"] == user_id), None)

    def by_token(self, digest: str) -> Account | None:
        return next((Account.from_json(e) for e in self._read()
                     if e.get("token_sha256") and hmac.compare_digest(e["token_sha256"], digest)), None)

    def insert(self, account: Account) -> bool:
        """Add the account unless the username exists; returns False when it was taken."""
        with self._locked() as users:
            if any(e["tenant_id"] == account.tenant_id and e["user_id"] == account.user_id for e in users):
                return False
            users.append(account.to_json())
            return True

    def save(self, account: Account) -> None:
        """Insert the account, replacing any existing entry with the same tenant and username."""
        with self._locked() as users:
            users[:] = [e for e in users if not (e["tenant_id"] == account.tenant_id and e["user_id"] == account.user_id)]
            users.append(account.to_json())

    def all(self) -> list[Account]:
        return [Account.from_json(e) for e in self._read()]


Connect = Callable[[], ContextManager[Any]]
COLUMNS = ("tenant_id", "user_id", "roles", "password_salt", "password_scrypt", "recovery_salt", "recovery_scrypt",
           "token_sha256")


class PostgresAccountStore:
    """Accounts in `<schema>.app_user` (Lakebase). `connect` is a pool's `connection` method."""

    def __init__(self, connect: Connect, schema: str = "wbg_auth"):
        from psycopg import sql
        self._connect, self._sql = connect, sql
        self._table = sql.SQL("{}.app_user").format(sql.Identifier(schema))

    def _one(self, where: str, params: tuple) -> Account | None:
        """The first account matching a fixed WHERE clause (values are always bound parameters), or None."""
        from psycopg.rows import dict_row
        query = self._sql.SQL("SELECT {} FROM {} WHERE " + where).format(
            self._sql.SQL(", ").join(map(self._sql.Identifier, COLUMNS)), self._table)
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(query, params).fetchone()
        return _from_row(row) if row else None

    def get(self, tenant_id: str, user_id: str) -> Account | None:
        return self._one("tenant_id = %s AND user_id = %s", (tenant_id, user_id))

    def by_token(self, digest: str) -> Account | None:
        return self._one("token_sha256 = %s", (digest,))

    def insert(self, account: Account) -> bool:
        """Add the account unless the username exists; returns False when it was taken."""
        query = self._sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (tenant_id, user_id) DO NOTHING RETURNING user_id").format(
            self._table, self._sql.SQL(", ").join(map(self._sql.Identifier, COLUMNS)),
            self._sql.SQL(", ").join(self._sql.Placeholder() * len(COLUMNS)))
        with self._connect() as conn:
            return conn.execute(query, _to_row(account)).fetchone() is not None

    def save(self, account: Account) -> None:
        """Upsert the account; every column except the key is replaced and updated_at is refreshed."""
        updates = self._sql.SQL(", ").join(self._sql.SQL("{0} = EXCLUDED.{0}").format(self._sql.Identifier(c)) for c in COLUMNS[2:])
        query = self._sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (tenant_id, user_id) DO UPDATE SET {}, "
                              "updated_at = now()").format(
            self._table, self._sql.SQL(", ").join(map(self._sql.Identifier, COLUMNS)),
            self._sql.SQL(", ").join(self._sql.Placeholder() * len(COLUMNS)), updates)
        with self._connect() as conn:
            conn.execute(query, _to_row(account))

    def all(self) -> list[Account]:
        """Every account, ordered by tenant and username."""
        from psycopg.rows import dict_row
        query = self._sql.SQL("SELECT {} FROM {} ORDER BY tenant_id, user_id").format(
            self._sql.SQL(", ").join(map(self._sql.Identifier, COLUMNS)), self._table)
        with self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            return [_from_row(r) for r in cur.execute(query).fetchall()]


def _secret(salt: str | None, digest: str | None) -> dict[str, str] | None:
    return {"salt": salt, "scrypt": digest} if salt and digest else None


def _from_row(row: dict[str, Any]) -> Account:
    """Build an Account from a table row (the salt and hash columns become the secret dicts)."""
    return Account(user_id=row["user_id"], tenant_id=row["tenant_id"], roles=tuple(row["roles"] or ()),
                   password=_secret(row["password_salt"], row["password_scrypt"]),
                   recovery=_secret(row["recovery_salt"], row["recovery_scrypt"]), token_sha256=row["token_sha256"])


def _to_row(account: Account) -> tuple:
    """The account's values in COLUMNS order, ready to bind to an INSERT."""
    password, recovery = account.password or {}, account.recovery or {}
    return (account.tenant_id, account.user_id, list(account.roles), password.get("salt"), password.get("scrypt"),
            recovery.get("salt"), recovery.get("scrypt"), account.token_sha256)


# ------------------------------------------------------------------------------------------------- directory
@dataclass(frozen=True)
class Identity:
    """Who sent a request: the owner and the session the token belongs to (the session id is safe to log)."""

    owner: Owner
    session_id: str

    @property
    def user_hash(self) -> str:
        return user_hash(self.owner)


@dataclass(frozen=True)
class _Session:
    identity: Identity
    expires_at: float


@dataclass
class _Sessions:
    """Password sign-in sessions: token SHA-256 -> session (owner, session id, expiry), in memory."""

    entries: dict[str, _Session] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def open(self, owner: Owner) -> str:
        """Start a session for `owner` and return its token; only the token's SHA-256 is kept."""
        token = secrets.token_urlsafe(32)
        identity = Identity(owner, f"ses_{secrets.token_hex(10)}")
        with self.lock:
            self.entries[token_digest(token)] = _Session(identity, time.time() + SESSION_HOURS * 3600)
        return token

    def identity(self, digest: str) -> Identity | None:
        """The identity of a live session; an expired one is removed and logged as ended."""
        with self.lock:
            session = self.entries.get(digest)
            if session and session.expires_at > time.time():
                return session.identity
            expired = self.entries.pop(digest, None)
        if expired:
            _ended(expired.identity, "expired")
        return None

    def end_all(self, owner: Owner, reason: str) -> None:
        """End every session of `owner` (used after a password reset) and log each one."""
        with self.lock:
            ended = [self.entries.pop(d).identity for d in [d for d, s in self.entries.items() if s.identity.owner == owner]]
        for identity in ended:
            _ended(identity, reason)


def _ended(identity: Identity, reason: str) -> None:
    security_event("auth.session_ended", "success", reason=reason, session_id=identity.session_id,
                   user_hash=identity.user_hash)


def script_identity(account: Account, digest: str) -> Identity:
    """A script token's fixed session id: part of the token's SHA-256, which reveals nothing about the token."""
    return Identity(account.owner, f"tok_{digest[:12]}")


class UserDirectory:
    """The app's users: who a token belongs to, sign-in, sign-up, password reset and roles."""

    def __init__(self, store: AccountStore, tenant_id: str = TENANT):
        self.store, self.tenant_id = store, tenant_id
        self._sessions = _Sessions()

    @classmethod
    def load(cls, path: Path) -> "UserDirectory":
        """A directory over a JSON user file (tests; the app itself uses the Lakebase store)."""
        return cls(JsonFileAccountStore(path))

    # ------------------------------------------------------------------------------------------- tokens
    def identify(self, token: str) -> Identity | None:
        """The owner and session of a token: a sign-in session (in memory, checked first), else a script token."""
        digest = token_digest(token)
        session = self._sessions.identity(digest)
        if session:
            return session
        account = self.store.by_token(digest)
        return script_identity(account, digest) if account else None

    def authenticate(self, token: str) -> Owner | None:
        identity = self.identify(token)
        return identity.owner if identity else None

    def account_owner(self, username: str) -> Owner | None:
        """The owner of an existing account (None for an unknown username), for logging a failed attempt."""
        account = self.store.get(self.tenant_id, normalise_username(username))
        return account.owner if account else None

    def is_reviewer(self, owner: Owner) -> bool:
        account = self.store.get(owner.tenant_id, owner.user_id)
        return account is not None and REVIEWER in account.roles

    # ------------------------------------------------------------------------------------ self-service
    def sign_in(self, username: str, password: str) -> str | None:
        """A session token for the right password (None otherwise)."""
        account = self.store.get(self.tenant_id, normalise_username(username))
        if not secret_matches(password, account.password if account else None):
            return None
        return self._sessions.open(account.owner)

    def sign_up(self, username: str, password: str) -> tuple[str, str]:
        """A new normal user: (session token, recovery code to show once). Raises AccountError or UsernameTaken."""
        user_id = normalise_username(username)
        check_new_credentials(user_id, password)
        code = new_recovery_code()
        account = Account(user_id=user_id, tenant_id=self.tenant_id, password=hash_secret(password),
                          recovery=hash_secret(normalise_code(code)))
        if not self.store.insert(account):
            raise UsernameTaken("This username is already taken. Please choose another one.")
        return self._sessions.open(account.owner), code

    def reset_password(self, username: str, recovery_code: str, new_password: str) -> tuple[str, str] | None:
        """With the right recovery code: a new password, a new recovery code (the used one stops working) and a new
        session; every other session of the user ends. None when the username or code is wrong."""
        user_id = normalise_username(username)
        account = self.store.get(self.tenant_id, user_id)
        if not secret_matches(normalise_code(recovery_code), account.recovery if account else None):
            return None
        # Checked only after the code matches, so a wrong code always gets the same None answer as an unknown user.
        check_new_credentials(user_id, new_password)
        code = new_recovery_code()
        self.store.save(replace(account, password=hash_secret(new_password), recovery=hash_secret(normalise_code(code))))
        self._sessions.end_all(account.owner, reason="password_reset")
        return self._sessions.open(account.owner), code

    # ------------------------------------------------------------------------------------------ operator
    def set_user(self, user_id: str, password: str | None = None, reviewer: bool = False) -> tuple[str | None, str | None]:
        """Create or replace a user from the command line: (script token or None, recovery code or None).

        With a password the user signs in with it and gets a recovery code; without one, a script token is issued.
        The operator may set short demo passwords: only the username rules apply here."""
        user_id = normalise_username(user_id)
        if not USERNAME.match(user_id):
            raise AccountError(f"{user_id!r} is not a valid username (3 to 32 lowercase letters, digits, '.', '-', '_').")
        existing = self.store.get(self.tenant_id, user_id)
        roles = tuple(sorted({*(existing.roles if existing else ()), REVIEWER} if reviewer else
                             {r for r in (existing.roles if existing else ()) if r != REVIEWER}))
        token = None if password else secrets.token_urlsafe(32)
        code = new_recovery_code() if password else None
        self.store.save(Account(user_id=user_id, tenant_id=self.tenant_id, roles=roles,
                                password=hash_secret(password) if password else (existing.password if existing else None),
                                recovery=hash_secret(normalise_code(code)) if code else (existing.recovery if existing else None),
                                token_sha256=token_digest(token) if token else (existing.token_sha256 if existing else None)))
        return token, code

    def issue_recovery_code(self, user_id: str) -> str:
        """A new recovery code for a user who lost theirs (the old one stops working)."""
        account = self.store.get(self.tenant_id, normalise_username(user_id))
        if account is None:
            raise AccountError(f"No user named {user_id!r}.")
        code = new_recovery_code()
        self.store.save(replace(account, recovery=hash_secret(normalise_code(code))))
        return code

    @staticmethod
    def add_user(path: Path, user_id: str, tenant_id: str = TENANT, reviewer: bool = False, password: str | None = None) -> str:
        """A script token for a user of a JSON user file (tests and local tools); only its hash is stored."""
        token = secrets.token_urlsafe(32)
        JsonFileAccountStore(path).save(Account(user_id=user_id, tenant_id=tenant_id, roles=(REVIEWER,) if reviewer else (),
                                                password=hash_secret(password) if password else None,
                                                token_sha256=token_digest(token)))
        return token

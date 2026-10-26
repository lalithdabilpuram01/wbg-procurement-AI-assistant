"""Principal resolution and data-access policy (static assessment policy from curated/access_policy.json).

A principal may use an object when one of its roles is in the object's authorized_roles. Column use depends on the
column classification and on how the column is used: 'output' (selected or returned) or 'filter' (WHERE / JOIN /
GROUP BY / ORDER BY). Prohibited columns are never usable. Nothing here is broadened after a failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from structured_agent.metadata.sources import curated
from structured_agent.registry.models import RegisteredColumn, RegisteredObject

ColumnUse = Literal["output", "filter"]


class AuthorizationError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    principal_id: str
    workspace: str
    roles: tuple[str, ...]


class AccessPolicy:
    def __init__(self, config: dict | None = None):
        config = config or curated("access_policy.json")
        self._workspaces = set(config["workspaces"])
        self._roles = config["roles"]
        self._principals = config["principals"]

    def resolve(self, principal_id: str, workspace: str) -> Principal:
        """Look up a principal; unknown principals or a workspace mismatch are refused."""
        entry = self._principals.get(principal_id)
        if entry is None:
            raise AuthorizationError(f"Unknown principal '{principal_id}'")
        if workspace not in self._workspaces or entry["workspace"] != workspace:
            raise AuthorizationError(f"Principal '{principal_id}' is not authorized for workspace '{workspace}'")
        return Principal(principal_id, workspace, tuple(entry["roles"]))

    def can_use_object(self, principal: Principal, obj: RegisteredObject) -> bool:
        return any(role in obj.authorized_roles for role in principal.roles)

    def can_use_column(self, principal: Principal, column: RegisteredColumn, use: ColumnUse) -> bool:
        if column.classification == "prohibited":
            return False
        key = "output_classifications" if use == "output" else "filter_classifications"
        return any(column.classification in self._roles[role][key] for role in principal.roles if role in self._roles)

    def max_rows(self, principal: Principal, default: int) -> int:
        limits = [self._roles[r]["max_rows"] for r in principal.roles if r in self._roles]
        return min(limits + [default]) if limits else default

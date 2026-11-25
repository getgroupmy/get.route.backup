"""Who may do what in the console.

Each console user has one role. Signing in with the admin token is the owner.
Checks run on the server; the console only hides what you cannot use."""
from __future__ import annotations

from dataclasses import dataclass

PERMISSIONS = {
    "overview.view": "See the overview and the request log",
    "clients.view": "See API clients",
    "clients.manage": "Create, revoke and delete API clients, change their limits",
    "backends.view": "See the Google Maps servers and their health",
    "backends.manage": "Add, change, reorder and remove Google Maps servers",
    "settings.manage": "Change time limits and how long the log is kept",
    "tests.run": "Run Try it",
    "audit.view": "Check the audit trail",
    "admins.manage": "Create console users and set their roles",
}

_ALL = set(PERMISSIONS)
ROLES: dict[str, dict] = {
    "owner": {"perms": _ALL, "label": "Owner: everything, including console users"},
    "admin": {"perms": _ALL - {"admins.manage"}, "label": "Admin: everything except console users"},
    "developer": {"perms": {"overview.view", "clients.view", "clients.manage", "backends.view", "tests.run"},
                  "label": "Developer: API keys, Try it, and what is happening"},
    "viewer": {"perms": {"overview.view", "clients.view", "backends.view", "audit.view"},
               "label": "Viewer: read only"},
}


@dataclass(frozen=True)
class Ctx:
    actor: str  # what the audit trail records: "admin" (token) or "admin:<username>"
    username: str | None
    role: str

    @property
    def perms(self) -> set[str]:
        return ROLES[self.role]["perms"]

    def can(self, perm: str) -> bool:
        return perm in self.perms

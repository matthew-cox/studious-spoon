"""Authorization policy (spec §6.1). The only place access decisions are made. Pure."""

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import cast

MANAGED_ROLES = frozenset({"admin", "editor", "viewer"})


@dataclass(frozen=True)
class Principal:
    sub: str
    username: str
    roles: frozenset[str]

    @property
    def managed_roles(self) -> frozenset[str]:
        return self.roles & MANAGED_ROLES

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def is_editor(self) -> bool:
        return "editor" in self.roles

    @property
    def is_viewer(self) -> bool:
        return "viewer" in self.roles


class Action(StrEnum):
    LIST = "list"
    READ = "read"
    STATS = "stats"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    BLOCK = "block"  # block and unblock
    AUDIT = "audit"  # a link's moderation history (who blocked, unblocked, deleted it)


class Decision(Enum):
    ALLOW = 200
    FORBIDDEN = 403
    NOT_FOUND = 404  # hides the link's existence from non-owners (D9)
    CONFLICT = 409  # owner changing a blocked link (§4.3)


@dataclass(frozen=True)
class LinkFacts:
    owner_sub: str
    blocked: bool


_COLLECTION_ACTIONS = {Action.LIST, Action.CREATE}


def decide(principal: Principal, action: Action, link: LinkFacts | None = None) -> Decision:
    if action not in _COLLECTION_ACTIONS and link is None:
        raise ValueError(f"action {action} requires a link")
    if not principal.managed_roles:
        return Decision.FORBIDDEN
    if principal.is_admin:
        return Decision.ALLOW
    if action is Action.LIST:
        return Decision.ALLOW
    if action is Action.CREATE:
        return Decision.ALLOW if principal.is_editor else Decision.FORBIDDEN

    facts = cast(LinkFacts, link)  # guaranteed by the check above
    owns = principal.is_editor and facts.owner_sub == principal.sub
    if action in (Action.READ, Action.STATS):
        return Decision.ALLOW if owns or principal.is_viewer else Decision.NOT_FOUND
    if action in (Action.UPDATE, Action.DELETE):
        if owns:
            return Decision.CONFLICT if facts.blocked else Decision.ALLOW
        return Decision.FORBIDDEN if principal.is_viewer else Decision.NOT_FOUND
    # Action.BLOCK, Action.AUDIT: admins only. Callers who can see the link get 403; others get 404.
    return Decision.FORBIDDEN if owns or principal.is_viewer else Decision.NOT_FOUND


def visible_owner(principal: Principal) -> str | None:
    """owner_sub filter for list/summary queries, or None when the caller may see every link."""
    if principal.is_admin or principal.is_viewer:
        return None
    return principal.sub

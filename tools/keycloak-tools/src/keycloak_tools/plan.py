from collections.abc import Sequence
from dataclasses import dataclass, field

from keycloak_tools.users import MANAGED_ROLES, DesiredUser


@dataclass(frozen=True)
class ExistingUser:
    id: str
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None
    enabled: bool
    roles: frozenset[str]


@dataclass(frozen=True)
class CreateUser:
    user: DesiredUser


@dataclass(frozen=True)
class UpdateProfile:
    user_id: str
    user: DesiredUser


@dataclass(frozen=True)
class SetPassword:
    user_id: str
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class AddRoles:
    user_id: str
    username: str
    roles: frozenset[str]


@dataclass(frozen=True)
class RemoveRoles:
    user_id: str
    username: str
    roles: frozenset[str]


Action = CreateUser | UpdateProfile | SetPassword | AddRoles | RemoveRoles


def _profile_differs(want: DesiredUser, have: ExistingUser) -> bool:
    return (
        not have.enabled
        or have.email != want.email
        or have.first_name != want.first_name
        or have.last_name != want.last_name
    )


def plan_changes(
    desired: Sequence[DesiredUser],
    existing: Sequence[ExistingUser],
    reset_passwords: bool = False,
) -> list[Action]:
    """Compute the actions that make Keycloak match `desired`. Pure; never deletes users."""
    by_username = {user.username: user for user in existing}
    actions: list[Action] = []
    for want in desired:
        have = by_username.get(want.username)
        if have is None:
            actions.append(CreateUser(want))
            continue
        if _profile_differs(want, have):
            actions.append(UpdateProfile(have.id, want))
        if reset_passwords:
            actions.append(SetPassword(have.id, want.username, want.password))
        current_managed = have.roles & MANAGED_ROLES
        if to_add := want.roles - current_managed:
            actions.append(AddRoles(have.id, want.username, frozenset(to_add)))
        if to_remove := current_managed - want.roles:
            actions.append(RemoveRoles(have.id, want.username, frozenset(to_remove)))
    return actions

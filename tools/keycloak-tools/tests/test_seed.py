from dataclasses import replace

import pytest
from pydantic import ValidationError

from keycloak_tools.plan import CreateUser, ExistingUser
from keycloak_tools.seed import SeedSettings, run_seed
from keycloak_tools.users import DesiredUser


class FakeKeycloakAdmin:
    def __init__(self) -> None:
        self.users: dict[str, ExistingUser] = {}
        self.passwords: dict[str, str] = {}

    def list_users(self) -> list[ExistingUser]:
        return list(self.users.values())

    def create_user(self, user: DesiredUser) -> str:
        uid = f"id-{len(self.users) + 1}"
        self.users[uid] = ExistingUser(
            uid, user.username, user.email, user.first_name, user.last_name, True, frozenset()
        )
        self.passwords[uid] = user.password
        return uid

    def update_profile(self, user_id: str, user: DesiredUser) -> None:
        self.users[user_id] = replace(
            self.users[user_id],
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            enabled=True,
        )

    def set_password(self, user_id: str, password: str) -> None:
        self.passwords[user_id] = password

    def add_realm_roles(self, user_id: str, roles: frozenset[str]) -> None:
        old = self.users[user_id]
        self.users[user_id] = replace(old, roles=old.roles | roles)

    def remove_realm_roles(self, user_id: str, roles: frozenset[str]) -> None:
        old = self.users[user_id]
        self.users[user_id] = replace(old, roles=old.roles - roles)


ALICE = DesiredUser(
    username="alice",
    email="alice@example.test",
    first_name="Alice",
    last_name="Admin",
    password="pw",
    roles=frozenset({"admin"}),
)


def test_first_run_creates_user_with_password_and_roles():
    admin = FakeKeycloakAdmin()
    assert run_seed(admin, [ALICE]) == [CreateUser(ALICE)]
    [created] = admin.users.values()
    assert created.roles == frozenset({"admin"})
    assert admin.passwords[created.id] == "pw"


def test_second_run_makes_no_changes():
    admin = FakeKeycloakAdmin()
    run_seed(admin, [ALICE])
    assert run_seed(admin, [ALICE]) == []


def test_role_and_profile_drift_is_repaired():
    admin = FakeKeycloakAdmin()
    run_seed(admin, [ALICE])
    uid = next(iter(admin.users))
    admin.remove_realm_roles(uid, frozenset({"admin"}))
    admin.add_realm_roles(uid, frozenset({"viewer"}))
    admin.users[uid] = replace(admin.users[uid], email="stale@example.test", enabled=False)
    run_seed(admin, [ALICE])
    assert admin.users[uid].roles == frozenset({"admin"})
    assert admin.users[uid].email == "alice@example.test"
    assert admin.users[uid].enabled


def test_reset_passwords_overwrites_existing_password():
    admin = FakeKeycloakAdmin()
    run_seed(admin, [ALICE])
    uid = next(iter(admin.users))
    admin.passwords[uid] = "changed-by-user"
    run_seed(admin, [ALICE], reset_passwords=True)
    assert admin.passwords[uid] == "pw"


def test_seed_settings_require_keycloak_url(monkeypatch):
    """In-container a missing URL must fail at startup, not silently target localhost."""
    monkeypatch.delenv("KEYCLOAK_URL", raising=False)
    monkeypatch.setenv("KEYCLOAK_ADMIN_USER", "kcadmin")
    monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", "pw")
    with pytest.raises(ValidationError, match="keycloak_url"):
        SeedSettings()

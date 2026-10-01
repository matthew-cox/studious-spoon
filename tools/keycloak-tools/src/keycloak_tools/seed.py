"""Seed realm users from a YAML file. Idempotent: a second run reports no changes."""

from collections.abc import Sequence
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from keycloak_tools.admin_client import KeycloakAdmin, KeycloakAdminClient
from keycloak_tools.plan import (
    Action,
    AddRoles,
    CreateUser,
    RemoveRoles,
    SetPassword,
    UpdateProfile,
    plan_changes,
)
from keycloak_tools.users import DesiredUser, load_users


class SeedSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    keycloak_url: str = "http://localhost:8080"
    keycloak_realm: str = "shortener"
    keycloak_admin_user: str
    keycloak_admin_password: str
    users_file: Path = Path("infra/keycloak/users.yaml")
    reset_passwords: bool = False


def run_seed(
    admin: KeycloakAdmin, desired: Sequence[DesiredUser], reset_passwords: bool = False
) -> list[Action]:
    actions = plan_changes(desired, admin.list_users(), reset_passwords=reset_passwords)
    for action in actions:
        match action:
            case CreateUser(user=user):
                user_id = admin.create_user(user)
                if user.roles:
                    admin.add_realm_roles(user_id, user.roles)
            case UpdateProfile(user_id=user_id, user=user):
                admin.update_profile(user_id, user)
            case SetPassword(user_id=user_id, password=password):
                admin.set_password(user_id, password)
            case AddRoles(user_id=user_id, roles=roles):
                admin.add_realm_roles(user_id, roles)
            case RemoveRoles(user_id=user_id, roles=roles):
                admin.remove_realm_roles(user_id, roles)
    return actions


def main(settings: SeedSettings | None = None) -> None:
    settings = settings or SeedSettings()
    admin = KeycloakAdminClient(
        settings.keycloak_url,
        settings.keycloak_realm,
        settings.keycloak_admin_user,
        settings.keycloak_admin_password,
    )
    actions = run_seed(admin, load_users(settings.users_file), settings.reset_passwords)
    if not actions:
        print("keycloak-seed: no changes")
    for action in actions:
        print(f"keycloak-seed: {action!r}")


if __name__ == "__main__":
    main()

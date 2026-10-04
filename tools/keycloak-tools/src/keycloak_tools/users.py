from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MANAGED_ROLES: frozenset[str] = frozenset({"admin", "editor", "viewer", "support"})


class DesiredUser(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    username: str = Field(pattern=r"^[a-z0-9._-]{3,64}$")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    password: str = Field(min_length=1, repr=False)
    roles: frozenset[str] = frozenset()

    @field_validator("email")
    @classmethod
    def _lowercase_email(cls, email: str) -> str:
        # Keycloak stores emails lowercased; compare like with like so re-seeding is a no-op.
        return email.lower()

    @field_validator("roles")
    @classmethod
    def _known_roles(cls, roles: frozenset[str]) -> frozenset[str]:
        unknown = roles - MANAGED_ROLES
        if unknown:
            raise ValueError(f"unknown role(s): {sorted(unknown)}")
        return roles


class UsersFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[DesiredUser]

    @model_validator(mode="after")
    def _unique_usernames(self) -> "UsersFile":
        seen: set[str] = set()
        for user in self.users:
            if user.username in seen:
                raise ValueError(f"duplicate username: {user.username}")
            seen.add(user.username)
        return self


def load_users(path: Path) -> list[DesiredUser]:
    return UsersFile.model_validate(yaml.safe_load(path.read_text())).users

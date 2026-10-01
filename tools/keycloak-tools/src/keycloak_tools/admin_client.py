from typing import Any, Protocol

import httpx

from keycloak_tools.plan import ExistingUser
from keycloak_tools.users import DesiredUser


class KeycloakAdmin(Protocol):
    def list_users(self) -> list[ExistingUser]: ...
    def create_user(self, user: DesiredUser) -> str: ...
    def update_profile(self, user_id: str, user: DesiredUser) -> None: ...
    def set_password(self, user_id: str, password: str) -> None: ...
    def add_realm_roles(self, user_id: str, roles: frozenset[str]) -> None: ...
    def remove_realm_roles(self, user_id: str, roles: frozenset[str]) -> None: ...


class KeycloakAdminClient:
    """Keycloak admin REST client authenticated as the master-realm bootstrap admin."""

    def __init__(
        self,
        base_url: str,
        realm: str,
        admin_user: str,
        admin_password: str,
        http: httpx.Client | None = None,
    ) -> None:
        self._http = http or httpx.Client(base_url=base_url, timeout=10.0)
        self._realm = realm
        response = self._http.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": admin_user,
                "password": admin_password,
            },
        )
        response.raise_for_status()
        self._http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    def _path(self, suffix: str) -> str:
        return f"/admin/realms/{self._realm}{suffix}"

    def _get(self, suffix: str, **params: Any) -> Any:
        response = self._http.get(self._path(suffix), params=params)
        response.raise_for_status()
        return response.json()

    def list_users(self) -> list[ExistingUser]:
        users = []
        for raw in self._get("/users", max=1000, briefRepresentation="false"):
            mappings = self._get(f"/users/{raw['id']}/role-mappings/realm")
            users.append(
                ExistingUser(
                    id=str(raw["id"]),
                    username=str(raw["username"]),
                    email=raw.get("email"),
                    first_name=raw.get("firstName"),
                    last_name=raw.get("lastName"),
                    enabled=bool(raw.get("enabled", False)),
                    roles=frozenset(str(m["name"]) for m in mappings),
                )
            )
        return users

    def _profile(self, user: DesiredUser) -> dict[str, Any]:
        return {
            "username": user.username,
            "email": user.email,
            "firstName": user.first_name,
            "lastName": user.last_name,
            "enabled": True,
            "emailVerified": True,
        }

    def create_user(self, user: DesiredUser) -> str:
        body = self._profile(user) | {
            "credentials": [{"type": "password", "value": user.password, "temporary": False}]
        }
        response = self._http.post(self._path("/users"), json=body)
        response.raise_for_status()
        return response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    def update_profile(self, user_id: str, user: DesiredUser) -> None:
        self._http.put(self._path(f"/users/{user_id}"), json=self._profile(user)).raise_for_status()

    def set_password(self, user_id: str, password: str) -> None:
        self._http.put(
            self._path(f"/users/{user_id}/reset-password"),
            json={"type": "password", "value": password, "temporary": False},
        ).raise_for_status()

    def _role_representations(self, roles: frozenset[str]) -> list[dict[str, Any]]:
        return [dict(self._get(f"/roles/{name}")) for name in sorted(roles)]

    def add_realm_roles(self, user_id: str, roles: frozenset[str]) -> None:
        self._http.post(
            self._path(f"/users/{user_id}/role-mappings/realm"),
            json=self._role_representations(roles),
        ).raise_for_status()

    def remove_realm_roles(self, user_id: str, roles: frozenset[str]) -> None:
        self._http.request(
            "DELETE",
            self._path(f"/users/{user_id}/role-mappings/realm"),
            json=self._role_representations(roles),
        ).raise_for_status()

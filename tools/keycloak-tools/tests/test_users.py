from pathlib import Path

import pytest
from pydantic import ValidationError

from keycloak_tools.users import load_users

REPO_ROOT = Path(__file__).resolve().parents[3]

VALID = """
users:
  - username: alice
    email: alice@example.test
    first_name: Alice
    last_name: Admin
    password: s3cret
    roles: [admin]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "users.yaml"
    path.write_text(text)
    return path


def test_loads_valid_file(tmp_path):
    [alice] = load_users(write(tmp_path, VALID))
    assert alice.username == "alice"
    assert alice.roles == frozenset({"admin"})


def test_password_is_hidden_from_repr(tmp_path):
    [alice] = load_users(write(tmp_path, VALID))
    assert "s3cret" not in repr(alice)


def test_unknown_role_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="unknown role"):
        load_users(write(tmp_path, VALID.replace("[admin]", "[superuser]")))


def test_duplicate_usernames_are_rejected(tmp_path):
    body = VALID + VALID.split("users:\n", 1)[1]
    with pytest.raises(ValidationError, match="duplicate username"):
        load_users(write(tmp_path, body))


def test_uppercase_username_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_users(write(tmp_path, VALID.replace("username: alice", "username: Alice")))


def test_user_without_roles_is_allowed(tmp_path):
    [user] = load_users(write(tmp_path, VALID.replace("roles: [admin]", "roles: []")))
    assert user.roles == frozenset()


def test_repo_users_file_defines_the_demo_users():
    users = {u.username: u.roles for u in load_users(REPO_ROOT / "infra/keycloak/users.yaml")}
    assert users == {
        "alice": frozenset({"admin"}),
        "eddie": frozenset({"editor"}),
        "erin": frozenset({"editor"}),
        "victor": frozenset({"viewer"}),
        "nora": frozenset(),
    }


def test_email_is_lowercased_to_match_keycloak_storage(tmp_path):
    [alice] = load_users(write(tmp_path, VALID.replace("alice@example.test", "Alice@Example.TEST")))
    assert alice.email == "alice@example.test"

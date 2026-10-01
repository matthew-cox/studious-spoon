from keycloak_tools.plan import (
    AddRoles,
    CreateUser,
    ExistingUser,
    RemoveRoles,
    SetPassword,
    UpdateProfile,
    plan_changes,
)
from keycloak_tools.users import DesiredUser


def desired(username="eddie", roles=("editor",), **kw) -> DesiredUser:
    fields = {
        "username": username,
        "email": f"{username}@example.test",
        "first_name": username.title(),
        "last_name": "Test",
        "password": "pw",
        "roles": frozenset(roles),
    }
    fields.update(kw)
    return DesiredUser(**fields)


def existing(user: DesiredUser, uid="id-1", roles=None, enabled=True, **kw) -> ExistingUser:
    fields = {
        "id": uid,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "enabled": enabled,
        "roles": frozenset(user.roles if roles is None else roles),
    }
    fields.update(kw)
    return ExistingUser(**fields)


def test_missing_users_are_created():
    eddie, nora = desired(), desired("nora", roles=())
    assert plan_changes([eddie, nora], []) == [CreateUser(eddie), CreateUser(nora)]


def test_identical_state_produces_no_actions():
    eddie = desired()
    assert plan_changes([eddie], [existing(eddie)]) == []


def test_profile_drift_is_corrected():
    eddie = desired()
    assert plan_changes([eddie], [existing(eddie, email="old@example.test")]) == [
        UpdateProfile("id-1", eddie)
    ]


def test_disabled_user_is_re_enabled():
    eddie = desired()
    assert plan_changes([eddie], [existing(eddie, enabled=False)]) == [UpdateProfile("id-1", eddie)]


def test_managed_roles_are_reconciled():
    eddie = desired(roles=("editor",))
    actions = plan_changes([eddie], [existing(eddie, roles={"viewer"})])
    assert actions == [
        AddRoles("id-1", "eddie", frozenset({"editor"})),
        RemoveRoles("id-1", "eddie", frozenset({"viewer"})),
    ]


def test_unmanaged_roles_are_never_removed():
    nora = desired("nora", roles=())
    current = {"default-roles-shortener", "offline_access", "uma_authorization"}
    assert plan_changes([nora], [existing(nora, roles=current)]) == []


def test_passwords_are_only_reset_when_asked():
    eddie = desired()
    assert plan_changes([eddie], [existing(eddie)], reset_passwords=False) == []
    assert plan_changes([eddie], [existing(eddie)], reset_passwords=True) == [
        SetPassword("id-1", "eddie", "pw")
    ]


def test_users_not_in_the_file_are_left_alone():
    eddie, stranger = desired(), desired("stranger")
    assert plan_changes([eddie], [existing(eddie), existing(stranger, uid="id-2")]) == []


def test_set_password_repr_hides_the_password():
    assert "secret-pw" not in repr(SetPassword("id-1", "eddie", "secret-pw"))

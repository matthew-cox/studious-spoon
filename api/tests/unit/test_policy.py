import pytest

from shortener_api.policy import (
    Action,
    Decision,
    LinkFacts,
    Principal,
    decide,
    visible_owner,
)

A, F, N, C = Decision.ALLOW, Decision.FORBIDDEN, Decision.NOT_FOUND, Decision.CONFLICT

ADMIN = Principal("sub-alice", "alice", frozenset({"admin", "default-roles-shortener"}))
EDITOR = Principal("sub-eddie", "eddie", frozenset({"editor"}))
VIEWER = Principal("sub-victor", "victor", frozenset({"viewer"}))
NOBODY = Principal("sub-nora", "nora", frozenset({"default-roles-shortener", "offline_access"}))

OWN = LinkFacts(owner_sub="sub-eddie", blocked=False)
OWN_BLOCKED = LinkFacts(owner_sub="sub-eddie", blocked=True)
OTHERS = LinkFacts(owner_sub="sub-erin", blocked=False)
OTHERS_BLOCKED = LinkFacts(owner_sub="sub-erin", blocked=True)

# (principal, action, link, expected) — spec §6.1, row by row
MATRIX = [
    # list / read / stats
    (ADMIN, Action.LIST, None, A), (EDITOR, Action.LIST, None, A),
    (VIEWER, Action.LIST, None, A), (NOBODY, Action.LIST, None, F),
    (ADMIN, Action.READ, OTHERS, A), (EDITOR, Action.READ, OWN, A),
    (EDITOR, Action.READ, OTHERS, N), (VIEWER, Action.READ, OTHERS, A),
    (NOBODY, Action.READ, OTHERS, F),
    (ADMIN, Action.STATS, OTHERS, A), (EDITOR, Action.STATS, OWN, A),
    (EDITOR, Action.STATS, OTHERS, N), (VIEWER, Action.STATS, OWN, A),
    (EDITOR, Action.READ, OWN_BLOCKED, A),
    # create
    (ADMIN, Action.CREATE, None, A), (EDITOR, Action.CREATE, None, A),
    (VIEWER, Action.CREATE, None, F), (NOBODY, Action.CREATE, None, F),
    # update / delete, not blocked
    (ADMIN, Action.UPDATE, OTHERS, A), (EDITOR, Action.UPDATE, OWN, A),
    (EDITOR, Action.UPDATE, OTHERS, N), (VIEWER, Action.UPDATE, OTHERS, F),
    (NOBODY, Action.UPDATE, OTHERS, F),
    (ADMIN, Action.DELETE, OTHERS, A), (EDITOR, Action.DELETE, OWN, A),
    (EDITOR, Action.DELETE, OTHERS, N), (VIEWER, Action.DELETE, OTHERS, F),
    # update / delete, blocked
    (ADMIN, Action.UPDATE, OTHERS_BLOCKED, A), (EDITOR, Action.UPDATE, OWN_BLOCKED, C),
    (EDITOR, Action.UPDATE, OTHERS_BLOCKED, N), (VIEWER, Action.UPDATE, OTHERS_BLOCKED, F),
    (ADMIN, Action.DELETE, OTHERS_BLOCKED, A), (EDITOR, Action.DELETE, OWN_BLOCKED, C),
    # block / unblock
    (ADMIN, Action.BLOCK, OTHERS, A), (ADMIN, Action.BLOCK, OTHERS_BLOCKED, A),
    (EDITOR, Action.BLOCK, OWN, F), (EDITOR, Action.BLOCK, OTHERS, N),
    (VIEWER, Action.BLOCK, OTHERS, F), (NOBODY, Action.BLOCK, OTHERS, F),
]  # fmt: skip


@pytest.mark.parametrize(("principal", "action", "link", "expected"), MATRIX)
def test_spec_matrix(principal, action, link, expected):
    assert decide(principal, action, link) is expected


def test_editor_who_is_also_viewer_can_read_others_but_not_change_them():
    both = Principal("sub-eddie", "eddie", frozenset({"editor", "viewer"}))
    assert decide(both, Action.READ, OTHERS) is A
    assert decide(both, Action.UPDATE, OTHERS) is F
    assert decide(both, Action.UPDATE, OWN) is A


@pytest.mark.parametrize("action", [Action.READ, Action.UPDATE, Action.DELETE, Action.BLOCK])
def test_link_scoped_actions_require_a_link(action):
    with pytest.raises(ValueError, match="requires a link"):
        decide(EDITOR, action)


def test_decision_values_are_http_statuses():
    assert [d.value for d in Decision] == [200, 403, 404, 409]


@pytest.mark.parametrize(
    ("principal", "expected"),
    [(ADMIN, None), (VIEWER, None), (EDITOR, "sub-eddie"),
     (Principal("s", "u", frozenset({"editor", "viewer"})), None)],
)  # fmt: skip
def test_visible_owner(principal, expected):
    assert visible_owner(principal) == expected


def test_principal_role_helpers_ignore_unmanaged_roles():
    assert NOBODY.managed_roles == frozenset()
    assert ADMIN.managed_roles == frozenset({"admin"})
    assert ADMIN.is_admin and not ADMIN.is_editor and not ADMIN.is_viewer
